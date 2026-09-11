# Daily Profit Target V1 Audit

- Branch: `codex/daily-profit-target-v1`
- Base main SHA: `40a6951682fde47873eb7022b8878240fa2ac120`
- New commit SHA (implementation): `cb543a7e3fb987a2a6b5b3b550c23b8d740f9fbd`
- Daily target: **1.00%**

| Check | Result |
| --- | --- |
| OPENING BALANCE SOURCE | PASS |
| TARGET CALCULATION | PASS |
| NET REALIZED ACCOUNTING | PASS |
| UNREALIZED EXCLUDED | PASS |
| TARGET LATCH | PASS |
| ISTANBUL RESET | PASS |
| RESTART RECOVERY | PASS |
| ENTRY GUARD | PASS |
| ACTIVE POSITION MANAGEMENT UNAFFECTED | PASS |
| DATA UNAVAILABLE FAIL CLOSED | PASS |
| TELEGRAM DEDUPE | PASS |
| DASHBOARD TARGET UI | PASS |

## Validation

- Full tests: PASS (`350 passed`)
- Dashboard self-test: PASS
- JS syntax: PASS
- Compileall: PASS
- Secret scan: PASS
- Diff check: PASS

- Trading signal logic changed: **NO**
- Risk increased: **NO**
- MAINNET: **BLOCKED**
- Live orders: **NO**
- Merge: **NO**
- Deploy: **NO**

The target uses only `REALIZED_PNL + COMMISSION + FUNDING_FEE` as its
numerator. The start-of-day balance is persisted or reconstructed from the
current Binance wallet and all signed USDT income changes since Istanbul
midnight. Existing-position management runs before the new-entry guard.
