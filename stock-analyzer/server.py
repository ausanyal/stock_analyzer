#!/usr/bin/env python3
"""
Local StockSage Quant Pro Server (http://127.0.0.1:8765)
Fetches live, exact market OHLCV bars from Official Nasdaq API + live fundamentals
(P/E, Market Cap, 52W High/Low, Dividend, Beta) from CNBC Real-Time Quote API,
normalized into the Yahoo v8 chart JSON structure expected by StockSage Quant Pro.
"""

import concurrent.futures
import datetime
import http.cookiejar
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

PORT = int(os.environ.get("PORT", 8765))
HOST = os.environ.get("HOST", "0.0.0.0")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_CACHE_LOCK = threading.Lock()
_QUOTE_CACHE = {}
_BARS_CACHE = {}
_INDIA_FUND_CACHE = {}
_NEWS_CACHE = {}
_INTEL_CACHE = {}
_YAHOO_SESSION = {"cookie": "", "crumb": "", "ts": 0.0}

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

WEEKLY_PRESETS = {
    "leaders": ["NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "AMD", "AVGO", "TSLA", "NFLX", "JPM", "V", "LLY", "UNH", "COST", "XOM"],
    "ai_semis": ["NVDA", "AMD", "AVGO", "TSM", "MU", "ANET", "PLTR", "CRM", "ORCL", "MSFT", "GOOG", "META"],
    "momentum": ["TSLA", "PLTR", "COIN", "NFLX", "UBER", "SHOP", "APP", "CRWD", "PANW", "AMD", "NVDA", "META"],
    "defensive": ["JPM", "UNH", "LLY", "JNJ", "PG", "KO", "WMT", "COST", "XOM", "CVX", "CAT", "V"],
    "etfs": ["SPY", "QQQ", "IWM", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "GLD", "TLT"],
}

INDIA_WEEKLY_PRESETS = {
    "in_leaders": ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN", "LT", "ITC", "SUNPHARMA", "M&M", "AXISBANK", "BAJFINANCE", "NTPC", "MARUTI", "TITAN"],
    "in_it": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "KPITTECH", "TATAELXSI", "OFSS"],
    "in_banks": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "BAJAJFINSV", "INDUSINDBK", "BANKBARODA", "PNB", "CHOLAFIN", "PFC"],
    "in_infra_auto": ["LT", "M&M", "MARUTI", "BAJAJ-AUTO", "EICHERMOT", "TVSMOTOR", "HAL", "BEL", "NTPC", "POWERGRID", "ADANIPORTS", "ULTRACEMCO"],
    "in_pharma_fmcg": ["SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "APOLLOHOSP", "LUPIN", "ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "TITAN", "TRENT"],
}

INDIA_INDEX_MAP = {
    "NIFTY50": ("in;NSX", "NIFTY 50 Index"),
    "BANKNIFTY": ("in;nbx", "NIFTY Bank Index"),
    "SENSEX": ("in;SEN", "BSE SENSEX Index"),
}

INDIA_COMPANY_SLUGS = {
    "RELIANCE": ("reliance-industries-ltd", "Reliance Industries Ltd."),
    "TCS": ("tata-consultancy-services-ltd", "Tata Consultancy Services Ltd."),
    "HDFCBANK": ("hdfc-bank-ltd", "HDFC Bank Ltd."),
    "ICICIBANK": ("icici-bank-ltd", "ICICI Bank Ltd."),
    "INFY": ("infosys-ltd", "Infosys Ltd."),
    "BHARTIARTL": ("bharti-airtel-ltd", "Bharti Airtel Ltd."),
    "SBIN": ("state-bank-of-india", "State Bank of India"),
    "LT": ("larsen-toubro-ltd", "Larsen & Toubro Ltd."),
    "ITC": ("itc-ltd", "ITC Ltd."),
    "SUNPHARMA": ("sun-pharmaceutical-industries-ltd", "Sun Pharmaceutical Industries Ltd."),
    "M&M": ("mahindra-mahindra-ltd", "Mahindra & Mahindra Ltd."),
    "AXISBANK": ("axis-bank-ltd", "Axis Bank Ltd."),
    "BAJFINANCE": ("bajaj-finance-ltd", "Bajaj Finance Ltd."),
    "NTPC": ("ntpc-ltd", "NTPC Ltd."),
    "MARUTI": ("maruti-suzuki-india-ltd", "Maruti Suzuki India Ltd."),
    "TITAN": ("titan-company-ltd", "Titan Company Ltd."),
    "TRENT": ("trent-ltd", "Trent Ltd."),
    "HAL": ("hindustan-aeronautics-ltd", "Hindustan Aeronautics Ltd."),
    "BEL": ("bharat-electronics-ltd", "Bharat Electronics Ltd."),
    "TATAMOTORS": ("tata-motors-ltd", "Tata Motors Ltd."),
    "TATASTEEL": ("tata-steel-ltd", "Tata Steel Ltd."),
    "WIPRO": ("wipro-ltd", "Wipro Ltd."),
    "HCLTECH": ("hcl-technologies-ltd", "HCL Technologies Ltd."),
    "KOTAKBANK": ("kotak-mahindra-bank-ltd", "Kotak Mahindra Bank Ltd."),
    "ULTRACEMCO": ("ultratech-cement-ltd", "UltraTech Cement Ltd."),
    "ADANIENT": ("adani-enterprises-ltd", "Adani Enterprises Ltd."),
    "ADANIPORTS": ("adani-ports-and-special-economic-zone-ltd", "Adani Ports & SEZ Ltd."),
    "POWERGRID": ("power-grid-corporation-of-india-ltd", "Power Grid Corp. of India Ltd."),
    "BAJAJ-AUTO": ("bajaj-auto-ltd", "Bajaj Auto Ltd."),
    "EICHERMOT": ("eicher-motors-ltd", "Eicher Motors Ltd."),
    "HINDUNILVR": ("hindustan-unilever-ltd", "Hindustan Unilever Ltd."),
    "NESTLEIND": ("nestle-india-ltd", "Nestle India Ltd."),
    "ASIANPAINT": ("asian-paints-ltd", "Asian Paints Ltd."),
    "PFC": ("power-finance-corporation-ltd", "Power Finance Corporation Ltd."),
    "PNB": ("punjab-national-bank", "Punjab National Bank"),
}


def normalize_india_symbol(sym: str) -> str:
    s = sym.upper().strip()
    s = re.sub(r"\.(NS|BO)$", "", s)
    if s in ("^NSEI", "NIFTY", "NIFTY 50", "NIFTY_50", "NIFTY50"):
        return "NIFTY50"
    if s in ("^BSESN", "BSE SENSEX", "SENSEX"):
        return "SENSEX"
    if s in ("^NSEBANK", "NIFTY BANK", "BANKNIFTY"):
        return "BANKNIFTY"
    return s


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


def _fetch_yahoo_v8_bars(y_sym: str, rng: str) -> list:
    y_rng = rng if rng in ("1mo", "3mo", "6mo", "1y", "2y", "5y") else "1y"
    y_cookie, y_crumb = _get_yahoo_session()
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        try:
            crumb_q = f"&crumb={urllib.parse.quote(y_crumb)}" if y_crumb else ""
            url = (
                f"https://{host}/v8/finance/chart/{urllib.parse.quote(y_sym)}"
                f"?range={y_rng}&interval=1d&includePrePost=false&events=div%2Csplit{crumb_q}"
            )
            d = _yahoo_get_json(url, y_cookie, timeout=7)
            res = ((d.get("chart") or {}).get("result") or [None])[0]
            if not res:
                continue
            ts_list = res.get("timestamp") or []
            q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            o_arr = q.get("open") or []
            h_arr = q.get("high") or []
            l_arr = q.get("low") or []
            c_arr = q.get("close") or []
            v_arr = q.get("volume") or []
            by_day = {}
            for i, raw_ts in enumerate(ts_list):
                if i >= len(c_arr):
                    break
                o = parse_num(o_arr[i] if i < len(o_arr) else None)
                h = parse_num(h_arr[i] if i < len(h_arr) else None)
                l = parse_num(l_arr[i] if i < len(l_arr) else None)
                c = parse_num(c_arr[i])
                v = parse_num(v_arr[i] if i < len(v_arr) else 0.0) or 0.0
                if None in (o, h, l, c):
                    continue
                dt_utc = datetime.datetime.fromtimestamp(int(raw_ts), datetime.timezone.utc)
                day_ts = int(datetime.datetime(dt_utc.year, dt_utc.month, dt_utc.day, tzinfo=datetime.timezone.utc).timestamp())
                by_day[day_ts] = {"time": day_ts, "open": o, "high": h, "low": l, "close": c, "volume": v}
            if by_day:
                return [by_day[k] for k in sorted(by_day.keys())]
        except Exception:
            continue
    return []


def fetch_nasdaq_bars(sym: str, rng: str) -> list:
    key = (sym.upper().strip(), rng)
    now = time.time()
    with _CACHE_LOCK:
        cached = _BARS_CACHE.get(key)
        if cached and (now - cached["ts"] < 180.0):
            return [dict(b) for b in cached["bars"]]

    # 1. Primary Cloud-Safe Feed: Yahoo Finance v8 Chart API (works on both Cloud Run datacenter IPs and local)
    y_bars = _fetch_yahoo_v8_bars(sym.upper().strip(), rng)
    if y_bars:
        with _CACHE_LOCK:
            _BARS_CACHE[key] = {"ts": now, "bars": y_bars}
        return [dict(b) for b in y_bars]

    # 2. Fallback: Official Nasdaq Historical API
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
            with urllib.request.urlopen(req, timeout=5) as r:
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


