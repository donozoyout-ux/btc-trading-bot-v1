"""Dashboard composition kept separate for mockable render tests."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Dict

from streamlit_ui import ai_shadow, analysis, health, journal, market, overview, position


def _panel(st, error_code: str, renderer, *args) -> None:
    try:
        renderer(st, *args)
    except Exception:
        # A malformed/missing backend subsection must not blank the whole app.
        st.error(f"{error_code}_UNAVAILABLE")


def render_dashboard(st, client) -> Dict[str, Any]:
    results = client.fetch_dashboard()
    bootstrap_result = results["bootstrap"]
    snapshot_result = results["snapshot"]
    health_result = results["health"]
    bootstrap = bootstrap_result.data or {}
    snapshot = dict(snapshot_result.data or {})
    health_data = health_result.data or {}
    account_result = results.get("account")
    if account_result is not None and account_result.available:
        snapshot["account_detail"] = account_result.data
    backend_state = "ONLINE" if snapshot_result.available else "WAKING" if snapshot_result.status == "WAKING" else "OFFLINE"

    st.markdown("<div class='btc-kicker'>BTCUSDT · TESTNET OBSERVABILITY</div>", unsafe_allow_html=True)
    st.title("BTC Trading Bot Console")
    if not snapshot_result.available:
        st.warning("Backend uyanıyor veya geçici olarak erişilemiyor.")
        if snapshot_result.error:
            st.caption(f"Durum: {snapshot_result.error}")

    labels = ["Genel Bakış", "Piyasa", "Pozisyon", "Analiz", "AI Shadow", "Sistem"]
    tabs = st.tabs(labels)
    contexts = tabs if len(tabs) == len(labels) else [nullcontext() for _ in labels]
    with contexts[0]:
        _panel(st, "OVERVIEW", overview.render, snapshot, bootstrap)
    with contexts[1]:
        _panel(st, "MARKET", market.render, snapshot)
    with contexts[2]:
        _panel(st, "POSITION", position.render, snapshot)
        _panel(st, "JOURNAL", journal.render, snapshot)
    with contexts[3]:
        _panel(st, "ANALYSIS", analysis.render, snapshot)
    with contexts[4]:
        _panel(st, "AI", ai_shadow.render, snapshot)
    with contexts[5]:
        _panel(st, "HEALTH", health.render, snapshot, bootstrap, health_data, backend_state)
    return {"backend_state": backend_state, "snapshot_available": snapshot_result.available}


def inject_style(st) -> None:
    st.markdown(
        """
        <style>
        .stApp { background: radial-gradient(circle at 80% 0%, #10273b 0, #07111f 38%, #050b14 100%); }
        .block-container { max-width: 1480px; padding-top: 1.5rem; }
        .btc-kicker { color: #31d0aa; font-size: .8rem; font-weight: 800; letter-spacing: .14em; }
        [data-testid="stMetric"] { background: rgba(13, 26, 43, .86); border: 1px solid #20354d; border-radius: 12px; padding: 12px 14px; }
        [data-testid="stMetricValue"] { font-size: 1.25rem; }
        .stTabs [data-baseweb="tab-list"] { gap: .35rem; overflow-x: auto; }
        .stTabs [data-baseweb="tab"] { background: #0d1a2b; border-radius: 9px 9px 0 0; min-height: 44px; }
        </style>
        """,
        unsafe_allow_html=True,
    )
