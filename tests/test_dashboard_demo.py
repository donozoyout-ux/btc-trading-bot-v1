from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_assets_exist():
    dashboard = ROOT / "dashboard"
    for name in ("index.html", "styles.css", "app.js"):
        assert (dashboard / name).is_file(), f"missing dashboard asset: {name}"


def test_dashboard_server_is_read_only():
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    assert 'orders_enabled": False' in source
    assert "place_order(" not in source
    assert 'api_key=None, api_secret=None' in source
    assert 'path == "/api/snapshot"' in source
    assert 'path == "/api/health"' in source


def test_dashboard_does_not_ship_secrets_to_browser():
    browser_source = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8").lower()
    forbidden = ("binance_api_key", "binance_api_secret", "coinglass_api_key", "coinmarketcap_api_key", "authorization")
    assert not any(token in browser_source for token in forbidden)
