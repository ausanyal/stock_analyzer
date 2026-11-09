#!/usr/bin/env python3
"""
Local StockSage Quant Pro Server (http://127.0.0.1:8765)
Fetches live, exact market OHLCV bars from Official Nasdaq API + live fundamentals
(P/E, Market Cap, 52W High/Low, Dividend, Beta) from CNBC Real-Time Quote API,
normalized into the Yahoo v8 chart JSON structure expected by StockSage Quant Pro.
"""

import concurrent.futures
import datetime
import http.server
import json
import math
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request

PORT = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_CACHE_LOCK = threading.Lock()
_QUOTE_CACHE = {}
_BARS_CACHE = {}

WEEKLY_PRESETS = {
    "leaders": ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "AMD", "AVGO", "TSLA", "NFLX", "JPM", "V", "LLY", "UNH", "COST", "XOM"],
    "ai_semis": ["NVDA", "AMD", "AVGO", "TSM", "MU", "ANET", "PLTR", "CRM", "ORCL", "MSFT", "GOOG", "META"],
    "momentum": ["TSLA", "PLTR", "COIN", "NFLX", "UBER", "SHOP", "APP", "CRWD", "PANW", "AMD", "NVDA", "META"],
    "defensive": ["JPM", "UNH", "LLY", "JNJ", "PG", "KO", "WMT", "COST", "XOM", "CVX", "CAT", "V"],
    "etfs": ["SPY", "QQQ", "IWM", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "GLD", "TLT"],
}


def parse_num(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r"[^0-9.\-]", "", str(val))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def fetch_cnbc_quote(sym: str) -> dict:
    key = sym.upper().strip()
    now = time.time()
    with _CACHE_LOCK:
        cached = _QUOTE_CACHE.get(key)
        if cached and (now - cached["ts"] < 12.0):
            return dict(cached["meta"])
    try:
        url = (
            "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
            f"?symbols={urllib.parse.quote(key)}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1"
        )
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        q = d.get("FormattedQuoteResult", {}).get("FormattedQuote", [{}])[0]
        meta = {
            "symbol": q.get("symbol", key),
            "longName": q.get("name") or q.get("shortName") or key,
            "shortName": q.get("shortName") or q.get("name") or key,
            "exchangeName": q.get("exchange") or "NASDAQ/NYSE",
            "instrumentType": q.get("issue_type") or "EQUITY",
            "currency": q.get("currencyCode") or "USD",
            "regularMarketPrice": parse_num(q.get("last")),
            "previousClose": parse_num(q.get("previous_day_closing")),
            "change": parse_num(q.get("change")),
            "changePct": parse_num(q.get("change_pct")),
            "open": parse_num(q.get("open")),
            "dayHigh": parse_num(q.get("high")),
            "dayLow": parse_num(q.get("low")),
            "regularMarketVolume": parse_num(q.get("volume")),
            "lastTime": q.get("last_time") or q.get("last_timedate") or "",
            "realTime": q.get("real_time") or "true",
            "fiftyTwoWeekHigh": parse_num(q.get("yrhiprice")),
            "fiftyTwoWeekLow": parse_num(q.get("yrloprice")),
            "trailingPE": parse_num(q.get("pe")),
            "forwardPE": parse_num(q.get("fwd_pe")),
            "marketCapStr": q.get("mktcapView") or "N/A",
            "beta": parse_num(q.get("beta")),
            "dividendYield": parse_num(q.get("dividendyield")),
            "dividendAmount": parse_num(q.get("dividend")),
            "eps": parse_num(q.get("eps")),
        }
        with _CACHE_LOCK:
            _QUOTE_CACHE[key] = {"ts": now, "meta": meta}
        return dict(meta)
    except Exception:
        return {"symbol": key, "longName": key, "shortName": key}