def fetch_india_fundamentals(norm_sym: str) -> dict:
    now = time.time()
    with _CACHE_LOCK:
        cached = _INDIA_FUND_CACHE.get(norm_sym)
        if cached and (now - cached["ts"] < 3600.0):
            return dict(cached["data"])

    search_id = None
    long_name = norm_sym
    if norm_sym in INDIA_COMPANY_SLUGS:
        search_id, long_name = INDIA_COMPANY_SLUGS[norm_sym]
    else:
        try:
            s_url = f"https://groww.in/v1/api/search/v1/entity?app=false&entity_type=Stocks&page=0&q={urllib.parse.quote(norm_sym)}&size=20"
            req = urllib.request.Request(s_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=6) as r:
                sd = json.loads(r.read().decode("utf-8"))
            items = sd.get("content", [])
            for it in items:
                if (it.get("nse_scrip_code") or "").upper() == norm_sym:
                    search_id = it.get("search_id")
                    long_name = it.get("title") or it.get("company_short_name") or norm_sym
                    break
            if not search_id and items:
                search_id = items[0].get("search_id")
                long_name = items[0].get("title") or items[0].get("company_short_name") or norm_sym
        except Exception:
            pass

    out = {"longName": long_name, "shortName": long_name}
    if search_id:
        try:
            c_url = f"https://groww.in/v1/api/stocks_data/v1/company/search_id/{urllib.parse.quote(search_id)}?page=0&size=1"
            req = urllib.request.Request(c_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=6) as r:
                cd = json.loads(r.read().decode("utf-8"))
            hdr = cd.get("header") or {}
            if hdr.get("displayName"):
                out["longName"] = hdr["displayName"]
                out["shortName"] = hdr.get("shortName") or hdr["displayName"]
            f_map = {}
            for f in cd.get("fundamentals") or []:
                k = f.get("name") or f.get("shortName")
                if k:
                    f_map[k] = f.get("value")
            out["trailingPE"] = parse_num(f_map.get("P/E Ratio(TTM)"))
            out["industryPE"] = parse_num(f_map.get("Industry P/E"))
            out["marketCapStr"] = f_map.get("Market Cap") or "N/A"
            out["dividendYield"] = parse_num(f_map.get("Dividend Yield"))
            out["eps"] = parse_num(f_map.get("EPS(TTM)"))
            out["pbRatio"] = parse_num(f_map.get("P/B Ratio"))
            out["roe"] = parse_num(f_map.get("ROE"))
        except Exception:
            pass

    with _CACHE_LOCK:
        _INDIA_FUND_CACHE[norm_sym] = {"ts": now, "data": out}
    return dict(out)


def fetch_india_quote(sym: str) -> dict:
    norm_sym = normalize_india_symbol(sym)
    cache_key = "IN:" + norm_sym
    now = time.time()
    with _CACHE_LOCK:
        cached = _QUOTE_CACHE.get(cache_key)
        if cached and (now - cached["ts"] < 12.0):
            return dict(cached["meta"])

    if norm_sym in INDIA_INDEX_MAP:
        _, idx_name = INDIA_INDEX_MAP[norm_sym]
        meta = {
            "symbol": norm_sym,
            "longName": idx_name,
            "shortName": idx_name,
            "exchangeName": "NSE INDIA INDEX",
            "instrumentType": "INDEX",
            "currency": "INR",
            "market": "IN",
            "marketCapStr": "N/A",
        }
        return meta

    fund = fetch_india_fundamentals(norm_sym)
    meta = {
        "symbol": norm_sym,
        "longName": fund.get("longName") or norm_sym,
        "shortName": fund.get("shortName") or norm_sym,
        "exchangeName": "NSE INDIA",
        "instrumentType": "EQUITY",
        "currency": "INR",
        "market": "IN",
        "trailingPE": fund.get("trailingPE"),
        "industryPE": fund.get("industryPE"),
        "forwardPE": None,
        "marketCapStr": fund.get("marketCapStr") or "N/A",
        "beta": None,
        "dividendYield": fund.get("dividendYield"),
        "dividendAmount": None,
        "eps": fund.get("eps"),
    }
    try:
        url = f"https://groww.in/v1/api/stocks_data/v1/tr_live_prices/exchange/NSE/segment/CASH/{urllib.parse.quote(norm_sym)}/latest"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=7) as r:
            q = json.loads(r.read().decode("utf-8"))
        ts_ms = q.get("tsInMillis")
        last_time_str = ""
        if ts_ms:
            dt_ist = datetime.datetime.fromtimestamp(float(ts_ms) / 1000.0, IST_TZ)
            last_time_str = dt_ist.strftime("%Y-%m-%dT%H:%M:%S+0530")
        meta.update({
            "regularMarketPrice": parse_num(q.get("ltp")),
            "previousClose": parse_num(q.get("close")),
            "change": parse_num(q.get("dayChange")),
            "changePct": parse_num(q.get("dayChangePerc")),
            "open": parse_num(q.get("open")),
            "dayHigh": parse_num(q.get("high")),
            "dayLow": parse_num(q.get("low")),
            "regularMarketVolume": parse_num(q.get("volume")),
            "lastTime": last_time_str,
            "realTime": "true",
            "fiftyTwoWeekHigh": parse_num(q.get("yearHighPrice")),
            "fiftyTwoWeekLow": parse_num(q.get("yearLowPrice")),
        })
        with _CACHE_LOCK:
            _QUOTE_CACHE[cache_key] = {"ts": now, "meta": meta}
    except Exception:
        pass
    return dict(meta)


