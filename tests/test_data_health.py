"""Unit tests for Data Health Engine using correct DataSafetyStatus enum."""

import time
import pytest
from core.models import Candle, DataHealthResult, DataSafetyStatus
from config.constants import DataSafetyStatus, SourceHealthStatus
from engines.data_health import DataHealthEngine


def create_sample_candles(count=60, interval_ms=5*60*1000, start_ts=1000000):
    candles = []
    for i in range(count):
        candles.append(
            Candle(
                timestamp=start_ts + (i * interval_ms),
                open=100.0 + i,
                high=105.0 + i,
                low=95.0 + i,
                close=102.0 + i,
                volume=10.0,
                is_closed=True,
            )
        )
    return candles


def test_data_health_safe():
    engine = DataHealthEngine()
    candles = create_sample_candles(count=70)
    status, reason = engine.validate_candles("5m", candles)
    assert status == DataSafetyStatus.SAFE
    assert "healthy" in reason


def test_data_health_open_candle_rejected():
    engine = DataHealthEngine()
    candles = create_sample_candles(count=70)
    candles[-1].is_closed = False
    status, reason = engine.validate_candles("5m", candles)
    assert status == DataSafetyStatus.UNSAFE
    assert "Open candle detected" in reason


def test_data_health_duplicate_timestamp():
    engine = DataHealthEngine()
    candles = create_sample_candles(count=70)
    candles[5].timestamp = candles[4].timestamp
    status, reason = engine.validate_candles("5m", candles)
    assert status == DataSafetyStatus.UNSAFE
    assert "Duplicate" in reason


def test_data_health_invalid_numeric():
    engine = DataHealthEngine()
    candles = create_sample_candles(count=70)
    candles[10].low = -5.0
    status, reason = engine.validate_candles("5m", candles)
    assert status == DataSafetyStatus.UNSAFE
    assert "Invalid low" in reason


def test_evaluate_health_returns_result_object():
    engine = DataHealthEngine()
    candles = []
    for i in range(70):
        candles.append(
            Candle(
                timestamp=1000000 + i * 300000,
                open=100.0 + i, high=105.0 + i, low=95.0 + i,
                close=102.0 + i, volume=10.0, is_closed=True,
            )
        )
    candles_dict = {tf: candles for tf in ["5m", "15m", "1h", "4h"]}
    result = engine.evaluate_health(candles_dict, coinglass_available=False, cmc_available=False)
    assert isinstance(result, DataHealthResult)
    assert result.overall_safety == DataSafetyStatus.SAFE
    assert "COINGLASS" in result.source_health
    assert result.source_health["COINGLASS"] == SourceHealthStatus.OFFLINE


def test_evaluate_health_coinglass_unavailable_marks_offline():
    engine = DataHealthEngine()
    candles = []
    for i in range(70):
        candles.append(
            Candle(
                timestamp=1000000 + i * 300000,
                open=100.0 + i, high=105.0 + i, low=95.0 + i,
                close=102.0 + i, volume=10.0, is_closed=True,
            )
        )
    candles_dict = {tf: candles for tf in ["5m", "15m", "1h", "4h"]}
    result = engine.evaluate_health(candles_dict, coinglass_available=False, cmc_available=True)
    assert result.source_health["COINGLASS"] == SourceHealthStatus.OFFLINE
    assert "CoinGlass unavailable" in result.details["COINGLASS"]


def test_candle_is_closed_mandatory():
    """Verify Candle.is_closed has no default — must be explicitly set."""
    with pytest.raises(Exception):
        Candle(
            timestamp=1000, open=100.0, high=105.0, low=95.0, close=102.0, volume=10.0
        )