def fetch_nasdaq_bars(sym: str, rng: str) -> list:
    key = (sym.upper().strip(), rng)
    now = time.time()
    with _CACHE_LOCK:
        cached = _BARS_CACHE.get(key)
        if cached and (now - cached["ts"] < 180.0):
            return [dict(b) for b in cached["bars"]]

    days_map = {"1mo": 35, "3mo": 95, "6mo": 185, "1y": 366, "2y": 732, "5y": 1825}
    days = days_map.get(rng, 366)
    to_dt = datetime.date.today() + datetime.timedelta(days=1)
    from_dt = to_dt - datetime.timedelta(days=days)

    for asset_class in ("stocks", "etf", "index"):
        try:
            url = (
                f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(sym.upper().strip())}/historical"
                f"?assetclass={asset_class}&fromdate={from_dt.isoformat()}&limit=1500&todate={to_dt.isoformat()}"
            )
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8"))
            rows = (d.get("data") or {}).get("tradesTable", {}).get("rows") or []
            if not rows:
                continue
            bars = []
            for row in rows:
                dt_str = row.get("date", "")
                try:
                    m, day, yr = map(int, dt_str.split("/"))
                    ts = int(datetime.datetime(yr, m, day, tzinfo=datetime.timezone.utc).timestamp())
                except Exception:
                    continue
                c = parse_num(row.get("close"))
                o = parse_num(row.get("open"))
                h = parse_num(row.get("high"))
                l = parse_num(row.get("low"))
                v = parse_num(row.get("volume")) or 0.0
                if None in (o, h, l, c):
                    continue
                bars.append({"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})
            if bars:
                bars.sort(key=lambda x: x["time"])
                with _CACHE_LOCK:
                    _BARS_CACHE[key] = {"ts": now, "bars": bars}
                return [dict(b) for b in bars]
        except Exception:
            continue
    return []


def build_chart_payload(sym: str, rng: str, intv: str) -> dict:
    meta = fetch_cnbc_quote(sym)
    bars = fetch_nasdaq_bars(sym, rng)
    if not bars:
        raise ValueError(f"No historical market data found for ticker '{sym.upper()}'")

    # Merge or append live real-time session bar from CNBC when market is open / ahead of Nasdaq EOD table
    live_price = meta.get("regularMarketPrice")
    if live_price is not None and live_price > 0:
        last_bar = bars[-1]
        prev_close = meta.get("previousClose")
        live_ts = None

        # 1. Parse exact quote date from CNBC lastTime (e.g. "2026-11-06T11:16:00.000-0500")
        m_dt = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(meta.get("lastTime") or ""))
        if m_dt:
            yr, mo, dy = map(int, m_dt.groups())
            live_ts = int(datetime.datetime(yr, mo, dy, tzinfo=datetime.timezone.utc).timestamp())

        # 2. Fallback: if last historical bar matches previousClose and price has moved, advance to next trading day
        if (live_ts is None or live_ts <= last_bar["time"]) and prev_close is not None:
            if abs(last_bar["close"] - prev_close) < 0.05 and abs(live_price - last_bar["close"]) >= 0.01:
                next_dt = datetime.datetime.fromtimestamp(last_bar["time"], datetime.timezone.utc) + datetime.timedelta(days=1)
                while next_dt.weekday() >= 5:
                    next_dt += datetime.timedelta(days=1)
                live_ts = int(next_dt.timestamp())

        live_open = meta.get("open") or live_price
        live_high = max(meta.get("dayHigh") or live_price, live_open, live_price)
        live_low = min(meta.get("dayLow") or live_price, live_open, live_price)
        live_vol = meta.get("regularMarketVolume") or last_bar["volume"]

        if live_ts is not None and live_ts > last_bar["time"]:
            bars.append({
                "time": live_ts,
                "open": live_open,
                "high": live_high,
                "low": live_low,
                "close": live_price,
                "volume": live_vol,
            })
        else:
            last_bar["close"] = live_price
            if meta.get("open"):
                last_bar["open"] = meta["open"]
            last_bar["high"] = max(last_bar["high"], live_high)
            last_bar["low"] = min(last_bar["low"], live_low)
            if meta.get("regularMarketVolume"):
                last_bar["volume"] = meta["regularMarketVolume"]

    # If weekly interval requested (5Y), aggregate daily bars into weekly bars
    if intv == "1wk" and len(bars) > 60:
        wk_bars = []
        cur_wk = None
        for b in bars:
            dt = datetime.datetime.fromtimestamp(b["time"], datetime.timezone.utc)
            yr_wk = dt.isocalendar()[:2]
            if cur_wk is None or cur_wk["yr_wk"] != yr_wk:
                if cur_wk:
                    wk_bars.append(cur_wk["bar"])
                cur_wk = {"yr_wk": yr_wk, "bar": dict(b)}
            else:
                cb = cur_wk["bar"]
                cb["high"] = max(cb["high"], b["high"])
                cb["low"] = min(cb["low"], b["low"])
                cb["close"] = b["close"]
                cb["volume"] += b["volume"]
        if cur_wk:
            wk_bars.append(cur_wk["bar"])
        bars = wk_bars

    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": [b["time"] for b in bars],
                    "indicators": {
                        "quote": [
                            {
                                "open": [b["open"] for b in bars],
                                "high": [b["high"] for b in bars],
                                "low": [b["low"] for b in bars],
                                "close": [b["close"] for b in bars],
                                "volume": [b["volume"] for b in bars],
                            }
                        ]
                    },
                    "events": {},
                }
            ],
            "error": None,
        }
    }


# ════════════════════════════════════════════════════════════════════════════
#  WEEKLY CROSS-SECTIONAL ALPHA SCANNER & RISK-PARITY BASKET ENGINE
#  Combines Qlib Cross-Sectional Z-Scores, WorldQuant Alpha #101,
#  Mansfield Relative Strength vs SPY, TTM Volatility Squeeze,
#  Connors RSI(2), 5-Day Merton Jump-Diffusion MC, Walk-Forward Hit Rate,
#  and Correlation-Penalized Risk-Parity (HRP-Style) Portfolio Allocation.
# ════════════════════════════════════════════════════════════════════════════
def _ema_series(vals: list, period: int) -> list:
    out = [None] * len(vals)
    if len(vals) < period or period <= 0:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = sum(vals[:period]) / period
    for i in range(period, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1.0 - k)
    return out


def _rsi_series(closes: list, period: int = 14) -> list:
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    g, l = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    g /= period
    l /= period
    out[period] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = (g * (period - 1) + (d if d > 0 else 0.0)) / period
        l = (l * (period - 1) + (-d if d < 0 else 0.0)) / period
        out[i] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
    return out


