---
name: quant-stock-analyzer
description: Institutional quantitative stock analysis, stochastic Monte Carlo simulations (Merton Jump-Diffusion, GBM, Bootstrap), Volume Profile (POC/VAH/VAL), quants.md systematic strategy backtesting, and trade proposal generation (Buy/Sell/Hold verdict, entry zone, ATR stop-loss, R-multiple profit targets, holding period, and Kelly position sizing). Activate whenever the user asks to analyze a stock ticker, run price simulations, inspect price bars, or propose whether/when to buy or sell.
---

# Quant Stock Analyzer Skill (`quants.md` & Institutional Quantitative Framework)

When the user asks you to analyze any stock ticker, simulate its price path, inspect its bars, or recommend whether to buy/sell/hold with an entry point and holding horizon:

## Step 1: Execute the Quantitative Telemetry Engine
Run the standalone quantitative CLI script (`stock-analyzer/quant_cli.py`) to fetch live OHLCV bars, compute technicals, Volume Profile, Hurst regime, and run a 1,000-path Merton Jump-Diffusion Monte Carlo simulation:

```bash
python3 /Users/aubins/Desktop/personal/Aubin/Coding/stock-analyzer/quant_cli.py <TICKER> --range 1y --days 63
```
*(Note: Requires outbound network access to Yahoo Finance v8 chart API; run with `BypassSandbox: true` if sandboxed network is restricted).*

## Step 2: Enforce `quants.md` Institutional Research Standards
Always evaluate and report across these five quantitative pillars:
1. **Market Regime & Time-Series Physics**:
   - Report the **Hurst Exponent ($H$)**: $H > 0.55$ indicates persistent trending/momentum behavior (favor trend-following & breakout entries); $H < 0.45$ indicates mean-reversion (favor Bollinger/RSI pullback entries); $H \approx 0.50$ indicates random walk.
   - Report **Sharpe Ratio**, **Sortino Ratio**, **Annualized Volatility**, and **Maximum Drawdown (Max DD)**.
2. **Granular Bar-by-Bar & Volume Profile Microstructure**:
   - Inspect the most recent price bars (`recent_bars`), noting institutional accumulation/distribution volume surges, candlestick rejections, and gap structure.
   - Anchor support/resistance to the 20-bin **Volume Point of Control (POC)**, **Value Area High (VAH)**, and **Value Area Low (VAL)**.
3. **Stochastic Simulation (Merton Jump-Diffusion + EWMA Volatility)**:
   - Report the 63-day (or custom horizon) probability of profit ($P(S_T > S_0)$), Bear Tail ($P_5$), Quartile ($P_{25}$), Median ($P_{50}$), Expected Mean, Quartile ($P_{75}$), Bull Tail ($P_{95}$), and 5% Expected Shortfall ($\text{CVaR}_{5\%}$).
4. **Actionable Trade Proposal**:
   - **Verdict**: `STRONG BUY` | `BUY` | `HOLD` | `SELL` | `STRONG SELL` + **Composite Quant Score (0–100)**.
   - **Ideal Entry Zone**: Confluence of Volume Profile POC/VAL, EMA20, and $0.5\times\text{ATR}$ pullback.
   - **Stop-Loss (Invalidation)**: $1.8\times\text{Wilder ATR(14)}$ Chandelier stop below structural support.
   - **Profit-Taking Ladder**: `TP1` ($1.5R$ trim), `TP2` ($2.5R$ base target), `TP3` ($4.0R$ / $P_{75}$ runner).
   - **Holding Horizon**: Calibrated to the Hurst exponent and ATR velocity (e.g., `1–3 Weeks` for mean-reversion vs. `2–6 Months` for persistent trends).
   - **Risk-Based Position Sizing**: Share count and portfolio percentage capped at $\le 1\%$ account equity risk per trade and Half-Kelly criterion.