def fetch_india_bars(sym: str, rng: str) -> list:
    norm_sym = normalize_india_symbol(sym)
    cache_key = ("IN:" + norm_sym, rng)
    now = time.time()
    with _CACHE_LOCK:
        cached = _BARS_CACHE.get(cache_key)
        if cached and (now - cached["ts"] < 180.0):
            return [dict(b) for b in cached["bars"]]

    days_map = {"1mo": 35, "3mo": 95, "6mo": 185, "1y": 366, "2y": 732, "5y": 1825}
    days = days_map.get(rng, 366)
    countback = max(35, int(days * 0.75))
    to_ts = int(now) + 86400
    from_ts = int(now) - days * 86400

    # 1. Primary: Moneycontrol TradingView UDF History API (NSE Equities & Indices)
    try:
        if norm_sym in INDIA_INDEX_MAP:
            mc_sym = INDIA_INDEX_MAP[norm_sym][0]
            url = (
                f"https://priceapi.moneycontrol.com/techCharts/indianMarket/index/history"
                f"?symbol={urllib.parse.quote(mc_sym)}&resolution=1D&from={from_ts}&to={to_ts}&countback={countback}"
            )
        else:
            url = (
                f"https://priceapi.moneycontrol.com/techCharts/indianMarket/stock/history"
                f"?symbol={urllib.parse.quote(norm_sym)}&resolution=1D&from={from_ts}&to={to_ts}&countback={countback}&currencyCode=INR"
            )
        mc_headers = {**HEADERS, "Referer": "https://www.moneycontrol.com/", "Origin": "https://www.moneycontrol.com"}
        req = urllib.request.Request(url, headers=mc_headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("s") == "ok" and d.get("t"):
            ts_arr = d.get("t", [])
            o_arr = d.get("o", [])
            h_arr = d.get("h", [])
            l_arr = d.get("l", [])
            c_arr = d.get("c", [])
            v_arr = d.get("v") or [0.0] * len(ts_arr)
            by_day = {}
            for i, raw_ts in enumerate(ts_arr):
                dt_ist = datetime.datetime.fromtimestamp(int(raw_ts), IST_TZ)
                day_ts = int(datetime.datetime(dt_ist.year, dt_ist.month, dt_ist.day, tzinfo=datetime.timezone.utc).timestamp())
                if day_ts < from_ts:
                    continue
                o, h, l, c = parse_num(o_arr[i]), parse_num(h_arr[i]), parse_num(l_arr[i]), parse_num(c_arr[i])
                v = parse_num(v_arr[i]) if i < len(v_arr) else 0.0
                if None in (o, h, l, c):
                    continue
                by_day[day_ts] = {"time": day_ts, "open": o, "high": h, "low": l, "close": c, "volume": v or 0.0}
            if by_day:
                bars = [by_day[k] for k in sorted(by_day.keys())]
                with _CACHE_LOCK:
                    _BARS_CACHE[cache_key] = {"ts": now, "bars": bars}
                return [dict(b) for b in bars]
    except Exception:
        pass

    # 2. Fallback: Groww Delayed NSE Charting Service
    try:
        url = (
            f"https://groww.in/v1/api/charting_service/v2/chart/delayed/exchange/NSE/segment/CASH/{urllib.parse.quote(norm_sym)}"
            f"?endTimeInMillis={to_ts * 1000}&intervalInMinutes=1440&startTimeInMillis={from_ts * 1000}"
        )
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        candles = d.get("candles") or []
        by_day = {}
        for row in candles:
            if len(row) < 5:
                continue
            dt_ist = datetime.datetime.fromtimestamp(int(row[0]), IST_TZ)
            day_ts = int(datetime.datetime(dt_ist.year, dt_ist.month, dt_ist.day, tzinfo=datetime.timezone.utc).timestamp())
            o, h, l, c = parse_num(row[1]), parse_num(row[2]), parse_num(row[3]), parse_num(row[4])
            v = parse_num(row[5]) if len(row) > 5 else 0.0
            if None in (o, h, l, c):
                continue
            by_day[day_ts] = {"time": day_ts, "open": o, "high": h, "low": l, "close": c, "volume": v or 0.0}
        if by_day:
            bars = [by_day[k] for k in sorted(by_day.keys())]
            with _CACHE_LOCK:
                _BARS_CACHE[cache_key] = {"ts": now, "bars": bars}
            return [dict(b) for b in bars]
    except Exception:
        pass

    # 3. Cloud-Safe Fallback: Yahoo Finance v8 Chart API (.NS / ^NSEI)
    y_india_sym = "^NSEI" if norm_sym == "NIFTY50" else ("^BSESN" if norm_sym == "SENSEX" else f"{norm_sym}.NS")
    y_bars = _fetch_yahoo_v8_bars(y_india_sym, rng)
    if y_bars:
        with _CACHE_LOCK:
            _BARS_CACHE[cache_key] = {"ts": now, "bars": y_bars}
        return [dict(b) for b in y_bars]

    return []


def build_chart_payload(sym: str, rng: str, intv: str, market: str = "US") -> dict:
    s_up = sym.upper().strip()
    is_india = (
        (market or "US").upper() == "IN"
        or s_up.endswith((".NS", ".BO"))
        or normalize_india_symbol(s_up) in INDIA_INDEX_MAP
    )
    if is_india:
        meta = fetch_india_quote(sym)
        bars = fetch_india_bars(sym, rng)
    else:
        meta = fetch_cnbc_quote(sym)
        bars = fetch_nasdaq_bars(sym, rng)
    if not bars:
        raise ValueError(f"No historical market data found for ticker '{sym.upper()}'")

    # Merge or append live real-time session bar when market is open / ahead of EOD table
    live_price = meta.get("regularMarketPrice")
    if live_price is not None and live_price > 0:
        last_bar = bars[-1]
        prev_close = meta.get("previousClose")
        live_ts = None

        # 1. Parse exact quote date from lastTime (e.g. "2026-11-06T11:16:00.000-0500" or IST)
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
    else:
        # Populate meta from latest historical bars (e.g. for NIFTY50 index)
        last_bar = bars[-1]
        prev_bar = bars[-2] if len(bars) > 1 else last_bar
        chg = last_bar["close"] - prev_bar["close"]
        chg_pct = (chg / prev_bar["close"] * 100.0) if prev_bar["close"] else 0.0
        meta.update({
            "regularMarketPrice": round(last_bar["close"], 2),
            "previousClose": round(prev_bar["close"], 2),
            "change": round(chg, 2),
            "changePct": round(chg_pct, 2),
            "open": round(last_bar["open"], 2),
            "dayHigh": round(last_bar["high"], 2),
            "dayLow": round(last_bar["low"], 2),
            "regularMarketVolume": last_bar["volume"],
            "fiftyTwoWeekHigh": round(max(b["high"] for b in bars[-min(252, len(bars)):]), 2),
            "fiftyTwoWeekLow": round(min(b["low"] for b in bars[-min(252, len(bars)):]), 2),
        })

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

    # 8. Fundamental Valuation (P/E, Fwd P/E, Industry P/E) & Short-Term Overextension ("Stretched" Guardrail)
    pe_ttm = meta.get("trailingPE")
    pe_fwd = meta.get("forwardPE")
    pe_ind = meta.get("industryPE")
    stretch_20_atr = (price - le20) / max(l_atr, 0.01)
    stretch_50_atr = (price - le50) / max(l_atr, 0.01)

    # High P/E without rapid forward earnings compression (e.g. defensive retail at ~50x P/E)
    fwd_ratio = (pe_fwd / pe_ttm) if (pe_ttm and pe_ttm > 0 and pe_fwd and pe_fwd > 0) else 0.90
    ind_stretched = True if (pe_ind is None or pe_ind <= 0) else (pe_ttm >= pe_ind * 1.15)
    is_val_stretched = bool(pe_ttm is not None and pe_ttm >= 42.0 and fwd_ratio > 0.80 and ind_stretched)
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


def run_weekly_scan(symbols: list = None, capital: float = 25000.0, preset: str = "leaders", market: str = "US") -> dict:
    is_india = (market or "US").upper() == "IN" or (preset or "").startswith("in_")
    mkt = "IN" if is_india else "US"
    ccy = "₹" if is_india else "$"
    bench_sym = "NIFTY50" if is_india else "SPY"
    bench_label = "NIFTY 50" if is_india else "SPY"

    if not symbols:
        if is_india:
            symbols = INDIA_WEEKLY_PRESETS.get(preset, INDIA_WEEKLY_PRESETS["in_leaders"])
        else:
            symbols = WEEKLY_PRESETS.get(preset, WEEKLY_PRESETS["leaders"])
    clean_syms = []
    for s in symbols:
        u = normalize_india_symbol(s) if is_india else s.strip().upper()
        if u and u not in clean_syms:
            clean_syms.append(u)
    clean_syms = clean_syms[:24]

    all_to_fetch = list(clean_syms)
    if bench_sym not in all_to_fetch:
        all_to_fetch.append(bench_sym)

    payloads = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        fut_map = {pool.submit(build_chart_payload, sym, "6mo", "1d", mkt): sym for sym in all_to_fetch}
        for fut in concurrent.futures.as_completed(fut_map):
            sym = fut_map[fut]
            try:
                payloads[sym] = fut.result()
            except Exception:
                pass

    spy_map = {}
    spy_closes = []
    if bench_sym in payloads:
        sr = payloads[bench_sym]["chart"]["result"][0]
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
                f"High-quality momentum ({c['rs_21d_vs_spy']:+.1f}% vs {bench_label}), but stretched +{c['stretch_20_atr']:.1f}× ATR above EMA20 "
                f"(RSI {c['rsi_14']:.1f}) at a steep {pe_str} — risky 1-week chase; wait for pullback to {ccy}{c['ema_20']} or DCA."
            )
        elif c["is_tech_stretched"] and c["wq_alpha101"] < 0:
            score = min(score, 56)
            action = "WAIT PULLBACK"
            setup_type = "⚠️ SHORT-TERM STRETCHED"
            rationale = (
                f"Extended +{c['stretch_20_atr']:.1f}× ATR above EMA20 ({ccy}{c['ema_20']}) with RSI {c['rsi_14']:.1f} "
                f"and negative 5D intraday body ({c['wq_alpha101']:+.2f}); wait for dip toward S1 ({ccy}{piv['s1']})."
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
                    f"Secular uptrend (>SMA200 {ccy}{c['sma_200']}, {pe_str}) with oversold Connors RSI(2)={c['connors_rsi_2']:.0f} "
                    f"into weekly support S1 ({ccy}{piv['s1']}); {c['walk_forward_5d']['win_rate_pct']:.0f}% historical 5D bounce rate."
                )
            elif score >= 58:
                setup_type = "🚀 MOMENTUM LEADER"
                rationale = (
                    f"Outperforming {bench_label} by {c['rs_21d_vs_spy']:+.1f}% (1M, {pe_str}) with {c['vol_acc_ratio']:.2f}× volume accumulation "
                    f"and {c['mc_5d']['prob_up_pct']:.0f}% 5D Monte Carlo win probability."
                )
            elif score <= 38:
                setup_type = "🛡️ RELATIVE LAGGARD"
                rationale = (
                    f"Trailing {bench_label} ({c['rs_21d_vs_spy']:+.1f}% 1M) with weak money flow ({c['vol_acc_ratio']:.2f}× Up/Dn vol) "
                    f"and below key moving averages."
                )
            else:
                setup_type = "⚖️ RANGE / NEUTRAL"
                rationale = (
                    f"Consolidating near Weekly Pivot ({ccy}{piv['pivot']}, {pe_str}) with RSI(14)={c['rsi_14']:.1f}; "
                    f"await breakout above {ccy}{piv['r1']} or pullback to {ccy}{piv['s1']}."
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

    bench_ret_5d = round(((spy_closes[-1] - spy_closes[max(0, len(spy_closes) - 6)]) / spy_closes[max(0, len(spy_closes) - 6)]) * 100.0, 2) if len(spy_closes) > 5 else 0.0

    return {
        "as_of": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "week_window": f"{mon_dt.strftime('%b %d')} – {fri_dt.strftime('%b %d, %Y')}",
        "market": mkt,
        "currency": "INR" if is_india else "USD",
        "currency_symbol": ccy,
        "benchmark": bench_label,
        "spy_ret_5d_pct": bench_ret_5d,
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


def _classify_headline_sentiment(title: str) -> str:
    t = (title or "").lower()
    bull_words = (
        "beat", "beats", "surge", "surges", "rally", "rallies", "upgrade", "upgraded",
        "outperform", "buy", "record", "growth", "profit", "jump", "jumps", "soar",
        "soars", "bull", "bullish", "strong", "gain", "gains", "win", "wins", "deal",
        "partnership", "dividend", "breakout", "higher", "top pick", "boost", "boosts",
        "raises", "raised", "preferred", "optimism", "buyback", "expands", "accelerates",
    )
    bear_words = (
        "miss", "misses", "drop", "drops", "plunge", "plunges", "downgrade", "downgraded",
        "underperform", "sell", "loss", "losses", "fall", "falls", "slump", "slumps",
        "bear", "bearish", "weak", "cut", "cuts", "lawsuit", "probe", "warning",
        "stretched", "overvalued", "decline", "declines", "lower", "risk", "crash",
        "antitrust", "fine", "investigation", "layoffs", "tumbles", "concern", "concerns",
    )
    b_pos = sum(1 for w in bull_words if w in t)
    b_neg = sum(1 for w in bear_words if w in t)
    if b_pos > b_neg:
        return "BULLISH"
    if b_neg > b_pos:
        return "BEARISH"
    return "NEUTRAL"


def _classify_headline_category(title: str) -> str:
    t = (title or "").lower()
    if any(w in t for w in ("earning", "eps", "revenue", "quarter", "q1", "q2", "q3", "q4", "guidance", "results", "profit")):
        return "EARNINGS"
    if any(w in t for w in ("upgrade", "downgrade", "price target", "analyst", "rating", "jpmorgan", "goldman", "morgan stanley", "ubs", "citi", "barclays", "jefferies", "bofa", "top pick", "outperform", "overweight")):
        return "ANALYST"
    if any(w in t for w in ("dividend", "buyback", "repurchase", "split", "payout")):
        return "CAPITAL RETURN"
    if any(w in t for w in ("sec ", "10-k", "10-q", "8-k", "insider", "lawsuit", "antitrust", "regulator", "doj", "ftc", "sebi", "rbi")):
        return "REGULATORY / FILING"
    if any(w in t for w in ("acqui", "merger", "deal", "partner", "stake", "joint venture", "contract")):
        return "M&A / DEAL"
    if any(w in t for w in ("ai ", "artificial intelligence", "chip", "cloud", "launch", "model", "product", "capex", "datacenter")):
        return "PRODUCT & AI"
    return "MARKET WIRE"


def _get_yahoo_session(force_refresh: bool = False):
    now = time.time()
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _YAHOO_SESSION.get("cookie")
            and _YAHOO_SESSION.get("crumb")
            and (now - _YAHOO_SESSION["ts"] < 3600.0)
        ):
            return _YAHOO_SESSION["cookie"], _YAHOO_SESSION["crumb"]

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    opener.addheaders = [("User-Agent", ua)]
    try:
        opener.open("https://fc.yahoo.com", timeout=5)
    except Exception:
        pass
    crumb = ""
    try:
        crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=5).read().decode("utf-8").strip()
    except Exception:
        pass
    cookie_str = "; ".join(f"{c.name}={c.value}" for c in cj)
    with _CACHE_LOCK:
        if crumb:
            _YAHOO_SESSION["cookie"] = cookie_str
            _YAHOO_SESSION["crumb"] = crumb
            _YAHOO_SESSION["ts"] = now
    return cookie_str, crumb


