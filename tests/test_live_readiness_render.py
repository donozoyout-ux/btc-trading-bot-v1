from pathlib import Path
from types import SimpleNamespace

import render_server


def test_render_index_includes_live_readiness_panel_and_client():
    html = render_server._render_index_html().decode("utf-8")
    assert 'id="liveReadinessPanel"' in html
    assert 'id="liveReadinessBadge"' in html
    assert 'src="/live-readiness.js"' in html


def test_live_readiness_client_asset_exists():
    path = Path(render_server.__file__).resolve().parent / "dashboard" / "live-readiness.js"
    assert path.is_file()


def test_render_runtime_readiness_uses_exchange_and_execution_evidence(monkeypatch, tmp_path):
    runtime = object.__new__(render_server.RenderDashboardRuntime)
    runtime._readiness_lock = __import__("threading").Lock()
    runtime._readiness_cached_at = 0.0
    runtime._readiness_cache = None
    runtime.execution_journal = SimpleNamespace(events_file=tmp_path / "events.jsonl")
    runtime.account_client = SimpleNamespace(
        configured=True,
        get_user_trades=lambda symbol, limit=1000: [
            {"time": 1_000, "side": "BUY", "qty": "0.01", "realizedPnl": "0", "commission": "0.01", "positionSide": "BOTH"},
            {"time": 2_000, "side": "SELL", "qty": "0.01", "realizedPnl": "1", "commission": "0.01", "positionSide": "BOTH"},
        ],
    )
    runtime.account = lambda force=False: {"connected": True, "wallet_balance_usdt": 5_000}
    runtime.binance = SimpleNamespace(status=lambda: {
        "market_basis": "FUTURES_NATIVE",
        "market_data_source": "PRODUCTION_FUTURES_PUBLIC",
    })

    previous = render_server.execution_status()
    try:
        render_server._update_execution_status(
            execution_thread="RUNNING",
            bot_status="RUNNING",
            execution_error=None,
        )
        payload = runtime.live_readiness(force=True)
        assert payload["source"] == "BINANCE_TESTNET_USER_TRADES"
        assert payload["performance"]["total_trades"] == 1
        assert payload["market_basis"] == "FUTURES_NATIVE"
    finally:
        render_server._update_execution_status(**previous)
