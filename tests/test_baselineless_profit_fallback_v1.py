import pytest

from core.state import BotState
from engines.baselineless_profit_guard import BaselineLessProfitGuard
from execution.safer_testnet_executor import SaferTestnetExecutor
from tests.test_profit_capture_v3 import FastClient, MemoryJournal, Notifier, settings


def guard():
    return BaselineLessProfitGuard()


def test_screenshot_short_profit_tightens_stop_without_fake_r():
    result = guard().evaluate(
        direction="SHORT",
        entry=77141.4,
        mark=76824.01,
        current_stop=77030.93,
        previous_peak_profit_pct=0.0,
        stop_min_gap=0.1,
    )
    assert result.armed is True
    assert result.action == "TIGHTEN_STOP"
    assert result.current_profit_pct == pytest.approx((77141.4 - 76824.01) / 77141.4)
    assert result.desired_lock_pct == pytest.approx(0.0022)
    assert result.new_stop == pytest.approx(77141.4 * (1 - 0.0022))


@pytest.mark.parametrize(
    "direction,entry,mark,stop",
    [
        ("LONG", 100.0, 100.40, 99.0),
        ("SHORT", 100.0, 99.60, 101.0),
    ],
)
def test_point_four_percent_move_locks_point_two_two_percent(direction, entry, mark, stop):
    result = guard().evaluate(
        direction=direction,
        entry=entry,
        mark=mark,
        current_stop=stop,
        stop_min_gap=0.0001,
    )
    assert result.action == "TIGHTEN_STOP"
    assert result.desired_lock_pct == pytest.approx(0.0022)


def test_peak_giveback_exits_without_reconstructing_initial_r():
    result = guard().evaluate(
        direction="SHORT",
        entry=100.0,
        mark=99.72,
        current_stop=99.85,
        previous_peak_profit_pct=0.0050,
        stop_min_gap=0.0001,
    )
    assert result.peak_profit_pct == pytest.approx(0.0050)
    assert result.current_profit_pct == pytest.approx(0.0028)
    assert result.giveback_fraction == pytest.approx(0.44)
    assert result.action == "CLOSE_FULL"
    assert result.state == "BASELINELESS_GIVEBACK_EXIT"


def test_fallback_never_widens_an_already_better_stop():
    result = guard().evaluate(
        direction="LONG",
        entry=100.0,
        mark=100.60,
        current_stop=100.45,
        previous_peak_profit_pct=0.0060,
        stop_min_gap=0.0001,
    )
    assert result.action == "NONE"
    assert result.new_stop is None
    assert result.protected_profit_pct == pytest.approx(0.0045)


def make_short_executor(mark=76824.01):
    client = FastClient(mark=mark)
    client.position.update({
        "position_amt": -0.0398,
        "side": "SHORT",
        "entry_price": 77141.4,
        "mark_price": float(mark),
        "unrealized_pnl": 13.17,
    })
    client.orders = [
        {
            "algoId": 1,
            "orderType": "STOP_MARKET",
            "side": "BUY",
            "triggerPrice": "77030.93",
            "quantity": ".0398",
            "reduceOnly": True,
        },
        {
            "algoId": 2,
            "orderType": "TAKE_PROFIT_MARKET",
            "side": "BUY",
            "triggerPrice": "76587.5",
            "quantity": ".0398",
            "reduceOnly": True,
        },
    ]
    journal = MemoryJournal()
    notifier = Notifier()
    executor = SaferTestnetExecutor(
        client,
        settings=settings(),
        execution_journal=journal,
        event_notifier=notifier,
    )
    # Deliberately partial restart context: entry exists on exchange position,
    # but immutable original stop is not proven.
    executor._entry_context = {
        "exchange_baseline_verified": False,
        "context_status": "PARTIAL",
    }
    executor.protection_reconciliation_required = False
    return executor, client, journal, notifier


def test_executor_fallback_tightens_real_exchange_stop_for_partial_context():
    executor, client, journal, notifier = make_short_executor()
    result = executor.manage_baselineless_profit_guard(client.position, BotState())
    assert result["status"] == "BASELINELESS_PROFIT_STOP_TIGHTENED"
    stop = next(row for row in client.orders if row["orderType"] == "STOP_MARKET")
    assert float(stop["triggerPrice"]) == pytest.approx(76971.7, abs=0.11)
    assert executor._has_verified_exchange_baseline() is False
    assert executor.baselineless_armed is True
    assert any(row["action"] == "BASELINELESS_PROFIT_STOP_TIGHTENED" for row in journal.records)
    assert [event for event, _, _ in notifier.events] == ["PROFIT_PROTECTION"]


def test_executor_fallback_full_exit_after_forty_percent_peak_giveback():
    executor, client, journal, notifier = make_short_executor(mark=76925.4)
    executor.baselineless_peak_profit_pct = 0.0050
    # Current profit is about 0.28%, so 44% of the 0.50% peak was given back.
    result = executor.manage_baselineless_profit_guard(client.position, BotState())
    assert result["status"] == "BASELINELESS_PROFIT_EXIT"
    assert client.full_closes == 1
    assert client.position["position_amt"] == 0
    assert client.orders == []
    assert any(row["action"] == "BASELINELESS_PROFIT_EXIT" for row in journal.records)
    assert [event for event, _, _ in notifier.events] == ["PROFIT_FADE"]


def test_baselineless_peak_state_survives_restart():
    executor, client, journal, _ = make_short_executor()
    executor.baselineless_peak_profit_pct = 0.0047
    executor.baselineless_armed = True
    executor.baselineless_last_action = "TIGHTEN_STOP"
    executor._write_runtime_state()

    restarted = SaferTestnetExecutor(
        client,
        settings=settings(),
        execution_journal=journal,
        event_notifier=Notifier(),
    )
    assert restarted.baselineless_peak_profit_pct == pytest.approx(0.0047)
    assert restarted.baselineless_armed is True
    assert restarted.baselineless_last_action == "TIGHTEN_STOP"
