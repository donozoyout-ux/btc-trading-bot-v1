import requests
import pytest

from data.render_market_client import (
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


class FallbackPrice:
    def __init__(self):
        self.calls = 0

    def get_mark_price(self, *_args, **_kwargs):
        self.calls += 1
        return 64000.0


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


def test_http_451_latches_testnet_public_fallback_and_stops_primary_hammering():
    clock = FakeClock()
    primary = Primary451ThenHealthy()
    fallback = FallbackPrice()
    client = RenderResilientBinanceFuturesMarketClient(
        primary=primary,
        fallback=fallback,
        restriction_cooldown_seconds=30,
        clock=clock,
    )

    assert client.get_mark_price("BTCUSDT") == 64000.0
    assert primary.calls == 1
    assert fallback.calls == 1
    assert client.status()["production_public_status"] == "HTTP_451_RESTRICTED"
    assert client.status()["market_data_source"] == "TESTNET_PUBLIC_FALLBACK"

    # Still inside cooldown: primary must not be retried.
    assert client.get_mark_price("BTCUSDT") == 64000.0
    assert primary.calls == 1
    assert fallback.calls == 2

    # After cooldown, production is retried and can recover.
    clock.advance(31)
    assert client.get_mark_price("BTCUSDT") == 65000.0
    assert primary.calls == 2
    assert client.status()["production_public_status"] == "AVAILABLE"
    assert client.status()["market_data_source"] == "PRODUCTION_PUBLIC"
    assert client.status()["fallback_active"] is False


def test_optional_derivatives_never_become_fake_neutral_when_both_sources_fail():
    clock = FakeClock()
    client = RenderResilientBinanceFuturesMarketClient(
        primary=Always451(),
        fallback=OptionalUnavailable(),
        restriction_cooldown_seconds=30,
        clock=clock,
    )

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
