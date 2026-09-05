from pathlib import Path

import requests

from config.constants import DataSource
from data.cmc_client import CoinMarketCapClient
from data.coinglass_client import CoinGlassClient


class Response:
    def __init__(self, payload, status_code=200): self.payload, self.status_code = payload, status_code
    def raise_for_status(self): return None
    def json(self): return self.payload


class Session:
    def __init__(self, payloads): self.payloads, self.calls, self.headers = list(payloads), 0, {}
    def get(self, *args, **kwargs):
        self.calls += 1
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        if isinstance(payload, Exception): raise payload
        if isinstance(payload, tuple): return Response(payload[1], payload[0])
        return Response(payload)


def test_missing_keys_are_unavailable_without_fake_values():
    cg = CoinGlassClient()
    cmc = CoinMarketCapClient()
    assert cg.get_aggregate_oi()["aggregate_oi_usd"] is None
    assert cg.get_liquidation_data()["total"] is None
    assert cmc.get_global_metrics()["btc_dominance"] is None
    assert cmc.get_global_metrics()["total_market_cap_usd"] is None


def test_coinglass_parses_real_values_and_observation_time():
    clock = lambda: 100.0
    client = CoinGlassClient("key", time_fn=clock)
    client.session = Session([
        {"code": "0", "data": ["BTC"]},
        {"code": "0", "data": [{"close": "123456.7"}]},
        {"code": "0", "data": [{"exchange": "All", "long_liquidation_usd": "10", "short_liquidation_usd": "20", "liquidation_usd": "30"}]},
    ])
    oi = client.get_aggregate_oi()
    liq = client.get_liquidation_data()
    assert oi == {"source": DataSource.COINGLASS, "status": "CONNECTED", "is_available": True, "observed_at": 100000, "error_category": None, "aggregate_oi_usd": 123456.7}
    assert liq["total"] == 30.0 and liq["observed_at"] == 100000


def test_cmc_parses_real_macro_values():
    client = CoinMarketCapClient("key", time_fn=lambda: 200.0)
    client.session = Session([{"data": {"btc_dominance": "56.2", "quote": {"USD": {"total_market_cap": "3000000", "total_volume_24h": "90000"}}}}])
    result = client.get_global_metrics()
    assert result["source"] == DataSource.COINMARKETCAP
    assert result["btc_dominance"] == 56.2
    assert result["total_market_cap_usd"] == 3000000.0
    assert result["observed_at"] == 200000


def test_cache_prevents_excessive_api_calls():
    now = [100.0]
    client = CoinMarketCapClient("key", cache_seconds=300, time_fn=lambda: now[0])
    client.session = Session([{"data": {"btc_dominance": 50, "quote": {"USD": {"total_market_cap": 1, "total_volume_24h": 2}}}}])
    client.get_global_metrics(); client.get_global_metrics()
    assert client.session.calls == 1


def test_api_errors_fail_soft_and_never_create_neutral_numbers():
    client = CoinGlassClient("key")
    client.session = Session([requests.ConnectionError("offline")])
    result = client.get_aggregate_oi()
    assert result["status"] == "NETWORK_ERROR"
    assert result["aggregate_oi_usd"] is None
    assert result["source"] == DataSource.UNAVAILABLE
    assert result["error_category"] == "NETWORK_ERROR"


def test_auth_401_is_classified_and_backed_off_for_five_minutes():
    now = [100.0]
    client = CoinGlassClient("bad-key", time_fn=lambda: now[0])
    client.session = Session([(401, {"code": "401"})])
    assert client.authenticate()["status"] == "AUTH_ERROR"
    now[0] += 299
    assert client.get_aggregate_oi()["status"] == "AUTH_ERROR"
    assert client.session.calls == 1


def test_coinglass_plan_and_rate_limit_categories():
    for code, expected in ((403, "PLAN_FORBIDDEN"), (429, "RATE_LIMITED")):
        client = CoinGlassClient("key")
        client.session = Session([(code, {})])
        assert client.authenticate()["status"] == expected


def test_coinglass_classifies_auth_code_inside_http_200_payload():
    client = CoinGlassClient("bad-key")
    client.session = Session([{"code": "401", "msg": "unauthorized"}])
    assert client.authenticate()["status"] == "AUTH_ERROR"


def test_runtime_contract_preserves_provenance_and_macro_is_advisory_only():
    dashboard = Path("dashboard_server.py").read_text(encoding="utf-8")
    runner = Path("runner.py").read_text(encoding="utf-8")
    executor = Path("execution/testnet_executor.py").read_text(encoding="utf-8")
    assert '"source": "COINGLASS" if cg_oi_value is not None' in dashboard
    assert '"macro_context"' in dashboard
    assert "DerivativesField(**raw)" in runner
    assert "macro_context" not in executor


def test_dashboard_and_logs_never_embed_external_api_keys():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    server = Path("render_server.py").read_text(encoding="utf-8")
    assert "example-coinglass-secret" not in html
    assert "example-cmc-secret" not in html
    assert 'logger.info("COINGLASS: {}"' in server
    assert 'logger.info("COINMARKETCAP: {}"' in server
