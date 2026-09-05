import time

import requests
import pytest

from data.render_market_client import (
    BinanceSpotPublicMarketClient,
    RenderResilientBinanceFuturesMarketClient,
    StrictPublicBinanceFuturesClient,
)


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(str(status_code), response=response)


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


class Primary451ThenHealthy:
    def __init__(self):
        self.calls = 0

    def get_mark_price(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise _http_error(451)
        return 65000.0


class PriceSource:
    def __init__(self, price=64000.0):
        self.calls = 0
        self.price = price

    def get_mark_price(self, *_args, **_kwargs):
        self.calls += 1
        return self.price


class UnavailablePrice:
    def get_mark_price(self, *_args, **_kwargs):
        raise requests.ConnectionError("unavailable")


class Always451:
    def __init__(self):
        self.calls = 0

    def get_long_short_ratio(self, *_args, **_kwargs):
        self.calls += 1
        raise _http_error(451)

    def get_taker_volume_ratio(self, *_args, **_kwargs):
        self.calls += 1
        raise _http_error(451)


class OptionalUnavailable:
    def __init__(self):
        self.calls = 0

    def get_long_short_ratio(self, *_args, **_kwargs):
        self.calls += 1
        raise requests.ConnectionError("unavailable")

    def get_taker_volume_ratio(self, *_args, **_kwargs):
        self.calls += 1
        raise requests.ConnectionError("unavailable")


class FakeResponse:
    def __init__(self, status_code=451, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _http_error(self.status_code)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *_args, **_kwargs):
        return self.response


def test_http_451_uses_real_spot_proxy_then_retries_native_futures():
    clock = FakeClock()
    primary = Primary451ThenHealthy()
    spot = PriceSource(64000.0)
    testnet = PriceSource(63000.0)
    client = RenderResilientBinanceFuturesMarketClient(
        primary=primary,
        spot_proxy=spot,
        fallback=testnet,
        restriction_cooldown_seconds=30,
        clock=clock,
    )

    assert client.get_mark_price("BTCUSDT") == 64000.0
    assert primary.calls == 1
    assert spot.calls == 1
    assert testnet.calls == 0
    assert client.status()["production_public_status"] == "HTTP_451_RESTRICTED"
    assert client.status()["market_data_source"] == "BINANCE_SPOT_PUBLIC_PROXY"
    assert client.status()["market_data_trading_safe"] is True
    assert client.status()["market_basis"] == "SPOT_PROXY"

    # Still inside cooldown: primary must not be retried.
    assert client.get_mark_price("BTCUSDT") == 64000.0
    assert primary.calls == 1
    assert spot.calls == 2

    # After cooldown, production Futures is retried and can recover.
    clock.advance(31)
    assert client.get_mark_price("BTCUSDT") == 65000.0
    assert primary.calls == 2
    assert client.status()["production_public_status"] == "AVAILABLE"
    assert client.status()["market_data_source"] == "PRODUCTION_FUTURES_PUBLIC"
    assert client.status()["market_basis"] == "FUTURES_NATIVE"
    assert client.status()["fallback_active"] is False


def test_spot_proxy_failure_uses_testnet_display_fallback_and_blocks_entries():
    clock = FakeClock()
    client = RenderResilientBinanceFuturesMarketClient(
        primary=Primary451ThenHealthy(),
        spot_proxy=UnavailablePrice(),
        fallback=PriceSource(63000.0),
        restriction_cooldown_seconds=30,
        clock=clock,
    )

    assert client.get_mark_price("BTCUSDT") == 63000.0
    status = client.status()
    assert status["market_data_source"] == "TESTNET_PUBLIC_FALLBACK"
    assert status["market_data_trading_safe"] is False
    assert status["market_basis"] == "TESTNET_FUTURES"


def test_optional_derivatives_never_become_fake_neutral_when_sources_fail():
    clock = FakeClock()
    client = RenderResilientBinanceFuturesMarketClient(
        primary=Always451(),
        spot_proxy=PriceSource(),
        fallback=OptionalUnavailable(),
        restriction_cooldown_seconds=30,
        clock=clock,
    )

    # Establish the real-price proxy first, as the dashboard does via klines/price.
    client.active_environment = "BINANCE_SPOT_PUBLIC_PROXY"
    client.fallback_active = True
    assert client.get_long_short_ratio("BTCUSDT") is None
    assert client.get_taker_volume_ratio("BTCUSDT") is None
    assert client.status()["derivatives_status"] == "UNAVAILABLE"


def test_strict_long_short_ratio_propagates_http_error_instead_of_returning_one():
    client = StrictPublicBinanceFuturesClient(testnet=False)
    client.session = FakeSession(FakeResponse(status_code=451))

    with pytest.raises(requests.HTTPError) as exc_info:
        client.get_long_short_ratio("BTCUSDT")

    assert exc_info.value.response.status_code == 451


def test_strict_taker_ratio_returns_none_for_valid_empty_payload():
    client = StrictPublicBinanceFuturesClient(testnet=True)
    client.session = FakeSession(FakeResponse(status_code=200, payload=[]))

    assert client.get_taker_volume_ratio("BTCUSDT") is None


def test_spot_public_client_returns_closed_candles_only():
    now_ms = int(time.time() * 1000)
    payload = [
        [now_ms - 600_000, "100", "110", "90", "105", "12", now_ms - 300_001, "0", 0, "0", "0", "0"],
        [now_ms - 300_000, "105", "120", "100", "115", "13", now_ms + 60_000, "0", 0, "0", "0", "0"],
    ]
    client = BinanceSpotPublicMarketClient()
    client.session = FakeSession(FakeResponse(status_code=200, payload=payload))

    candles = client.get_klines("BTCUSDT", "5m", 10)
    assert len(candles) == 1
    assert candles[0].close == 105.0
    assert candles[0].is_closed is True
