from engines.live_readiness import evaluate_live_readiness, reconstruct_trade_cycles


def _fill(time, side, qty, realized=0, commission=0.1, position_side="BOTH"):
    return {
        "time": time,
        "side": side,
        "qty": str(qty),
        "realizedPnl": str(realized),
        "commission": str(commission),
        "positionSide": position_side,
    }


def test_reconstructs_flat_to_flat_trade_and_includes_fees():
    fills = [
        _fill(1_000, "BUY", 0.01, commission=0.2),
        _fill(2_000, "SELL", 0.01, realized=5.0, commission=0.2),
    ]
    cycles = reconstruct_trade_cycles(fills)
    assert len(cycles) == 1
    assert cycles[0]["direction"] == "LONG"
    assert cycles[0]["net_pnl_usdt"] == 4.6


def test_open_position_does_not_count_as_completed_trade():
    cycles = reconstruct_trade_cycles([_fill(1_000, "BUY", 0.01)])
    assert cycles == []


def test_ready_requires_all_nine_criteria():
    day = 86_400_000
    fills = []
    for i in range(50):
        start = i * day
        fills.extend([
            _fill(start + 1_000, "BUY", 0.01, commission=0.01),
            _fill(start + 2_000, "SELL", 0.01, realized=1.0, commission=0.01),
        ])
    payload = evaluate_live_readiness(
        fills=fills,
        wallet_balance_usdt=5_000,
        account_connected=True,
        market_basis="FUTURES_NATIVE",
        execution_thread="RUNNING",
        execution_error=None,
        critical_events=[],
        now_ms=55 * day,
    )
    assert payload["status"] == "READY"
    assert payload["passed"] == 9
    assert payload["performance"]["total_trades"] == 50
    assert payload["performance"]["net_pnl_usdt"] > 0


def test_hard_market_data_failure_keeps_status_not_ready():
    day = 86_400_000
    fills = []
    for i in range(50):
        fills.extend([
            _fill(i * day + 1, "BUY", 0.01, commission=0.01),
            _fill(i * day + 2, "SELL", 0.01, realized=1.0, commission=0.01),
        ])
    payload = evaluate_live_readiness(
        fills=fills,
        wallet_balance_usdt=5_000,
        account_connected=True,
        market_basis="SPOT_PROXY",
        execution_thread="RUNNING",
        execution_error=None,
        critical_events=[],
        now_ms=55 * day,
    )
    assert payload["status"] == "NOT_READY"
    assert "futures_native" in payload["hard_failures"]


def test_kill_switch_event_is_a_hard_failure():
    payload = evaluate_live_readiness(
        fills=[],
        wallet_balance_usdt=5_000,
        account_connected=True,
        market_basis="FUTURES_NATIVE",
        execution_thread="RUNNING",
        execution_error=None,
        critical_events=[{"action": "RECONCILIATION_FAILURE", "status": "KILL_SWITCH"}],
        now_ms=1_000,
    )
    assert payload["critical_execution_incidents"] == 1
    assert "critical_errors" in payload["hard_failures"]
