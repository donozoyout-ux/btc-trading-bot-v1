# Historical Backtest Phase 1 — Summary Report

**Generated:** 2026-09-04T10:45:42Z
**Symbol:** BTC/USDT (Binance USDT-M Futures)
**Backtest Mode:** Technical Baseline — Derivatives UNAVAILABLE
**Period:** 2023-09-04 to 2026-09-04
**Total 5M Candles:** 315,575
**Total Trades:** 16

---

## VERDICT

HISTORICAL BACKTEST PHASE 1 VERDICT: **PASS**
DATASET VERDICT: **PASS**

---

## OVERALL PERFORMANCE

| Metric | Value |
|---|---|
| Total Trades | 16 |
| Wins / Losses | 5 / 11 |
| Win Rate | 31.25% |
| Net PnL | $-6.47 |
| Gross PnL | $619.61 |
| Total Fees | $61.27 |
| Profit Factor | 1.1 |
| Expectancy | $-0.40 |
| Average R | -0.303R |
| Median R | -1.075R |
| Best Trade R | 2.43R |
| Worst Trade R | -1.13R |
| Max Drawdown | 1.63% |
| Max Consecutive Wins | 2 |
| Max Consecutive Losses | 3 |
| Total Return | -0.06% |
| Final Equity | $9,993.53 |

---

## SETUP BREAKDOWN

| Setup | Trades | Win Rate | PF | Expectancy | Avg R | Max DD |
|---|---|---|---|---|---|---|
| TREND_PULLBACK | 1 | 0.0% | 0.0 | $-56.72 | -1.13R | 0.57% |
| BREAKOUT_RETEST | 15 | 33.33% | 1.21 | $3.35 | -0.248R | 1.62% |
| COUNTER_TREND_REACTION | 0 | 0.0% | 0.0 | $0.00 | 0.0R | 0.0% |

---

## DIRECTION BREAKDOWN

| Direction | Trades | Win Rate | PF | Net PnL | Avg R | Max DD |
|---|---|---|---|---|---|---|
| LONG | 15 | 33.33% | 1.21 | $50.25 | -0.248R | 1.62% |
| SHORT | 1 | 0.0% | 0.0 | $-56.72 | -1.13R | 0.57% |

---

## SIGNAL FUNNEL (CONDITIONAL CHAIN)

Each stage counts only evaluations that passed ALL previous stages.
Rejected evaluations appear once, at their first failure stage.

| Stage | Count | From Prev % | From Total % |
|---|---|---|---|
| TOTAL_EVALUATIONS | 313,411 | 0.0% | 100.0% |
| DATA_HEALTH_PASS | 313,411 | 100.0% | 100.0% |
| KILL_SWITCH_PASS | 13,143 | 4.2% | 4.2% |
| ↳ rejected: Kill Switch latched: Kill Switch Activated: Max consecutive losses reached: 3 | 300,268 | — | — |
| STRUCTURE_ELIGIBLE | 9,372 | 71.3% | 3.0% |
| ↳ rejected: 4H and 1H structure both MIXED | 3,771 | — | — |
| GOOD_TRADE_LOCATION | 7,992 | 85.3% | 2.5% |
| ↳ rejected: Location BAD_LOCATION | 1,167 | — | — |
| ↳ rejected: Location NEUTRAL | 213 | — | — |
| SETUP_DETECTED | 1,758 | 22.0% | 0.6% |
| ↳ rejected: No setup detected | 6,234 | — | — |
| ENTRY_TRIGGER_DETECTED | 35 | 2.0% | 0.0% |
| ↳ rejected: Trigger IN_POSITION | 1,594 | — | — |
| ↳ rejected: Trigger WAITING_TRIGGER | 129 | — | — |
| DERIVATIVES_ACCEPTABLE | 35 | 100.0% | 0.0% |
| TRADE_PLAN_CREATED | 35 | 100.0% | 0.0% |
| RISK_PASS | 12 | 34.3% | 0.0% |
| ↳ rejected: INSUFFICIENT_RR | 23 | — | — |
| TRADES_OPENED | 12 | 100.0% | 0.0% |

---

## KILL-SWITCH & RISK REJECTIONS

| Metric | Value |
|---|---|
| Evaluations blocked by latched kill-switch | 300,268 |
| Total risk-stage rejections | 23 |

| Rejection Bucket | Count |
|---|---|
| INSUFFICIENT_RR | 23 |

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

---

## RECOMMENDED NEXT ANALYSIS

- Phase 2: Parameter sensitivity analysis
- Phase 3: Walk-forward optimization (OUT OF SCOPE for Phase 1)
- Phase 4: Regime-adaptive strategy (OUT OF SCOPE for Phase 1)

---

**DO NOT OPTIMIZE YET.** Results are baseline.
**READY FOR PHASE 2 ANALYSIS: YES**
