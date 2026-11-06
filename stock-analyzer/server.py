#!/usr/bin/env python3
"""
Local StockSage Quant Pro Server (http://127.0.0.1:8765)
Fetches live, exact market OHLCV bars from Official Nasdaq API + live fundamentals
(P/E, Market Cap, 52W High/Low, Dividend, Beta) from CNBC Real-Time Quote API,
normalized into the Yahoo v8 chart JSON structure expected by StockSage Quant Pro.
"""

import datetime
import http.server
import json
import os
import re
import urllib.parse
import urllib.request

PORT = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
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
    try:
        url = (
            "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
            f"?symbols={urllib.parse.quote(sym.upper())}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1"
        )
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        q = d.get("FormattedQuoteResult", {}).get("FormattedQuote", [{}])[0]
        return {
            "symbol": q.get("symbol", sym.upper()),
            "longName": q.get("name") or q.get("shortName") or sym.upper(),
            "shortName": q.get("shortName") or q.get("name") or sym.upper(),
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
    except Exception:
        return {"symbol": sym.upper(), "longName": sym.upper(), "shortName": sym.upper()}


def fetch_nasdaq_bars(sym: str, rng: str) -> list:
    days_map = {"1mo": 35, "3mo": 95, "6mo": 185, "1y": 366, "2y": 732, "5y": 1825}
    days = days_map.get(rng, 366)
    to_dt = datetime.date.today() + datetime.timedelta(days=1)
    from_dt = to_dt - datetime.timedelta(days=days)

    for asset_class in ("stocks", "etf", "index"):
        try:
            url = (
                f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(sym.upper())}/historical"
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
                return bars
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

        if parsed.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        return super().do_GET()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), StockSageHandler)
    print(f"StockSage Quant Pro Server (Nasdaq + CNBC Live Feed) running at http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
