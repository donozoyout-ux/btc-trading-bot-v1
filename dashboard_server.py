"""Read-only BTC demo dashboard server.

Serves the animated dashboard and exposes a market-data-only JSON API backed by
real Binance Futures public data plus the existing strategy pipeline. No order
endpoint is exposed and no signed Binance request is made by this server.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
import webbrowser
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import numpy as np
from loguru import logger

from config.settings import get_settings
from core.models import Candle
from core.state import BotState
from data.binance_client import BinanceFuturesClient
from data.coinglass_client import CoinGlassClient
from data.cmc_client import CoinMarketCapClient
from engines.regime_engine import MarketRegimeEngine
from engines.volatility_engine import VolatilityEngine
from runner import MasterPipeline


ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT / "dashboard"
CACHE_TTL_SECONDS = 15


def _jsonable(value: Any) -> Any:
    """Convert domain values to JSON-safe primitives without leaking secrets."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        return _jsonable(value.value)
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except TypeError:
            return _jsonable(value.model_dump())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _safe_call(fn, fallback=None):
    try:
        return fn(), None
    except Exception as exc:
        logger.warning(f"Dashboard source call failed: {exc}")
        return fallback, str(exc)


def _sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) < period:
        return out
    csum = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1 :] = (csum[period:] - csum[:-period]) / period
    return out


def _bollinger(values: np.ndarray, period: int = 20, stddev: float = 2.0):
    mid = _sma(values, period)
    upper = np.full(len(values), np.nan, dtype=float)
    lower = np.full(len(values), np.nan, dtype=float)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        sigma = float(np.std(window, ddof=0))
        upper[i] = mid[i] + stddev * sigma
        lower[i] = mid[i] - stddev * sigma
    return mid, upper, lower


