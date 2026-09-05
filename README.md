# BTC Trading Bot — Master Specification V1

Production-grade, multi-timeframe algorithmic trading system for BTC/USDT on Binance Futures (USDT-M), designed strictly according to the **Master Specification V1**.

---

## 1. Decision Pipeline Architecture

The bot does not accumulate arbitrary indicator points. Every single evaluation cycle executes the strict 10-engine pipeline:

```
DATA HEALTH
   ↓
MARKET REGIME (4H + 1H Confirm)
   ↓
MARKET STRUCTURE (Swings, BOS, CHoCH)
   ↓
SUPPORT / RESISTANCE & CONFLUENCE
   ↓
TRADE LOCATION (Zone Proximity & Anti-Trap Filter)
   ↓
SETUP DETECTION (Setup A / B / C)
   ↓
ENTRY TRIGGER (5M Multi-Factor State Machine)
   ↓
DERIVATIVES CONFIRMATION (OI, Funding, Crowding)
   ↓
RISK ENGINE & POSITION SIZER (Structural Stops & R:R)
   ↓
FINAL DECISION (ENTRY / WATCH / NO TRADE)
```

---

## 2. Multi-Timeframe Architecture

| Timeframe | Role | Key Indicators / Behaviors |
| :--- | :--- | :--- |
| **4H** | Macro Market Regime & Structure | EMA20/50/200, ADX14/DMI, RSI14, ATR14, 90-bar Volatility %ile |
| **1H** | Trend Confirmation & Pullback | Market Structure (HH/HL/LH/LL), Major S/R Levels, Fib Retracements |
| **15M** | Setup Detection & Trade Location | Trend Pullback, Breakout Retest, Confluence Zone Interaction |
| **5M** | Entry Trigger & Micro Momentum | Wick Rejection, Engulfing, Micro BOS, Volume Spikes |

> [!IMPORTANT]
> **Zero-Lookahead Guarantee**: Swings are confirmed using 2 bars left and 2 bars right, meaning bar $t$ is confirmed only when bar $t+2$ closes. Open unclosed bars are strictly discarded before running any analytical engine.

---

## 3. Core Strategy Setups

1. **Setup A — Trend Pullback (Trend Continuation)**:
   - 4H in `BULL` / `STRONG_BULL`, 1H Bullish Structure.
   - 15M pulling back into S/R confluence support on contracting volume.
   - 5M bullish trigger confirmed. Risk allocation: `0.50%`.

2. **Setup B — Breakout + Retest**:
   - Clear horizontal resistance broken with high volume ($\text{RVOL} \ge 1.5$).
   - Price returns to retest the broken level holding as new support.
   - 5M confirmation before entry.

3. **Setup C — Counter-Trend Reaction**:
   - 1H `BEAR`, 15M `BEARISH`, but price reaches Major Confluence Support ($\ge 2$ levels).
   - 5M Bollinger Band (20, 2) lower stretch + 5M RSI oversold ($< 30$).
   - Rejection candle / Micro BOS on 5M.
   - **Veto Filter**: If 4H/1H ADX $\ge 35$ or aggressive volume expansion, counter-trend trade is rejected!
   - Target: Bollinger Middle Band or nearest resistance. Risk allocation: `0.25%`.

---

## 4. Risk Safeguards & Daily Kill Switch

- **Structural Stop Loss**: Always placed below the invalidation swing / S/R zone + small ATR buffer (never an arbitrary fixed percentage).
- **Minimum R:R**: Minimum $1.5\text{R} - 2.0\text{R}$ required; otherwise `REJECT_TRADE`.
- **Daily Kill Switch**: Automatically disables all new trades upon:
  - Daily loss reaching $\ge 2.0\%$.
  - 3 consecutive losses.
  - Critical exchange / API data errors.

---

## 5. CLI Usage & Modes

