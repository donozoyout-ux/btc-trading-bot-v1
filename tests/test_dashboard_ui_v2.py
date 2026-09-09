"""Static contract checks for the compact Turkish trading console."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_compact_turkish_summary_and_priority_order():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    required = (
        "TOPLAM BAKİYE",
        "BUGÜNKÜ NET KÂR",
        "AÇIK KÂR/ZARAR",
        "AKTİF İŞLEM",
        "BOT DURUMU",
        "BUGÜNKÜ İŞLEM",
    )
    assert all(label in html for label in required)
    assert html.index('class="summary-grid"') < html.index('id="activeTradePanel"')
    assert html.index('id="activeTradePanel"') < html.index('class="workbench glass chart-primary"')
    assert html.index('class="workbench glass chart-primary"') < html.index("PİYASA ANALİZİ")


def test_advanced_account_orders_and_sources_default_to_collapsed():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert html.count("<details class=\"glass console-detail\">") == 4
    assert "<details open" not in html
    for label in ("Gelişmiş Analiz", "Hesap Ayrıntıları", "Pozisyonlar ve Emirler", "Sistem ve Kaynaklar"):
        assert label in html


def test_mobile_layout_and_required_chart_heights_are_explicit():
    css = (ROOT / "dashboard" / "ui-v2.css").read_text(encoding="utf-8")
    assert "height:350px" in css
    assert "height:100px" in css
    assert "@media(max-width:720px)" in css
    assert ".summary-grid{grid-template-columns:repeat(2,1fr)" in css
    assert ".active-trade-grid{grid-template-columns:repeat(2,1fr)" in css


def test_ui_uses_authoritative_daily_performance_and_unavailable_semantics():
    app = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    server = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    assert "a.daily_performance" in app
    assert "VERİ YOK" in app
    assert 'path == "/api/daily-performance"' in server
    assert '"source": "BINANCE_TESTNET_INCOME_HISTORY"' in server
    assert "state.daily_realized_pnl_usdt" not in app


def test_active_trade_can_use_execution_state_when_private_account_is_protected():
    app = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "d.execution?.position" in app
    assert "exchange.side!=='FLAT'" in app
    assert "$('activeTradePanel').hidden=!hasPosition" in app


def test_visible_shell_is_turkish_and_has_no_trading_controls():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    for forbidden in ("<button>Buy", "<button>Sell", "Place order", "Cancel order"):
        assert forbidden not in html
    assert "MAINNET: <b>KAPALI</b>" in html
    assert "Salt okunur" in html


def test_required_active_trade_and_profit_protection_ids_are_unique():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    ids = re.findall(r'id="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    for required in (
        "activeTradeSide", "positionIntelEntry", "positionIntelMark",
        "activeTradeSize", "activeTradeLeverage", "activeTradePnl",
        "positionCurrentStop", "positionIntelTp1", "positionIntelTp2",
        "activeTradeLiquidation", "positionCurrentR", "positionMfeR",
        "positionGivebackR", "positionProtectedR", "positionLastReason",
    ):
        assert required in ids
