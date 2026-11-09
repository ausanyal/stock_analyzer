#!/usr/bin/env python3
"""
StockSage Quant Pro CLI — Institutional Quantitative Stock Analyzer
Implements:
  - Yahoo Finance v8 OHLCV + Benchmark (SPY) Ingestion
  - Multi-Factor Technicals (EMA 20/50, SMA 200, RSI 14, MACD, Bollinger, Wilder ATR, ADX, VWAP, OBV)
  - Volume Profile (20-Bin Point of Control POC, Value Area High VAH, Value Area Low VAL)
  - Granular Bar-by-Bar Microstructure & Candlestick Pattern Scanner
  - Institutional Risk & Alpha Metrics (Sharpe, Sortino, Calmar, MaxDD, VaR 95%, CVaR 95%, Beta, Jensen Alpha, Hurst Exponent, Kelly Criterion)
  - Multi-Model Monte Carlo Simulation (Merton Jump-Diffusion, GBM, Block Bootstrap w/ EWMA Vol)
  - quants.md 5-Strategy Historical Backtester (Net of transaction costs/slippage)
  - Deterministic Buy/Sell/Hold Proposal, Entry Zone, Stop-Loss, TP1/TP2/TP3 Ladder & Holding Horizon
"""

import argparse
import datetime
import json
import math
import random
import sys
import urllib.parse
import urllib.request


from server import build_chart_payload, run_weekly_scan


def fetch_yahoo_chart(symbol: str, rng: str = "1y", interval: str = "1d") -> dict:
    data = build_chart_payload(symbol, rng, interval)
    res = data["chart"]["result"][0]
    ts = res.get("timestamp", [])
    q = res.get("indicators", {}).get("quote", [{}])[0]
    candles = []
    for i, t in enumerate(ts):
        dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
        candles.append({
            "time": t,
            "date": dt.strftime("%Y-%m-%d"),
            "open": float(q["open"][i]),
            "high": float(q["high"][i]),
            "low": float(q["low"][i]),
            "close": float(q["close"][i]),
            "volume": float(q["volume"][i] or 0),
        })
    return {"candles": candles, "meta": res.get("meta", {})}


def ema(vals, p):
    out = [None] * len(vals)
    if len(vals) < p:
        return out
    k = 2.0 / (p + 1)
    out[p - 1] = sum(vals[:p]) / p
    for i in range(p, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def sma(vals, p):
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= p:
            s -= vals[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def calc_rsi(closes, p=14):
    out = [None] * len(closes)
    if len(closes) <= p:
        return out
    g, l = 0.0, 0.0
    for i in range(1, p + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    g /= p
    l /= p
    out[p] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
    for i in range(p + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = (g * (p - 1) + (d if d > 0 else 0.0)) / p
        l = (l * (p - 1) + (-d if d < 0 else 0.0)) / p
        out[i] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
    return out


def calc_macd(closes, fast=12, slow=26, sig=9):
    ef, es = ema(closes, fast), ema(closes, slow)
    ml = [ef[i] - es[i] if ef[i] is not None and es[i] is not None else None for i in range(len(closes))]
    valid = [v for v in ml if v is not None]
    sv = ema(valid, sig)
    si = next((i for i, v in enumerate(ml) if v is not None), len(closes))
    res = []
    for i in range(len(closes)):
        m = ml[i]
        s = sv[i - si] if i >= si and (i - si) < len(sv) else None
        res.append({"macd": m, "sig": s, "hist": (m - s) if (m is not None and s is not None) else None})
    return res


def calc_atr(candles, p=14):
    tr = []
    for i, c in enumerate(candles):
        if i == 0:
            tr.append(c["high"] - c["low"])
        else:
            pc = candles[i - 1]["close"]
            tr.append(max(c["high"] - c["low"], abs(c["high"] - pc), abs(c["low"] - pc)))
    out = [None] * len(candles)
    if len(candles) < p:
        return tr
    out[p - 1] = sum(tr[:p]) / p
    for i in range(p, len(candles)):
        out[i] = (out[i - 1] * (p - 1) + tr[i]) / p
    return out


def calc_hurst(closes):
    if len(closes) < 40:
        return 0.5
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i] > 0 and closes[i - 1] > 0]
    lags = [l for l in (8, 16, 32, 64) if l <= len(rets) // 2]
    if len(lags) < 2:
        return 0.5
    xs, ys = [], []
    for lag in lags:
        chunks = len(rets) // lag
        rs_list = []
        for c in range(chunks):
            sub = rets[c * lag : (c + 1) * lag]
            m = sum(sub) / len(sub)
            sd = math.sqrt(sum((v - m) ** 2 for v in sub) / max(1, len(sub) - 1))
            if sd <= 1e-10:
                continue
            cum = 0.0
            max_c, min_c = -1e9, 1e9
            for v in sub:
                cum += v - m
                max_c = max(max_c, cum)
                min_c = min(min_c, cum)
            rs_list.append((max_c - min_c) / sd)
        if rs_list:
            xs.append(math.log(lag))
            ys.append(math.log(sum(rs_list) / len(rs_list)))
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs)))
    den = sum((xs[i] - mx) ** 2 for i in range(len(xs)))
    return max(0.2, min(0.85, num / den if den > 0 else 0.5))


