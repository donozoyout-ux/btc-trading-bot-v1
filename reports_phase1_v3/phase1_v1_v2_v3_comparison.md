# Phase 1 v1 vs v2 vs v3 — Comparison Report

**Dataset:** BTC/USDT USDT-M Futures, 2023-09-04 → 2026-09-04, 315,575 × 5M closed candles
**Strategy config:** UNCHANGED across all three versions
**Derivatives mode:** UNAVAILABLE (Mode A — Technical Baseline)

---

## ROOT CAUSE SUMMARY

**Bug:** `BotState.check_kill_switch()` set `kill_switch_activated = True` permanently when
`consecutive_losses >= MAX_CONSECUTIVE_LOSSES`. The only way to clear this flag was
`reset_daily_metrics_if_new_day()`, which only unflagged when `kill_switch_reason` contained
"Daily loss limit". A consecutive-loss reason never contains that text, so the flag was NEVER
cleared. Additionally, the backtest simulation clock used wall-clock time (never advanced), so
the daily reset also NEVER triggered.

**Impact:** after ~2 months (3 consecutive losses), the bot permanently stopped trading for the
remaining ~35 months. Result: 16 trades (all in the first ~2 months) instead of 681 trades
over 3 years.

---

## WHAT CHANGED BETWEEN VERSIONS

| Version | What Changed | Strategy Params Changed? |
|---|---|---|
| v1 | Original baseline — wall-clock daily reset, permanent kill-switch latch | NO |
| v2 | Simulation clock fixed (candle timestamp), pre-cycle kill-switch attribution, conditional funnel, risk reason codes surfaced | NO |
| v3 | Kill-switch decomposed into DAILY_LOSS_GUARD / CONSECUTIVE_LOSS_GUARD / EMERGENCY_LATCH; CONSECUTIVE_LOSS_GUARD resets at new simulation trading day; dead string-classifier `_classify_risk_rejection` removed, buckets now use `RiskAssessment.reason_code` enum directly | NO |

---

## RESULTS COMPARISON

| Metric | v1 | v2 | v3 (= v3b re-run) |
|---|---|---|---|
| **Total Trades** | **16** | **16** | **681** |
| Wins / Losses | 5 / 11 | 5 / 11 | 191 / 490 |
| Win Rate | 31.25% | 31.25% | 28.05% |
| Net PnL | $-6.47 | $-6.47 | $-1,796.35 |
| Gross Profit / Gross Loss | $619.61 / — | $619.61 / — | $21,259.29 / $20,578.21 |
| Total Fees | $61.27 | $61.27 | $2,477.43 |
| Profit Factor | 1.1 | 1.1 | 1.03 |
| Expectancy | $-0.40 | $-0.40 | $-2.64 |
| Average R / Median R | — | — | -0.508R / -1.07R |
| Best Trade R | 2.43R | 2.43R | 2.43R |
| Worst Trade R | -1.13R | -1.13R | -21.06R |
| Max Drawdown | 1.63% | 1.63% | 38.32% |
| Max Consecutive Wins | 2 | 2 | 5 |
| Max Consecutive Losses | 3 | 3 | 18 |
| Total Return | -0.06% | -0.06% | -17.96% |
| Final Equity ($10k start) | $9,993.53 | $9,993.53 | $8,203.65 |
| Total Evaluations | 313,411 | 313,411 | 313,411 |

v3b re-run (after `_classify_risk_rejection` cleanup) reproduced v3 exactly:
Trades=681, Net=$-1,796.35, WR=28.05%, PF=1.03. The refactor is behavior-preserving.

### Guard Block Comparison

| Guard Type | v1 | v2 | v3 |
|---|---|---|---|
| DAILY_LOSS_GUARD | Not tracked | Not tracked | 0 |
| CONSECUTIVE_LOSS_GUARD (kill-switch blocks) | Not tracked | Not tracked | 14,975 |
| EMERGENCY_LATCH | Not tracked | Not tracked | 0 |
| Kill-Switch Latch (permanent, pre-cycle) | Not tracked | 300,268 | 0 (removed by design) |
| Risk rejection BAD_RISK_REWARD | Not tracked | 23 as INSUFFICIENT_RR | 1,770 |
| Risk rejection CONSECUTIVE_LOSS_GUARD | Not tracked | Not tracked | 87 |

Note: v2 bucket `INSUFFICIENT_RR` was renamed to the canonical enum value `BAD_RISK_REWARD`
in v3 (same rejection, `RiskReasonCode.BAD_RISK_REWARD`; human-readable reason unchanged,
e.g. "Insufficient R:R (0.75 < 1.50)").

### Funnel Comparison

| Stage | v1 (independent counters) | v2 (conditional) | v3 (conditional) |
|---|---|---|---|
| Data Health Pass | 313,411 | 313,411 | 313,411 |
| Kill-Switch Pass | Not tracked | 13,143 | 298,436 |
| Structure Eligible | 260,263 | 9,372 | 247,846 |
| Good Location | 276,315 | 7,992 | 217,193 |
| Setup Detected | 108,483 | 1,758 | 83,681 |
| Trigger Detected | 23,396 | 35 | 2,407 |
| Trade Plans Created | 23,396 | 35 | 2,407 |
| Risk Pass | 16 | 12 | 550 |
| Trades Opened (funnel counter) | 16 | 12 | 550 |

---

## KEY INSIGHTS

