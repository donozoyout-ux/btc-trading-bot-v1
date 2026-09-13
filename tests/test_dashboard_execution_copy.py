from pathlib import Path


def test_dashboard_describes_testnet_execution_without_read_only_confusion():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")

    assert "Binance Futures TESTNET hesap ve execution verileri" in html
    assert "İmzalı hesap + otomatik TESTNET execution" in html
    assert "DASHBOARD'DAN EMİR EYLEMİ YOK" in html

    # The Render deployment can run automatic TESTNET execution even though
    # the web dashboard intentionally exposes no manual buy/sell endpoints.
    assert "Salt okunur Binance Futures TESTNET verileri" not in html
    assert "İmzalı ve salt okunur" not in html
