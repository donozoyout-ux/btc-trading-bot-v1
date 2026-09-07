"""Phase-1 AI telemetry display; never an execution control."""

from __future__ import annotations

from typing import Any, Mapping

from streamlit_ui.formatting import istanbul_time, number, text


UNAVAILABLE_AI = {
    "status": "UNAVAILABLE", "decision": "AI_UNAVAILABLE", "success_probability": None,
    "expected_r_24": None, "model_version": None, "schema_version": None,
    "observed_at": None, "execution_authority": False,
}


def payload(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    value = snapshot.get("ai_entry_shadow")
    return value if isinstance(value, Mapping) else UNAVAILABLE_AI


def render(st, snapshot: Mapping[str, Any]) -> None:
    st.subheader("AI Entry Shadow")
    st.warning("SHADOW — EMİR YETKİSİ YOK")
    ai = payload(snapshot)
    cols = st.columns(4)
    cols[0].metric("AI status", text(ai.get("status"), "UNAVAILABLE"))
    cols[1].metric("AI decision", text(ai.get("decision"), "AI_UNAVAILABLE"))
    probability = ai.get("success_probability")
    try:
        probability_display = number(float(probability) * 100, 1, "%")
    except (TypeError, ValueError):
        probability_display = "—"
    cols[2].metric("Başarı olasılığı", probability_display)
    cols[3].metric("Expected R 24", number(ai.get("expected_r_24"), 3))
    cols = st.columns(3)
    cols[0].metric("Model", text(ai.get("model_version"), "UNAVAILABLE"))
    cols[1].metric("Schema", text(ai.get("schema_version"), "UNAVAILABLE"))
    cols[2].metric("Observed", istanbul_time(ai.get("observed_at")))
    st.caption("Execution Authority: NONE")
