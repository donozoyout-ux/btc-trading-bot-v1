"""Streamlit Community Cloud entrypoint for the read-only BTC dashboard."""

from __future__ import annotations

import os

import streamlit as st

from streamlit_ui import DEFAULT_BACKEND_URL
from streamlit_ui.api_client import BackendAPIClient
from streamlit_ui.app import inject_style, render_dashboard


def resolve_backend_url() -> str:
    configured = os.environ.get("STREAMLIT_BACKEND_URL")
    if configured:
        return configured
    try:
        return str(st.secrets.get("STREAMLIT_BACKEND_URL", DEFAULT_BACKEND_URL))
    except Exception:
        return DEFAULT_BACKEND_URL


@st.cache_resource
def backend_client(base_url: str) -> BackendAPIClient:
    return BackendAPIClient(base_url)


def main() -> None:
    st.set_page_config(page_title="BTC Trading Bot Console", page_icon="₿", layout="wide")
    inject_style(st)
    try:
        client = backend_client(resolve_backend_url())
    except ValueError:
        st.warning("STREAMLIT_BACKEND_URL geçersiz; varsayılan salt-okunur backend kullanılıyor.")
        client = backend_client(DEFAULT_BACKEND_URL)

    @st.fragment(run_every="20s")
    def live_dashboard() -> None:
        render_dashboard(st, client)

    live_dashboard()


if __name__ == "__main__":
    main()
