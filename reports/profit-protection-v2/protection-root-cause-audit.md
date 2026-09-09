# Protection Root-Cause Audit

Base reviewed: `origin/main` at `d7be48bee249eaea745cc6333ff9700bc0eb3cde`.

## Observed fault

`TestnetExecutor.manage_existing_position()` used one combined predicate: an active position was considered protected only when both `STOP_MARKET` and `TAKE_PROFIT_MARKET` appeared in the open algo-order set. Either missing type entered the same `UNPROTECTED_TESTNET_POSITION` kill-switch path. The base executor journaled and attempted Telegram delivery on every runtime evaluation. Telegram's in-memory dedupe did not survive a Render process restart.

## Paths that can leave a position with STOP but no target

- TP1 fills and the remaining target later fills, is cancelled externally, expires, or is absent while the position still has a remainder.
- A TP2/TP_FINAL partial fill changes the remaining position/order set and Binance removes the completed algo target.
- `_replace_target_safely()` creates and verifies a replacement, then cancellation or rollback leaves a target-set mismatch. The V1 reconciliation lock already detects ambiguous duplicate/stale targets, but a target-only absence previously fell into the generic unprotected path.
- Render restarts with a live STOP but stale/missing persisted target IDs. `recover_from_exchange()` reconstructs roles from live order shape and price; one remaining target can only be labelled `UNKNOWN_TARGET`, and no target cannot be reconstructed from IDs alone.
- `_restore_protective_roles()` deliberately refuses to invent TP1/TP2 roles when prices are ambiguous. This is correct fail-closed behavior but means persisted identity cannot be treated as exchange truth.
- Partial-position reconciliation resizes the STOP to remaining quantity. A target can independently disappear or its summed quantity can become invalid for the remaining position.

## Paths that can leave a position with target but no STOP

- STOP is filled/triggered, cancelled externally, rejected, expired, or absent while the exchange still reports a non-zero remainder.
- STOP replacement follows create -> verify -> cancel. A failure after cancellation or an ambiguous final set is caught by `PROTECTION_RECONCILIATION_REQUIRED`; the position must still be treated as critically unprotected if no valid exact-quantity STOP exists.
- A partial fill/reduction can leave the old STOP absent or with an invalid quantity. `_reconcile_active_protection()` detects missing, multiple, invalid-trigger, wrong-side, non-reduce-only, and wrong-quantity STOP sets.
- Restart can expose stale persisted STOP IDs. The implementation now trusts only the current signed exchange algo-order response, never the persisted list.

## Corrected classification

- Missing/invalid exact-quantity exchange STOP: `UNPROTECTED_POSITION`, critical emergency latch and state-transition Telegram alert.
- Valid STOP plus missing/invalid target set: `PROTECTION_DEGRADED_TARGET_MISSING`. No false critical STOP alert. New entries and adaptive changes remain blocked while repair is unresolved.
- Target repair source order: current adaptive TP2, prior adaptive TP2, verified entry-context TP2. A candidate must remain beyond current mark and entry in the position direction.
- Repair is create -> exchange verify -> persist. Quantity cannot exceed current exchange position. The existing STOP is revalidated after target creation and is never removed.
- Unsafe or unverifiable repair records `TARGET_PROTECTION_REPAIR_FAILED`, retains the STOP, and leaves reconciliation fail-closed.

## Telegram persistence

`last_alert_type`, `last_alert_reason`, `last_alert_sent_at`, and `active_alert_state` are stored in execution runtime state. Equal active faults do not resend across cycles or restarts. Recovery records and sends exactly one `PROTECTION_RECOVERED`; a later new failure is eligible for a fresh alert.

