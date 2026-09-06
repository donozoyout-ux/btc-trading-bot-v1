# Adaptive Trade Management V1 Audit

## Scope and base

Implementation is on local branch `codex/adaptive-trade-management-v1`. Its merge base is current `origin/main` (`f5c1911`) and it starts from the rebased bad-entry-hardening commit (`77208e4`) so the requested entry-quality and execution safety gates are retained. No commit, push, deployment, live TESTNET request, smoke trade, or order submission was performed.

## 1. Files changed

- `backtest/simulator.py`
- `config/constants.py`
- `config/hypotheses.py`
- `config/settings.py`
- `core/models.py`
- `dashboard/app.js`
- `dashboard/chart-intelligence.js`
- `dashboard/index.html`
- `dashboard/timezone.js`
- `dashboard/trade-tracker.js`
- `data/binance_execution_client.py`
- `engines/position_manager.py`
- `engines/trade_plan_engine.py`
- `execution/safer_testnet_executor.py`
- `execution/testnet_runtime.py`
- `runner.py`
- `tests/test_adaptive_management_v1.py`
- `reports/adaptive-management-v1-audit.md`

## 2. Previous TP behavior

The audited baseline used the setup target as TP1 (falling back to about 1.5R when invalid), a fixed 2.5R TP2, and an effectively fixed 50/50 TESTNET target split. Active-position runtime reconciled the exchange position and checked SL/TP protection, but did not reclassify the thesis from closed market evidence.

## 3. New target behavior

`TradePlanEngine` now accepts explicit target context: direction/setup/entry/invalidation, regime and confidence, volatility/ATR, support and resistance (including timeframe-grouped levels), breakout level, overextension, momentum, and volume quality. It deterministically selects the nearest directional structural obstacle for TP1 and the next structural target for TP2. ATR/R targets remain a declared fallback only when usable structure is absent. The output records TP R values, target mode, sources, confidence, reasons, stop source, and management profile. Invalid LONG/SHORT target ordering is rejected. Existing `RiskEngine` minimum-RR acceptance remains unchanged.

Target allocation uses centralized initial-hypothesis profiles: conservative 70/30, balanced 50/50, and trend-runner 35/65. Exchange lot-size constraints retain the single-final-target fallback.

## 4. Recovery-wait logic

The pure `PositionManager` treats a negative PnL as insufficient evidence to exit. A position inside initial risk, with intact structure and continuing regime or momentum support, returns `RECOVERY_WAIT` with `TEMPORARY_ADVERSE_MOVE`, `THESIS_STILL_VALID`, and `STRUCTURE_INTACT`. It places no orders itself.

## 5. Early-exit logic

Objective invalidation (initial invalidation hit, or a confirmed opposite structure plus BOS/CHoCH; regime flip requires confirming structure and momentum evidence) returns `EXIT_EARLY`. A single noisy indicator cannot trigger the path. If early exit is disabled, the result is fail-safe `NO_CHANGE` and the exchange stop remains authoritative.

## 6. Risk invariants

- A LONG stop can only stay unchanged or move upward; a SHORT stop can only stay unchanged or move downward.
- A detected widened stop is restored to the initial max-loss boundary.
- Position growth returns `POSITION_SIZE_INCREASE_DETECTED` and `AVERAGING_DOWN_BLOCKED` without an add-size action.
- No management action increases quantity, averages down, martingales, or widens initial loss.
- Missing/unhealthy market analysis returns no speculative change and retains existing protection.
- MFE/MAE, initial context, closed-candle clock, and target-replan state persist across restart.

## 7. Protection replacement logic

Stop and TP replacement create and verify the new reduce-only conditional order before cancelling the old order, then reconcile again. TP changes additionally verify that a STOP remains live. Replacement failure is journaled; an existing stop is retained where possible, and the emergency latch is activated if no stop can be verified. Partial close support is reduce-only, rejects zero/full/oversized quantities, verifies the position actually decreased, and verifies that STOP protection remains. Full early close uses the existing reduce-only close path.

Runtime order is: exchange reconciliation → real-time protection verification → dashboard closed-candle snapshot → pure management classification → at most one action for that closed 5M timestamp. Duplicate timestamps are rejected.

## 8. Dashboard TSİ changes

`dashboard/timezone.js` centralizes `Intl.DateTimeFormat` with locale `tr-TR` and IANA zone `Europe/Istanbul`. Chart axes, hover time, event times, last update, external update, timeframe last-close, and trade-tracker update use the helpers. The chart visibly labels `TSİ · Europe/Istanbul`. Raw candle UTC epoch values are not modified; formatting occurs only at presentation time.

The dashboard now exposes position state, thesis validity, current R, entry/mark, initial/current stop, TP1/TP2, management profile, action, and reason. `RECOVERY_WAIT` explicitly explains that the position is negative while the structural thesis remains valid.

## 9. Tests added

Tests cover LONG/SHORT structure targets, range/counter-trend/low/extreme-volatility modes, deterministic fallback and ordering, LONG/SHORT recovery wait, opposite-structure early exit, stop tightening/no widening, add-size blocking, TP2 continuation/cooldown, profile split allocation, safe replacement ordering and failure preservation, closed-5M deduplication, IANA timezone use/raw epoch preservation, and backtest comparison modes.

## 10. Tests passed

- `python -m compileall -q .`: PASS
- `python dashboard_server.py --self-test`: PASS
- `pytest -q`: **240 passed, 0 failed**
- JavaScript syntax (`node --check` for changed dashboard scripts): PASS

## 11. Known limitations

- Adaptive V1 is deterministic and telemetry-first; no profitability improvement is claimed.
- Backtest adaptive behavior requires an explicit closed-candle management-context provider; default mode remains `STATIC_EXIT_BASELINE` for historical parity.
- TP2 extension is capped and cooldown-controlled. V1 does not repeatedly trail or endlessly extend targets.
- Very small exchange positions can remain unsplittable and therefore use one final TP.
- No live TESTNET adaptive-management cycle was run in this task.

## 12. Strategy parameter changes

All new values live in `config/hypotheses.py` and are labelled Adaptive Management V1 **initial hypotheses — not optimized**. They are exposed through `BotSettings`: feature flags, breakeven/stop thresholds, replan threshold/cooldown/cap, split profiles, and target fallbacks. Existing strategy entry thresholds and RiskEngine minimum acceptable R:R were not weakened.

## 13. MAINNET execution status

**BLOCKED.** The existing TESTNET boundary still requires a testnet client, `BINANCE_TESTNET=true`, `ENV=testnet`, explicit order submission, non-read-only mode, non-shadow mode, and configured credentials. No mainnet path was added.

## 14. TESTNET status

Implementation and deterministic tests are ready for a controlled TESTNET adaptive-management validation. Live validation remains pending and must be performed separately with deliberate authorization while observing an existing/new TESTNET-only position.
