# Bad Entry Root-Cause Audit

Scope: local code at `5afbbf5`; no live order or position action was performed.

1. Setup B LONG is allowed by `SetupEngine.detect_setup_b_breakout_retest` when a bull regime exists, any earlier closed candle in the rolling window closed above a zone, any recent candle touched the zone and closed above its minimum, and current price remains above the minimum. The function then uses a fixed 2.5% target.
2. No. The pre-hardening retest test is a permissive touch/close test; it does not require a separately identified rejection, engulfing, micro BOS, or strong close.
3. Yes. `TradeLocationEngine.evaluate_location` marks resistance as bad for LONG only when `regime_result.overextended_up` is also true. There is no reward-space check against the next resistance.
4. Yes. `EntryTriggerEngine.evaluate_5m_patterns` does not consume 5M directional structure.
5. Yes. Neither `SetupEngine` nor `EntryTriggerEngine` vetoes a LONG for a confirmed bearish 5M BOS.
6. Yes. The pipeline has regime-level overextension handling, but no dedicated 5M/15M anti-chase gate immediately before risk/execution.
7. Yes. A strong directional body and volume at 95% of average each add one point; two points create `ENTRY_READY`.
8. No. `curr.volume >= avg_vol * 0.95` is labelled `Volume Expansion`, although it can be below average and below configured RVOL expansion.
9. No. `StrategyOrchestrator.summarize` adds MTF conflicts to `blocking_reasons`, but `eligible` depends only on final decision, risk acceptance, and kill switch. `TestnetExecutor.process_snapshot` does not inspect those reasons.
10. `RiskEngine.evaluate_risk` sizes from `state.account_balance_usdt`.
11. `DashboardRuntime.__init__` initializes that state from `INITIAL_CAPITAL_USDT`; the signed account is read later and was not synchronized before pipeline risk sizing.
12. No. `TestnetExecutor.process_snapshot` normalizes and submits using the decision price without querying TESTNET mark price.
13. No. Entry reconciliation checks executed quantity and position existence, but not average-fill deviation from planned entry.
14. Yes. `RenderResilientBinanceFuturesMarketClient` may expose a public fallback/proxy basis while `BinanceFuturesExecutionClient` submits to Futures TESTNET; no pre-hardening basis tolerance check bridges them.
15. No dedicated anti-chase engine exists. Location and regime overextension are partial upstream filters, not a final entry-quality assessment.

## Root cause

The incident class is enabled by several independent gaps: configured capital rather than signed exchange capital can drive sizing; permissive Setup B and trigger semantics can promote weak confirmation; short-timeframe opposing structure is advisory; nearby reward space is not enforced; and execution does not validate strategy-vs-TESTNET mark/fill basis. None alone proves the reported market state existed at the exact fill, but the code path permits the combination.
