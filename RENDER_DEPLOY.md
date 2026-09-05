# Render deployment

The repository is ready to run as a Render Python web service.

## Manual service settings

- Runtime: Python
- Build command: `python -m pip install --upgrade pip && pip install -r requirements.txt && python -m compileall -q . && python dashboard_server.py --self-test`
- Start command: `python start_dashboard.py`
- Health check path: `/api/bootstrap`

`start_dashboard.py` launches the Render wrapper, binds to `0.0.0.0`, and reads Render's injected `PORT` variable automatically. Do not hard-code a port in Render.

The Render wrapper adds an always-visible **Render Runtime** panel to the existing dashboard. It appears before external market-data requests finish, so a cold-starting service no longer looks empty. It also exposes the network-free `GET /api/bootstrap` endpoint for service health and configuration-presence checks.

## Required environment values for signed Binance TESTNET account reads

Set these in Render > Environment. Never commit their values to Git.

```text
BINANCE_API_KEY=<Futures Testnet key>
BINANCE_API_SECRET=<Futures Testnet secret>
BINANCE_TESTNET=true
ACCOUNT_READ_ONLY=false
ORDER_SUBMISSION_ENABLED=true
SHADOW_MODE=false
RUN_EXECUTION_SMOKE_TEST=false
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

The default application settings remain read-only. Render starts the automatic
execution loop only when all explicit TESTNET flags above are configured. The
cloud wrapper never invokes the one-time smoke test, so a service restart cannot
repeat the controlled BUY/close validation. If any execution flag is missing or
conflicting, the deployment remains dashboard-only and order submission is
disabled.

Run a single Render instance for testnet automation. Before enabling it, clear
unrelated open BTCUSDT orders; a flat account with existing orders activates the
kill switch and blocks new entries.

## Render plan note

The blueprint defaults to the free web plan so deployment does not unexpectedly select a paid tier. Free services may sleep when inactive. A continuously running trading/monitoring process needs an always-on Render plan or a separate always-on worker when that phase is implemented.

## Smoke checks

After deployment:

1. Open `/` and confirm the full dashboard plus **Render Runtime** panel loads immediately.
2. Open `/api/bootstrap` and confirm `ok: true` and `ui: READY`.
3. Open `/api/health` for the deeper application/account status.
4. If Binance TESTNET credentials are configured, verify the account status without exposing credentials.
5. Review Render logs; startup output reports only whether secrets are configured, never their values.