### Run Historical Zero-Lookahead Backtest
```powershell
# Run benchmark backtest across synthetic data
python main.py backtest --synthetic --bars 1500

# Run backtest fetching real historical data from Binance Futures
python main.py backtest
```

### Run Live Shadow Mode (Forward Testing)
```powershell
# Connects to Binance Futures live public feeds, tracks virtual fills, and logs MFE/MAE
python main.py shadow --poll 15 --cycles 10
```

### Run Automated Unit & Integration Tests
```powershell
pytest tests -v
```

### Run the Read-Only Demo Account Dashboard
```powershell
# Validates the safety lock, public market feed, and optional testnet auth
python dashboard_server.py --self-test

# Opens the local BTC Intelligence Console at http://127.0.0.1:8080
python dashboard_server.py

# Verifies the Telegram bot identity and sends one read-only connection message
python dashboard_server.py --telegram-test
```

The dashboard runs the complete observation chain: closed-candle Chart Reading
V3, deterministic MTF interpretation, Setup A/B/C orchestration, RSS news risk,
optional advisory AI, deduplicated Telegram events, and enriched Shadow journal
records. It reads real public Binance Futures market data and, when a local
`.env` contains `BINANCE_TESTNET=true` plus testnet credentials, signed demo
account data. Account values are never simulated, and the dashboard exposes no
order submission or cancellation endpoint.

Telegram integration is outbound-only. Set `TELEGRAM_ENABLED=true`,
`TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` in the local `.env`; the token and
chat identifier are never included in browser payloads.

Optional AI analysis uses the OpenAI Responses API only when `AI_ENABLED=true`
and `OPENAI_API_KEY` is set. Its schema always returns
`execution_authority=false`; it cannot alter deterministic setup, direction,
trade plan, sizing, kill switch, or risk rejection.

The unattended dashboard has no interactive admin login wall. API credentials
remain server-side and are never serialized to the browser. Shadow decisions are stored in
`journal_logs/shadow_decisions.jsonl`; `SHADOW_MODE=true` never enables orders.

### Automated Binance Futures TESTNET execution

Execution is fail-closed and available only when all of these settings are
explicit: `ENV=testnet`, `BINANCE_TESTNET=true`,
`ORDER_SUBMISSION_ENABLED=true`, `ACCOUNT_READ_ONLY=false`, and
`SHADOW_MODE=false`. The execution client contains only the Binance Futures
Testnet base URL; production signed trading is not implemented.

```powershell
# Prints only configuration presence, never secret values
python main.py execution-doctor

# One-time TESTNET BUY → verify → reduce-only close → verify flat
# Also requires RUN_EXECUTION_SMOKE_TEST=true
python main.py execution-smoke

# Startup runs the configured smoke exactly once, then starts the normal loop
python main.py testnet-auto
```

`TEST_ORDER_NOTIONAL_USDT=10` is a target. Binance exchange filters remain
authoritative; BTCUSDT may require a larger Testnet-only minimum notional. The
normalized smoke notional is refused if it exceeds
`TEST_ORDER_MAX_NOTIONAL_USDT`. Execution events are written without secrets to
`journal_logs/execution_events.jsonl`, and restart state is recovered from
Binance before new entries are considered.

---

## 6. Directory Layout

```
bitcoinalimsatim4/
├── config/              # Constants, Enums, Settings
├── core/                # Data models (Candle, SwingPoint, Regime, Risk, Trade)
├── data/                # Binance Futures, CoinGlass, CMC, and CandleManager
├── engines/             # 10 Decoupled Strategy Engines
├── integrations/        # RSS News V2 and optional advisory AI
├── notifications/       # Telegram client and deduplicated event notifier
├── execution/           # Shadow, Testnet, and Live Executors
├── journal/             # Decision Logger (JSONL) & Metrics Calculator
├── backtest/            # Backtest Simulator & Historical Data Loader
├── tests/               # Pytest suite
├── runner.py            # Master Pipeline Orchestrator
└── main.py              # Rich CLI console
```
