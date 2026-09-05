# Bad Entry Hardening Report

## BAD ENTRY ROOT CAUSE

The executable path could combine configured rather than signed TESTNET capital, permissive breakout/retest and volume semantics, no final opposing-5M-structure gate, no structural reward-space veto, and no strategy-to-TESTNET mark/fill basis check. The incident snapshot is not treated as proof of the exact entry-time state; the code audit establishes that these states were permitted.

## Results

- REAL BALANCE SIZING: PASS
- MTF BLOCKER SEMANTICS: PASS
- BREAKOUT RETEST HARDENING: PASS
- ANTI-CHASE FILTER: PASS
- 5M TRIGGER QUALITY: PASS
- NEARBY RESISTANCE/SUPPORT FILTER: PASS
- TESTNET PRICE BASIS CHECK: PASS
- ENTRY SNAPSHOT JOURNAL: PASS
- MAINNET EXECUTION: BLOCKED
- TESTS: 222 passed / 0 failed after rebase onto current `origin/main`
- STRATEGY PARAMETERS OPTIMIZED: NO
- COMMIT / PUSH: NO
- READY FOR NEW TESTNET FORWARD OBSERVATION: YES

## Before / After Execution Path

Before: deterministic candidate -> informational MTF conflicts -> risk sized from in-memory configured capital -> executor eligibility -> market order without TESTNET mark comparison -> fill accepted without deviation check -> SL/TP.

After: deterministic candidate -> chronological confirmed retest -> structural 5M trigger -> entry-quality/anti-chase and nearby-zone veto -> risk synchronized to signed TESTNET capital -> true hard blockers -> frozen entry snapshot -> TESTNET mark/basis tolerance -> market order -> fill tolerance -> SL/TP, or immediate flatten on unsafe fill deviation.

## New Initial Hypothesis

`entry_max_atr_extension = 2.0`: initial, configurable 5M/15M anti-chase distance from the 20-bar mean measured in ATR. It is not optimized and no performance claim is made.

## Strategy Behavior Change

Setup B now requires a chronological quality breakout, later zone hold, and directional retest confirmation. The 5M trigger now requires actual price-action confirmation and labels volume expansion only at configured RVOL. These changes intentionally harden entry quality and may reduce historical candidates/trades; historical performance was not silently regenerated or claimed improved.

## Files Changed

- `config/hypotheses.py`
- `config/settings.py`
- `core/models.py`
- `dashboard_server.py`
- `data/binance_execution_client.py`
- `engines/entry_quality_engine.py`
- `engines/setup_engine.py`
- `engines/strategy_orchestrator.py`
- `engines/trigger_engine.py`
- `execution/testnet_executor.py`
- `execution/testnet_runtime.py`
- `journal/execution_journal.py`
- `runner.py`
- `render_server.py`
- `tests/test_bad_entry_hardening.py`
- `tests/test_short_setups_and_derivatives_veto.py`
- `tests/test_testnet_execution.py`
- `tests/test_market_authority_and_risk_telemetry.py`
- `reports/bad-entry-root-cause-audit.md`
- `reports/bad-entry-hardening-report.md`

## Live Safety Statement

No live smoke test was run. No production or TESTNET API order endpoint was called. The existing open TESTNET position was not queried, modified, protected, closed, or otherwise touched by this local audit. All executor order-flow validation used fake clients.