def _yahoo_get_json(url: str, cookie_str: str, timeout: int = 8) -> dict:
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Cookie": cookie_str,
    }
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _raw_val(obj, default=None):
    if obj is None:
        return default
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, dict):
        if "raw" in obj and obj["raw"] is not None:
            try:
                return float(obj["raw"])
            except Exception:
                return default
    return default


def _fmt_val(obj, default="N/A") -> str:
    if obj is None:
        return default
    if isinstance(obj, dict):
        if obj.get("fmt"):
            return str(obj["fmt"])
        if obj.get("raw") is not None:
            return str(obj["raw"])
        return default
    return str(obj)


def _fmt_compact_num(val, ccy: str = "") -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except Exception:
        return "N/A"
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1e12:
        return f"{sign}{ccy}{av / 1e12:.2f}T"
    if av >= 1e9:
        return f"{sign}{ccy}{av / 1e9:.2f}B"
    if av >= 1e6:
        return f"{sign}{ccy}{av / 1e6:.2f}M"
    if av >= 1e3:
        return f"{sign}{ccy}{av / 1e3:.1f}K"
    return f"{sign}{ccy}{av:,.2f}"


def fetch_stock_news(symbol: str, market: str = "US", company_name: str = "") -> dict:
    is_india = (market or "US").upper() == "IN" or symbol.upper().endswith((".NS", ".BO"))
    clean_sym = normalize_india_symbol(symbol) if is_india else symbol.strip().upper()
    cache_key = f"{'IN' if is_india else 'US'}:{clean_sym}"
    now = time.time()
    with _CACHE_LOCK:
        cached = _NEWS_CACHE.get(cache_key)
        if cached and (now - cached["ts"] < 180.0):
            return dict(cached["data"])

    query_sym = f"{clean_sym}.NS" if (is_india and clean_sym not in INDIA_INDEX_MAP) else clean_sym
    raw_items = []

    def _pull_yahoo_news():
        out = []
        try:
            y_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query_sym)}&quotesCount=1&newsCount=10"
            req = urllib.request.Request(y_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for n in data.get("news", [])[:10]:
                title = (n.get("title") or "").strip()
                if not title:
                    continue
                pub_ts = n.get("providerPublishTime") or int(now)
                pub_str = (
                    datetime.datetime.fromtimestamp(int(pub_ts), tz=datetime.timezone.utc).strftime("%b %d, %H:%M UTC")
                    if pub_ts
                    else "Recent"
                )
                out.append({
                    "title": title,
                    "publisher": n.get("publisher") or "Yahoo Finance Wire",
                    "link": n.get("link") or f"https://finance.yahoo.com/quote/{query_sym}",
                    "published": pub_str,
                    "ts": int(pub_ts),
                    "source_feed": "Yahoo Wire",
                })
        except Exception:
            pass
        return out

    def _pull_google_rss():
        out = []
        try:
            import email.utils
            import xml.etree.ElementTree as ET
            gl = "IN" if is_india else "US"
            hl = "en-IN" if is_india else "en-US"
            short_co = re.sub(r"\b(Inc\.?|Corp\.?|Corporation|Ltd\.?|Limited|Platforms|Holdings|PLC)\b", "", company_name or "", flags=re.I).strip()
            if short_co and short_co.upper() != clean_sym:
                q_str = f'"{clean_sym}" OR "{short_co}" stock'
            else:
                q_str = f"{clean_sym} {'NSE stock India' if is_india else 'stock market earnings'}"
            rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q_str)}&hl={hl}&gl={gl}&ceid={gl}:en"
            req = urllib.request.Request(rss_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=6) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
            for item_el in root.findall("./channel/item")[:10]:
                raw_title = (item_el.findtext("title") or "").strip()
                if not raw_title:
                    continue
                parts = raw_title.rsplit(" - ", 1)
                title = parts[0].strip()
                publisher = parts[1].strip() if len(parts) > 1 else (item_el.findtext("source") or "Google Finance News")
                link = (item_el.findtext("link") or "").strip()
                pub_raw = (item_el.findtext("pubDate") or "").strip()
                ts_val = int(now) - 3600
                pub_str = pub_raw[:22] or "Recent"
                if pub_raw:
                    try:
                        dt = email.utils.parsedate_to_datetime(pub_raw)
                        ts_val = int(dt.timestamp())
                        pub_str = dt.astimezone(datetime.timezone.utc).strftime("%b %d, %H:%M UTC")
                    except Exception:
                        pass
                out.append({
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "published": pub_str,
                    "ts": ts_val,
                    "source_feed": "Google News",
                })
        except Exception:
            pass
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_y = pool.submit(_pull_yahoo_news)
        f_g = pool.submit(_pull_google_rss)
        for fut in (f_y, f_g):
            try:
                raw_items.extend(fut.result())
            except Exception:
                pass

    # Deduplicate by normalized first 48 alphanumeric characters & sort newest first
    raw_items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    seen_keys = set()
    items = []
    for it in raw_items:
        norm_k = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:45]
        if not norm_k or norm_k in seen_keys:
            continue
        seen_keys.add(norm_k)
        sent = _classify_headline_sentiment(it["title"])
        cat = _classify_headline_category(it["title"])
        items.append({
            "title": it["title"],
            "publisher": it["publisher"],
            "link": it["link"],
            "published": it["published"],
            "sentiment": sent,
            "category": cat,
            "source_feed": it.get("source_feed", "Wire"),
        })
        if len(items) >= 12:
            break

    bull_c = sum(1 for x in items if x["sentiment"] == "BULLISH")
    bear_c = sum(1 for x in items if x["sentiment"] == "BEARISH")
    neut_c = sum(1 for x in items if x["sentiment"] == "NEUTRAL")
    overall = "BULLISH" if bull_c > bear_c else ("BEARISH" if bear_c > bull_c else "NEUTRAL")

    payload = {
        "symbol": clean_sym,
        "market": "IN" if is_india else "US",
        "overall_sentiment": overall,
        "bullish_count": bull_c,
        "bearish_count": bear_c,
        "neutral_count": neut_c,
        "headlines": items,
    }
    with _CACHE_LOCK:
        _NEWS_CACHE[cache_key] = {"ts": now, "data": payload}
    return dict(payload)


