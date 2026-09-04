# BTC Intelligence Console — Demo Dashboard

Read-only animated dashboard for the BTC trading bot. It uses real Binance Futures **public market data** and the existing `MasterPipeline`, while exposing no order endpoint.

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

## Safety boundary

This dashboard is intentionally **market-data only**:

- no `/order` route
- no signed Binance request route
- no Binance credentials passed to the browser
- the dashboard Binance client is instantiated with `api_key=None` and `api_secret=None`
- CoinGlass / CoinMarketCap keys stay server-side in `.env`
- visible UI explicitly says `DEMO · READ ONLY`

This is the correct step before Shadow/Testnet. Real order execution must stay disabled until the existing backtest/Phase-2 work and testnet safety gates are complete.

## Smoke check

```powershell
python dashboard_server.py --self-test
```

Run the existing full test suite before deployment:

```powershell
pytest tests -v
```