### Insight 1: The v1/v2 "16 trades" result was a measurement artifact
The 16 trades in v1/v2 cover only the first ~2 months. The kill-switch latch permanently
disabled trading after the 3rd consecutive loss. This is NOT a strategy performance signal —
it is a risk-control infrastructure bug, now fixed.

### Insight 2: v3 shows the TRUE baseline behavior
With corrected kill-switch semantics the strategy trades across all 3 years (681 trades,
~2 per week): 28.05% win rate, PF 1.03, -$2.64 expectancy, -17.96% total return.

### Insight 3: The strategy is NOT profitable in Mode A
Derivatives UNAVAILABLE (Technical Baseline): negative expectancy, PF barely above 1.0,
38.32% max drawdown. Baseline without derivatives data does not stand alone.

### Insight 4: CONSECUTIVE_LOSS_GUARD blocks 4.8% of evaluations
14,975 of 313,411 evaluations (4.78%) hit the consecutive-loss cooldown. Expected behavior —
prevents overtrading during drawdowns — and it now releases at the next simulation day.

### Insight 5: BAD_RISK_REWARD is the primary risk gate
1,770 rejections for R:R below the 1.50 threshold vs 87 for CONSECUTIVE_LOSS_GUARD.
The 1.50 minimum-R:R filter is the binding constraint after the cooldown.

---

## TRUE BASELINE PERFORMANCE ANALYSIS (Mode A — derivatives UNAVAILABLE)

Source: `reports/historical-backtest-phase1-summary.json` (v3b re-run, identical to v3).

### Overall
- 681 trades over 36 months (0.93 trades/day), 191 W / 490 L, WR 28.05%
- Net -$1,796.35 on $10,000 (-17.96%); peak equity $11,715.53, trough $7,226.49
- PF 1.03, expectancy -$2.64/trade, avg -0.508R, median -1.07R, std(R) 1.331
- R distribution: 444 trades in [-2R,-1R), only 70 in [+2R,+3R]; p50 R = -1.07, p90 = +2.27
- Exits: 489 STOP_LOSS (avg -1.047R, -$22,369.70 total), 191 TAKE_PROFIT_2 (avg +0.87R,
  +$20,587.38 total), 1 BACKTEST_END. Losses come from stop-outs, not from missing TPs.

### By setup
- BREAKOUT_RETEST: 568 trades (83%), WR 27.46%, PF 1.02, net -$1,664.59, max DD 32.69%
- TREND_PULLBACK: 111 trades, WR 31.53%, PF 1.13, net -$86.47, max DD 8.56%
- COUNTER_TREND_REACTION: 2 trades, 0 wins, net -$45.29 (negligible sample)

### By direction
- LONG: 589 trades, WR 27.33%, PF 1.01, net -$1,977.38, max DD 36.21%
- SHORT: 92 trades, WR 32.61%, PF 1.23, net +$181.03, max DD 5.04%, expectancy +$1.97
- SHORT is the only profitable slice, but n=92 is too small to conclude edge.

### By regime / volatility
- STRONG_BULL: 587 trades, net -$1,932.09 (PF 1.01) — most trading, negative
- STRONG_BEAR: 94 trades, net +$135.74 (PF 1.21)
- HIGH volatility: 110 trades, PF 1.17, expectancy +$1.80 (only positive vol bucket)
- EXTREME volatility: 69 trades, PF 1.20, expectancy +$2.63
- LOW/NORMAL volatility (502 trades combined): net -$2,175.33 — the bleed is in calm markets
- OVEREXTENDED_UP: 95 trades, PF 1.54, +$1,011.91 — strongest niche, needs Phase 2 validation

### Candidate → trade reconciliation (v3b)
- Total candidates 681, passed risk 681, produced trade 681
- Unreconciled candidates 0, unreconciled trades 0 → **RECONCILIATION PASS**
- Every executed trade carries `candidate_id` (`_process_entry` → `reconcile_trade`);
  `_last_candidate_id` is set per evaluation before entry processing.

### Known observation (not blocking)
- Funnel counters show RISK_PASS = 550 / TRADES_OPENED = 550, while the executed trade
  list and candidate tracker both show 681. Candidate⇄trade reconciliation passes (681/681,
  0 unreconciled), so no trade is orphaned; the 131-count gap is between the funnel's
  report-based pass counter and the entry-execution path. Flagged for Phase 2
  instrumentation, no strategy conclusion depends on it.

---

## STRATEGY CHANGES MADE

**NONE.** No strategy parameters changed between v1, v2, v3, v3b. Only risk-control
infrastructure: simulation-clock daily reset; kill-switch decomposition with
daily-releasing consecutive-loss cooldown; explicit `RiskReasonCode`/`GuardType`
attribution; candidate tracking and reconciliation.

## LOOKAHEAD SAFETY

**PASS** — zero-lookahead maintained (strictly closed candles only); intra-bar ambiguity
resolved via conservative worst-case policy.

## FINAL VERDICT

**Phase 1.5 VERDICT: PASS** — all acceptance criteria met (67/67 tests; daily-loss guard
resets on simulation day; consecutive loss is a cooldown, not a permanent latch; emergency
latch only for operational conditions; conditional funnel monotonic with conversions ≤100%;
risk rejection reason coverage 100% with guard-type attribution; full candidate→trade
reconciliation, 0 unreconciled).

**READY FOR PHASE 2 ANALYSIS: YES. DO NOT OPTIMIZE YET — results are baseline.**
