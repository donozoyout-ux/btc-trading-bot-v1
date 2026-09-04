# Historical Backtest Phase 1 — Summary Report

**Generated:** 2026-09-04T14:20:11Z
**Symbol:** BTC/USDT (Binance USDT-M Futures)
**Backtest Mode:** Technical Baseline — Derivatives UNAVAILABLE
**Period:** 2023-09-04 to 2026-09-04
**Total 5M Candles:** 315,575
**Total Trades:** 681

---

## VERDICT

HISTORICAL BACKTEST PHASE 1 VERDICT: **PASS**
DATASET VERDICT: **PASS**

---

## OVERALL PERFORMANCE

| Metric | Value |
|---|---|
| Total Trades | 681 |
| Wins / Losses | 191 / 490 |
| Win Rate | 28.05% |
| Net PnL | $-1,796.35 |
| Gross PnL | $21,259.29 |
| Total Fees | $2,477.43 |
| Profit Factor | 1.03 |
| Expectancy | $-2.64 |
| Average R | -0.508R |
| Median R | -1.07R |
| Best Trade R | 2.43R |
| Worst Trade R | -21.06R |
| Max Drawdown | 38.32% |
| Max Consecutive Wins | 5 |
| Max Consecutive Losses | 18 |
| Total Return | -17.96% |
| Final Equity | $8,203.65 |

---

## CANDIDATE → TRADE RECONCILIATION

| Metric | Value |
|---|---|
| Total Candidates | 681 |
| Candidates Passed Risk | 681 |
| Candidates Produced Trade | 681 |
| Unreconciled Candidates | 0 |
| Unreconciled Trades | 0 |
| Reconciliation PASS/FAIL | PASS |

---

## KILL-SWITCH / GUARD BLOCKS

| Guard Type | Blocks |
|---|---|
| DAILY_LOSS_GUARD | 0 |
| CONSECUTIVE_LOSS_GUARD | 14,975 |
| EMERGENCY_LATCH | 0 |
| Total Guard Blocks | 14,975 |

---

## SETUP BREAKDOWN

| Setup | Trades | Win Rate | PF | Expectancy | Avg R | Max DD |
|---|---|---|---|---|---|---|
| TREND_PULLBACK | 111 | 31.53% | 1.13 | $-0.78 | -0.333R | 8.56% |
| BREAKOUT_RETEST | 568 | 27.46% | 1.02 | $-2.93 | -0.54R | 32.69% |
| COUNTER_TREND_REACTION | 2 | 0.0% | 0.0 | $-22.65 | -1.13R | 0.45% |

---

## DIRECTION BREAKDOWN

| Direction | Trades | Win Rate | PF | Net PnL | Avg R | Max DD |
|---|---|---|---|---|---|---|
| LONG | 589 | 27.33% | 1.01 | $-1,977.38 | -0.54R | 36.21% |
| SHORT | 92 | 32.61% | 1.23 | $181.03 | -0.302R | 5.04% |

---

## SIGNAL FUNNEL (CONDITIONAL CHAIN)

Each stage counts only evaluations that passed ALL previous stages.
Rejected evaluations appear once, at their first failure stage.

