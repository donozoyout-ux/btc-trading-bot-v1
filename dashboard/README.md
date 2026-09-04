# BTC Intelligence Console — Demo Dashboard

Read-only animated dashboard for the BTC trading bot. It combines real Binance
Futures public data, signed TESTNET account reads, Chart Reading V3, strategy,
news, advisory AI, Telegram events, and Shadow journaling while exposing no
order endpoint.

## Run

```powershell
python dashboard_server.py --open
```

Then open `http://127.0.0.1:8080`.

## Dashboard coverage

- BTCUSDT price, mark price and rolling 24h change
- 5M / 15M / 1H / 4H candlestick chart
- EMA20 / EMA50 / EMA200
- Bollinger Bands (20, 2)
- RSI14 and ADX14 displays; ATR is calculated server-side with the existing engine
- Strategy S/R confluence zones
- Market regime, confidence, volatility and overextended states
- 4H / 1H market structure
- setup, trigger, derivatives, risk and final decision
- Open Interest, funding, long/short and taker-flow ratios
- CoinGlass / CoinMarketCap availability and provenance status when keys are configured
- pre-trade entry / stop / TP1 / TP2 / position size / risk amount when a plan exists
- kill-switch status and human-readable decision reason
- closed-candle 4H / 1H / 15M / 5M structure, BOS, CHoCH, breakout/retest,
  volume, patterns and ATR overextension
- deterministic MTF bias and Strategy Orchestrator blockers
- normalized RSS news risk/sentiment and optional OpenAI advisory explanation
- Shadow-mode decision IDs and enriched JSONL journal records

## Safety boundary

This dashboard is intentionally **observation-only**:

- no `/order` route
- signed Binance requests are limited to TESTNET account reads
- no Binance credentials passed to the browser
- the dashboard Binance client is instantiated with `api_key=None` and `api_secret=None`
- CoinGlass / CoinMarketCap keys stay server-side in `.env`
- visible UI explicitly says `DEMO · READ ONLY`
- Shadow mode and the legacy Testnet executor both keep order submission disabled

## API

Public read endpoints are `/api/health`, `/api/snapshot`, `/api/news`, and
`/api/chart-intelligence`. `/api/account`, `/api/telegram`, and `/api/ai/status`
require a bearer token when `DASHBOARD_ADMIN_TOKEN` is configured.

The helper POST endpoints `/api/telegram/test`,
`/api/telegram/current-decision`, and `/api/ai/analyze` exist only when the
admin token is configured and supplied. There is no buy, sell, close, or order
endpoint.

## Telegram notification connection

The optional Telegram integration is outbound-only and cannot accept trading
commands. Configure these backend-only `.env` values:

```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your_botfather_token
TELEGRAM_CHAT_ID=your_private_chat_id
```

Then verify the bot identity and send one safe connection message:

```powershell
python dashboard_server.py --telegram-test
```

If you do not know the chat ID, set the token and enable Telegram, send `/start`
to the bot, then run `python dashboard_server.py --telegram-discover-chat`.
The command only discovers the destination; it does not execute incoming bot
commands.

This is the correct step before Shadow/Testnet. Real order execution must stay disabled until the existing backtest/Phase-2 work and testnet safety gates are complete.

## Smoke check

```powershell
python dashboard_server.py --self-test
```

Run the existing full test suite before deployment:

```powershell
pytest tests -v
```