def _atr_series(bars: list, period: int = 14) -> list:
    if not bars:
        return []
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b["high"] - b["low"])
        else:
            pc = bars[i - 1]["close"]
            tr.append(max(b["high"] - b["low"], abs(b["high"] - pc), abs(b["low"] - pc)))
    out = [None] * len(bars)
    if len(bars) < period:
        return tr
    out[period - 1] = sum(tr[:period]) / period
    for i in range(period, len(bars)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _analyze_weekly_candidate(sym: str, payload: dict, spy_map: dict, spy_closes: list) -> dict:
    res = payload["chart"]["result"][0]
    meta = res.get("meta", {})
    ts_list = res.get("timestamp", [])
    q = res.get("indicators", {}).get("quote", [{}])[0]
    bars = []
    for i, t in enumerate(ts_list):
        bars.append({
            "time": t,
            "open": float(q["open"][i]),
            "high": float(q["high"][i]),
            "low": float(q["low"][i]),
            "close": float(q["close"][i]),
            "volume": float(q["volume"][i] or 0.0),
        })
    if len(bars) < 30:
        raise ValueError(f"Insufficient bars for {sym}")

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["volume"] for b in bars]
    n = len(closes)
    price = closes[-1]

    # 1. Returns & Mansfield Relative Strength vs SPY
    def pct_ret(arr, lookback):
        idx = max(0, len(arr) - 1 - lookback)
        return ((arr[-1] - arr[idx]) / max(arr[idx], 1e-6)) * 100.0

    ret_1d = pct_ret(closes, 1)
    ret_5d = pct_ret(closes, 5)
    ret_21d = pct_ret(closes, 21)
    ret_63d = pct_ret(closes, min(63, n - 1))

    spy_ret_5d = pct_ret(spy_closes, 5) if spy_closes else 0.0
    spy_ret_21d = pct_ret(spy_closes, 21) if spy_closes else 0.0
    spy_ret_63d = pct_ret(spy_closes, min(63, len(spy_closes) - 1)) if spy_closes else 0.0

    rs_5d = ret_5d - spy_ret_5d
    rs_21d = ret_21d - spy_ret_21d
    rs_63d = ret_63d - spy_ret_63d

    # Mansfield Relative Strength (50-day ratio vs SPY)
    ratios = []
    for b in bars[-55:]:
        sc = spy_map.get(b["time"])
        if sc and sc > 0:
            ratios.append(b["close"] / sc)
    if len(ratios) >= 15:
        avg_r = sum(ratios) / len(ratios)
        mansfield_rs = ((ratios[-1] / max(avg_r, 1e-6)) - 1.0) * 100.0
    else:
        mansfield_rs = 0.5 * rs_21d + 0.5 * rs_63d

    # 2. Moving Averages & Oscillators (RSI-14 & Connors RSI-2)
    e10 = _ema_series(closes, 10)
    e20 = _ema_series(closes, 20)
    e50 = _ema_series(closes, 50)
    s200_p = min(200, int(n * 0.8))
    s200 = sum(closes[-s200_p:]) / s200_p
    le10 = e10[-1] or price
    le20 = e20[-1] or price
    le50 = e50[-1] or price

    rsi14_arr = _rsi_series(closes, 14)
    rsi2_arr = _rsi_series(closes, 2)
    l_rsi14 = rsi14_arr[-1] if rsi14_arr[-1] is not None else 50.0
    l_rsi2 = rsi2_arr[-1] if rsi2_arr[-1] is not None else 50.0

    atr_arr = _atr_series(bars, 14)
    l_atr = atr_arr[-1] if atr_arr[-1] is not None else price * 0.022
    atr_pct = (l_atr / price) * 100.0

    # 3. TTM Volatility Squeeze (Bollinger 20,2 vs Keltner 20,1.5*ATR)
    squeeze_flags = []
    for idx in range(max(20, n - 6), n):
        sl = closes[idx - 19 : idx + 1]
        m20 = sum(sl) / 20.0
        sd20 = math.sqrt(sum((x - m20) ** 2 for x in sl) / 20.0)
        bb_u, bb_l = m20 + 2.0 * sd20, m20 - 2.0 * sd20
        em = e20[idx] or m20
        at = atr_arr[idx] or l_atr
        kc_u, kc_l = em + 1.5 * at, em - 1.5 * at
        squeeze_flags.append(bb_u < kc_u and bb_l > kc_l)

    squeeze_now = squeeze_flags[-1] if squeeze_flags else False
    squeeze_prev = any(squeeze_flags[:-1]) if len(squeeze_flags) > 1 else False
    if squeeze_now:
        squeeze_status = "SQUEEZE ON"
    elif squeeze_prev and price > le20:
        squeeze_status = "FIRED BULL"
    else:
        squeeze_status = "NORMAL"

    # 4. WorldQuant Formulaic Alpha #101 & Institutional Volume Accumulation
    avg_vol20 = sum(vols[-20:]) / max(1, len(vols[-20:]))
    wq101_terms = []
    for b in bars[-5:]:
        rng_hl = max(b["high"] - b["low"], 0.001)
        rvol = min(2.5, b["volume"] / max(avg_vol20, 1.0)) if avg_vol20 > 0 else 1.0
        wq101_terms.append(((b["close"] - b["open"]) / rng_hl) * rvol)
    wq_alpha101 = sum(wq101_terms) / len(wq101_terms)

    up_vol, dn_vol = 0.0, 0.0
    for i in range(max(1, n - 10), n):
        if closes[i] >= closes[i - 1]:
            up_vol += vols[i]
        else:
            dn_vol += vols[i]
    vol_acc_ratio = (up_vol / max(dn_vol, 1.0)) if dn_vol > 0 else (2.0 if up_vol > 0 else 1.0)
    vol_acc_ratio = min(3.5, max(0.25, vol_acc_ratio))

    # 5. Weekly Floor Pivots (from trailing 5 trading days)
    w_high = max(highs[-5:])
    w_low = min(lows[-5:])
    w_close = price
    pivot_p = (w_high + w_low + w_close) / 3.0
    pivot_r1 = 2.0 * pivot_p - w_low
    pivot_s1 = 2.0 * pivot_p - w_high
    pivot_r2 = pivot_p + (w_high - w_low)
    pivot_s2 = pivot_p - (w_high - w_low)

    # 6. 5-Day Merton Jump-Diffusion Monte Carlo & Weekly Expected Move (±1σ Cone)
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n) if closes[i] > 0 and closes[i - 1] > 0]
    mu_d = sum(log_rets) / len(log_rets)
    var_d = sum((r - mu_d) ** 2 for r in log_rets) / max(1, len(log_rets) - 1)
    ewma_var = var_d
    for r in log_rets:
        ewma_var = 0.94 * ewma_var + 0.06 * (r - mu_d) ** 2
    sigma_d = math.sqrt(ewma_var)
    sigma_weekly = sigma_d * math.sqrt(5.0)
    exp_move_1sigma_pct = sigma_weekly * 100.0
    exp_low_1sigma = price * (1.0 - sigma_weekly)
    exp_high_1sigma = price * (1.0 + sigma_weekly)

    outliers = [r for r in log_rets if abs(r - mu_d) > 2.4 * sigma_d]
    lam_j = max(0.01, min(0.08, len(outliers) / max(1, len(log_rets))))
    mu_j = (sum(outliers) / len(outliers) - mu_d) if outliers else -0.012
    sig_j = sigma_d * 1.6
    k_j = math.exp(mu_j + 0.5 * sig_j ** 2) - 1.0

    rng_mc = random.Random(hash(sym) & 0xFFFFFFFF)
    mc_finals = []
    n_paths = 500
    for _ in range(n_paths):
        p = price
        for _ in range(5):
            jmp = (mu_j + sig_j * rng_mc.gauss(0, 1)) if rng_mc.random() < lam_j else 0.0
            drift = mu_d - 0.5 * ewma_var - lam_j * k_j
            p *= math.exp(drift + sigma_d * rng_mc.gauss(0, 1) + jmp)
        mc_finals.append(p)
    mc_finals.sort()
    mc_prob_up_5d = (sum(1 for f in mc_finals if f > price) / n_paths) * 100.0
    mc_med_5d = mc_finals[int(n_paths * 0.50)]
    mc_p85_5d = mc_finals[int(n_paths * 0.85)]
    mc_p15_5d = mc_finals[int(n_paths * 0.15)]
    mc_exp_ret_5d = ((sum(mc_finals) / n_paths - price) / price) * 100.0

    # 7. Rolling 5-Day Walk-Forward Validation (Historical 5D Hit Rate in Current Regime)
    is_pullback_mode = (l_rsi2 < 35.0 or l_rsi14 < 47.0) and price >= s200 * 0.94
    wf_rets = []
    for i in range(25, n - 5):
        r14 = rsi14_arr[i] or 50.0
        r2 = rsi2_arr[i] or 50.0
        em20 = e20[i] or closes[i]
        em50 = e50[i] or closes[i]
        if is_pullback_mode:
            cond = (r2 < 40.0 or r14 < 48.0) and closes[i] >= em50 * 0.94
        else:
            cond = closes[i] >= em20 and em20 >= em50 * 0.99 and 45.0 <= r14 <= 72.0
        if cond:
            wf_rets.append(((closes[i + 5] - closes[i]) / closes[i]) * 100.0)
    if len(wf_rets) < 5:
        wf_rets = [((closes[i + 5] - closes[i]) / closes[i]) * 100.0 for i in range(max(1, n - 45), n - 5)]
    wf_win_rate = (sum(1 for r in wf_rets if r > 0) / max(1, len(wf_rets))) * 100.0
    wf_avg_ret = sum(wf_rets) / max(1, len(wf_rets))

    # 8. Fundamental Valuation (P/E, Fwd P/E) & Short-Term Overextension ("Stretched" Guardrail)
    pe_ttm = meta.get("trailingPE")
    pe_fwd = meta.get("forwardPE")
    stretch_20_atr = (price - le20) / max(l_atr, 0.01)
    stretch_50_atr = (price - le50) / max(l_atr, 0.01)

    # High P/E without rapid forward earnings compression (e.g. defensive retail at ~50x P/E)
    fwd_ratio = (pe_fwd / pe_ttm) if (pe_ttm and pe_ttm > 0 and pe_fwd and pe_fwd > 0) else 0.90
    is_val_stretched = bool(pe_ttm is not None and pe_ttm >= 42.0 and fwd_ratio > 0.80)
    is_tech_stretched = bool(
        (stretch_20_atr >= 1.65 or stretch_50_atr >= 3.2 or l_rsi14 >= 64.5)
        and not is_pullback_mode
        and squeeze_status == "NORMAL"
    )

    f_val_stretch = 0.0
    if pe_ttm is not None and pe_ttm > 0:
        if pe_ttm <= 24.0:
            f_val_stretch += 7.0  # Attractive valuation margin of safety
        elif pe_ttm <= 32.0:
            f_val_stretch += 3.0
        elif is_val_stretched:
            f_val_stretch -= min(16.0, (pe_ttm - 35.0) * 0.75)

    if is_tech_stretched:
        # Penalize short-term weekly chasing when already extended above EMA20/50
        f_val_stretch -= min(14.0, max(4.0, (stretch_20_atr - 1.0) * 5.5))
        if wq_alpha101 < 0:
            f_val_stretch -= 6.0  # Intraday exhaustion at highs

    # 9. Raw Factor Composite for Cross-Sectional Z-Scoring
    f_rs = 0.35 * rs_5d + 0.40 * rs_21d + 0.25 * mansfield_rs
    f_flow = wq_alpha101 * 45.0 + (vol_acc_ratio - 1.0) * 18.0
    f_tech = f_val_stretch
    if price > le10 > le20 > le50:
        f_tech += 14.0
    elif price > le20 > le50:
        f_tech += 10.0
    elif price < le20 < le50:
        f_tech -= 14.0
    if price > s200:
        f_tech += 7.0
    else:
        f_tech -= 8.0
    if is_pullback_mode:
        f_tech += 15.0  # Institutional Connors RSI pullback in uptrend
    elif 48.0 <= l_rsi14 <= 62.0:
        f_tech += 9.0
    elif l_rsi14 > 64.5:
        f_tech -= 7.0
    if squeeze_status == "FIRED BULL":
        f_tech += 14.0
    elif squeeze_status == "SQUEEZE ON" and price >= le20:
        f_tech += 11.0

    f_stat = (wf_win_rate - 50.0) * 0.50 + (mc_prob_up_5d - 50.0) * 0.50 + mc_exp_ret_5d * 3.0

    # Map recent 60 aligned daily returns by timestamp for pairwise correlation matrix
    ret_by_ts = {}
    for i in range(max(1, n - 65), n):
        if closes[i] > 0 and closes[i - 1] > 0:
            ret_by_ts[bars[i]["time"]] = math.log(closes[i] / closes[i - 1])

    return {
        "symbol": sym.upper(),
        "name": meta.get("longName") or meta.get("shortName") or sym.upper(),
        "price": round(price, 2),
        "pe_ttm": round(pe_ttm, 1) if pe_ttm is not None else None,
        "pe_fwd": round(pe_fwd, 1) if pe_fwd is not None else None,
        "stretch_20_atr": round(stretch_20_atr, 2),
        "is_val_stretched": is_val_stretched,
        "is_tech_stretched": is_tech_stretched,
        "change_1d_pct": round(meta.get("changePct") if meta.get("changePct") is not None else ret_1d, 2),
        "ret_5d_pct": round(ret_5d, 2),
        "ret_21d_pct": round(ret_21d, 2),
        "rs_5d_vs_spy": round(rs_5d, 2),
        "rs_21d_vs_spy": round(rs_21d, 2),
        "mansfield_rs": round(mansfield_rs, 2),
        "wq_alpha101": round(wq_alpha101, 3),
        "vol_acc_ratio": round(vol_acc_ratio, 2),
        "rsi_14": round(l_rsi14, 1),
        "connors_rsi_2": round(l_rsi2, 1),
        "ema_10": round(le10, 2),
        "ema_20": round(le20, 2),
        "ema_50": round(le50, 2),
        "sma_200": round(s200, 2),
        "atr_14": round(l_atr, 2),
        "atr_pct": round(atr_pct, 2),
        "squeeze_status": squeeze_status,
        "is_pullback_setup": is_pullback_mode,
        "sigma_daily": sigma_d,
        "sigma_weekly_pct": round(exp_move_1sigma_pct, 2),
        "exp_range_1sigma": [round(exp_low_1sigma, 2), round(exp_high_1sigma, 2)],
        "mc_5d": {
            "prob_up_pct": round(mc_prob_up_5d, 1),
            "exp_ret_pct": round(mc_exp_ret_5d, 2),
            "p15_bear": round(mc_p15_5d, 2),
            "median": round(mc_med_5d, 2),
            "p85_bull": round(mc_p85_5d, 2),
        },
        "walk_forward_5d": {
            "win_rate_pct": round(wf_win_rate, 1),
            "avg_ret_pct": round(wf_avg_ret, 2),
            "samples": len(wf_rets),
        },
        "weekly_pivots": {
            "s2": round(pivot_s2, 2),
            "s1": round(pivot_s1, 2),
            "pivot": round(pivot_p, 2),
            "r1": round(pivot_r1, 2),
            "r2": round(pivot_r2, 2),
        },
        "_raw_factors": {
            "rs": f_rs,
            "flow": f_flow,
            "tech": f_tech,
            "stat": f_stat,
        },
        "_ret_by_ts": ret_by_ts,
    }


