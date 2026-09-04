# Render deployment

The repository is ready to run as a Render Python web service.

## Manual service settings

- Runtime: Python
- Build command: `python -m pip install --upgrade pip && pip install -r requirements.txt && python -m compileall -q . && python dashboard_server.py --self-test`
- Start command: `python start_dashboard.py`
- Health check path: `/`

`start_dashboard.py` binds to `0.0.0.0` and reads Render's injected `PORT` variable automatically. Do not hard-code a port in Render.

## Required environment values for signed Binance TESTNET account reads

Set these in Render > Environment. Never commit their values to Git.

```text
BINANCE_API_KEY=<Futures Testnet key>
BINANCE_API_SECRET=<Futures Testnet secret>
BINANCE_TESTNET=true
ACCOUNT_READ_ONLY=true
```

Optional integrations:

```text
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_CHAT_ID=<chat id>
COINGLASS_API_KEY=<secret>
COINMARKETCAP_API_KEY=<secret>
AI_ENABLED=false
OPENAI_API_KEY=<secret, only when AI_ENABLED=true>
DASHBOARD_ADMIN_TOKEN=<strong random secret>
```

The default `render.yaml` deliberately keeps signed account access read-only. Order execution is not enabled by this deployment file.

## Render plan note

The blueprint defaults to the free web plan so deployment does not unexpectedly select a paid tier. Free services may sleep when inactive. A continuously running trading/monitoring process needs an always-on Render plan or a separate always-on worker when that phase is implemented.

## Smoke checks

After deployment:

1. Open `/` and confirm the dashboard loads.
2. Open `/api/health` and confirm the service reports `ok: true`.
3. If Binance TESTNET credentials are configured, verify the account status without exposing credentials.
4. Review Render logs; startup output reports only whether secrets are configured, never their values.
