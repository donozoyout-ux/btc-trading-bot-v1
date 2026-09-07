"""Current exchange position and adaptive-management telemetry."""

from __future__ import annotations

from typing import Any, Mapping

from streamlit_ui.formatting import number, price, ratio, text


def _position(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    execution = snapshot.get("execution") or {}
    current = execution.get("position") or {}
    if current:
        return current
    positions = ((snapshot.get("account_detail") or {}).get("positions") or [])
    return positions[0] if positions else {}


def _quantity(position: Mapping[str, Any]) -> str:
    try:
        return number(abs(float(position.get("position_amt") or position.get("size") or 0)), 6, " BTC")
    except (TypeError, ValueError):
        return "—"


def render(st, snapshot: Mapping[str, Any]) -> None:
    st.subheader("Pozisyon")
    execution = snapshot.get("execution") or {}
    position, context = _position(snapshot), execution.get("entry_context") or {}
    intelligence = execution.get("position_intelligence") or {}
    side = text(position.get("side"), "FLAT")
    cols = st.columns(4)
    cols[0].metric("Durum", side)
    cols[1].metric("Giriş", price(position.get("entry_price") or context.get("actual_entry_price")))
    cols[2].metric("Mark", price(position.get("mark_price")))
    cols[3].metric("Miktar", _quantity(position) if position else "—")
    cols = st.columns(4)
    cols[0].metric("Kaldıraç", number(position.get("leverage"), 0, "×"))
    cols[1].metric("Gerçekleşmemiş PnL", number(position.get("unrealized_pnl"), 2, " USDT"))
    cols[2].metric("Likidasyon", price(position.get("liquidation_price")))
    cols[3].metric("Yönetim profili", text(context.get("management_profile")))

    st.markdown("#### Adaptive Management")
    cols = st.columns(4)
    cols[0].metric("State", text(intelligence.get("state"), "NO_CHANGE"))
    cols[1].metric("Current R", ratio(intelligence.get("current_r") or intelligence.get("observed_r")))
    cols[2].metric("MFE", ratio(intelligence.get("mfe_r") or execution.get("management_mfe_r")))
    cols[3].metric("MAE", ratio(intelligence.get("mae_r") or execution.get("management_mae_r")))
    cols = st.columns(4)
    cols[0].metric("Initial stop", price(intelligence.get("initial_stop") or context.get("actual_initial_stop")))
    cols[1].metric("Current stop", price(intelligence.get("new_stop") or intelligence.get("old_stop")))
    cols[2].metric("TP1", price(intelligence.get("new_tp1") or intelligence.get("old_tp1")))
    cols[3].metric("TP2", price(intelligence.get("new_tp2") or intelligence.get("old_tp2")))
    validity = st.columns(4)
    validity[0].metric("Thesis", text(intelligence.get("thesis_valid"), "UNAVAILABLE"))
    validity[1].metric("Structure", text(intelligence.get("structure_valid"), "UNAVAILABLE"))
    validity[2].metric("Momentum", text(intelligence.get("momentum_support"), "UNAVAILABLE"))
    validity[3].metric("Regime", text(intelligence.get("regime_support"), "UNAVAILABLE"))
    reasons = intelligence.get("reason_codes") or []
    st.caption("Reason codes: " + (" · ".join(map(str, reasons)) if reasons else "UNAVAILABLE"))
