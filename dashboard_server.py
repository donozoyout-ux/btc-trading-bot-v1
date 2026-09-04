"""BTC Intelligence Console backend.

The dashboard combines real public BTC market data, the deterministic strategy
pipeline, a read-only Binance Futures Testnet account connector, chart-reading,
news context, Telegram notifications and an optional advisory AI layer.

No dashboard route can submit an exchange order.
"""

from __future__ import annotations

import argparse
import hmac as secrets_hmac
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
import requests
from loguru import logger

from config.settings import get_settings
from core.models import Candle
from core.state import BotState
from data.binance_client import BinanceFuturesClient
from data.coinglass_client import CoinGlassClient
from data.cmc_client import CoinMarketCapClient
from engines.chart_reader import ChartReader
from engines.regime_engine import MarketRegimeEngine
from engines.volatility_engine import VolatilityEngine
from integrations.ai_analyst import AIAnalyst
from integrations.news_engine import NewsEngine
from integrations.telegram_notifier import TelegramNotifier
from runner import MasterPipeline


ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT / "dashboard"
CACHE_TTL_SECONDS = 15


def _jsonable(value: Any) -> Any:
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
        logger.warning(f"Dashboard source call failed: {type(exc).__name__}")
        return fallback, type(exc).__name__


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
        for raw in reversed(valid.tolist()):
            value = float(raw)
            if value != 0.0:
                return round(value, 6)
        return None

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
    """Long-lived dashboard runtime with cached external-source snapshots."""

    def __init__(self):
        self.settings = get_settings()

        # Public analytics always use real production public data, with no keys.
        self.binance = BinanceFuturesClient(
            api_key=None,
            api_secret=None,
            testnet=False,
            read_only=True,
        )

        # Signed account client is TESTNET-only and read-only.
        self.account_client: Optional[BinanceFuturesClient] = None
        if self.settings.BINANCE_TESTNET and self.settings.BINANCE_API_KEY and self.settings.BINANCE_API_SECRET:
            self.account_client = BinanceFuturesClient(
                api_key=self.settings.BINANCE_API_KEY,
                api_secret=self.settings.BINANCE_API_SECRET,
                testnet=True,
                read_only=True,
                recv_window_ms=self.settings.BINANCE_RECV_WINDOW_MS,
            )

        self.coinglass = CoinGlassClient(api_key=self.settings.COINGLASS_API_KEY)
        self.cmc = CoinMarketCapClient(api_key=self.settings.COINMARKETCAP_API_KEY)
        self.pipeline = MasterPipeline(self.settings)
        self.chart_reader = ChartReader()
        self.news_engine = NewsEngine(
            self.settings.news_rss_urls,
            enabled=self.settings.NEWS_ENABLED,
            max_items=self.settings.NEWS_MAX_ITEMS,
            lookback_hours=self.settings.NEWS_LOOKBACK_HOURS,
        )
        self.telegram = TelegramNotifier(
            self.settings.TELEGRAM_BOT_TOKEN,
            self.settings.TELEGRAM_CHAT_ID,
            enabled=self.settings.TELEGRAM_ENABLED,
        )
        self.ai = AIAnalyst(
            self.settings.OPENAI_API_KEY,
            self.settings.OPENAI_MODEL,
            enabled=self.settings.AI_ENABLED,
            timeout=self.settings.AI_TIMEOUT_SEC,
            provider=self.settings.AI_PROVIDER,
        )
        self.state = BotState(
            account_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
            start_of_day_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
        )

        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._snapshot: Optional[Dict[str, Any]] = None
        self._news_cached_at = 0.0
        self._news_cache: Optional[Dict[str, Any]] = None
        self._last_telegram_decision_key: Optional[str] = None

        logger.info("BINANCE MARKET DATA: PUBLIC PRODUCTION FEED")
        logger.info("BINANCE ACCOUNT MODE: TESTNET" if self.settings.BINANCE_TESTNET else "BINANCE ACCOUNT MODE: BLOCKED")
        logger.info("ACCOUNT ACCESS: READ ONLY")
        logger.info("ORDER SUBMISSION: DISABLED IN DASHBOARD")

    @property
    def requires_auth(self) -> bool:
        return bool(self.settings.DASHBOARD_ADMIN_TOKEN)

    def authorized(self, token: Optional[str]) -> bool:
        expected = self.settings.DASHBOARD_ADMIN_TOKEN
        if not expected:
            return True
        if not token:
            return False
        return secrets_hmac.compare_digest(str(token), str(expected))

    def _fetch_candles(self) -> Dict[str, List[Candle]]:
        limits = {"4h": 320, "1h": 420, "15m": 500, "5m": 600}
        data = {tf: self.binance.get_klines("BTCUSDT", tf, limits[tf]) for tf in limits}
        if any(not rows for rows in data.values()):
            raise RuntimeError("One or more Binance candle timeframes returned no closed candles")
        return data

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
            logger.warning(f"Dashboard S/R calculation failed: {type(exc).__name__}")
            return []

    @staticmethod
    def _account_error(exc: Exception) -> Dict[str, Any]:
        category = "ACCOUNT_UNAVAILABLE"
        http_status = None
        binance_code = None
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            http_status = exc.response.status_code
            try:
                body = exc.response.json()
                binance_code = body.get("code")
                message = str(body.get("msg") or "").lower()
            except Exception:
                message = ""
            if binance_code in (-2014, -2015) or "api-key" in message or "api key" in message:
                category = "INVALID_API_KEY"
            elif binance_code == -1022 or "signature" in message:
                category = "INVALID_SIGNATURE"
            elif binance_code == -1021 or "timestamp" in message:
                category = "TIMESTAMP_ERROR"
        elif isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            category = "NETWORK_ERROR"
        return {
            "status": "ERROR",
            "environment": "TESTNET",
            "connected": False,
            "read_only": True,
            "orders_enabled": False,
            "error_category": category,
            "http_status": http_status,
            "binance_code": binance_code,
            "positions": [],
            "open_orders": [],
            "open_position_count": 0,
            "open_order_count": 0,
        }

    def account_snapshot(self) -> Dict[str, Any]:
        if not self.settings.BINANCE_TESTNET:
            return {
                "status": "BLOCKED",
                "environment": "PRODUCTION_BLOCKED",
                "connected": False,
                "read_only": True,
                "orders_enabled": False,
                "error_category": "TESTNET_REQUIRED",
                "positions": [],
                "open_orders": [],
                "open_position_count": 0,
                "open_order_count": 0,
            }
        if not self.settings.BINANCE_API_KEY or not self.settings.BINANCE_API_SECRET:
            return {
                "status": "UNAVAILABLE",
                "environment": "TESTNET",
                "connected": False,
                "read_only": True,
                "orders_enabled": False,
                "error_category": "CREDENTIALS_MISSING",
                "positions": [],
                "open_orders": [],
                "open_position_count": 0,
                "open_order_count": 0,
            }
        if self.account_client is None:
            return {
                "status": "UNAVAILABLE",
                "environment": "TESTNET",
                "connected": False,
                "read_only": True,
                "orders_enabled": False,
                "error_category": "CLIENT_UNAVAILABLE",
                "positions": [],
                "open_orders": [],
                "open_position_count": 0,
                "open_order_count": 0,
            }
        try:
            result = self.account_client.get_account_summary()
            result["status"] = "HEALTHY"
            return result
        except Exception as exc:
            logger.warning(f"Binance Testnet account read failed: {type(exc).__name__}")
            return self._account_error(exc)

    def news_snapshot(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        # News moves slower than market data; avoid hammering RSS sources.
        if not force and self._news_cache is not None and (now - self._news_cached_at) < 180:
            return self._news_cache
        self._news_cache = self.news_engine.snapshot()
        self._news_cached_at = now
        return self._news_cache

    def _maybe_notify_telegram(self, snapshot: Dict[str, Any]) -> None:
        if not self.telegram.configured:
            return
        decision = snapshot.get("decision") or {}
        final_decision = str(decision.get("final_decision") or "")
        setup = str(decision.get("setup") or "")
        trigger = str(decision.get("trigger_state") or "")
        candle_time = ((snapshot.get("candles") or {}).get("5m") or [{}])[-1].get("time")
        key = f"{candle_time}|{final_decision}|{setup}|{trigger}"
        if key == self._last_telegram_decision_key:
            return

        actionable = "ENTRY" in final_decision or (setup not in ("", "NONE") and self.settings.TELEGRAM_NOTIFY_WAIT)
        if actionable:
            result = self.telegram.send_message(TelegramNotifier.format_decision(snapshot))
            if result.get("ok"):
                self._last_telegram_decision_key = key

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
            account = self.account_snapshot()
            chart_reading = self.chart_reader.analyze(candles)
            news = self.news_snapshot()

            snapshot = {
                "meta": {
                    "mode": "DEMO / TESTNET ACCOUNT READ-ONLY",
                    "symbol": "BTCUSDT",
                    "generated_at": int(time.time() * 1000),
                    "refresh_seconds": CACHE_TTL_SECONDS,
                    "orders_enabled": False,
                    "signed_account_reads_enabled": bool(self.account_client),
                    "dashboard_auth_required": self.requires_auth,
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
                "chart_reading": chart_reading,
                "news": news,
                "account": account,
                "ai": self.ai.status(),
                "telegram": self.telegram.status(),
                "state": {
                    "simulation_balance_usdt": self.state.account_balance_usdt,
                    "daily_pnl_usdt": self.state.daily_realized_pnl_usdt,
                    "consecutive_losses": self.state.consecutive_losses,
                    "kill_switch_active": self.state.kill_switch_activated,
                    "kill_switch_reason": self.state.kill_switch_reason,
                    "active_position": _jsonable(self.state.active_position),
                },
                "sources": {
                    "binance": {
                        "status": "HEALTHY" if not any([mark_err, oi_err, funding_err]) else "DEGRADED",
                        "errors": [e for e in [mark_err, oi_err, funding_err, ls_err, taker_err] if e],
                    },
                    "binance_account": {
                        "status": account.get("status"),
                        "environment": account.get("environment"),
                        "read_only": True,
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
                    "news": {"status": news.get("status")},
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
            self._maybe_notify_telegram(snapshot)
            return snapshot

    def ai_analysis(self) -> Dict[str, Any]:
        snapshot = self.snapshot(force=False)
        return self.ai.analyze(snapshot)

    def telegram_test(self) -> Dict[str, Any]:
        return self.telegram.send_message(
            "BTC Trading Bot — Telegram bağlantısı aktif.\nMode: DEMO / TESTNET READ-ONLY\nOrder submission: DISABLED",
            force=True,
        )

    def telegram_decision(self) -> Dict[str, Any]:
        snapshot = self.snapshot(force=False)
        return self.telegram.send_message(TelegramNotifier.format_decision(snapshot), force=True)

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "mode": "DEMO / TESTNET ACCOUNT READ-ONLY",
            "orders_enabled": False,
            "account_read_only": True,
            "dashboard_dir": DASHBOARD_DIR.exists(),
            "dashboard_auth_required": self.requires_auth,
            "market_data_ok": True,
            "testnet_account_configured": bool(
                self.settings.BINANCE_TESTNET and self.settings.BINANCE_API_KEY and self.settings.BINANCE_API_SECRET
            ),
            "coinglass_configured": bool(self.settings.COINGLASS_API_KEY),
            "coinmarketcap_configured": bool(self.settings.COINMARKETCAP_API_KEY),
            "telegram": self.telegram.status(),
            "ai": self.ai.status(),
            "news_enabled": self.settings.NEWS_ENABLED,
        }


RUNTIME: Optional[DashboardRuntime] = None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "BTCBotDemo/2.0"

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

    def _token(self) -> Optional[str]:
        return self.headers.get("X-Dashboard-Token")

    def _require_auth(self) -> bool:
        if RUNTIME.authorized(self._token()):
            return True
        self._send_json({"ok": False, "error": "UNAUTHORIZED"}, HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._send_json(RUNTIME.health())
            return

        if path.startswith("/api/") and not self._require_auth():
            return

        try:
            if path == "/api/snapshot":
                force = parse_qs(parsed.query).get("force", ["0"])[0] == "1"
                self._send_json(RUNTIME.snapshot(force=force))
                return
            if path == "/api/account":
                self._send_json(RUNTIME.account_snapshot())
                return
            if path == "/api/news":
                self._send_json(RUNTIME.news_snapshot(force=False))
                return
            if path == "/api/ai-analysis":
                self._send_json(RUNTIME.ai_analysis())
                return
        except Exception as exc:
            logger.exception("Dashboard API request failed")
            self._send_json({"ok": False, "error": type(exc).__name__}, HTTPStatus.SERVICE_UNAVAILABLE)
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/") or not self._require_auth():
            return
        try:
            if path == "/api/telegram/test":
                self._send_json(RUNTIME.telegram_test())
                return
            if path == "/api/telegram/decision":
                self._send_json(RUNTIME.telegram_decision())
                return
            if path == "/api/ai-analysis":
                self._send_json(RUNTIME.ai_analysis())
                return
            self._send_json({"ok": False, "error": "NOT_FOUND"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            logger.exception("Dashboard action failed")
            self._send_json({"ok": False, "error": type(exc).__name__}, HTTPStatus.SERVICE_UNAVAILABLE)


def _self_test() -> int:
    missing = [name for name in ["index.html", "styles.css", "app.js"] if not (DASHBOARD_DIR / name).exists()]
    if missing:
        print("SELF-TEST FAIL: missing static files:", ", ".join(missing))
        return 1

    # Guardrails that can be checked without network/credentials.
    demo_client = BinanceFuturesClient(api_key="x", api_secret="y", testnet=True, read_only=True)
    try:
        demo_client.place_order()
        print("SELF-TEST FAIL: read-only account client accepted place_order")
        return 1
    except PermissionError:
        pass

    json.dumps({"mode": "DEMO", "orders_enabled": False}, allow_nan=False)
    print("SELF-TEST PASS: assets + JSON + read-only account guard")
    return 0


def main() -> None:
    global RUNTIME
    parser = argparse.ArgumentParser(description="BTC Trading Bot intelligence dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())

    RUNTIME = DashboardRuntime()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    logger.info(f"BTC Intelligence Console: {url}")
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
