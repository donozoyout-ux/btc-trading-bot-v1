"""Structure and deterministic entry-decision presentation."""

from __future__ import annotations

from typing import Any, Mapping

from streamlit_ui.formatting import number, text


def render(st, snapshot: Mapping[str, Any]) -> None:
    st.subheader("Analiz")
    decision = snapshot.get("decision") or {}
    frames = ((snapshot.get("chart_intelligence") or {}).get("timeframes") or {})
    st.markdown("#### Yapı")
    cols = st.columns(4)
    for column, timeframe in zip(cols, ("4h", "1h", "15m", "5m")):
        frame = frames.get(timeframe) or {}
        column.metric(timeframe.upper(), text(frame.get("structure")))
        column.caption(f"BOS: {text(frame.get('bos'))} · CHoCH: {text(frame.get('choch'))}")
    frame_5m = frames.get("5m") or {}
    cols = st.columns(3)
    cols[0].metric("Destek", number(frame_5m.get("nearest_support"), 2))
    cols[1].metric("Direnç", number(frame_5m.get("nearest_resistance"), 2))
    cols[2].metric("Lokasyon", text(decision.get("location")))

    st.markdown("#### Entry Decision")
    quality = decision.get("entry_quality_assessment") or {}
    plan = decision.get("trade_plan") or {}
    cols = st.columns(4)
    cols[0].metric("Setup", text(decision.get("setup")))
    cols[1].metric("Yön", text(decision.get("setup_direction")))
    cols[2].metric("Entry quality", text(quality.get("decision"), "UNAVAILABLE"))
    cols[3].metric("Risk", text(decision.get("risk_status")))
    cols = st.columns(3)
    cols[0].metric("Final decision", text(snapshot.get("final_decision") or decision.get("final_decision")))
    cols[1].metric("Target mode", text(plan.get("target_mode"), "UNAVAILABLE"))
    cols[2].metric("Risk / Reward", number(plan.get("risk_reward"), 2))
    blockers = ((snapshot.get("strategy") or {}).get("hard_blockers") or
                (snapshot.get("strategy") or {}).get("blocking_reasons") or [])
    st.caption("Blockers: " + (" · ".join(map(str, blockers)) if blockers else "NONE"))
