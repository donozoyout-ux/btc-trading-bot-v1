from pathlib import Path

import render_server


def test_render_bootstrap_is_network_free_and_safe(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("BINANCE_TESTNET", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "dummy-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "dummy-secret")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    payload = render_server.bootstrap_payload()

    assert payload["ok"] is True
    assert payload["runtime"] == "RENDER"
    assert payload["ui"] == "READY"
    assert payload["orders_enabled"] is False
    assert payload["shadow_mode"] is True
    assert payload["account_read_only"] is True
    assert payload["binance_testnet"] is True
    assert payload["binance_credentials_configured"] is True
    assert payload["telegram_configured"] is True
    serialized = str(payload)
    assert "dummy-key" not in serialized
    assert "dummy-secret" not in serialized
    assert "dummy-token" not in serialized


def test_render_index_injects_visible_runtime_panel_and_bridge():
    html = render_server._render_index_html().decode("utf-8")
    assert 'id="renderRuntimePanel"' in html
    assert 'id="renderBootBadge"' in html
    assert 'src="/render-bridge.js"' in html
    assert "BTC Intelligence Console" in html


def test_render_bridge_asset_exists():
    assert (Path(__file__).resolve().parents[1] / "dashboard" / "render-bridge.js").is_file()
