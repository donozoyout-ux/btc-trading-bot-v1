"""Market metrics and backend-provided closed-candle chart."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping

import plotly.graph_objects as go
from zoneinfo import ZoneInfo

from streamlit_ui.formatting import number, price, text


ISTANBUL = ZoneInfo("Europe/Istanbul")


def _times(rows: Iterable[Mapping[str, Any]]):
    result = []
    for row in rows:
        try:
            result.append(datetime.fromtimestamp(float(row.get("time")) or 0, tz=timezone.utc).astimezone(ISTANBUL))
        except (TypeError, ValueError, OSError):
            result.append(None)
    return result


def build_chart(snapshot: Mapping[str, Any], timeframe: str = "5m") -> go.Figure:
    candles = [row for row in ((snapshot.get("candles") or {}).get(timeframe) or []) if row.get("is_closed", True)]
    figure = go.Figure()
    if not candles:
        figure.update_layout(template="plotly_dark", annotations=[{"text": "DATA_UNAVAILABLE", "showarrow": False}])
        return figure
    times = _times(candles)
    figure.add_trace(go.Candlestick(
        x=times, open=[r.get("open") for r in candles], high=[r.get("high") for r in candles],
        low=[r.get("low") for r in candles], close=[r.get("close") for r in candles], name="BTCUSDT",
        increasing_line_color="#31d0aa", decreasing_line_color="#ff5d78",
    ))
    indicators = (snapshot.get("indicators") or {}).get(timeframe) or {}
    colors = {"ema20": "#4fd1ff", "ema50": "#f4b860", "ema200": "#b58cff",
              "bb_upper": "#65758b", "bb_mid": "#52657d", "bb_lower": "#65758b"}
    for name, color in colors.items():
        series = indicators.get(name) or []
        if series:
            figure.add_trace(go.Scatter(x=_times(series), y=[p.get("value") for p in series], mode="lines", name=name.upper(), line={"color": color, "width": 1.2}))
    for zone in snapshot.get("zones") or []:
        low, high = zone.get("price_min"), zone.get("price_max")
        if low is not None and high is not None:
            figure.add_hrect(y0=low, y1=high, fillcolor="#31d0aa", opacity=0.06, line_width=0)
    plan = (snapshot.get("decision") or {}).get("trade_plan") or {}
    for label, key, color in (("ENTRY", "entry_price", "#ffffff"), ("STOP", "stop_loss", "#ff5d78"),
                              ("TP1", "tp1", "#31d0aa"), ("TP2", "tp2", "#4fd1ff")):
        value = plan.get(key)
        if value is not None:
            figure.add_hline(y=value, line_dash="dot", line_color=color, annotation_text=label)
    figure.update_layout(template="plotly_dark", height=520, margin={"l": 10, "r": 10, "t": 35, "b": 10},
                         xaxis_rangeslider_visible=False, legend={"orientation": "h"}, paper_bgcolor="#07111f",
                         plot_bgcolor="#07111f", xaxis_title="Europe/Istanbul · TSİ")
    return figure


def render(st, snapshot: Mapping[str, Any]) -> None:
    st.subheader("Piyasa")
    market, decision = snapshot.get("market") or {}, snapshot.get("decision") or {}
    cols = st.columns(4)
    cols[0].metric("BTCUSDT", price(market.get("price")))
    cols[1].metric("Mark", price(market.get("mark_price")))
    cols[2].metric("Rejim", text(decision.get("regime")))
    cols[3].metric("Volatilite", text(decision.get("volatility")))
    cols = st.columns(4)
    cols[0].metric("Rejim skoru", number(decision.get("regime_score")))
    cols[1].metric("Güven", text(decision.get("confidence")))
    cols[2].metric("Aşırı uzama ↑", "EVET" if decision.get("overextended_up") else "HAYIR")
    cols[3].metric("Aşırı uzama ↓", "EVET" if decision.get("overextended_down") else "HAYIR")
    timeframe = st.selectbox("Zaman dilimi", ("5m", "15m", "1h", "4h"), index=0)
    st.plotly_chart(build_chart(snapshot, timeframe), width="stretch", config={"displaylogo": False})