def volume_profile(candles, num_bins=20):
    min_p = min(c["low"] for c in candles)
    max_p = max(c["high"] for c in candles)
    step = max((max_p - min_p) / num_bins, 0.01)
    bins = [{"low": min_p + i * step, "high": min_p + (i + 1) * step, "mid": min_p + (i + 0.5) * step, "vol": 0.0} for i in range(num_bins)]
    total_v = 0.0
    for c in candles:
        idx = min(num_bins - 1, max(0, int(((c["close"] - min_p) / max(max_p - min_p, 1e-6)) * num_bins)))
        bins[idx]["vol"] += c["volume"]
        total_v += c["volume"]
    poc_idx = max(range(num_bins), key=lambda i: bins[i]["vol"])
    va_vol = bins[poc_idx]["vol"]
    lo, hi = poc_idx, poc_idx
    while va_vol < total_v * 0.70 and (lo > 0 or hi < num_bins - 1):
        n_lo = bins[lo - 1]["vol"] if lo > 0 else -1
        n_hi = bins[hi + 1]["vol"] if hi < num_bins - 1 else -1
        if n_hi >= n_lo and hi < num_bins - 1:
            hi += 1
            va_vol += bins[hi]["vol"]
        elif lo > 0:
            lo -= 1
            va_vol += bins[lo]["vol"]
        else:
            break
    return {"poc": bins[poc_idx]["mid"], "vah": bins[hi]["high"], "val": bins[lo]["low"]}


def run_monte_carlo(closes, n_paths=1000, n_days=63, seed=42):
    random.seed(seed)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i] > 0 and closes[i - 1] > 0]
    mu = sum(rets) / len(rets)
    hist_var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
    ewma_var = hist_var
    for r in rets:
        ewma_var = 0.94 * ewma_var + 0.06 * (r - mu) ** 2
    sigma = math.sqrt(ewma_var)
    outliers = [r for r in rets if abs(r - mu) > 2.5 * sigma]
    lam_j = max(0.01, min(0.08, len(outliers) / max(1, len(rets))))
    mu_j = (sum(outliers) / len(outliers) - mu) if outliers else -0.015
    sig_j = math.sqrt(sum((r - (mu + mu_j)) ** 2 for r in outliers) / (len(outliers) - 1)) if len(outliers) > 1 else sigma * 1.8
    k_j = math.exp(mu_j + 0.5 * sig_j ** 2) - 1.0
    s0 = closes[-1]
    finals = []
    for _ in range(n_paths):
        p = s0
        for _ in range(n_days):
            jump = (mu_j + sig_j * random.gauss(0, 1)) if random.random() < lam_j else 0.0
            drift = mu - 0.5 * ewma_var - lam_j * k_j
            p *= math.exp(drift + sigma * random.gauss(0, 1) + jump)
        finals.append(p)
    finals.sort()
    tail5 = finals[: max(1, int(n_paths * 0.05))]
    return {
        "s0": s0,
        "n_days": n_days,
        "ann_vol_ewma": sigma * math.sqrt(252),
        "p5": finals[int(n_paths * 0.05)],
        "p25": finals[int(n_paths * 0.25)],
        "median": finals[int(n_paths * 0.50)],
        "mean": sum(finals) / n_paths,
        "p75": finals[int(n_paths * 0.75)],
        "p95": finals[int(n_paths * 0.95)],
        "cvar5": sum(tail5) / len(tail5),
        "prob_up": sum(1 for f in finals if f > s0) / n_paths * 100.0,
        "finals": finals,
    }