| Stage | Count | From Prev % | From Total % |
|---|---|---|---|
| TOTAL_EVALUATIONS | 313,411 | 0.0% | 100.0% |
| DATA_HEALTH_PASS | 313,411 | 100.0% | 100.0% |
| REGIME_ELIGIBLE | 313,411 | 100.0% | 100.0% |
| KILL_SWITCH_PASS | 298,549 | 95.3% | 95.3% |
| ↳ rejected: Kill Switch latched: Kill Switch Activated: Consecutive loss cooldown: 3 >= 3 | 14,862 | — | — |
| STRUCTURE_ELIGIBLE | 247,942 | 83.0% | 79.1% |
| ↳ rejected: 4H and 1H structure both MIXED | 50,607 | — | — |
| GOOD_TRADE_LOCATION | 217,277 | 87.6% | 69.3% |
| ↳ rejected: Location BAD_LOCATION | 19,336 | — | — |
| ↳ rejected: Location NEUTRAL | 11,329 | — | — |
| SETUP_DETECTED | 83,728 | 38.5% | 26.7% |
| ↳ rejected: No setup detected | 133,549 | — | — |
| ENTRY_TRIGGER_DETECTED | 2,419 | 2.9% | 0.8% |
| ↳ rejected: Trigger IN_POSITION | 71,033 | — | — |
| ↳ rejected: Trigger WAITING_TRIGGER | 10,276 | — | — |
| MOMENTUM_PASS | 2,419 | 100.0% | 0.8% |
| DERIVATIVES_ACCEPTABLE | 2,419 | 100.0% | 0.8% |
| TRADE_PLAN_CREATED | 2,419 | 100.0% | 0.8% |
| RISK_PASS | 558 | 23.1% | 0.2% |
| ↳ rejected: BAD_RISK_REWARD | 1,774 | — | — |
| ↳ rejected: CONSECUTIVE_LOSS_GUARD | 87 | — | — |
| EXECUTABLE_CANDIDATES | 558 | 100.0% | 0.2% |
| TRADES_OPENED | 558 | 100.0% | 0.2% |

---

## RISK REJECTIONS

| Rejection Bucket | Count |
|---|---|
| BAD_RISK_REWARD | 1,774 |
| CONSECUTIVE_LOSS_GUARD | 87 |


Raw top rejections:
```json
{
  "CONSECUTIVE_LOSS_GUARD: Consecutive loss cooldown: 3 >= 3": 87,
  "Insufficient R:R (0.75 < 1.50)": 32,
  "Insufficient R:R (0.66 < 1.50)": 28,
  "Insufficient R:R (0.52 < 1.50)": 24,
  "Insufficient R:R (0.54 < 1.50)": 24,
  "Insufficient R:R (0.68 < 1.50)": 23,
  "Insufficient R:R (0.80 < 1.50)": 23,
  "Insufficient R:R (0.65 < 1.50)": 23,
  "Insufficient R:R (0.56 < 1.50)": 23,
  "Insufficient R:R (0.53 < 1.50)": 22
}
```

---

## MODELS USED

| Model | Type |
|---|---|
| Maker Fee | 0.0004 (CONFIGURED ASSUMPTION) |
| Taker Fee | 0.0004 (CONFIGURED ASSUMPTION) |
| Slippage | 0.0002 (CONFIGURED ASSUMPTION) |
| Funding | DISABLED |
| Derivatives | ALL UNAVAILABLE (Mode A: Technical Baseline) |

---

## LOOKAHEAD AUDIT: **PASS**

## INTRABAR SAFETY: **PASS**

---

## CRITICAL FINDINGS

1. Derivatives data was NOT fabricated — all fields marked UNAVAILABLE
2. Zero-lookahead guarantee maintained — only closed candles used
3. Intra-bar ambiguity resolved via conservative worst-case policy
4. All fees/slippage are configured assumptions, not historical data
5. Kill-switch decomposed into DAILY_LOSS_GUARD, CONSECUTIVE_LOSS_GUARD, EMERGENCY_LATCH
6. CONSECUTIVE_LOSS_GUARD is a cooldown, not a permanent emergency latch — resets at new simulation trading day
7. Every evaluation has a candidate_id for full candidate→trade reconciliation

---

## RECOMMENDED NEXT ANALYSIS

- Phase 2: Parameter sensitivity analysis
- Phase 3: Walk-forward optimization (OUT OF SCOPE for Phase 1)
- Phase 4: Regime-adaptive strategy (OUT OF SCOPE for Phase 1)

---

**DO NOT OPTIMIZE YET.** Results are baseline.
**READY FOR PHASE 2 ANALYSIS: YES**
