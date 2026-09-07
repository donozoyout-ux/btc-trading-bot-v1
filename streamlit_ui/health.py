"""Infrastructure status without unsupported uptime claims."""

from __future__ import annotations

from typing import Any, Mapping

from streamlit_ui.formatting import text


def render(st, snapshot: Mapping[str, Any], bootstrap: Mapping[str, Any], health: Mapping[str, Any], backend_state: str) -> None:
    st.subheader("Sistem")
    st.markdown("#### Infrastructure")
    cols = st.columns(3)
    cols[0].metric("STREAMLIT", "ONLINE")
    cols[1].metric("BACKEND", backend_state)
    keepalive = health.get("keepalive") or bootstrap.get("keepalive") or "UNKNOWN"
    cols[2].metric("Keepalive", text(keepalive, "UNKNOWN"))
    execution = snapshot.get("execution") or {}
    meta = snapshot.get("meta") or {}
    cols = st.columns(4)
    cols[0].metric("TESTNET", text(execution.get("environment"), "UNAVAILABLE"))
    cols[1].metric("MAINNET", "BLOCKED")
    cols[2].metric("Order authority", "NONE (FRONTEND)")
    cols[3].metric("Backend orders", "ENABLED" if meta.get("orders_enabled") else "DISABLED")
    sources = snapshot.get("sources") or {}
    if sources:
        rows = [{"Kaynak": name, "Durum": text((value or {}).get("status"), "UNAVAILABLE")}
                for name, value in sources.items()]
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("DATA_UNAVAILABLE — Kaynak sağlığı henüz alınamadı.")
