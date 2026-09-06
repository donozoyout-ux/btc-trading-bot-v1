# Adaptive Trade Management V1 Audit

## Scope and base

Implementation is on branch `codex/adaptive-trade-management-v1`, rebased onto current `origin/main` (`858f5ad`). Current main already contains the bad-entry hardening and Render keepalive work; both are retained. No deployment, live TESTNET request, smoke trade, or order submission was performed during this correctness repair.

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

The pure `PositionManager` treats a negative PnL as insufficient evidence to exit. A position inside initial risk, with intact structure and continuing regime or momentum support, returns `RECOVERY_WAIT` with `TEMPORARY_ADVERSE_MOVE`, `THESIS_STILL_VALID`, and `STRUCTURE_INTACT`. Chart Reader trend values are normalized explicitly: UP supports LONG, DOWN supports SHORT, RANGE is neutral, and UNAVAILABLE never becomes support. Opposing momentum is reported truthfully even when the complete thesis still justifies recovery wait. It places no orders itself.

## 5. Early-exit logic

Objective invalidation (initial invalidation hit, or opposite BOS confirmed by matching opposite structure) returns `EXIT_EARLY`. An opposite CHoCH alone only weakens the thesis; it requires an opposing regime and/or opposing normalized momentum on a closed 5M frame before it can invalidate. A single noisy indicator cannot trigger the path. If early exit is disabled, the result is fail-safe `NO_CHANGE` and the exchange stop remains authoritative.

## 6. Risk invariants

- A LONG stop can only stay unchanged or move upward; a SHORT stop can only stay unchanged or move downward.
- A detected widened stop is restored to the initial max-loss boundary.
- Position growth returns `POSITION_SIZE_INCREASE_DETECTED` and `AVERAGING_DOWN_BLOCKED` without an add-size action.
- No management action increases quantity, averages down, martingales, or widens initial loss.
- Management readiness requires healthy/safe Binance market data, a valid mark, and an AVAILABLE 5M frame containing closed candles. Missing/degraded analysis returns `MARKET_ANALYSIS_UNAVAILABLE_KEEP_EXISTING_PROTECTION`, makes no speculative change, and retains existing protection.
- After entry fill reconciliation and successful protection placement, an immutable exchange baseline stores `actual_entry_price`, exchange-normalized `actual_initial_position_size`, exchange-normalized `actual_initial_stop`, decision ID, and opened time. Planned entry/size/stop remain separate telemetry fields. Adaptive R and invariant checks use only the verified exchange baseline.
- MFE/MAE, verified initial context, closed-candle clock, and target-replan state persist across restart when the local journal file survives.

## 7. Protection replacement logic

Stop and TP replacement create and verify the new reduce-only conditional order before cancelling the old order, then reconcile again. Stop replacement now requires exactly one final stop with the correct close side, reduce-only flag, and exchange-normalized remaining quantity. If old-stop cancellation fails, redundant protection is preferred to no protection, `PROTECTION_RECONCILIATION_REQUIRED` is persisted, and further adaptive modifications fail closed until exchange reconciliation proves a single valid stop.

Partial exchange reductions are detected from the reconciled before/after position sizes. A known completed TP1 is removed from internal protection state and journaled; an unidentified reduction is labelled `UNKNOWN_PARTIAL_REDUCTION`. The original verified size, MFE/MAE, and target-replan counters are not reset. The remaining stop is resized through create → verify → cancel → final reconcile, and remaining TP quantity is required not to exceed the remaining position.

Active-position startup and every management reconciliation also validate protection directly against the current exchange position. This covers a TP fill that occurs while the process is offline, where no before/after transition is observable. An oversized stop is replaced at the exact same trigger price with current remaining quantity, verified before the old stop is cancelled, and journaled as `RESTART_PROTECTION_QUANTITY_RECONCILED` during recovery. This operational repair is allowed when adaptive entry context is missing; it does not reconstruct risk or change any target/stop price.

Target replacement now rolls back the newly created target if cancellation of the old target fails. A proven rollback records `TARGET_REPLACEMENT_ROLLED_BACK` and reports deterministic replacement failure without claiming a replan. If rollback cannot be proven, the executor persists `PROTECTION_RECONCILIATION_REQUIRED`, preserves the stop, and blocks further adaptive changes. Successful replacement requires correct close side, reduce-only status, requested quantity, disappearance of the old target, and cumulative open target quantity no greater than the current exchange position.

Recovered exchange orders regain semantic roles only when deterministic: STOP is structural and two distinct directionally ordered targets are TP1/TP2. A lone target or otherwise ambiguous target set remains `UNKNOWN_TARGET` rather than receiving a fabricated TP identity.

- **RESTART PARTIAL-FILL STOP RECONCILIATION: PASS**
- **TARGET REPLACEMENT ROLLBACK: PASS**
- **TARGET QUANTITY INVARIANT: PASS**
- **MISSING CONTEXT OPERATIONAL PROTECTION: PASS**

Early exit now submits the reduce-only close, requires a confirmed flat position, removes stale regular and algo reduce-only orders, re-fetches exchange state, and only then journals `EARLY_EXIT` as confirmed. Uncertain closure or cleanup emits `EARLY_EXIT_RECONCILIATION_FAILURE`; an open position must retain a verified stop or the emergency latch activates.