def fetch_stock_intelligence(symbol: str, market: str = "US") -> dict:
    """
    Comprehensive Google Finance-style Institutional Intelligence Engine:
    Aggregates Overview, Analysis (Analyst Ratings, Price Targets, Multiples, Margins),
    Earnings (Quarterly Beat/Miss, Surprise %, Next Earnings Forecast),
    Financials (5Q & 4Y Income Statement, Balance Sheet, Cash Flow),
    Holdings (Insiders/Promoters %, Institutional Top Holders, Short Interest, Insider Trades),
    Events (Upcoming Earnings/Dividends, Analyst Upgrades/Downgrades, SEC Filings),
    Related Peer Stocks, and Stable Multi-Source News.
    """
    is_india = (market or "US").upper() == "IN" or symbol.upper().endswith((".NS", ".BO"))
    clean_sym = normalize_india_symbol(symbol) if is_india else symbol.strip().upper()
    mkt = "IN" if is_india else "US"
    ccy = "₹" if is_india else "$"
    cache_key = f"{mkt}:{clean_sym}"
    now = time.time()

    with _CACHE_LOCK:
        cached = _INTEL_CACHE.get(cache_key)
        if cached and (now - cached["ts"] < 300.0):
            return dict(cached["data"])

    y_sym = f"{clean_sym}.NS" if (is_india and clean_sym not in INDIA_INDEX_MAP) else clean_sym
    if clean_sym == "NIFTY50":
        y_sym = "^NSEI"
    elif clean_sym == "SENSEX":
        y_sym = "^BSESN"
    elif clean_sym == "BANKNIFTY":
        y_sym = "^NSEBANK"

    # Warm up Yahoo Cookie + Crumb session once before parallel requests
    y_cookie, y_crumb = _get_yahoo_session()

    def _fetch_qsummary():
        nonlocal y_cookie, y_crumb
        modules = ",".join([
            "assetProfile",
            "price",
            "summaryDetail",
            "defaultKeyStatistics",
            "financialData",
            "recommendationTrend",
            "upgradeDowngradeHistory",
            "earnings",
            "earningsHistory",
            "earningsTrend",
            "calendarEvents",
            "majorHoldersBreakdown",
            "institutionOwnership",
            "fundOwnership",
            "insiderTransactions",
            "netSharePurchaseActivity",
            "secFilings",
        ])
        for attempt in range(2):
            ck, cr = (y_cookie, y_crumb) if attempt == 0 else _get_yahoo_session(force_refresh=True)
            if not cr:
                continue
            try:
                url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(y_sym)}?crumb={urllib.parse.quote(cr)}&modules={modules}"
                d = _yahoo_get_json(url, ck, timeout=8)
                res = (d.get("quoteSummary") or {}).get("result") or []
                if res:
                    return res[0]
            except Exception:
                continue
        return {}

    def _fetch_timeseries():
        if not y_crumb:
            return {}
        types = ",".join([
            "quarterlyTotalRevenue",
            "quarterlyGrossProfit",
            "quarterlyOperatingIncome",
            "quarterlyEBITDA",
            "quarterlyNetIncome",
            "quarterlyDilutedEPS",
            "quarterlyOperatingCashFlow",
            "quarterlyCapitalExpenditure",
            "quarterlyFreeCashFlow",
            "quarterlyTotalCashAndCashEquivalents",
            "quarterlyTotalDebt",
            "quarterlyTotalAssets",
            "quarterlyStockholdersEquity",
            "annualTotalRevenue",
            "annualGrossProfit",
            "annualOperatingIncome",
            "annualEBITDA",
            "annualNetIncome",
            "annualFreeCashFlow",
            "annualOperatingCashFlow",
        ])
        p1 = int(now) - 86400 * 365 * 5
        try:
            url = (
                f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{urllib.parse.quote(y_sym)}"
                f"?symbol={urllib.parse.quote(y_sym)}&type={types}&period1={p1}&period2={int(now)}&crumb={urllib.parse.quote(y_crumb)}"
            )
            d = _yahoo_get_json(url, y_cookie, timeout=8)
            out = {}
            for item in (d.get("timeseries") or {}).get("result") or []:
                t_list = (item.get("meta") or {}).get("type") or []
                if not t_list:
                    continue
                t_name = t_list[0]
                if t_name in item and item[t_name]:
                    out[t_name] = item[t_name]
            return out
        except Exception:
            return {}

    def _fetch_peers():
        try:
            url = f"https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{urllib.parse.quote(y_sym)}"
            d = _yahoo_get_json(url, y_cookie, timeout=5)
            res = (d.get("finance") or {}).get("result") or []
            if res:
                syms = [r.get("symbol", "") for r in (res[0].get("recommendedSymbols") or []) if r.get("symbol")]
                return [re.sub(r"\.(NS|BO)$", "", s) for s in syms[:5]]
        except Exception:
            pass
        return []

    def _fetch_base_quote():
        return fetch_india_quote(clean_sym) if is_india else fetch_cnbc_quote(clean_sym)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        f_qs = pool.submit(_fetch_qsummary)
        f_ts = pool.submit(_fetch_timeseries)
        f_pr = pool.submit(_fetch_peers)
        f_bq = pool.submit(_fetch_base_quote)
        qs = f_qs.result() or {}
        ts_map = f_ts.result() or {}
        peer_syms = f_pr.result() or []
        base_meta = f_bq.result() or {}

    prof = qs.get("assetProfile") or {}
    pr = qs.get("price") or {}
    sd = qs.get("summaryDetail") or {}
    ks = qs.get("defaultKeyStatistics") or {}
    fd = qs.get("financialData") or {}
    rt = qs.get("recommendationTrend") or {}
    ud = qs.get("upgradeDowngradeHistory") or {}
    er = qs.get("earnings") or {}
    eh = qs.get("earningsHistory") or {}
    et = qs.get("earningsTrend") or {}
    ce = qs.get("calendarEvents") or {}
    mh = qs.get("majorHoldersBreakdown") or {}
    io = qs.get("institutionOwnership") or {}
    fo = qs.get("fundOwnership") or {}
    itx = qs.get("insiderTransactions") or {}
    nsp = qs.get("netSharePurchaseActivity") or {}
    sec = qs.get("secFilings") or {}

    long_name = (
        pr.get("longName")
        or base_meta.get("longName")
        or pr.get("shortName")
        or clean_sym
    )

    # Fetch news using full company name for better Google News RSS precision
    news_data = fetch_stock_news(clean_sym, mkt, company_name=long_name)

    # ── 1. OVERVIEW & EXTENDED HOURS ──
    reg_price = _raw_val(pr.get("regularMarketPrice")) or base_meta.get("regularMarketPrice")
    prev_close = _raw_val(pr.get("regularMarketPreviousClose")) or _raw_val(sd.get("previousClose")) or base_meta.get("previousClose")
    reg_chg = _raw_val(pr.get("regularMarketChange"))
    if reg_chg is None and reg_price and prev_close:
        reg_chg = reg_price - prev_close
    reg_chg_pct = _raw_val(pr.get("regularMarketChangePercent"))
    if reg_chg_pct is not None:
        reg_chg_pct *= 100.0
    elif base_meta.get("changePct") is not None:
        reg_chg_pct = base_meta.get("changePct")

    post_price = _raw_val(pr.get("postMarketPrice"))
    post_chg = _raw_val(pr.get("postMarketChange"))
    post_chg_pct = _raw_val(pr.get("postMarketChangePercent"))
    if post_chg_pct is not None:
        post_chg_pct *= 100.0

    pre_price = _raw_val(pr.get("preMarketPrice"))
    pre_chg = _raw_val(pr.get("preMarketChange"))
    pre_chg_pct = _raw_val(pr.get("preMarketChangePercent"))
    if pre_chg_pct is not None:
        pre_chg_pct *= 100.0

    mkt_cap_raw = _raw_val(pr.get("marketCap")) or _raw_val(sd.get("marketCap"))
    mkt_cap_fmt = _fmt_val(pr.get("marketCap")) if pr.get("marketCap") else (base_meta.get("marketCapStr") or "N/A")
    if mkt_cap_fmt != "N/A" and not mkt_cap_fmt.startswith(ccy):
        mkt_cap_fmt = ccy + mkt_cap_fmt

    div_yield_raw = _raw_val(sd.get("dividendYield"))
    div_yield_pct = round(div_yield_raw * 100.0, 2) if div_yield_raw is not None else base_meta.get("dividendYield")
    div_rate = _raw_val(sd.get("dividendRate")) or base_meta.get("dividendAmount")
    qtr_div = round(div_rate / 4.0, 2) if div_rate else None
    ex_div_fmt = _fmt_val(sd.get("exDividendDate"), default="—")

    pe_ttm = _raw_val(sd.get("trailingPE")) or base_meta.get("trailingPE")
    pe_fwd = _raw_val(sd.get("forwardPE")) or _raw_val(ks.get("forwardPE")) or base_meta.get("forwardPE")
    eps_ttm = _raw_val(ks.get("trailingEps")) or base_meta.get("eps")
    eps_fwd = _raw_val(ks.get("forwardEps"))
    beta_val = _raw_val(sd.get("beta")) or _raw_val(ks.get("beta")) or base_meta.get("beta")
    shares_fmt = _fmt_val(ks.get("sharesOutstanding"), default="N/A")
    float_fmt = _fmt_val(ks.get("floatShares"), default="N/A")
    employees = prof.get("fullTimeEmployees")
    emp_fmt = f"{int(employees):,}" if isinstance(employees, (int, float)) else "N/A"

    w52_high = _raw_val(sd.get("fiftyTwoWeekHigh")) or base_meta.get("fiftyTwoWeekHigh")
    w52_low = _raw_val(sd.get("fiftyTwoWeekLow")) or base_meta.get("fiftyTwoWeekLow")

    overview = {
        "symbol": clean_sym,
        "name": long_name,
        "exchange": pr.get("exchangeName") or base_meta.get("exchangeName") or ("NSE INDIA" if is_india else "NASDAQ/NYSE"),
        "currency": ccy,
        "currency_code": "INR" if is_india else "USD",
        "sector": prof.get("sector") or "Equities",
        "industry": prof.get("industry") or "Institutional Equity",
        "website": prof.get("website") or "",
        "city_country": ", ".join(x for x in (prof.get("city"), prof.get("country")) if x),
        "employees": employees,
        "employees_fmt": emp_fmt,
        "business_summary": (prof.get("longBusinessSummary") or "")[:650],
        "regular_price": round(reg_price, 2) if reg_price is not None else None,
        "regular_change": round(reg_chg, 2) if reg_chg is not None else None,
        "regular_change_pct": round(reg_chg_pct, 2) if reg_chg_pct is not None else None,
        "post_market_price": round(post_price, 2) if post_price is not None else None,
        "post_market_change": round(post_chg, 2) if post_chg is not None else None,
        "post_market_change_pct": round(post_chg_pct, 2) if post_chg_pct is not None else None,
        "pre_market_price": round(pre_price, 2) if pre_price is not None else None,
        "pre_market_change": round(pre_chg, 2) if pre_chg is not None else None,
        "pre_market_change_pct": round(pre_chg_pct, 2) if pre_chg_pct is not None else None,
        "open": _raw_val(pr.get("regularMarketOpen")) or _raw_val(sd.get("open")) or base_meta.get("open"),
        "day_high": _raw_val(pr.get("regularMarketDayHigh")) or _raw_val(sd.get("dayHigh")) or base_meta.get("dayHigh"),
        "day_low": _raw_val(pr.get("regularMarketDayLow")) or _raw_val(sd.get("dayLow")) or base_meta.get("dayLow"),
        "prev_close": prev_close,
        "volume": _raw_val(pr.get("regularMarketVolume")) or _raw_val(sd.get("volume")) or base_meta.get("regularMarketVolume"),
        "avg_volume": _raw_val(sd.get("averageVolume")) or _raw_val(pr.get("averageDailyVolume3Month")),
        "market_cap_raw": mkt_cap_raw,
        "market_cap_fmt": mkt_cap_fmt,
        "pe_ttm": round(pe_ttm, 2) if pe_ttm is not None else None,
        "pe_fwd": round(pe_fwd, 2) if pe_fwd is not None else None,
        "eps_ttm": round(eps_ttm, 2) if eps_ttm is not None else None,
        "eps_fwd": round(eps_fwd, 2) if eps_fwd is not None else None,
        "dividend_yield_pct": div_yield_pct,
        "dividend_rate": round(div_rate, 2) if div_rate is not None else None,
        "quarterly_dividend": qtr_div,
        "ex_dividend_date": ex_div_fmt,
        "week_52_high": round(w52_high, 2) if w52_high is not None else None,
        "week_52_low": round(w52_low, 2) if w52_low is not None else None,
        "beta": round(beta_val, 2) if beta_val is not None else None,
        "shares_outstanding_fmt": shares_fmt,
        "float_shares_fmt": float_fmt,
        "pb_ratio": round(_raw_val(ks.get("priceToBook")), 2) if _raw_val(ks.get("priceToBook")) is not None else None,
    }

    # ── 2. ANALYSIS (Wall Street Consensus, Price Targets, Multiples, Margins) ──
    trends = rt.get("trend") or []
    cur_t = trends[0] if trends else {}
    sb = int(cur_t.get("strongBuy") or 0)
    b_cnt = int(cur_t.get("buy") or 0)
    h_cnt = int(cur_t.get("hold") or 0)
    s_cnt = int(cur_t.get("sell") or 0)
    ss = int(cur_t.get("strongSell") or 0)
    tot_analysts = sb + b_cnt + h_cnt + s_cnt + ss
    bull_analyst_pct = round(((sb + b_cnt) / tot_analysts) * 100.0, 1) if tot_analysts > 0 else None

    tgt_low = _raw_val(fd.get("targetLowPrice"))
    tgt_mean = _raw_val(fd.get("targetMeanPrice"))
    tgt_med = _raw_val(fd.get("targetMedianPrice"))
    tgt_high = _raw_val(fd.get("targetHighPrice"))
    upside_pct = round(((tgt_mean - reg_price) / reg_price) * 100.0, 1) if (tgt_mean and reg_price and reg_price > 0) else None
    rec_key = (fd.get("recommendationKey") or "hold").upper().replace("_", " ")

    def _pct_field(d, k):
        v = _raw_val(d.get(k))
        return round(v * 100.0, 2) if v is not None else None

    analysis = {
        "recommendation_key": rec_key if rec_key != "NONE" else ("BUY" if (bull_analyst_pct or 0) >= 60 else "HOLD"),
        "analyst_count": tot_analysts or int(_raw_val(fd.get("numberOfAnalystOpinions")) or 0),
        "ratings": {
            "strong_buy": sb,
            "buy": b_cnt,
            "hold": h_cnt,
            "sell": s_cnt,
            "strong_sell": ss,
            "bullish_pct": bull_analyst_pct,
        },
        "rating_history": [
            {
                "period": t.get("period", "0m"),
                "strong_buy": int(t.get("strongBuy") or 0),
                "buy": int(t.get("buy") or 0),
                "hold": int(t.get("hold") or 0),
                "sell": int(t.get("sell") or 0),
                "strong_sell": int(t.get("strongSell") or 0),
            }
            for t in trends[:4]
        ],
        "price_targets": {
            "low": round(tgt_low, 2) if tgt_low is not None else None,
            "mean": round(tgt_mean, 2) if tgt_mean is not None else None,
            "median": round(tgt_med, 2) if tgt_med is not None else None,
            "high": round(tgt_high, 2) if tgt_high is not None else None,
            "current": round(reg_price, 2) if reg_price is not None else None,
            "upside_pct": upside_pct,
        },
        "valuation": {
            "pe_ttm": overview["pe_ttm"],
            "pe_fwd": overview["pe_fwd"],
            "peg_ratio": round(_raw_val(ks.get("pegRatio")), 2) if _raw_val(ks.get("pegRatio")) is not None else None,
            "ps_ratio": round(_raw_val(sd.get("priceToSalesTrailing12Months")), 2) if _raw_val(sd.get("priceToSalesTrailing12Months")) is not None else None,
            "pb_ratio": overview["pb_ratio"],
            "ev_ebitda": round(_raw_val(ks.get("enterpriseToEbitda")), 2) if _raw_val(ks.get("enterpriseToEbitda")) is not None else None,
            "ev_revenue": round(_raw_val(ks.get("enterpriseToRevenue")), 2) if _raw_val(ks.get("enterpriseToRevenue")) is not None else None,
            "enterprise_value_fmt": _fmt_val(ks.get("enterpriseValue"), default="N/A"),
        },
        "profitability": {
            "gross_margin_pct": _pct_field(fd, "grossMargins"),
            "operating_margin_pct": _pct_field(fd, "operatingMargins"),
            "profit_margin_pct": _pct_field(fd, "profitMargins"),
            "ebitda_margin_pct": _pct_field(fd, "ebitdaMargins"),
            "roe_pct": _pct_field(fd, "returnOnEquity"),
            "roa_pct": _pct_field(fd, "returnOnAssets"),
            "revenue_growth_yoy_pct": _pct_field(fd, "revenueGrowth"),
            "earnings_growth_yoy_pct": _pct_field(fd, "earningsGrowth"),
        },
    }

    # ── 3. EARNINGS (EPS Beat/Miss History, Revenue/Earnings Chart, Next Quarter Consensus) ──
    eh_list = (eh.get("history") or [])
    eps_history = []
    beat_count = 0
    surprises = []
    for item in eh_list:
        act = _raw_val(item.get("epsActual"))
        est = _raw_val(item.get("epsEstimate"))
        diff = _raw_val(item.get("epsDifference"))
        surp = _raw_val(item.get("surprisePercent"))
        surp_pct = round(surp * 100.0, 2) if surp is not None else None
        q_fmt = _fmt_val(item.get("quarter"), default=item.get("period", "Q"))
        is_beat = (diff is not None and diff >= 0) or (act is not None and est is not None and act >= est)
        if is_beat:
            beat_count += 1
        if surp_pct is not None:
            surprises.append(surp_pct)
        eps_history.append({
            "quarter": q_fmt,
            "period": item.get("period", ""),
            "eps_estimate": round(est, 2) if est is not None else None,
            "eps_actual": round(act, 2) if act is not None else None,
            "eps_diff": round(diff, 2) if diff is not None else None,
            "surprise_pct": surp_pct,
            "beat": is_beat,
        })

    fin_chart = (er.get("financialsChart") or {})
    q_chart = []
    for q_it in (fin_chart.get("quarterly") or []):
        rev_r = _raw_val(q_it.get("revenue"))
        ern_r = _raw_val(q_it.get("earnings"))
        m_pct = round((ern_r / rev_r) * 100.0, 1) if (rev_r and rev_r > 0 and ern_r is not None) else None
        q_chart.append({
            "date": q_it.get("fiscalQuarter") or q_it.get("date") or "",
            "revenue_raw": rev_r,
            "revenue_fmt": _fmt_compact_num(rev_r, ccy),
            "earnings_raw": ern_r,
            "earnings_fmt": _fmt_compact_num(ern_r, ccy),
            "margin_pct": m_pct,
        })

    ce_earn = (ce.get("earnings") or {})
    e_dates = ce_earn.get("earningsDate") or []
    next_earn_fmt = _fmt_val(e_dates[0], default="TBD") if e_dates else "TBD"
    next_earn_raw = _raw_val(e_dates[0]) if e_dates else None
    days_until_earn = None
    if next_earn_raw:
        days_until_earn = int(round((next_earn_raw - now) / 86400.0))

    call_dates = ce_earn.get("earningsCallDate") or []
    last_call_fmt = _fmt_val(call_dates[0], default="—") if call_dates else "—"

    trend_estimates = []
    for tr_it in (et.get("trend") or [])[:4]:
        p_lbl = tr_it.get("period", "")
        ee = tr_it.get("earningsEstimate") or {}
        re_est = tr_it.get("revenueEstimate") or {}
        ep_t = tr_it.get("epsTrend") or {}
        ep_rev = tr_it.get("epsRevisions") or {}
        g_raw = _raw_val(tr_it.get("growth")) or _raw_val(ee.get("growth"))
        trend_estimates.append({
            "period": p_lbl,
            "end_date": tr_it.get("endDate") or "",
            "eps_avg": _raw_val(ee.get("avg")),
            "eps_low": _raw_val(ee.get("low")),
            "eps_high": _raw_val(ee.get("high")),
            "eps_year_ago": _raw_val(ee.get("yearAgoEps")),
            "rev_avg_fmt": _fmt_compact_num(_raw_val(re_est.get("avg")), ccy),
            "rev_growth_pct": round(_raw_val(re_est.get("growth")) * 100.0, 1) if _raw_val(re_est.get("growth")) is not None else None,
            "eps_growth_pct": round(g_raw * 100.0, 1) if g_raw is not None else None,
            "eps_30d_ago": _raw_val(ep_t.get("30daysAgo")),
            "eps_90d_ago": _raw_val(ep_t.get("90daysAgo")),
            "up_last_30d": int(_raw_val(ep_rev.get("upLast30days")) or 0),
            "down_last_30d": int(_raw_val(ep_rev.get("downLast30days")) or 0),
        })

    earnings = {
        "next_earnings_date": next_earn_fmt,
        "last_earnings_call_date": last_call_fmt,
        "days_until_earnings": days_until_earn,
        "next_eps_estimate": {
            "avg": _raw_val(ce_earn.get("earningsAverage")),
            "low": _raw_val(ce_earn.get("earningsLow")),
            "high": _raw_val(ce_earn.get("earningsHigh")),
        },
        "next_rev_estimate": {
            "avg_fmt": _fmt_compact_num(_raw_val(ce_earn.get("revenueAverage")), ccy),
            "low_fmt": _fmt_compact_num(_raw_val(ce_earn.get("revenueLow")), ccy),
            "high_fmt": _fmt_compact_num(_raw_val(ce_earn.get("revenueHigh")), ccy),
        },
        "beat_count_last_4q": beat_count,
        "total_quarters_tracked": len(eps_history),
        "avg_surprise_pct": round(sum(surprises) / len(surprises), 2) if surprises else None,
        "eps_history": eps_history,
        "quarterly_chart": q_chart,
        "trend_estimates": trend_estimates,
    }

    # ── 4. FINANCIALS (5Q & 4Y Income Statement, Balance Sheet, Cash Flow from Timeseries) ──
    def _extract_ts_by_date(prefix: str, field_map: dict) -> list:
        by_dt = {}
        for out_key, ts_key in field_map.items():
            full_k = prefix + ts_key
            for entry in ts_map.get(full_k) or []:
                dt = entry.get("asOfDate")
                val = _raw_val(entry.get("reportedValue"))
                if not dt or val is None:
                    continue
                by_dt.setdefault(dt, {"date": dt})[out_key] = val
        rows = [by_dt[k] for k in sorted(by_dt.keys())]
        for r in rows:
            rev = r.get("revenue")
            ni = r.get("net_income")
            r["net_margin_pct"] = round((ni / rev) * 100.0, 1) if (rev and rev > 0 and ni is not None) else None
            for k in ("revenue", "gross_profit", "operating_income", "ebitda", "net_income", "operating_cash_flow", "capex", "free_cash_flow", "total_cash", "total_debt", "total_assets", "equity"):
                if k in r:
                    r[k + "_fmt"] = _fmt_compact_num(r[k], ccy)
        return rows

    q_fields = {
        "revenue": "TotalRevenue",
        "gross_profit": "GrossProfit",
        "operating_income": "OperatingIncome",
        "ebitda": "EBITDA",
        "net_income": "NetIncome",
        "eps": "DilutedEPS",
        "operating_cash_flow": "OperatingCashFlow",
        "capex": "CapitalExpenditure",
        "free_cash_flow": "FreeCashFlow",
        "total_cash": "TotalCashAndCashEquivalents",
        "total_debt": "TotalDebt",
        "total_assets": "TotalAssets",
        "equity": "StockholdersEquity",
    }
    a_fields = {
        "revenue": "TotalRevenue",
        "gross_profit": "GrossProfit",
        "operating_income": "OperatingIncome",
        "ebitda": "EBITDA",
        "net_income": "NetIncome",
        "operating_cash_flow": "OperatingCashFlow",
        "free_cash_flow": "FreeCashFlow",
    }
    q_fin_rows = _extract_ts_by_date("quarterly", q_fields)[-5:]
    a_fin_rows = _extract_ts_by_date("annual", a_fields)[-4:]

    # Fallback to earnings.financialsChart if timeseries returned empty
    if not q_fin_rows and q_chart:
        for qc in q_chart:
            q_fin_rows.append({
                "date": qc["date"],
                "revenue": qc["revenue_raw"],
                "revenue_fmt": qc["revenue_fmt"],
                "net_income": qc["earnings_raw"],
                "net_income_fmt": qc["earnings_fmt"],
                "net_margin_pct": qc["margin_pct"],
            })

    financials = {
        "quarterly": q_fin_rows,
        "annual": a_fin_rows,
        "highlights": {
            "total_revenue_ttm_fmt": _fmt_compact_num(_raw_val(fd.get("totalRevenue")), ccy),
            "ebitda_ttm_fmt": _fmt_compact_num(_raw_val(fd.get("ebitda")), ccy),
            "total_cash_fmt": _fmt_compact_num(_raw_val(fd.get("totalCash")), ccy),
            "total_cash_per_share": _raw_val(fd.get("totalCashPerShare")),
            "total_debt_fmt": _fmt_compact_num(_raw_val(fd.get("totalDebt")), ccy),
            "debt_to_equity_pct": round(_raw_val(fd.get("debtToEquity")), 1) if _raw_val(fd.get("debtToEquity")) is not None else None,
            "current_ratio": round(_raw_val(fd.get("currentRatio")), 2) if _raw_val(fd.get("currentRatio")) is not None else None,
            "quick_ratio": round(_raw_val(fd.get("quickRatio")), 2) if _raw_val(fd.get("quickRatio")) is not None else None,
            "operating_cash_flow_ttm_fmt": _fmt_compact_num(_raw_val(fd.get("operatingCashflow")), ccy),
            "free_cash_flow_ttm_fmt": _fmt_compact_num(_raw_val(fd.get("freeCashflow")), ccy),
        },
    }

    # ── 5. HOLDINGS (Ownership Breakdown, Top Institutions & Funds, Short Interest, Insider Trades) ──
    ins_pct = _pct_field(mh, "insidersPercentHeld") or 0.0
    inst_pct = _pct_field(mh, "institutionsPercentHeld") or 0.0
    inst_flt_pct = _pct_field(mh, "institutionsFloatPercentHeld")
    pub_pct = round(max(0.0, 100.0 - ins_pct - inst_pct), 2)
    inst_count = int(_raw_val(mh.get("institutionsCount")) or 0)
    short_flt_pct = _pct_field(ks, "shortPercentOfFloat")
    short_ratio = _raw_val(ks.get("shortRatio"))
    shares_short_fmt = _fmt_val(ks.get("sharesShort"), default="N/A")

    top_institutions = []
    for row in (io.get("ownershipList") or [])[:8]:
        top_institutions.append({
            "organization": row.get("organization") or "Institution",
            "pct_held": round((_raw_val(row.get("pctHeld")) or 0.0) * 100.0, 2),
            "shares_fmt": _fmt_val(row.get("position"), default="—"),
            "value_fmt": _fmt_compact_num(_raw_val(row.get("value")), ccy),
            "change_pct": round((_raw_val(row.get("pctChange")) or 0.0) * 100.0, 2),
            "report_date": _fmt_val(row.get("reportDate"), default="—"),
        })

    top_funds = []
    for row in (fo.get("ownershipList") or [])[:5]:
        top_funds.append({
            "organization": row.get("organization") or "Fund",
            "pct_held": round((_raw_val(row.get("pctHeld")) or 0.0) * 100.0, 2),
            "shares_fmt": _fmt_val(row.get("position"), default="—"),
            "value_fmt": _fmt_compact_num(_raw_val(row.get("value")), ccy),
            "change_pct": round((_raw_val(row.get("pctChange")) or 0.0) * 100.0, 2),
        })

    insider_txs = []
    for tx in (itx.get("transactions") or [])[:8]:
        txt = tx.get("transactionText") or tx.get("moneyText") or "Transaction"
        is_buy = "purchase" in txt.lower() or "buy" in txt.lower()
        insider_txs.append({
            "name": tx.get("filerName") or "Insider",
            "relation": tx.get("filerRelation") or "Officer/Director",
            "date": _fmt_val(tx.get("startDate"), default="—"),
            "transaction": txt,
            "shares_fmt": _fmt_val(tx.get("shares"), default="—"),
            "value_fmt": _fmt_compact_num(_raw_val(tx.get("value")), ccy) if _raw_val(tx.get("value")) else "—",
            "is_buy": is_buy,
        })

    holdings = {
        "insiders_pct": ins_pct,
        "institutions_pct": inst_pct,
        "institutions_float_pct": inst_flt_pct,
        "public_other_pct": pub_pct,
        "institutions_count": inst_count,
        "short_pct_of_float": short_flt_pct,
        "short_ratio_days": round(short_ratio, 2) if short_ratio is not None else None,
        "shares_short_fmt": shares_short_fmt,
        "net_purchase_6m": {
            "period": nsp.get("period") or "6m",
            "buy_shares_fmt": _fmt_val(nsp.get("buyInfoShares"), default="0"),
            "sell_shares_fmt": _fmt_val(nsp.get("sellInfoShares"), default="0"),
            "net_shares_fmt": _fmt_val(nsp.get("netInfoShares"), default="0"),
        },
        "top_institutions": top_institutions,
        "top_funds": top_funds,
        "insider_transactions": insider_txs,
    }

    # ── 6. EVENTS (Upcoming Calendar, Wall Street Upgrades/Downgrades, SEC Filings) ──
    upcoming_events = []
    if next_earn_fmt and next_earn_fmt != "TBD":
        upcoming_events.append({
            "type": "EARNINGS RELEASE",
            "date": next_earn_fmt,
            "title": f"Upcoming Quarterly Earnings Report ({clean_sym})",
            "detail": (
                f"Consensus EPS: {ccy}{earnings['next_eps_estimate']['avg']:.2f} · Est. Revenue: {earnings['next_rev_estimate']['avg_fmt']}"
                if earnings["next_eps_estimate"]["avg"] is not None
                else "Estimated Reporting Window"
            ),
            "badge": "b-purple",
        })
    if ex_div_fmt and ex_div_fmt != "—":
        upcoming_events.append({
            "type": "EX-DIVIDEND DATE",
            "date": ex_div_fmt,
            "title": f"Ex-Dividend Cutoff ({div_yield_pct or 0:.2f}% Yield)",
            "detail": f"Annualized Rate: {ccy}{div_rate:.2f}/sh ({ccy}{qtr_div:.2f}/qtr)" if div_rate else "Dividend Eligibility Date",
            "badge": "b-green",
        })
    div_date_fmt = _fmt_val(ce.get("dividendDate"), default="")
    if div_date_fmt and div_date_fmt != "N/A":
        upcoming_events.append({
            "type": "DIVIDEND PAYDATE",
            "date": div_date_fmt,
            "title": "Scheduled Cash Dividend Distribution",
            "detail": f"Payable to shareholders of record prior to {ex_div_fmt}",
            "badge": "b-blue",
        })
    if last_call_fmt and last_call_fmt != "—":
        upcoming_events.append({
            "type": "EARNINGS CALL",
            "date": last_call_fmt,
            "title": "Recent Earnings Call & Management Q&A",
            "detail": f"Beat {beat_count}/{len(eps_history) or 4} recent quarters (Avg surprise {earnings['avg_surprise_pct'] or 0:+.1f}%)",
            "badge": "b-gray",
        })

    analyst_actions = []
    for act in (ud.get("history") or [])[:10]:
        ep = act.get("epochGradeDate")
        dt_s = datetime.datetime.fromtimestamp(int(ep), tz=datetime.timezone.utc).strftime("%Y-%m-%d") if ep else "Recent"
        analyst_actions.append({
            "date": dt_s,
            "firm": act.get("firm") or "Wall Street Desk",
            "action": (act.get("action") or "main").upper(),
            "from_grade": act.get("fromGrade") or "",
            "to_grade": act.get("toGrade") or "",
            "pt_action": act.get("priceTargetAction") or "",
            "current_pt": act.get("currentPriceTarget"),
            "prior_pt": act.get("priorPriceTarget"),
        })

    sec_filings = []
    for fil in (sec.get("filings") or [])[:8]:
        sec_filings.append({
            "date": fil.get("date") or "",
            "type": fil.get("type") or "Filing",
            "title": fil.get("title") or "SEC Regulatory Filing",
            "url": fil.get("edgarUrl") or (fil.get("exhibits", [{}])[0].get("url") if fil.get("exhibits") else ""),
        })

    events_sec = {
        "upcoming_events": upcoming_events,
        "analyst_actions": analyst_actions,
        "sec_filings": sec_filings,
    }

    # ── 7. RELATED PEER STOCKS (WITH LIVE MINI-QUOTES) ──
    if not peer_syms:
        peer_pool = INDIA_WEEKLY_PRESETS["in_leaders"] if is_india else WEEKLY_PRESETS["leaders"]
        peer_syms = [s for s in peer_pool if s != clean_sym][:5]
    else:
        peer_syms = [s for s in peer_syms if s != clean_sym][:5]

    related_stocks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        f_map = {
            pool.submit(fetch_india_quote if is_india else fetch_cnbc_quote, ps): ps
            for ps in peer_syms
        }
        for fut in concurrent.futures.as_completed(f_map):
            ps = f_map[fut]
            try:
                pq = fut.result() or {}
                if pq.get("regularMarketPrice"):
                    related_stocks.append({
                        "symbol": ps,
                        "name": pq.get("shortName") or pq.get("longName") or ps,
                        "price": round(pq["regularMarketPrice"], 2),
                        "change_pct": round(pq.get("changePct") or 0.0, 2),
                    })
            except Exception:
                pass

    # ── 8. GOOGLE FINANCE-STYLE COMPOSITE OUTLOOK & AI EXECUTIVE SUMMARY ──
    # Blend Wall Street Analyst ratings + Multi-Source News Sentiment
    if tot_analysts > 0:
        a_bull = (sb + b_cnt) / tot_analysts
        a_neut = h_cnt / tot_analysts
        a_bear = (s_cnt + ss) / tot_analysts
    else:
        a_bull, a_neut, a_bear = 0.45, 0.35, 0.20

    n_tot = max(1, news_data["bullish_count"] + news_data["neutral_count"] + news_data["bearish_count"])
    n_bull = news_data["bullish_count"] / n_tot
    n_neut = news_data["neutral_count"] / n_tot
    n_bear = news_data["bearish_count"] / n_tot

    raw_bull = round((0.55 * a_bull + 0.45 * n_bull) * 100.0, 1)
    raw_bear = round((0.55 * a_bear + 0.45 * n_bear) * 100.0, 1)
    raw_neut = round(max(0.0, 100.0 - raw_bull - raw_bear), 1)
    outlook_lbl = "BULLISH" if raw_bull > raw_bear + 8 else ("BEARISH" if raw_bear > raw_bull + 8 else "NEUTRAL")

    # Build a crisp, data-driven Executive Summary matching Google Finance's AI Overview style
    summary_parts = []
    if reg_price is not None:
        w52_pos = ""
        if w52_high and w52_low and w52_high > w52_low:
            pct_off_hi = ((reg_price - w52_high) / w52_high) * 100.0
            w52_pos = f" ({pct_off_hi:+.1f}% from its 52-week high of {ccy}{w52_high:,.2f})"
        summary_parts.append(
            f"{long_name} ({clean_sym}) trades at {ccy}{reg_price:,.2f}{w52_pos} with a market capitalization of {mkt_cap_fmt}"
            + (f" and trailing P/E of {pe_ttm:.1f}x" if pe_ttm else "")
            + (f" (forward P/E {pe_fwd:.1f}x)." if pe_fwd else ".")
        )
    if q_fin_rows:
        lq = q_fin_rows[-1]
        q_str = f"In the latest reported quarter ({lq.get('date')}), revenue reached {lq.get('revenue_fmt', 'N/A')}"
        if lq.get("net_income_fmt"):
            q_str += f" with net income of {lq.get('net_income_fmt')} ({lq.get('net_margin_pct') or 0:.1f}% net margin)"
        if lq.get("free_cash_flow_fmt"):
            q_str += f" and free cash flow of {lq.get('free_cash_flow_fmt')}"
            if lq.get("capex_fmt"):
                q_str += f" (after {lq.get('capex_fmt')} in capital expenditures)"
        summary_parts.append(q_str + ".")
    if tot_analysts > 0 and tgt_mean:
        summary_parts.append(
            f"Wall Street consensus across {tot_analysts} analysts is {analysis['recommendation_key']} ({sb + b_cnt} Buy, {h_cnt} Hold, {s_cnt + ss} Sell) "
            f"with a mean 12-month price target of {ccy}{tgt_mean:,.2f} ({upside_pct:+.1f}% implied upside; range {ccy}{tgt_low or tgt_mean:,.2f}–{ccy}{tgt_high or tgt_mean:,.2f})."
        )
    if eps_history:
        last_eh = eps_history[-1]
        summary_parts.append(
            f"{clean_sym} has beaten consensus EPS estimates in {beat_count} of the last {len(eps_history)} quarters "
            f"(most recently {last_eh['eps_actual']} vs. {last_eh['eps_estimate']} est., {last_eh['surprise_pct'] or 0:+.1f}% surprise). "
            f"Next earnings release is scheduled for {next_earn_fmt}."
        )
    overview["ai_executive_summary"] = " ".join(summary_parts)

    result_payload = {
        "symbol": clean_sym,
        "market": mkt,
        "currency": ccy,
        "as_of": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "outlook": {
            "bullish_pct": raw_bull,
            "neutral_pct": raw_neut,
            "bearish_pct": raw_bear,
            "label": outlook_lbl,
        },
        "overview": overview,
        "analysis": analysis,
        "earnings": earnings,
        "financials": financials,
        "holdings": holdings,
        "events": events_sec,
        "related_stocks": related_stocks,
        "news": news_data,
    }

    if qs or news_data.get("headlines"):
        with _CACHE_LOCK:
            _INTEL_CACHE[cache_key] = {"ts": now, "data": result_payload}
    return dict(result_payload)


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
            mkt = qs.get("market", ["US"])[0]
            try:
                payload = build_chart_payload(sym, rng, intv, market=mkt)
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
            mkt = qs.get("market", ["US"])[0]
            default_preset = "in_leaders" if mkt.upper() == "IN" else "leaders"
            default_cap = "500000" if mkt.upper() == "IN" else "25000"
            preset = qs.get("preset", [default_preset])[0]
            syms_raw = qs.get("symbols", [""])[0]
            cap_raw = qs.get("capital", [default_cap])[0]
            try:
                capital = float(cap_raw)
            except ValueError:
                capital = 500000.0 if mkt.upper() == "IN" else 25000.0
            syms = [s.strip() for s in syms_raw.split(",") if s.strip()] if syms_raw else None
            try:
                report = run_weekly_scan(symbols=syms, capital=capital, preset=preset, market=mkt)
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

        if parsed.path == "/api/fundamentals":
            qs = urllib.parse.parse_qs(parsed.query)
            sym = qs.get("symbol", ["NVDA"])[0]
            mkt = qs.get("market", ["US"])[0]
            try:
                intel_payload = fetch_stock_intelligence(sym, mkt)
                body = json.dumps(intel_payload).encode("utf-8")
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

        if parsed.path == "/api/news":
            qs = urllib.parse.parse_qs(parsed.query)
            sym = qs.get("symbol", ["NVDA"])[0]
            mkt = qs.get("market", ["US"])[0]
            try:
                news_payload = fetch_stock_news(sym, mkt)
                body = json.dumps(news_payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc), "headlines": []}).encode("utf-8"))
            return

        if parsed.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if parsed.path == "/api/shutdown":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_response(403)
                self.end_headers()
                return
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
        server = http.server.ThreadingHTTPServer((HOST, PORT), StockSageHandler)
    except OSError:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/shutdown", timeout=2)
        except Exception:
            pass
        time.sleep(0.5)
        server = http.server.ThreadingHTTPServer((HOST, PORT), StockSageHandler)
    print(f"StockSage Quant Pro Server (Nasdaq + CNBC Live Feed) running at http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
