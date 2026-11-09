# StockSage Quant Pro 📈

Institutional-grade quantitative stock analysis web application, stochastic simulation suite, `quants.md` systematic strategy backtester, and CLI research engine.

## Features
- **Interactive Multi-Pane Charting**: TradingView Lightweight Charts v4 with OHLCV candlesticks, EMA 20/50, SMA 200, 20-Day VWAP, Bollinger Bands (20,2), Entry/Stop/Target price lines, RSI (14), and MACD (12,26,9).
- **Live Market Data Proxy (`server.py`)**: Fetches real-time OHLCV bars from the Official Nasdaq Historical API (`api.nasdaq.com`) and live fundamentals (P/E, Market Cap, EPS, 52W High/Low, Dividend Yield) from the CNBC Real-Time Quote API (`quote.cnbc.com`).
- **Multi-Model Stochastic Simulation Suite**:
  - Merton Jump-Diffusion (fat-tail crash aware)
  - Geometric Brownian Motion (GBM)
  - Historical Block Bootstrap (non-parametric)
  - RiskMetrics EWMA ($\lambda = 0.94$) vs. Equal-Weighted volatility
  - 24-bin Terminal Price Distribution Histogram, Win Probability ($P(S_T > S_0)$), and 5% Expected Shortfall ($\text{CVaR}_{5\%}$)
- **`quants.md` Systematic Strategy Backtester**:
  - Backtests 5 canonical quantitative strategies net of transaction costs/slippage (bps) against Buy & Hold:
    1. Trend EMA 20/50 Crossover + ADX Filter
    2. RSI(14) Mean Reversion + SMA 200 Filter
    3. Regime-Adaptive Bollinger Bands (switches between Breakout and Reversion using the Hurst Exponent $H$)
    4. MACD Momentum + OBV Volume Confirmation
    5. Donchian 20/10 Turtle Breakout
- **Volume Profile & Granular Bar-by-Bar Microstructure**:
  - 20-bin Volume Point of Control (`POC`), 70% Value Area High (`VAH`), and Value Area Low (`VAL`)
  - Candlestick & volume anomaly scanner (Doji, Hammer, Shooting Star, Bullish/Bearish Engulfing, Gaps, $\ge 1.5\times$ Institutional Accumulation/Distribution)
- **Executive Trade Proposal Engine**:
  - Composite Quant Score (`0–100`), Verdict (`STRONG BUY` / `BUY` / `HOLD` / `SELL` / `STRONG SELL`), Ideal Entry Zone, $1.8\times\text{Wilder ATR}$ Chandelier Stop-Loss, `TP1` ($1.5R$) / `TP2` ($2.5R$) / `TP3` ($4.0R$) targets with Monte Carlo hit odds, Regime-calibrated Holding Horizon, and 1%-Risk / Half-Kelly Position Sizing.
  - Optional Gemini 2.5 Flash / Pro deep equity research synthesis.
- **Weekly Alpha Recommendations & Cross-Sectional Playbook (Mon–Fri Horizon)**:
  - Parallel multi-sector universe scanner (`leaders`, `ai_semis`, `momentum`, `defensive`, `etfs`, or custom watchlist).
  - **Cross-Sectional Z-Score Ranking (`Qlib` style)** combining **Mansfield Relative Strength vs. SPY**, **WorldQuant Formulaic Alpha #101**, **TTM Volatility Squeeze** (Bollinger inside Keltner), **Connors RSI(2)** short-horizon pullback detection, **5-Day Merton Jump-Diffusion Monte Carlo** ($\pm 1\sigma$ weekly expected move cone), **Rolling 5-Day Walk-Forward Validation (`RankIC` hit rate)**, and **Weekly Floor Pivots (`S1, P, R1`)**.
  - **Correlation-Penalized Risk-Parity (HRP-Style) Weekly Basket Allocator**: Computes the $5 \times 5$ pairwise daily return correlation matrix across the Top 5 Weekly Picks and allocates portfolio capital by inverse volatility and diversification.

## Quick Start

### 1. Run the Web Application
```bash
python3 stock-analyzer/server.py
```
Then open **http://127.0.0.1:8765** in your browser.

### 2. Run the Command-Line Quant Engine
```bash
# Single-ticker institutional analysis
python3 stock-analyzer/quant_cli.py GOOG --range 1y --days 63

# Weekly Top 5 Alpha Picks & Correlation-Penalized Risk-Parity Portfolio Basket
python3 stock-analyzer/quant_cli.py --weekly --preset leaders --capital 25000
```