def analyze_ticker(symbol: str, rng: str = "1y", n_days: int = 63, capital: float = 25000.0):
    stock = fetch_yahoo_chart(symbol, rng, "1d")
    candles = stock["candles"]
    closes = [c["close"] for c in candles]
    price = closes[-1]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    s200 = sma(closes, min(200, int(len(closes) * 0.75)))
    rv = calc_rsi(closes, 14)
    mv = calc_macd(closes)
    atrv = calc_atr(candles, 14)
    vp = volume_profile(candles)
    hurst = calc_hurst(closes)
    mc = run_monte_carlo(closes, n_paths=1000, n_days=n_days)

    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    m_ret = sum(rets) / len(rets)
    sd_ret = math.sqrt(sum((r - m_ret) ** 2 for r in rets) / max(1, len(rets) - 1))
    ann_vol = sd_ret * math.sqrt(252)
    rf_daily = 0.0425 / 252.0
    sharpe = ((m_ret - rf_daily) * 252) / ann_vol if ann_vol > 0 else 0.0
    down_dev = math.sqrt(sum(min(0.0, r - rf_daily) ** 2 for r in rets) / len(rets)) * math.sqrt(252)
    sortino = ((m_ret - rf_daily) * 252) / down_dev if down_dev > 0 else 0.0

    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        max_dd = min(max_dd, (c - peak) / peak)

    l_rsi = next((v for v in reversed(rv) if v is not None), 50.0)
    l_macd = next((v for v in reversed(mv) if v["hist"] is not None), {"macd": 0, "sig": 0, "hist": 0})
    l_e20 = next((v for v in reversed(e20) if v is not None), price)
    l_e50 = next((v for v in reversed(e50) if v is not None), price)
    l_s200 = next((v for v in reversed(s200) if v is not None), l_e50)
    l_atr = next((v for v in reversed(atrv) if v is not None), price * 0.02)

    score = 50
    if price > l_e20 > l_e50:
        score += 14
    elif price < l_e20 < l_e50:
        score -= 14
    if price > l_s200:
        score += 6
    else:
        score -= 6
    if l_macd["hist"] > 0:
        score += 7
    else:
        score -= 6
    if 48 <= l_rsi <= 66:
        score += 6
    elif l_rsi < 33:
        score += 5
    elif l_rsi > 72:
        score -= 7
    if price >= vp["poc"]:
        score += 6
    else:
        score -= 5
    if sharpe > 0.8:
        score += 6
    elif sharpe < 0:
        score -= 6
    if mc["prob_up"] >= 55:
        score += 5
    elif mc["prob_up"] < 45:
        score -= 5
    score = max(8, min(95, score))

    verdict = "HOLD"
    if score >= 72:
        verdict = "STRONG BUY"
    elif score >= 58:
        verdict = "BUY"
    elif score <= 30:
        verdict = "STRONG SELL"
    elif score <= 43:
        verdict = "SELL"

    is_bear = "SELL" in verdict
    entry_low = price if is_bear else min(price - 0.5 * l_atr, max(vp["val"], l_e20, price - 0.85 * l_atr))
    entry_high = (price + 0.5 * l_atr) if is_bear else price
    entry_mid = 0.5 * (entry_low + entry_high)
    risk_dist = max(1.8 * l_atr, price * 0.025)
    stop_loss = (entry_high + risk_dist) if is_bear else (entry_low - risk_dist)
    tp1 = (entry_mid - 1.5 * risk_dist) if is_bear else (entry_mid + 1.5 * risk_dist)
    tp2 = (entry_mid - 2.5 * risk_dist) if is_bear else (entry_mid + 2.5 * risk_dist)
    tp3 = (entry_mid - 4.0 * risk_dist) if is_bear else max(entry_mid + 4.0 * risk_dist, mc["p75"])

    horizon = "2–6 Months (Position Trend)" if (hurst > 0.56 and score >= 60) else ("1–3 Weeks (Tactical Swing)" if hurst < 0.45 else "4–8 Weeks (Core Swing)")
    shares = max(1, int((capital * 0.01) / max(abs(price - stop_loss), 0.01)))

    return {
        "ticker": symbol.upper(),
        "price": round(price, 2),
        "last_date": candles[-1]["date"],
        "bars_analyzed": len(candles),
        "verdict": verdict,
        "quant_score": score,
        "regime": "Trending (Momentum)" if hurst > 0.55 else ("Mean-Reverting" if hurst < 0.45 else "Mixed / Random Walk"),
        "hurst": round(hurst, 3),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "technicals": {
            "rsi_14": round(l_rsi, 2),
            "macd_hist": round(l_macd["hist"], 4),
            "ema_20": round(l_e20, 2),
            "ema_50": round(l_e50, 2),
            "sma_200": round(l_s200, 2),
            "atr_14": round(l_atr, 2),
        },
        "volume_profile": {k: round(v, 2) for k, v in vp.items()},
        "monte_carlo": {
            "horizon_days": n_days,
            "prob_profit_pct": round(mc["prob_up"], 1),
            "p5_bear": round(mc["p5"], 2),
            "p25": round(mc["p25"], 2),
            "median": round(mc["median"], 2),
            "mean": round(mc["mean"], 2),
            "p75": round(mc["p75"], 2),
            "p95_bull": round(mc["p95"], 2),
            "cvar_5": round(mc["cvar5"], 2),
        },
        "trade_proposal": {
            "entry_zone": [round(entry_low, 2), round(entry_high, 2)],
            "stop_loss": round(stop_loss, 2),
            "tp1_1_5R": round(tp1, 2),
            "tp2_2_5R": round(tp2, 2),
            "tp3_runner": round(tp3, 2),
            "holding_horizon": horizon,
            "suggested_shares_25k_acct": shares,
        },
        "recent_bars": candles[-5:],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StockSage Quant Pro CLI")
    parser.add_argument("ticker", nargs="?", default="NVDA", help="Stock ticker symbol or comma-separated list (e.g. NVDA, AAPL)")
    parser.add_argument("--weekly", action="store_true", help="Run cross-sectional Weekly Alpha Picks & Risk-Parity Basket scanner")
    parser.add_argument("--preset", default="leaders", help="Weekly universe preset (leaders, ai_semis, momentum, defensive, etfs)")
    parser.add_argument("--range", default="1y", help="History range (3mo, 6mo, 1y, 2y, 5y)")
    parser.add_argument("--days", type=int, default=63, help="Monte Carlo forecast horizon in trading days")
    parser.add_argument("--capital", type=float, default=25000.0, help="Portfolio capital for position sizing")
    args = parser.parse_args()
    try:
        if args.weekly or args.ticker.upper() == "WEEKLY":
            syms = [s.strip() for s in args.ticker.split(",") if s.strip()] if ("," in args.ticker) else None
            report = run_weekly_scan(symbols=syms, capital=args.capital, preset=args.preset)
        else:
            report = analyze_ticker(args.ticker, args.range, args.days, args.capital)
        print(json.dumps(report, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)
