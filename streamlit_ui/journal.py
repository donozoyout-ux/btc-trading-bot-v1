"""Compact view of execution journal state already exposed by the snapshot."""

from __future__ import annotations

from typing import Any, Mapping

from streamlit_ui.formatting import istanbul_time, text


def render(st, snapshot: Mapping[str, Any]) -> None:
    st.markdown("#### Son Runtime Durumu")
    execution = snapshot.get("execution") or {}
    cols = st.columns(3)
    cols[0].metric("Son sonuç", text(execution.get("last_execution_result"), "UNAVAILABLE"))
    cols[1].metric("Son hata", text(execution.get("last_error"), "NONE"))
    cols[2].metric("Güncelleme", istanbul_time(execution.get("updated_at")))
    last_order = execution.get("last_binance_order") or {}
    if last_order:
        st.json({key: last_order.get(key) for key in ("status", "side", "type", "quantity", "average_fill_price")})
    else:
        st.info("JOURNAL_UNAVAILABLE — Backend snapshot içinde son emir kaydı yok.")