def _series(values: np.ndarray, timestamps: List[int], warmup_zero: bool = True) -> List[Dict[str, float]]:
    result: List[Dict[str, float]] = []
    for ts, raw in zip(timestamps, values):
        value = float(raw)
        if not np.isfinite(value):
            continue
        if warmup_zero and value == 0.0:
            continue
        result.append({"time": int(ts // 1000), "value": value})
    return result


def _indicator_payload(candles: List[Candle]) -> Dict[str, Any]:
    closes = np.array([c.close for c in candles], dtype=float)
    timestamps = [c.timestamp for c in candles]
    if len(closes) == 0:
        return {}

    ema20 = MarketRegimeEngine.calculate_ema(closes, 20)
    ema50 = MarketRegimeEngine.calculate_ema(closes, 50)
    ema200 = MarketRegimeEngine.calculate_ema(closes, 200)
    rsi = MarketRegimeEngine.calculate_rsi(closes, 14)
    adx, plus_di, minus_di = MarketRegimeEngine.calculate_adx_dmi(candles, 14)
    atr = VolatilityEngine().compute_atr_series(candles)
    bb_mid, bb_upper, bb_lower = _bollinger(closes, 20, 2.0)

    def latest(series: np.ndarray) -> Optional[float]:
        valid = series[np.isfinite(series)]
        if len(valid) == 0:
            return None
        value = float(valid[-1])
        return None if value == 0.0 else round(value, 6)

    return {
        "ema20": _series(ema20, timestamps),
        "ema50": _series(ema50, timestamps),
        "ema200": _series(ema200, timestamps),
        "rsi14": _series(rsi, timestamps),
        "adx14": _series(adx, timestamps),
        "plus_di": _series(plus_di, timestamps),
        "minus_di": _series(minus_di, timestamps),
        "atr14": _series(atr, timestamps),
        "bb_mid": _series(bb_mid, timestamps, warmup_zero=False),
        "bb_upper": _series(bb_upper, timestamps, warmup_zero=False),
        "bb_lower": _series(bb_lower, timestamps, warmup_zero=False),
        "latest": {
            "ema20": latest(ema20),
            "ema50": latest(ema50),
            "ema200": latest(ema200),
            "rsi14": latest(rsi),
            "adx14": latest(adx),
            "plus_di": latest(plus_di),
            "minus_di": latest(minus_di),
            "atr14": latest(atr),
            "bb_mid": latest(bb_mid),
            "bb_upper": latest(bb_upper),
            "bb_lower": latest(bb_lower),
        },
    }


class DashboardRuntime:
    """Long-lived read-only runtime with 15-second snapshot caching."""

    def __init__(self):
        self.settings = get_settings()
        # Real production PUBLIC market data, but no credentials are passed. This
        # dashboard runtime therefore cannot submit signed Binance orders.
        self.binance = BinanceFuturesClient(api_key=None, api_secret=None, testnet=False)
        self.coinglass = CoinGlassClient(api_key=self.settings.COINGLASS_API_KEY)
        self.cmc = CoinMarketCapClient(api_key=self.settings.COINMARKETCAP_API_KEY)
        self.pipeline = MasterPipeline(self.settings)
        self.state = BotState(
            account_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
            start_of_day_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
        )
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._snapshot: Optional[Dict[str, Any]] = None

    def _fetch_candles(self) -> Dict[str, List[Candle]]:
        limits = {"4h": 320, "1h": 420, "15m": 500, "5m": 600}
        return {tf: self.binance.get_klines("BTCUSDT", tf, limits[tf]) for tf in limits}

    def _zones(self, candles: Dict[str, List[Candle]], current_price: float) -> List[Dict[str, Any]]:
        try:
            s4 = self.pipeline.structure_engine.analyze_structure("4h", candles["4h"])
            s1 = self.pipeline.structure_engine.analyze_structure("1h", candles["1h"])
            s15 = self.pipeline.structure_engine.analyze_structure("15m", candles["15m"])
            zones = self.pipeline.sr_engine.evaluate_sr_zones(
                current_price=current_price,
                swings_4h=(s4.swing_highs, s4.swing_lows),
                swings_1h=(s1.swing_highs, s1.swing_lows),
                swings_15m=(s15.swing_highs, s15.swing_lows),
                candles_1h=candles["1h"],
            )
            return [_jsonable(z) for z in zones]
        except Exception as exc:
            logger.warning(f"Dashboard S/R zone calculation failed: {exc}")
            return []

    def snapshot(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            if not force and self._snapshot is not None and (now - self._cached_at) < CACHE_TTL_SECONDS:
                return self._snapshot

            candles = self._fetch_candles()
            current_price = candles["5m"][-1].close

            mark_price, mark_err = _safe_call(lambda: self.binance.get_mark_price("BTCUSDT"))
            open_interest, oi_err = _safe_call(lambda: self.binance.get_open_interest("BTCUSDT"))
            funding, funding_err = _safe_call(lambda: self.binance.get_funding_rate("BTCUSDT"))
            long_short, ls_err = _safe_call(lambda: self.binance.get_long_short_ratio("BTCUSDT"))
            taker_ratio, taker_err = _safe_call(lambda: self.binance.get_taker_volume_ratio("BTCUSDT"))

            cg_liq = self.coinglass.get_liquidation_data("BTC")
            cg_oi = self.coinglass.get_aggregate_oi("BTC")
            cmc = self.cmc.get_global_metrics()

            derivatives_input = {
                "open_interest": open_interest,
                "funding_rate": funding,
                "long_short_ratio": long_short,
                "taker_buy_ratio": taker_ratio,
                "liquidations_24h": cg_liq.get("total") if cg_liq.get("is_available") else None,
            }
            report = self.pipeline.run_cycle(candles, self.state, derivatives_input=derivatives_input)

            candle_payload: Dict[str, Any] = {}
            indicator_payload: Dict[str, Any] = {}
            for tf, rows in candles.items():
                candle_payload[tf] = [
                    {
                        "time": int(c.timestamp // 1000),
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
                    for c in rows
                ]
                indicator_payload[tf] = _indicator_payload(rows)

            close_24h = candles["5m"][-289].close if len(candles["5m"]) >= 289 else candles["5m"][0].close
            change_24h_pct = ((current_price - close_24h) / close_24h) * 100.0 if close_24h else 0.0

            snapshot = {
                "meta": {
                    "mode": "DEMO / MARKET-DATA ONLY",
                    "symbol": "BTCUSDT",
                    "generated_at": int(time.time() * 1000),
                    "refresh_seconds": CACHE_TTL_SECONDS,
                    "orders_enabled": False,
                    "signed_endpoints_enabled": False,
                },
                "market": {
                    "price": current_price,
                    "mark_price": mark_price,
                    "change_24h_pct": change_24h_pct,
                    "open_interest_btc": open_interest,
                    "funding_rate": funding,
                    "long_short_ratio": long_short,
                    "taker_buy_sell_ratio": taker_ratio,
                },
                "decision": _jsonable(report),
                "state": {
                    "balance_usdt": self.state.account_balance_usdt,
                    "daily_pnl_usdt": self.state.daily_realized_pnl_usdt,
                    "consecutive_losses": self.state.consecutive_losses,
                    "kill_switch_active": self.state.kill_switch_activated,
                    "kill_switch_reason": self.state.kill_switch_reason,
                    "active_position": _jsonable(self.state.active_position),
                },
                "sources": {
                    "binance": {
                        "status": "HEALTHY" if not any([mark_err, oi_err, funding_err, ls_err, taker_err]) else "DEGRADED",
                        "errors": [e for e in [mark_err, oi_err, funding_err, ls_err, taker_err] if e],
                    },
                    "coinglass": {
                        "status": "HEALTHY" if cg_liq.get("is_available") or cg_oi.get("is_available") else "UNAVAILABLE",
                        "liquidations": _jsonable(cg_liq),
                        "aggregate_oi": _jsonable(cg_oi),
                    },
                    "coinmarketcap": {
                        "status": "HEALTHY" if cmc.get("is_available") else "UNAVAILABLE",
                        "metrics": _jsonable(cmc),
                    },
                },
                "candles": candle_payload,
                "indicators": indicator_payload,
                "zones": self._zones(candles, current_price),
                "risk_config": {
                    "trend_risk_pct": self.settings.TREND_RISK_PCT,
                    "counter_trend_risk_pct": self.settings.COUNTER_TREND_RISK_PCT,
                    "min_risk_reward": self.settings.MIN_RISK_REWARD,
                    "max_daily_loss_pct": self.settings.MAX_DAILY_LOSS_PCT,
                    "max_consecutive_losses": self.settings.MAX_CONSECUTIVE_LOSSES,
                },
            }
            self._snapshot = snapshot
            self._cached_at = now
            return snapshot

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "mode": "DEMO / MARKET-DATA ONLY",
            "orders_enabled": False,
            "dashboard_dir": DASHBOARD_DIR.exists(),
            "coinglass_configured": bool(self.settings.COINGLASS_API_KEY),
            "coinmarketcap_configured": bool(self.settings.COINMARKETCAP_API_KEY),
        }


RUNTIME: Optional[DashboardRuntime] = None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "BTCBotDemo/1.0"

    def log_message(self, fmt: str, *args) -> None:
        logger.info("dashboard " + fmt, *args)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'",
        )

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(_jsonable(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._send_json(RUNTIME.health())
            return

        if path == "/api/snapshot":
            try:
                force = parse_qs(parsed.query).get("force", ["0"])[0] == "1"
                self._send_json(RUNTIME.snapshot(force=force))
            except Exception as exc:
                logger.exception("Dashboard snapshot failed")
                self._send_json({"ok": False, "error": str(exc), "mode": "DEMO"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return

        if path in {"/", "/index.html"}:
            self._send_static(DASHBOARD_DIR / "index.html")
            return

        candidate = (DASHBOARD_DIR / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(DASHBOARD_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self._send_static(candidate)


def _self_test() -> int:
    missing = [name for name in ["index.html", "styles.css", "app.js"] if not (DASHBOARD_DIR / name).exists()]
    if missing:
        print("SELF-TEST FAIL: missing static files:", ", ".join(missing))
        return 1
    json.dumps({"mode": "DEMO", "orders_enabled": False}, allow_nan=False)
    print("SELF-TEST PASS: dashboard assets + JSON contract + read-only mode")
    return 0


def main() -> None:
    global RUNTIME
    parser = argparse.ArgumentParser(description="BTC Trading Bot read-only demo dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--open", action="store_true", help="Open dashboard in the default browser")
    parser.add_argument("--self-test", action="store_true", help="Run local non-network smoke checks and exit")
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())

    RUNTIME = DashboardRuntime()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    logger.info(f"BTC demo dashboard: {url} (READ-ONLY / NO ORDERS)")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