Runtime order is: exchange reconciliation → real-time protection verification → dashboard closed-candle snapshot → pure management classification → at most one action for that closed 5M timestamp. Duplicate timestamps are rejected.

## 8. Dashboard TSİ changes

`dashboard/timezone.js` centralizes `Intl.DateTimeFormat` with locale `tr-TR` and IANA zone `Europe/Istanbul`. Chart axes, hover time, event times, last update, external update, timeframe last-close, and trade-tracker update use the helpers. The chart visibly labels `TSİ · Europe/Istanbul`. Raw candle UTC epoch values are not modified; formatting occurs only at presentation time.

The dashboard now exposes position state, thesis validity, current R, entry/mark, initial/current stop, TP1/TP2, management profile, action, and reason. `RECOVERY_WAIT` explicitly explains that the position is negative while the structural thesis remains valid.

## 9. Tests added

Tests cover LONG/SHORT structure targets, range/counter-trend/low/extreme-volatility modes, deterministic fallback and ordering, Chart Reader trend/volume normalization, unavailable-analysis fail-closed behavior, LONG/SHORT CHoCH weakening and confirmed invalidation, recovery telemetry with opposing momentum, stop tightening/no widening, add-size blocking, TP2 continuation/cooldown, profile split allocation, safe replacement ordering and failure preservation, closed-5M deduplication, IANA timezone use/raw epoch preservation, static target isolation, incompatible pipeline rejection, and adaptive backtest stop/TP2/HOLD transitions.

## 10. Backtest parity boundary

- **CORE ADAPTIVE MANAGEMENT PARITY: PASS.** The shared PositionManager drives early exit, stop tightening, TP2 replan, recovery wait, and hold from closed-candle inputs.
- **TP SPLIT EXECUTION/P&L PARITY: NOT YET IMPLEMENTED.** Live TESTNET uses actual split TP1/TP2 quantities; historical accounting does not yet reproduce the full partial-TP P&L lifecycle.
- `ADAPTIVE_MANAGEMENT_V1` disables the legacy ExitEngine TP1 auto-breakeven behavior so PositionManager is the sole adaptive stop owner. `STATIC_EXIT_BASELINE` retains the prior auto-breakeven setting unchanged.

## 11. Restart durability and fail-closed behavior

- **LOCAL JOURNAL RESTART CONTEXT: SUPPORTED WHEN FILE SURVIVES**
- **RENDER FREE FILESYSTEM DURABILITY: NOT GUARANTEED**
- **MISSING CONTEXT BEHAVIOR: FAIL-CLOSED**

If an exchange position survives while its verified entry context does not, runtime persists and exposes `RECOVERED_POSITION_CONTEXT_UNAVAILABLE`, retains existing exchange protection, blocks new entry because the position remains open, and performs no adaptive stop, target, partial-close, or early-exit action. It never redefines the current tightened stop or remaining quantity as original risk. Operator warning/journal emission is deduplicated until the position is flat or reliable context is restored.

## 12. Tests passed

- `python -m compileall -q .`: PASS
- `python dashboard_server.py --self-test`: PASS
- `pytest -q`: **279 passed, 0 failed**
- JavaScript syntax (`node --check` for changed dashboard scripts): PASS

## 13. Known limitations

- Adaptive V1 is deterministic and telemetry-first; no profitability improvement is claimed.
- Backtest adaptive behavior requires an explicit closed-candle management-context provider. It applies early exits, tighter stops, TP2 replans, recovery waits, and holds to subsequent candles while preserving original initial risk separately. `TAKE_PARTIAL` is **NOT ACTIVE** in PositionManager V1, and split TP execution/P&L parity remains pending.
- Historical simulation does not model Binance API latency, exchange quantity/price quantization during later protection replacement, or replacement-request failure modes. Live TESTNET keeps the stricter create/verify/cancel/reconcile workflow.
- `STATIC_EXIT_BASELINE` constructs its pipeline with dynamic targets disabled and no adaptive position-management transitions. `ADAPTIVE_MANAGEMENT_V1` enables both; incompatible injected pipelines fail explicitly.
- TP2 extension is capped and cooldown-controlled. V1 does not repeatedly trail or endlessly extend targets.
- Very small exchange positions can remain unsplittable and therefore use one final TP.
- No live TESTNET adaptive-management cycle was run in this task.

## 14. Strategy parameter changes

All new values live in `config/hypotheses.py` and are labelled Adaptive Management V1 **initial hypotheses — not optimized**. They are exposed through `BotSettings`: feature flags, breakeven/stop thresholds, replan threshold/cooldown/cap, split profiles, and target fallbacks. Existing strategy entry thresholds and RiskEngine minimum acceptable R:R were not weakened.

## 15. MAINNET execution status

**BLOCKED.** The existing TESTNET boundary still requires a testnet client, `BINANCE_TESTNET=true`, `ENV=testnet`, explicit order submission, non-read-only mode, non-shadow mode, and configured credentials. No mainnet path was added.

## 16. TESTNET status

Implementation and deterministic tests are ready for a controlled TESTNET adaptive-management validation. Live validation remains pending and must be performed separately with deliberate authorization while observing an existing/new TESTNET-only position.
