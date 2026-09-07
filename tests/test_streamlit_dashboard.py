from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import requests

from config.settings import BotSettings
from streamlit_ui.ai_shadow import payload as ai_payload
from streamlit_ui.api_client import APIResult, BackendAPIClient, READ_ONLY_ENDPOINTS
from streamlit_ui.app import render_dashboard
from streamlit_ui.formatting import istanbul_time
from streamlit_ui.market import build_chart


class Response:
    def __init__(self, status=200, payload=None, invalid=False):
        self.status_code = status
        self._payload = payload
        self.invalid = invalid

    def json(self):
        if self.invalid:
            raise ValueError("not json")
        return self._payload


def test_api_client_success_uses_read_only_get():
    session = MagicMock()
    session.get.return_value = Response(payload={"ok": True})
    client = BackendAPIClient("https://example.com", session=session, max_attempts=1)
    result = client.snapshot()
    assert result.available
    assert result.data == {"ok": True}
    session.get.assert_called_once()
    assert session.method_calls[0].args[0].endswith("/api/snapshot")


def test_api_client_timeout_is_bounded_and_waking():
    session = MagicMock()
    session.get.side_effect = requests.Timeout()
    result = BackendAPIClient("https://example.com", session=session, max_attempts=2).snapshot()
    assert result.status == "WAKING"
    assert result.error == "BACKEND_TIMEOUT"
    assert session.get.call_count == 2


def test_api_client_invalid_json_and_backend_unavailable_fail_soft():
    invalid = MagicMock()
    invalid.get.return_value = Response(payload=None, invalid=True)
    assert BackendAPIClient("https://example.com", session=invalid, max_attempts=1).health().error == "INVALID_JSON"
    offline = MagicMock()
    offline.get.side_effect = requests.ConnectionError()
    result = BackendAPIClient("https://example.com", session=offline, max_attempts=1).bootstrap()
    assert result.status == "UNAVAILABLE"
    assert result.data is None


def test_client_blocks_every_non_get_dashboard_route():
    client = BackendAPIClient("https://example.com", session=MagicMock(), max_attempts=1)
    assert READ_ONLY_ENDPOINTS == {"/api/bootstrap", "/api/health", "/api/snapshot", "/api/account"}
    assert client._get("/api/order").error == "READ_ONLY_ENDPOINT_BLOCKED"
    assert not hasattr(client, "post")
    assert not hasattr(client, "place_order")


def test_streamlit_runtime_path_has_no_binance_or_execution_imports():
    root = Path(__file__).resolve().parents[1]
    sources = (root / "streamlit_app.py").read_text(encoding="utf-8")
    for path in (root / "streamlit_ui").glob("*.py"):
        sources += path.read_text(encoding="utf-8")
    assert "data.binance" not in sources
    assert "binance_execution_client" not in sources
    assert "execution.testnet" not in sources
    assert "place_market_order" not in sources


def test_mainnet_remains_blocked():
    settings = BotSettings(
        ENV="production", BINANCE_TESTNET=False, ACCOUNT_READ_ONLY=False,
        SHADOW_MODE=False, ORDER_SUBMISSION_ENABLED=True,
    )
    assert settings.ORDER_SUBMISSION_ENABLED is False
    assert settings.testnet_execution_enabled is False


def test_missing_ai_is_unavailable_not_neutral():
    result = ai_payload({})
    assert result["status"] == "UNAVAILABLE"
    assert result["success_probability"] is None
    assert result["execution_authority"] is False


def test_timestamp_is_displayed_in_istanbul_without_mutating_raw_value():
    raw = 0
    assert istanbul_time(raw) == "01.01.1970 02:00:00 TSİ"
    assert raw == 0


def test_chart_uses_only_closed_backend_candles():
    snapshot = {"candles": {"5m": [
        {"time": 1_700_000_000, "open": 100, "high": 102, "low": 99, "close": 101, "is_closed": True},
        {"time": 1_700_000_300, "open": 101, "high": 999, "low": 1, "close": 500, "is_closed": False},
    ]}}
    figure = build_chart(snapshot)
    assert len(figure.data[0].close) == 1
    assert list(figure.data[0].close) == [101]


def test_page_renders_with_mocked_backend_data():
    snapshot = {
        "meta": {"generated_at": 1_700_000_000_000, "orders_enabled": False},
        "execution": {"environment": "TESTNET", "position": {"side": "FLAT", "position_amt": 0}},
        "market": {"price": 100, "mark_price": 100},
        "decision": {"regime": "RANGE", "volatility": "NORMAL", "setup": "NONE", "setup_direction": "WAIT"},
        "candles": {"5m": []}, "chart_intelligence": {"timeframes": {}},
        "sources": {"binance": {"status": "HEALTHY"}},
    }
    client = MagicMock()
    client.fetch_dashboard.return_value = {
        "bootstrap": APIResult("AVAILABLE", {"binance_testnet": True}),
        "snapshot": APIResult("AVAILABLE", snapshot),
        "health": APIResult("AVAILABLE", {"ok": True}),
        "account": APIResult("AVAILABLE", {"positions": [], "open_orders": []}),
    }
    st = MagicMock()
    st.tabs.return_value = [nullcontext() for _ in range(6)]
    st.columns.side_effect = lambda count: [MagicMock() for _ in range(count)]
    st.selectbox.return_value = "5m"
    result = render_dashboard(st, client)
    assert result == {"backend_state": "ONLINE", "snapshot_available": True}
    st.title.assert_called_once()
    st.plotly_chart.assert_called_once()


def test_page_renders_backend_waking_state_without_crash():
    client = MagicMock()
    client.fetch_dashboard.return_value = {
        "bootstrap": APIResult("AVAILABLE", {"generated_at": 1}),
        "snapshot": APIResult("WAKING", error="BACKEND_TIMEOUT"),
        "health": APIResult("WAKING", error="BACKEND_TIMEOUT"),
        "account": APIResult("WAKING", error="BACKEND_TIMEOUT"),
    }
    st = MagicMock()
    st.tabs.return_value = [nullcontext() for _ in range(6)]
    st.columns.side_effect = lambda count: [MagicMock() for _ in range(count)]
    st.selectbox.return_value = "5m"
    result = render_dashboard(st, client)
    assert result["backend_state"] == "WAKING"
    st.warning.assert_called()
