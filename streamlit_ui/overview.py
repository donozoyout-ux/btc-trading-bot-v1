"""System overview cards sourced only from backend payloads."""

from __future__ import annotations

from typing import Any, Mapping

from streamlit_ui.formatting import istanbul_time, text


def render(st, snapshot: Mapping[str, Any], bootstrap: Mapping[str, Any]) -> None:
    st.subheader("Genel Bakış")
    meta, execution = snapshot.get("meta") or {}, snapshot.get("execution") or {}
    source = (snapshot.get("sources") or {}).get("binance") or {}
    columns = st.columns(4)
    columns[0].metric("Backend", "ONLINE" if snapshot else text(bootstrap.get("backend_status"), "WAKING"))
    columns[1].metric("Ortam", text(execution.get("environment") or meta.get("mode"), "UNAVAILABLE"))
    columns[2].metric("Emirler", "AÇIK" if meta.get("orders_enabled") else "KAPALI")
    columns[3].metric("MAINNET", "BLOCKED")
    columns = st.columns(4)
    columns[0].metric("Execution thread", text(execution.get("execution_thread"), "UNAVAILABLE"))
    columns[1].metric("TESTNET", "AKTİF" if bootstrap.get("binance_testnet", True) else "UNAVAILABLE")
    columns[2].metric("Piyasa kaynağı", text(source.get("environment") or bootstrap.get("market_data_source"), "UNAVAILABLE"))
    columns[3].metric("Son güncelleme", istanbul_time(meta.get("generated_at") or bootstrap.get("generated_at")))
    st.caption("Salt okunur gözlem arayüzü · Tüm emir ve pozisyon yönetimi Render backend içinde kalır.")
