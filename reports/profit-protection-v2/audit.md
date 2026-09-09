# Adaptive Profit Protection V2 Audit

## Safety and behavior

| Field | Result |
|---|---|
| UNPROTECTED ROOT CAUSE | Combined STOP+TARGET predicate and restart-volatile notification dedupe identified and separated |
| TARGET-ONLY MISSING HANDLING | `PROTECTION_DEGRADED_TARGET_MISSING`; verified STOP retained; entries blocked until repair |
| STOP CRITICAL INVARIANT | Exact-quantity, close-side, reduce-only, positive-trigger `STOP_MARKET` remains mandatory |
| TARGET SELF HEAL | Verified context -> directionally valid TP2/TP_FINAL -> create -> verify -> persist |
| KILL SWITCH TELEGRAM DEDUPE | Persisted state transitions; one fault alert, one recovery, re-arms after recovery |
| PROFIT LOCK LADDER | Separate V2 initial hypotheses at 0.75R BE and 1.0/1.5/2.0R lock tiers |
| MFE GIVEBACK | MFE advances only from closed 5M close; giveback = MFE - current R |
| PROFIT FADE PARTIAL | At most once per position; 35% initial hypothesis; reduce-only and reconciled |
| RUNNER LOGIC | Healthy structure/regime/momentum/volume may remain `PROFIT_TRAIL` |
| TP2 GREED CONTROL | Profit fade emits `TARGET_EXTENSION_BLOCKED_PROFIT_FADE` and cannot extend TP2 |
| RESTART PERSISTENCE | MFE, partial-used flag, position intelligence, reconciliation and alert state persisted |
| MAINNET | BLOCKED by unchanged TESTNET execution boundary |
| TESTS | 303 passed / 0 failed at implementation validation |

The configured V2 values are labelled **INITIAL HYPOTHESES** in `config/hypotheses.py`. They are not claimed as optimized or production guarantees. V1 remains selectable as `ADAPTIVE_MANAGEMENT_V1`; V2 is separately selectable as `PROFIT_PROTECTION_V2`.

## Backtest

No real historical dataset was available in this workspace run. No performance value is fabricated.

| Metric | V1 | V2 |
|---|---:|---:|
| NET PNL | N/A | N/A |
| Profit factor | N/A | N/A |
| Average R | N/A | N/A |
| Max drawdown | N/A | N/A |
| MFE capture | N/A | N/A |
| Winner -> loser | N/A | N/A |

The simulator exposes both modes and reports average MFE, average realized R, MFE capture ratio, winning-trade giveback, winner-to-loser/breakeven counts, and MFE reach counts. Split partial-fill execution/P&L parity remains explicitly unclaimed.
