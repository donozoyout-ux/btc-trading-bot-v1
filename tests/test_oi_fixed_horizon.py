from dashboard_server import DashboardRuntime


def runtime_stub():
    runtime = DashboardRuntime.__new__(DashboardRuntime)
    runtime._binance_oi_observations = []
    runtime._last_oi_change = {"value": None, "source": "UNAVAILABLE", "observed_at": None}
    return runtime


def test_first_oi_observation_is_unavailable():
    result = runtime_stub()._observe_binance_oi(1_000_000, 100.0, 1_100_000)
    assert result == {"value": None, "source": "UNAVAILABLE", "observed_at": None}


def test_same_closed_candle_refresh_does_not_create_or_change_delta():
    runtime = runtime_stub()
    first = runtime._observe_binance_oi(1_000_000, 100.0, 1_100_000)
    repeated = runtime._observe_binance_oi(1_000_000, 999.0, 1_200_000)
    assert repeated == first
    assert len(runtime._binance_oi_observations) == 1


def test_second_consecutive_closed_5m_observation_creates_real_delta():
    runtime = runtime_stub()
    runtime._observe_binance_oi(1_000_000, 100.0, 1_100_000)
    result = runtime._observe_binance_oi(1_300_000, 105.0, 1_400_000)
    assert result["value"] == 0.05
    assert result["source"] == "BINANCE"
    assert result["observed_at"] == 1_400_000
    repeated = runtime._observe_binance_oi(1_300_000, 90.0, 1_500_000)
    assert repeated == result


def test_gap_or_missing_oi_remains_unavailable():
    runtime = runtime_stub()
    runtime._observe_binance_oi(1_000_000, 100.0, 1_100_000)
    gap = runtime._observe_binance_oi(1_600_000, 105.0, 1_700_000)
    missing = runtime._observe_binance_oi(1_900_000, None, 2_000_000)
    assert gap["value"] is None and gap["source"] == "UNAVAILABLE"
    assert missing["value"] is None and missing["source"] == "UNAVAILABLE"
