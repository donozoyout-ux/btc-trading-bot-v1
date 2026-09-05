from pathlib import Path
from types import SimpleNamespace

import render_server
from config.settings import BotSettings


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
    assert 'id="botRuntimeStatus"' in html
    assert 'id="lastExecutionResult"' in html


def test_render_bridge_asset_exists():
    assert (Path(__file__).resolve().parents[1] / "dashboard" / "render-bridge.js").is_file()


def test_render_bootstrap_reports_explicit_testnet_execution(monkeypatch, tmp_path):
    settings = BotSettings(
        _env_file=None,
        ENV="testnet",
        BINANCE_TESTNET=True,
        BINANCE_API_KEY="dummy-key",
        BINANCE_API_SECRET="dummy-secret",
        ACCOUNT_READ_ONLY=False,
        ORDER_SUBMISSION_ENABLED=True,
        SHADOW_MODE=False,
        JOURNAL_DIR=str(tmp_path),
    )
    monkeypatch.setattr(render_server.base, "RUNTIME", SimpleNamespace(settings=settings), raising=False)
    payload = render_server.bootstrap_payload()
    assert payload["orders_enabled"] is True
    assert payload["account_read_only"] is False
    assert payload["shadow_mode"] is False


def test_render_runs_configured_smoke_before_the_auto_loop():
    source = Path(render_server.__file__).read_text(encoding="utf-8")
    assert "runtime.run_loop()" in source
    assert "RUN BEFORE AUTO LOOP" in source
    assert "IGNORED ON CLOUD" not in source


def test_render_bootstrap_exposes_safe_execution_thread_status():
    previous = render_server.execution_status()
    try:
        render_server._update_execution_status(
            execution_thread="RUNNING",
            bot_status="RUNNING",
            smoke_test="PASS",
            last_execution_result="NO_ELIGIBLE_SIGNAL",
            execution_error=None,
        )
        payload = render_server.bootstrap_payload()
        assert payload["execution_mode"] == "TESTNET"
        assert payload["execution_thread"] == "RUNNING"
        assert payload["bot_status"] == "RUNNING"
        assert payload["smoke_test"] == "PASS"
        assert payload["execution_error"] is None
    finally:
        render_server._update_execution_status(**previous)
