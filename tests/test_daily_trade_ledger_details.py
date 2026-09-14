from engines.daily_trade_ledger import DailyTradeLedger


def fill(time, side, qty, price, realized=0.0, commission=0.0, order_id=None):
    return {
        "symbol": "BTCUSDT",
        "time": time,
        "side": side,
        "qty": str(qty),
        "price": str(price),
        "realizedPnl": str(realized),
        "commission": str(commission),
        "orderId": order_id,
    }


def test_daily_ledger_reports_prices_fees_net_and_stop_reason():
    rows = [
        fill(1_000, "BUY", 0.01, 100_000, commission=0.4, order_id=10),
        fill(2_000, "SELL", 0.01, 99_000, realized=-10.0, commission=0.4, order_id=11),
    ]
    report = DailyTradeLedger().build(
        rows,
        day_start_ms=0,
        day_end_ms=10_000,
        ending_position=0.0,
        order_history=[{"orderId": 11, "type": "STOP_MARKET", "reduceOnly": True}],
    )
    assert report["closed_trades_today"] == 1
    assert report["losing_trades_today"] == 1
    trade = report["trades"][0]
    assert trade["direction"] == "LONG"
    assert trade["entry_price"] == 100_000
    assert trade["exit_price"] == 99_000
    assert trade["realized_pnl_usdt"] == -10.0
    assert trade["commission_usdt"] == -0.8
    assert trade["net_pnl_usdt"] == -10.8
    assert trade["exit_reason"] == "STOP_LOSS"


def test_daily_ledger_reports_take_profit_reason():
    rows = [
        fill(1_000, "SELL", 0.02, 100_000, commission=0.5, order_id=20),
        fill(2_000, "BUY", 0.02, 98_000, realized=40.0, commission=0.5, order_id=21),
    ]
    report = DailyTradeLedger().build(
        rows,
        day_start_ms=0,
        day_end_ms=10_000,
        ending_position=0.0,
        algo_history=[{"actualOrderId": 21, "orderType": "TAKE_PROFIT_MARKET"}],
    )
    trade = report["trades"][0]
    assert trade["direction"] == "SHORT"
    assert trade["net_pnl_usdt"] == 39.0
    assert trade["exit_reason"] == "TAKE_PROFIT"


def test_truncated_history_uses_ending_position_anchor_and_skips_censored_trade():
    rows = [
        # Close of an older long whose opening fill is outside this history window.
        fill(1_000, "SELL", 0.01, 100_000, realized=2.0, commission=0.1, order_id=1),
        # Fully observable trade.
        fill(2_000, "BUY", 0.01, 100_000, commission=0.1, order_id=2),
        fill(3_000, "SELL", 0.01, 101_000, realized=10.0, commission=0.1, order_id=3),
    ]
    report = DailyTradeLedger().build(
        rows,
        day_start_ms=0,
        day_end_ms=10_000,
        ending_position=0.0,
    )
    assert report["closed_trades_today"] == 1
    assert report["trades"][0]["entry_opened_at"] == 2_000
    assert report["trades"][0]["net_pnl_usdt"] == 9.8
