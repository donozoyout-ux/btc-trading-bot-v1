# AI Decision Engine V1 — Phase 1 Audit

## Result

| Check | Result | Evidence |
|---|---|---|
| Forensic last trade data | **UNAVAILABLE** | Available execution journal contains no non-smoke TESTNET entry event. The forensic output reports `SOURCE_DATA_UNAVAILABLE`; it does not infer missing history. |
| Dataset builder | **PASS** | Versioned, chronological snapshot JSONL-to-CSV builder with metadata and duplicate-key enforcement. |
| Candidate selection bias mitigation | **PASS** | Directional executed and rejected candidates are both retained; tests cover both classes. |
| Feature schema | **PASS** | `entry-ai-v1`; stable numeric/categorical fields and explicit derivative availability flags. |
| Zero lookahead | **PASS** | Features only consume closed candles at or before candidate T. Mutation of T+1 data leaves the feature vector unchanged while labels can change. |
| Label pipeline | **PASS** | `entry-ai-labels-v1`; directional 12/24/48-bar outcomes and conservative same-bar stop-first handling. |
| Entry classifier | **IMPLEMENTED** | Deterministic tree-based gradient-boosted decision stumps, predicting `ENTRY_SUCCESS_24`. |
| Entry regressor | **IMPLEMENTED** | Deterministic tree-based gradient-boosted decision stumps, predicting `EXPECTED_R_24`. |
| Walk forward | **NO_REAL_DATA** | Expanding chronological folds and OOS/slice reporting are implemented. Available journals lack a complete candidate-plus-future-candle dataset, so no metrics are invented. |
| Shadow AI | **PASS** | Missing/corrupt/schema-incompatible models fail soft as `UNAVAILABLE`/`DEGRADED`. |
| AI execution authority | **NONE** | `execution_authority=false`; inference runs only after the deterministic final decision and is not passed into strategy, risk, execution, or position management. |
| MAINNET | **BLOCKED** | Existing settings validation and TESTNET-only boundary remain unchanged. |
| Live TESTNET orders | **NO** | Validation used compile, self-test, unit tests, and static JavaScript checks only. |
| Profitability improvement claim | **NO** | There is no real chronological out-of-sample evidence in this phase. |

## Model choice

XGBoost was not added because it would introduce a comparatively large deployment dependency. V1 uses a small pure-NumPy, deterministic tree-stump gradient boosting classifier and regressor. Model artifacts record the model/schema versions, training time range, feature lists, row count, Git SHA, deterministic seed, library versions, and training diagnostics. Training diagnostics are explicitly not treated as out-of-sample evidence.

## Frozen label definition

`ENTRY_SUCCESS_24` is 1 only when the candidate's initial TP1 is reached before its initial stop within the next 24 closed 5M bars. When both barriers occur in one 5M bar and lower-timeframe evidence is unavailable, the label uses the conservative stop-first result.

`EXPECTED_R_24` is the directional close-at-24 R outcome, capped at +1R/-1R when the first TP1/initial-stop barrier is observed. This definition is versioned as `entry-ai-labels-v1`.

## Validation

- Compile: **PASS**
- Dashboard self-test: **PASS**
- JavaScript syntax: **PASS**
- Pytest: **290 passed / 0 failed**
- Secret scan: performed before commit; tracked changes contain no environment files, credentials, tokens, signed request strings, or local absolute Windows paths.
