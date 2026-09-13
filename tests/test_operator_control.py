from types import SimpleNamespace

from execution.operator_control import OperatorControlState
from execution.testnet_runtime import TestnetExecutionRuntime


class MemoryRepo:
    durability = "PERSISTENT"

    def __init__(self):
        self.data = {}

    def load(self, key):
        return dict(self.data.get(key) or {})

    def save(self, key, value):
        self.data[key] = dict(value)


def test_operator_control_lock_unlock_persists():
    repo = MemoryRepo()
    state = OperatorControlState(state_repository=repo)
    assert state.read()["manual_entry_lock"] is False

    locked = state.lock_entries(locked_by="TELEGRAM", reason="OPERATOR_MANUAL_CLOSE")
    assert locked["manual_entry_lock"] is True
    assert state.read()["reason"] == "OPERATOR_MANUAL_CLOSE"
    assert state.durability == "PERSISTENT"

    unlocked = state.unlock_entries(unlocked_by="TELEGRAM")
    assert unlocked["manual_entry_lock"] is False
    assert state.read()["manual_entry_lock"] is False


class FakeOperator:
    def __init__(self, locked):
        self.locked = locked

    def read(self):
        return {
            "manual_entry_lock": self.locked,
            "reason": "OPERATOR_MANUAL_MODE" if self.locked else None,
            "locked_by": "TELEGRAM" if self.locked else None,
        }


class FlatExecutor:
    def __init__(self):
        self.process_calls = 0

    def manage_existing_position(self, state):
        return {"status": "FLAT", "position": {"position_amt": 0.0}}

    def process_snapshot(self, snapshot, state):
        self.process_calls += 1
        return {"status": "WOULD_ENTER"}


class ActiveExecutor(FlatExecutor):
    def manage_existing_position(self, state):
        return {
            "status": "POSITION_MANAGEMENT",
            "position": {"position_amt": 0.01, "side": "LONG"},
        }

    def manage_adaptive_position(self, snapshot, state, position):
        return {"status": "MANAGED_ACTIVE_POSITION"}


class Dashboard:
    def __init__(self, with_pipeline=True):
        if with_pipeline:
            self.pipeline = object()
        self.snapshot_calls = 0

    def snapshot(self, force=False):
        self.snapshot_calls += 1
        return {"final_decision": "LONG_ENTRY"}


def runtime_with(executor, locked, dashboard):
    runtime = TestnetExecutionRuntime.__new__(TestnetExecutionRuntime)
    runtime.executor = executor
    runtime.operator_control = FakeOperator(locked)
    runtime.dashboard = dashboard
    runtime.state = SimpleNamespace()
    return runtime


def test_manual_entry_lock_blocks_new_auto_entries_when_flat():
    executor = FlatExecutor()
    dashboard = Dashboard()
    runtime = runtime_with(executor, True, dashboard)

    result = runtime.run_cycle()

    assert result["status"] == "MANUAL_ENTRY_LOCKED"
    assert result["locked_by"] == "TELEGRAM"
    assert executor.process_calls == 0
    assert dashboard.snapshot_calls == 0


def test_manual_entry_lock_does_not_disable_existing_position_management():
    executor = ActiveExecutor()
    dashboard = Dashboard()
    runtime = runtime_with(executor, True, dashboard)

    result = runtime.run_cycle()

    assert result["status"] == "MANAGED_ACTIVE_POSITION"
    assert dashboard.snapshot_calls == 1


def test_auto_entries_resume_after_operator_unlock():
    executor = FlatExecutor()
    dashboard = Dashboard()
    runtime = runtime_with(executor, False, dashboard)

    result = runtime.run_cycle()

    assert result["status"] == "WOULD_ENTER"
    assert executor.process_calls == 1
    assert dashboard.snapshot_calls == 1