def _pearson_corr(map_a: dict, map_b: dict) -> float:
    common = [t for t in map_a if t in map_b]
    if len(common) < 10:
        return 0.35
    xs = [map_a[t] for t in common]
    ys = [map_b[t] for t in common]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(common)))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 1e-12 or dy <= 1e-12:
        return 0.0
    return max(-0.99, min(0.99, num / (dx * dy)))


def run_weekly_scan(symbols: list = None, capital: float = 25000.0, preset: str = "leaders") -> dict:
    if not symbols:
        symbols = WEEKLY_PRESETS.get(preset, WEEKLY_PRESETS["leaders"])
    clean_syms = []
    for s in symbols:
        u = s.strip().upper()
        if u and u not in clean_syms:
            clean_syms.append(u)
    clean_syms = clean_syms[:24]

    all_to_fetch = list(clean_syms)
    if "SPY" not in all_to_fetch:
        all_to_fetch.append("SPY")

    payloads = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        fut_map = {pool.submit(build_chart_payload, sym, "6mo", "1d"): sym for sym in all_to_fetch}
        for fut in concurrent.futures.as_completed(fut_map):
            sym = fut_map[fut]
            try:
                payloads[sym] = fut.result()
            except Exception:
                pass

    spy_map = {}
    spy_closes = []
    if "SPY" in payloads:
        sr = payloads["SPY"]["chart"]["result"][0]
        sts = sr.get("timestamp", [])
        sq = sr.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        for t, c in zip(sts, sq):
            if c is not None:
                spy_map[t] = float(c)
                spy_closes.append(float(c))

    candidates = []
    for sym in clean_syms:
        if sym not in payloads:
            continue
        try:
            candidates.append(_analyze_weekly_candidate(sym, payloads[sym], spy_map, spy_closes))
        except Exception:
            continue

    if not candidates:
        raise ValueError("No valid tickers returned from weekly scan.")

    # Cross-Sectional Z-Score Normalization (Qlib Multi-Factor Ranking)
    for fk in ("rs", "flow", "tech", "stat"):
        vals = [c["_raw_factors"][fk] for c in candidates]
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1)) or 1.0
        for c in candidates:
            c["_raw_factors"][fk + "_z"] = max(-2.5, min(2.5, (c["_raw_factors"][fk] - m) / sd))

    for c in candidates:
        rf = c["_raw_factors"]
        # Absolute quality baseline + Cross-sectional Z-score boost
        raw_abs = (
            52.0
            + 0.45 * max(-25.0, min(25.0, rf["tech"]))
            + 0.35 * max(-20.0, min(20.0, rf["rs"]))
            + 0.30 * max(-20.0, min(20.0, rf["flow"]))
            + 0.35 * max(-20.0, min(20.0, rf["stat"]))
        )
        z_comp = 0.32 * rf["tech_z"] + 0.28 * rf["rs_z"] + 0.20 * rf["flow_z"] + 0.20 * rf["stat_z"]
        score = int(round(max(12.0, min(97.0, 0.65 * raw_abs + 0.35 * (55.0 + z_comp * 18.0)))))

        price = c["price"]
        atr = c["atr_14"]
        sig_w = c["sigma_weekly_pct"] / 100.0
        piv = c["weekly_pivots"]
        pe_str = f"P/E {c['pe_ttm']}x" if c.get("pe_ttm") else "N/A P/E"

        # Guardrail: Prevent short-term weekly chasing on stocks that are both Valuation-Stretched & Technically Overextended
        if c["is_val_stretched"] and c["is_tech_stretched"]:
            score = min(score, 55)
            action = "WAIT PULLBACK / DCA"
            setup_type = "⚠️ STRETCHED / RICH P/E"
            rationale = (
                f"High-quality momentum ({c['rs_21d_vs_spy']:+.1f}% vs SPY), but stretched +{c['stretch_20_atr']:.1f}× ATR above EMA20 "
                f"(RSI {c['rsi_14']:.1f}) at a steep {pe_str} — risky 1-week chase; wait for pullback to ${c['ema_20']} or DCA."
            )
        elif c["is_tech_stretched"] and c["wq_alpha101"] < 0:
            score = min(score, 56)
            action = "WAIT PULLBACK"
            setup_type = "⚠️ SHORT-TERM STRETCHED"
            rationale = (
                f"Extended +{c['stretch_20_atr']:.1f}× ATR above EMA20 (${c['ema_20']}) with RSI {c['rsi_14']:.1f} "
                f"and negative 5D intraday body ({c['wq_alpha101']:+.2f}); wait for dip toward S1 (${piv['s1']})."
            )
        else:
            if score >= 70:
                action = "STRONG BUY"
            elif score >= 58:
                action = "BUY"
            elif score <= 38:
                action = "AVOID / SHORT"
            else:
                action = "WATCH / HOLD"

            if c["squeeze_status"] in ("SQUEEZE ON", "FIRED BULL") and score >= 58:
                setup_type = "⚡ TTM VOL SQUEEZE"
                rationale = (
                    f"Volatility coil ({c['squeeze_status']}, {pe_str}) with Mansfield RS {c['mansfield_rs']:+.1f}%, "
                    f"WQ Alpha#101 {c['wq_alpha101']:+.2f}, and {c['walk_forward_5d']['win_rate_pct']:.0f}% 5D walk-forward hit rate."
                )
            elif c["is_pullback_setup"] and score >= 56:
                setup_type = "🎯 DIP-BUY PULLBACK"
                rationale = (
                    f"Secular uptrend (>SMA200 ${c['sma_200']}, {pe_str}) with oversold Connors RSI(2)={c['connors_rsi_2']:.0f} "
                    f"into weekly support S1 (${piv['s1']}); {c['walk_forward_5d']['win_rate_pct']:.0f}% historical 5D bounce rate."
                )
            elif score >= 58:
                setup_type = "🚀 MOMENTUM LEADER"
                rationale = (
                    f"Outperforming SPY by {c['rs_21d_vs_spy']:+.1f}% (1M, {pe_str}) with {c['vol_acc_ratio']:.2f}× volume accumulation "
                    f"and {c['mc_5d']['prob_up_pct']:.0f}% 5D Monte Carlo win probability."
                )
            elif score <= 38:
                setup_type = "🛡️ RELATIVE LAGGARD"
                rationale = (
                    f"Trailing SPY ({c['rs_21d_vs_spy']:+.1f}% 1M) with weak money flow ({c['vol_acc_ratio']:.2f}× Up/Dn vol) "
                    f"and below key moving averages."
                )
            else:
                setup_type = "⚖️ RANGE / NEUTRAL"
                rationale = (
                    f"Consolidating near Weekly Pivot (${piv['pivot']}, {pe_str}) with RSI(14)={c['rsi_14']:.1f}; "
                    f"await breakout above ${piv['r1']} or pullback to ${piv['s1']}."
                )

        c["weekly_alpha_score"] = score
        c["weekly_action"] = action
        c["setup_type"] = setup_type
        c["rationale"] = rationale

        # Mon-Fri Execution Plan (Entry Zone, Weekly ATR Stop, Friday TP1 & TP2)
        is_bear = action == "AVOID / SHORT"
        if is_bear:
            entry_low = price
            entry_high = round(price + 0.30 * atr, 2)
            entry_mid = 0.5 * (entry_low + entry_high)
            stop_loss = round(entry_high + 1.15 * atr, 2)
            tp1 = round(entry_mid - max(1.85 * atr, price * sig_w * 0.90), 2)
            tp2 = round(min(piv["s1"], entry_mid - max(2.80 * atr, price * sig_w * 1.45)), 2)
        else:
            entry_low = round(max(piv["s1"], price - 0.35 * atr), 2) if price > piv["s1"] else round(price - 0.30 * atr, 2)
            entry_high = price
            entry_mid = 0.5 * (entry_low + entry_high)
            stop_loss = round(min(entry_low, price) - 1.15 * atr, 2)
            tp1 = round(entry_mid + max(1.85 * atr, price * sig_w * 0.90), 2)
            tp2 = round(max(piv["r1"], entry_mid + max(2.80 * atr, price * sig_w * 1.45)), 2)

        risk_per_sh = max(abs(entry_mid - stop_loss), 0.01)
        rr_weekly = abs(tp1 - entry_mid) / risk_per_sh
        c["weekly_plan"] = {
            "entry_zone": [round(min(entry_low, entry_high), 2), round(max(entry_low, entry_high), 2)],
            "stop_loss": stop_loss,
            "stop_pct": round(((stop_loss - price) / price) * 100.0, 2),
            "friday_tp1": tp1,
            "tp1_pct": round(((tp1 - price) / price) * 100.0, 2),
            "friday_tp2": tp2,
            "tp2_pct": round(((tp2 - price) / price) * 100.0, 2),
            "rr_ratio": round(rr_weekly, 2),
        }

    candidates.sort(key=lambda x: x["weekly_alpha_score"], reverse=True)
    for idx, c in enumerate(candidates):
        c["rank"] = idx + 1

    # Top 5 Bullish Weekly Picks (excluding Stretched/Rich-PE & Laggards) -> Correlation-Penalized Risk-Parity Basket
    top_picks = [c for c in candidates if c["weekly_action"] in ("STRONG BUY", "BUY")][:5]
    if not top_picks:
        top_picks = candidates[: min(5, len(candidates))]

    k_top = len(top_picks)
    corr_matrix = [[1.0] * k_top for _ in range(k_top)]
    for i in range(k_top):
        for j in range(i + 1, k_top):
            rho = _pearson_corr(top_picks[i]["_ret_by_ts"], top_picks[j]["_ret_by_ts"])
            corr_matrix[i][j] = round(rho, 2)
            corr_matrix[j][i] = round(rho, 2)

    raw_weights = []
    for i, c in enumerate(top_picks):
        if k_top > 1:
            avg_pos_corr = sum(max(0.0, corr_matrix[i][j]) for j in range(k_top) if j != i) / (k_top - 1)
        else:
            avg_pos_corr = 0.0
        c["avg_basket_corr"] = round(avg_pos_corr, 2)
        # HRP-inspired weight: Conviction^1.5 * (1 - 0.55 * AvgPositiveCorrelation) / DailyVol
        conv = (c["weekly_alpha_score"] / 100.0) ** 1.5
        div_mult = max(0.35, 1.0 - 0.55 * avg_pos_corr)
        inv_vol = 1.0 / max(c["sigma_daily"], 0.008)
        raw_weights.append(conv * div_mult * inv_vol)

    sum_w = sum(raw_weights) or 1.0
    norm_weights = [w / sum_w for w in raw_weights]
    # Cap single-position concentration at 32% and redistribute
    for _ in range(3):
        excess = 0.0
        uncapped = 0
        for i in range(k_top):
            if norm_weights[i] > 0.32:
                excess += norm_weights[i] - 0.32
                norm_weights[i] = 0.32
            else:
                uncapped += 1
        if excess > 0 and uncapped > 0:
            for i in range(k_top):
                if norm_weights[i] < 0.32:
                    norm_weights[i] += excess / uncapped

    basket = []
    total_alloc = 0.0
    total_risk = 0.0
    weighted_exp_ret_5d = 0.0
    for i, c in enumerate(top_picks):
        w = norm_weights[i]
        target_dollars = capital * w
        shares = max(1, int(target_dollars // c["price"])) if target_dollars >= c["price"] else 0
        actual_dollars = round(shares * c["price"], 2)
        stop_dist = abs(c["price"] - c["weekly_plan"]["stop_loss"])
        risk_dollars = round(shares * stop_dist, 2)
        total_alloc += actual_dollars
        total_risk += risk_dollars
        weighted_exp_ret_5d += w * c["mc_5d"]["exp_ret_pct"]
        basket.append({
            "rank": i + 1,
            "symbol": c["symbol"],
            "name": c["name"],
            "setup_type": c["setup_type"],
            "weekly_alpha_score": c["weekly_alpha_score"],
            "price": c["price"],
            "weight_pct": round(w * 100.0, 1),
            "shares": shares,
            "alloc_dollars": actual_dollars,
            "stop_loss": c["weekly_plan"]["stop_loss"],
            "friday_tp1": c["weekly_plan"]["friday_tp1"],
            "risk_dollars": risk_dollars,
            "avg_pair_corr": c["avg_basket_corr"],
        })

    # Clean internal fields before JSON serialization
    for c in candidates:
        c.pop("_raw_factors", None)
        c.pop("_ret_by_ts", None)
        c.pop("sigma_daily", None)

    today_dt = datetime.date.today()
    # Calculate Monday–Friday window label for the current/upcoming trading week
    days_since_mon = today_dt.weekday()
    mon_dt = today_dt - datetime.timedelta(days=days_since_mon) if days_since_mon <= 4 else today_dt + datetime.timedelta(days=(7 - days_since_mon))
    fri_dt = mon_dt + datetime.timedelta(days=4)

    return {
        "as_of": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "week_window": f"{mon_dt.strftime('%b %d')} – {fri_dt.strftime('%b %d, %Y')}",
        "benchmark": "SPY",
        "spy_ret_5d_pct": round(((spy_closes[-1] - spy_closes[max(0, len(spy_closes) - 6)]) / spy_closes[max(0, len(spy_closes) - 6)]) * 100.0, 2) if len(spy_closes) > 5 else 0.0,
        "universe_size": len(candidates),
        "capital": capital,
        "top_picks": candidates[:5],
        "portfolio_basket": {
            "methodology": "Correlation-Penalized Inverse-Vol Risk Parity (HRP-Style)",
            "capital": capital,
            "allocated_dollars": round(total_alloc, 2),
            "cash_reserve_dollars": round(max(0.0, capital - total_alloc), 2),
            "total_stop_risk_dollars": round(total_risk, 2),
            "total_stop_risk_pct": round((total_risk / max(capital, 1.0)) * 100.0, 2),
            "expected_5d_basket_return_pct": round(weighted_exp_ret_5d, 2),
            "correlation_matrix": {
                "symbols": [p["symbol"] for p in top_picks],
                "matrix": corr_matrix,
            },
            "allocations": basket,
        },
        "rankings": candidates,
    }


class StockSageHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/chart":
            qs = urllib.parse.parse_qs(parsed.query)
            sym = qs.get("symbol", ["NVDA"])[0]
            rng = qs.get("range", ["1y"])[0]
            intv = qs.get("interval", ["1d"])[0]
            try:
                payload = build_chart_payload(sym, rng, intv)
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"chart": {"result": None, "error": {"description": str(exc)}}}).encode("utf-8")
                )
            return

        if parsed.path == "/api/weekly-scan":
            qs = urllib.parse.parse_qs(parsed.query)
            preset = qs.get("preset", ["leaders"])[0]
            syms_raw = qs.get("symbols", [""])[0]
            cap_raw = qs.get("capital", ["25000"])[0]
            try:
                capital = float(cap_raw)
            except ValueError:
                capital = 25000.0
            syms = [s.strip() for s in syms_raw.split(",") if s.strip()] if syms_raw else None
            try:
                report = run_weekly_scan(symbols=syms, capital=capital, preset=preset)
                body = json.dumps(report).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if parsed.path == "/api/shutdown":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"shutdown":true}')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        return super().do_GET()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), StockSageHandler)
    except OSError:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/shutdown", timeout=2)
        except Exception:
            pass
        time.sleep(0.5)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), StockSageHandler)
    print(f"StockSage Quant Pro Server (Nasdaq + CNBC Live Feed) running at http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
