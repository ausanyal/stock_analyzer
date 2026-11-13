---
name: quant-stock-analyzer
description: Institutional quantitative stock analysis, Weekly Alpha Recommendations & Cross-Sectional Scanner (Qlib Z-scores, WorldQuant Alpha#101, TTM Squeeze, Connors RSI-2, HRP Risk-Parity Basket), stochastic Monte Carlo simulations (Merton Jump-Diffusion, GBM, Bootstrap), Volume Profile (POC/VAH/VAL), quants.md systematic strategy backtesting, and trade proposal generation. Activate whenever the user asks to analyze a stock ticker, get weekly stock recommendations, run price simulations, inspect price bars, or propose whether/when to buy or sell.
---

# Quant Stock Analyzer Skill (`quants.md` & Institutional Quantitative Framework)

When the user asks you to analyze any stock ticker, generate **weekly stock recommendations**, simulate its price path, inspect its bars, or recommend whether to buy/sell/hold with an entry point and holding horizon:

## Step 1: Execute the Quantitative Telemetry Engine
- **Single-Ticker Deep Dive (US or India NSE)**:
  ```bash
  # US Equity (NASDAQ/NYSE in USD $)
  python3 /Users/aubins/Desktop/personal/Aubin/Coding/stock-analyzer/quant_cli.py NVDA --market US --range 1y --days 63
  # Indian Equity (NSE/BSE in INR ₹)
  python3 /Users/aubins/Desktop/personal/Aubin/Coding/stock-analyzer/quant_cli.py RELIANCE --market IN --range 1y --days 63
  ```
- **Weekly Top Picks & Cross-Sectional Alpha Scanner (Mon–Fri Playbook)**:
  ```bash
  # US Weekly Picks (benchmarked vs SPY)
  python3 /Users/aubins/Desktop/personal/Aubin/Coding/stock-analyzer/quant_cli.py --weekly --market US --preset leaders --capital 25000
  # India NSE Weekly Picks (benchmarked vs NIFTY 50)
  python3 /Users/aubins/Desktop/personal/Aubin/Coding/stock-analyzer/quant_cli.py --weekly --market IN --preset in_leaders --capital 500000
  ```
  *(Supported US `--preset` values: `leaders`, `ai_semis`, `momentum`, `defensive`, `etfs`; Supported India `--preset` values: `in_leaders`, `in_it`, `in_banks`, `in_infra_auto`, `in_pharma_fmcg`, or pass custom tickers)*
*(Note: Requires outbound network access to Nasdaq/CNBC for US and Moneycontrol/Groww NSE APIs for India; run with `BypassSandbox: true` if sandboxed network is restricted).*

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

## Step 3: Deliberative Planner (`PLANNER_SI`), Deep Reasoner (`REASONER_SI`) & 8 Attached Skills
When operating the StockSage Quant Pro Copilot (`POST /api/chat` in `stock-analyzer/server.py` and `executePageDriverCommands` in `stock-analyzer/index.html`), see [`PLANNER_SI_AND_SKILLS.md`](./PLANNER_SI_AND_SKILLS.md) for the full 3-Stage Deliberative Architecture:
- **Dual Investment Horizon Engine (`⚡ Swing 1–8W` vs. `🏛️ Long-Term Compounder 1–3Y`)**:
  - **Swing Mode (`1–8W`)**: `65%` Technical Timing / Regime + `35%` Street Valuation, `1Y · 1D` chart, `63D` Merton Jump-Diffusion Monte Carlo, `1.8× ATR` Chandelier stops, and `GET /api/weekly-scan` HRP Risk-Parity Playbook.
  - **Long-Term Compounder Mode (`1–3Y`)**: `80%` Fundamental Compounding (ROIC, 3Y Growth, 2-Stage 10Y Intrinsic DCF, Shareholder Yield) + `20%` Secular Weekly Structure (`50W EMA` / `200W SMA`), `5Y · 1W` chart, `252D` Monte Carlo, **3-Tranche DCA Accumulation Schedule** (`40%` Starter / `35%` 50W EMA / `25%` 200W SMA & Bear DCF Floor), and `GET /api/longterm-scan` Core Wealth Portfolio Scanner.
- **Stage 0 (Conversational & Conceptual NLP Router)**: Routes prompts across `conversational_meta`, `follow_up`, `conceptual_investing` (16 equity/quant/DCF/DCA/risk topics answered in warm first-person prose with zero unsolicited KPI tables or `<details>` clutter), `conversational_stock_check`, and `quant_deep_dive`.
- **Stage 1 (`PLANNER_SI`)**: Formulates 3 falsifiable hypotheses (`H1` Technical/Regime, `H2` Fundamental/Street DCF & Compounder Quality, `H3` Stochastic Tail-Risk/Catalyst), selects from the 8 Attached Skills (`skill_page_driver`, `skill_rag_fundamentals_filings`, `skill_earnings_street_consensus`, `skill_quant_regime_backtester`, `skill_stochastic_tail_risk`, `skill_cross_sectional_screener`, `skill_news_catalyst_rag`, `skill_long_term_compounder`), and emits deterministic `ui_commands` (`SET_HORIZON`, `SWITCH_MARKET`, `LOAD_TICKER`, `SET_RANGE`, `OPEN_INTEL_TAB`, `SET_FIN_FREQ`, `FILTER_NEWS`, `RUN_MONTE_CARLO`, `PLOT_CHART_LINES`, `SET_CAPITAL`, `RUN_WEEKLY_SCAN`, `RUN_LONGTERM_SCAN`, `SCROLL_TO_PANEL`).
- **Stage 2 (Parallel RAG & Tool Executor)**: Retrieves the 8-corpus, 17-module server RAG pack (`[RAG:OVERVIEW]`, `[RAG:ANALYST_CONSENSUS]`, `[RAG:EARNINGS_MATRIX]`, `[RAG:FINANCIAL_STATEMENTS]`, `[RAG:OWNERSHIP_SHORT]`, `[RAG:SEC_FILINGS_EVENTS]`, `[RAG:NEWS_WIRE]`, `[RAG:LONG_TERM_COMPOUNDER_DCF]`) + server OHLCV quant & scanner telemetry (`[TOOL:QUANT_DOSSIER]`, `[TOOL:LONGTERM_COMPOUNDER_SCAN]`, `[TOOL:WEEKLY_ALPHA_SCAN]`).
- **Stage 3 (`REASONER_SI` · Adaptive Vertex AI `/v1` & Gemini `/v1beta`)**: Uses adaptive temperature/prompts (`80–220` words warm prose on conversational/conceptual turns; conversational human lead-in + Chain-of-Verification grounding on deep dives), reconciles `1–8W` Swing Technicals vs. `1–3Y` Secular Compounding & 2-Stage DCF, and drives the live StockSage UI.
