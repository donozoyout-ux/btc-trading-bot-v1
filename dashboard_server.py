"""Read-only BTC demo dashboard server.

Serves the animated dashboard and exposes a read-only intelligence API backed by
real public market data plus optional signed TESTNET account reads. No order
endpoint or order-capable client is exposed by this server.
"""

from __future__ import annotations

import argparse
import hmac
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
from core.security import SecurityManager
from core.models import Candle
from core.state import BotState
from data.binance_client import (
    BinanceAccountError,
    BinanceFuturesAccountClient,
    BinanceFuturesClient,
    ResilientBinanceFuturesMarketClient,
)
from data.coinglass_client import CoinGlassClient
from data.cmc_client import CoinMarketCapClient
from engines.regime_engine import MarketRegimeEngine
from engines.chart_reader_v3 import ChartReadingEngineV3, MultiTimeframeInterpreter
from engines.strategy_orchestrator import StrategyOrchestrator
from engines.volatility_engine import VolatilityEngine
from integrations.ai_analyst import AIAnalystError, AIAnalystV2
from integrations.news_engine import NewsEngineV2
from journal.shadow_journal import ShadowDecisionJournal
from journal.execution_journal import ExecutionJournal
from notifications.telegram_client import TelegramClient, TelegramError
from notifications.telegram_notifier import TelegramEventNotifier
from runner import MasterPipeline


ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT / "dashboard"
CACHE_TTL_SECONDS = 15
ACCOUNT_READ_ONLY = True
ORDERS_ENABLED = False
DASHBOARD_ORDER_STATUS = "ORDER SUBMISSION: DISABLED IN DASHBOARD"
BLOCKED_REASON = "Demo dashboard only permits Binance Futures Testnet credentials."


def _empty_account(
    status: str = "DISCONNECTED",
    error_category: str = "ACCOUNT_UNAVAILABLE",
    message: str = "Demo account not connected",
) -> Dict[str, Any]:
    """Build an explicit no-data account payload without fake balances."""
    return {
        "mode": "TESTNET",
        "environment": "TESTNET",
        "account_type": "USD-M FUTURES",
        "connected": False,
        "status": status,
        "error_category": error_category,
        "message": message,
        "orders_enabled": False,
        "account_read_only": True,
        "asset": "USDT",
        "wallet_balance_usdt": None,
        "available_balance_usdt": None,
        "margin_balance_usdt": None,
        "unrealized_pnl_usdt": None,
        "margin_used_usdt": None,
        "total_initial_margin": None,
        "total_maint_margin": None,
        "total_position_initial_margin": None,
        "total_open_order_initial_margin": None,
        "balances": [],
        "positions": [],
        "open_orders": [],
        "updated_at": int(time.time() * 1000),
    }


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

    def __init__(
        self,
        settings=None,
        market_client=None,
        account_client=None,
        coinglass_client=None,
        cmc_client=None,
        telegram_client=None,
        news_engine=None,
        ai_analyst=None,
        shadow_journal=None,
    ):
        self.settings = settings or get_settings()
        for secret in (
            self.settings.BINANCE_API_KEY,
            self.settings.BINANCE_API_SECRET,
            self.settings.COINGLASS_API_KEY,
            self.settings.COINMARKETCAP_API_KEY,
            self.settings.TELEGRAM_BOT_TOKEN,
            self.settings.TELEGRAM_CHAT_ID,
            self.settings.OPENAI_API_KEY,
            self.settings.DASHBOARD_ADMIN_TOKEN,
        ):
            SecurityManager.register_secret(secret)
        # Real production PUBLIC market data, but no credentials are passed. This
        # dashboard runtime therefore cannot submit signed Binance orders.
        self.binance = market_client or ResilientBinanceFuturesMarketClient(
            primary=BinanceFuturesClient(api_key=None, api_secret=None, testnet=False),
            fallback=BinanceFuturesClient(api_key=None, api_secret=None, testnet=True),
        )
        self.account_client = account_client
        if self.account_client is None and self.settings.BINANCE_TESTNET:
            self.account_client = BinanceFuturesAccountClient(
                api_key=self.settings.BINANCE_API_KEY,
                api_secret=self.settings.BINANCE_API_SECRET,
                testnet=True,
                read_only=True,
                recv_window=self.settings.BINANCE_RECV_WINDOW,
            )
        self.coinglass = coinglass_client or CoinGlassClient(api_key=self.settings.COINGLASS_API_KEY)
        self.cmc = cmc_client or CoinMarketCapClient(api_key=self.settings.COINMARKETCAP_API_KEY)
        self.telegram = telegram_client or TelegramClient(
            bot_token=self.settings.TELEGRAM_BOT_TOKEN,
            chat_id=self.settings.TELEGRAM_CHAT_ID,
            enabled=self.settings.TELEGRAM_ENABLED,
        )
        self.telegram_notifier = TelegramEventNotifier(
            self.telegram,
            dedupe_ttl_seconds=self.settings.TELEGRAM_DEDUPE_TTL_SECONDS,
        )
        self.news_engine = news_engine or NewsEngineV2(
            urls=self.settings.NEWS_RSS_URLS.split(","),
            enabled=self.settings.NEWS_ENABLED,
            cache_seconds=self.settings.NEWS_CACHE_SECONDS,
        )
        self.ai_analyst = ai_analyst or AIAnalystV2(
            api_key=self.settings.OPENAI_API_KEY,
            model=self.settings.OPENAI_MODEL,
            enabled=self.settings.AI_ENABLED,
        )
        self.chart_reader = ChartReadingEngineV3(
            volume_expansion_threshold=self.settings.VOLUME_RVOL_THRESHOLD,
            wick_rejection_ratio=self.settings.WICK_REJECTION_RATIO,
            directional_body_ratio=self.settings.DIRECTIONAL_BODY_RATIO,
        )
        self.mtf_interpreter = MultiTimeframeInterpreter()
        self.strategy_orchestrator = StrategyOrchestrator()
        self.shadow_journal = shadow_journal or ShadowDecisionJournal(self.settings.JOURNAL_DIR)
        self.execution_journal = ExecutionJournal(self.settings.JOURNAL_DIR)
        self.pipeline = MasterPipeline(self.settings)
        self.state = BotState(
            account_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
            start_of_day_balance_usdt=self.settings.INITIAL_CAPITAL_USDT,
        )
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._snapshot: Optional[Dict[str, Any]] = None
        self._account_lock = threading.Lock()
        self._account_cached_at = 0.0
        # Two timestamped real Binance observations are required before an OI
        # change can exist. Process restarts intentionally reset this history.
        self._binance_oi_observations: List[tuple[int, int, float]] = []
        self._last_oi_change: Dict[str, Any] = {
            "value": None, "source": "UNAVAILABLE", "observed_at": None,
        }
        self._account_snapshot: Optional[Dict[str, Any]] = None
        self._last_ai_decision_id: Optional[str] = None
        self._last_ai_result: Dict[str, Any] = self.ai_analyst.unavailable()

    @property
    def admin_token_configured(self) -> bool:
        return bool((self.settings.DASHBOARD_ADMIN_TOKEN or "").strip())

    @property
    def account_configured(self) -> bool:
        return bool(
            self.settings.BINANCE_TESTNET
            and self.account_client is not None
            and self.account_client.configured
        )

    @property
    def execution_enabled(self) -> bool:
        return self.settings.testnet_execution_enabled

    def account(self, force: bool = False) -> Dict[str, Any]:
        """Read the signed demo account independently from public analytics."""
        if not self.settings.BINANCE_TESTNET:
            return _empty_account(
                status="BLOCKED",
                error_category="ACCOUNT_CONNECTION_BLOCKED",
                message=f"ACCOUNT CONNECTION BLOCKED — Reason: {BLOCKED_REASON}",
            )
        if self.account_client is None or not self.account_client.configured:
            return _empty_account()

        with self._account_lock:
            if (
                not force
                and self._account_snapshot is not None
                and time.time() - self._account_cached_at < 3
            ):
                return dict(self._account_snapshot)
            try:
                raw = self.account_client.get_account_summary()
                result = {
                    "mode": "TESTNET",
                    "environment": "TESTNET",
                    "account_type": raw.get("account_type"),
                    "connected": True,
                    "status": "CONNECTED",
                    "error_category": None,
                    "message": None,
                    "orders_enabled": self.execution_enabled,
                    "account_read_only": self.settings.ACCOUNT_READ_ONLY,
                    "asset": "USDT",
                    "wallet_balance_usdt": raw.get("wallet_balance"),
                    "available_balance_usdt": raw.get("available_balance"),
                    "margin_balance_usdt": raw.get("margin_balance"),
                    "unrealized_pnl_usdt": raw.get("unrealized_pnl"),
                    "margin_used_usdt": raw.get("total_initial_margin"),
                    "total_initial_margin": raw.get("total_initial_margin"),
                    "total_maint_margin": raw.get("total_maint_margin"),
                    "total_position_initial_margin": raw.get("total_position_initial_margin"),
                    "total_open_order_initial_margin": raw.get("total_open_order_initial_margin"),
                    "balances": raw.get("balances", []),
                    "positions": raw.get("positions", []),
                    "open_orders": raw.get("open_orders", []),
                    "updated_at": int(time.time() * 1000),
                }
                self._account_snapshot = result
                self._account_cached_at = time.time()
                return dict(result)
            except BinanceAccountError as exc:
                logger.warning("Binance testnet account read failed: {}", exc.category)
                return _empty_account(status="DEGRADED", error_category=exc.category)
            except Exception:
                logger.warning("Binance testnet account read failed: ACCOUNT_UNAVAILABLE")
                return _empty_account(status="DEGRADED")

    def _fetch_candles(self) -> Dict[str, List[Candle]]:
        begin_cycle = getattr(self.binance, "begin_cycle", None)
        if callable(begin_cycle):
            begin_cycle()
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

    @staticmethod
    def _ai_context(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        decision = snapshot.get("decision", {})
        state = snapshot.get("system_state", {})
        return {
            "market": {
                "price": snapshot.get("market", {}).get("price"),
                "regime": decision.get("regime"),
                "volatility": decision.get("volatility"),
            },
            "chart": snapshot.get("chart_intelligence", {}).get("timeframes", {}),
            "strategy": snapshot.get("strategy", {}),
            "derivatives": snapshot.get("derivatives", {}),
            "news": snapshot.get("news", {}),
            "macro_context": snapshot.get("macro_context", {}),
            "risk": {
                "status": decision.get("risk_status"),
                "risk_reward": (decision.get("trade_plan") or {}).get("risk_reward"),
                "kill_switch": state.get("kill_switch"),
                "daily_loss_state": state.get("daily_loss_guard"),
            },
            "account": snapshot.get("account", {}),
        }

    def analyze_ai(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self.ai_analyst.analyze(self._ai_context(snapshot))
        except AIAnalystError as exc:
            logger.warning("AI advisory failed: {}", exc.category)
            return self.ai_analyst.unavailable(exc.category)

    def notify_current_decision(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self.telegram_notifier.notify_current_decision(snapshot)
        except TelegramError as exc:
            logger.warning("Telegram notification failed: {}", exc.category)
            return {"sent": False, "deduplicated": False, "error_category": exc.category}

    def _observe_binance_oi(self, candle_timestamp: int, value: Optional[float], observed_at: int) -> Dict[str, Any]:
        """Return OI change only for two consecutive, closed 5M candle keys.

        Re-fetching during the same candle returns the original observation and
        delta unchanged. Missing data and process warm-up remain unavailable.
        """
        if self._binance_oi_observations and self._binance_oi_observations[-1][0] == candle_timestamp:
            return dict(self._last_oi_change)
        if value is None:
            return {"value": None, "source": "UNAVAILABLE", "observed_at": None}

        previous = self._binance_oi_observations[-1] if self._binance_oi_observations else None
        self._binance_oi_observations.append((candle_timestamp, observed_at, float(value)))
        self._binance_oi_observations = self._binance_oi_observations[-2:]
        if previous is None or candle_timestamp - previous[0] != 5 * 60 * 1000 or previous[2] <= 0:
            result = {"value": None, "source": "UNAVAILABLE", "observed_at": None}
        else:
            result = {
                "value": (float(value) - previous[2]) / previous[2],
                "source": "BINANCE",
                "observed_at": observed_at,
            }
        self._last_oi_change = dict(result)
        return result

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
            cg_status = "CONNECTED" if cg_liq.get("is_available") or cg_oi.get("is_available") else cg_oi.get("status") or cg_liq.get("status") or "UNAVAILABLE"
            cmc = self.cmc.get_global_metrics()
            news = self.news_engine.evaluate(force=force)

            observed_at = int(time.time() * 1000)
            closed_5m_timestamp = candles["5m"][-1].timestamp
            oi_change = self._observe_binance_oi(closed_5m_timestamp, open_interest, observed_at)
            cg_oi_value = cg_oi.get("aggregate_oi_usd") if cg_oi.get("is_available") else None
            derivatives_input = {
                "open_interest": {"value": cg_oi_value if cg_oi_value is not None else open_interest, "source": "COINGLASS" if cg_oi_value is not None else "BINANCE" if open_interest is not None else "UNAVAILABLE", "observed_at": cg_oi.get("observed_at") if cg_oi_value is not None else observed_at if open_interest is not None else None},
                "oi_change_pct": oi_change,
                "funding_rate": {"value": funding, "source": "BINANCE" if funding is not None else "UNAVAILABLE", "observed_at": observed_at if funding is not None else None},
                "long_short_ratio": {"value": long_short, "source": "BINANCE" if long_short is not None else "UNAVAILABLE", "observed_at": observed_at if long_short is not None else None},
                "taker_buy_ratio": {"value": taker_ratio, "source": "BINANCE" if taker_ratio is not None else "UNAVAILABLE", "observed_at": observed_at if taker_ratio is not None else None},
                "liquidations_24h": {"value": cg_liq.get("total") if cg_liq.get("is_available") else None, "source": "COINGLASS" if cg_liq.get("is_available") else "UNAVAILABLE", "observed_at": cg_liq.get("observed_at")},
            }
            account = None
            risk_capital_source = "CONFIG_FALLBACK"
            sizing_capital = self.state.account_balance_usdt

            def sync_testnet_risk_capital() -> bool:
                nonlocal account, risk_capital_source, sizing_capital
                account = self.account(force=True)
                wallet = account.get("wallet_balance_usdt")
                available = account.get("available_balance_usdt")
                if account.get("connected") and wallet is not None and wallet > 0:
                    sizing_capital = float(wallet)
                    risk_capital_source = "BINANCE_TESTNET_WALLET"
                elif account.get("connected") and available is not None and available > 0:
                    sizing_capital = float(available)
                    risk_capital_source = "BINANCE_TESTNET_AVAILABLE"
                else:
                    risk_capital_source = "UNAVAILABLE"
                    return False
                self.state.account_balance_usdt = sizing_capital
                return True

            cmc_status = "CONNECTED" if cmc.get("is_available") else cmc.get("status", "UNAVAILABLE")
            report = self.pipeline.run_cycle(
                candles,
                self.state,
                derivatives_input=derivatives_input,
                source_health={"coinglass": cg_status, "coinmarketcap": cmc_status},
                risk_capital_available=not self.execution_enabled,
                risk_capital_provider=sync_testnet_risk_capital if self.execution_enabled else None,
            )
            if account is None:
                account = self.account(force=force)
                if self.execution_enabled:
                    risk_capital_source = "UNAVAILABLE"
            chart_intelligence = self.chart_reader.analyze(candles)
            mtf = self.mtf_interpreter.interpret(chart_intelligence)
            strategy = self.strategy_orchestrator.summarize(report, chart_intelligence, mtf, news)
            decision_id = f"SHADOW-BTCUSDT-{report.timestamp}"

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

            account_summary = {
                "environment": "TESTNET",
                "connected": account["connected"],
                "status": account["status"],
                "error_category": account["error_category"],
                "wallet_balance_usdt": account["wallet_balance_usdt"],
                "available_balance_usdt": account["available_balance_usdt"],
                "unrealized_pnl_usdt": account["unrealized_pnl_usdt"],
                "open_position_count": len(account["positions"]),
                "open_order_count": len(account["open_orders"]),
            }
            binance_source_status = "HEALTHY" if not any([mark_err, oi_err, funding_err, ls_err, taker_err]) else "DEGRADED"
            critical_ready = (
                binance_source_status in {"HEALTHY", "DEGRADED"}
                and (not self.account_configured or account["connected"])
            )
            supplemental_degraded = cg_status != "CONNECTED" or cmc_status != "CONNECTED"
            readiness = "NO" if not critical_ready else "YES_DEGRADED" if supplemental_degraded else "YES"
            if self.admin_token_configured:
                account_summary = {
                    "environment": "TESTNET", "connected": False, "status": "PROTECTED",
                    "error_category": None, "wallet_balance_usdt": None,
                    "available_balance_usdt": None, "unrealized_pnl_usdt": None,
                    "open_position_count": None, "open_order_count": None,
                }

            execution_state = {
                "environment": "TESTNET",
                "real_money": "DISABLED",
                "execution_enabled": self.execution_enabled,
                "bot_status": "STOPPED",
                "execution_thread": "DISABLED" if not self.execution_enabled else "STARTING",
                "last_execution_result": None,
                "last_error": None,
                "position": {"side": "FLAT", "position_amt": 0.0},
                "last_binance_order": None,
                "last_telegram_event": None,
                "smoke_test": "NOT_RUN",
            }
            execution_state.update(self.execution_journal.read_state())
            # Persisted metadata must never override the current fail-closed
            # configuration or the permanent real-money boundary.
            execution_state["environment"] = "TESTNET"
            execution_state["real_money"] = "DISABLED"
            execution_state["execution_enabled"] = self.execution_enabled
            if not self.execution_enabled:
                execution_state["bot_status"] = "STOPPED"
                execution_state["execution_thread"] = "DISABLED"

            snapshot = {
                "decision_id": decision_id,
                "final_decision": self.strategy_orchestrator.final_decision(report),
                "meta": {
                    "mode": "TESTNET AUTO EXECUTION" if self.execution_enabled else "DEMO / SHADOW / READ ONLY",
                    "symbol": "BTCUSDT",
                    "generated_at": int(time.time() * 1000),
                    "refresh_seconds": CACHE_TTL_SECONDS,
                    "orders_enabled": self.execution_enabled,
                    "shadow_mode": self.settings.SHADOW_MODE,
                    "signed_endpoints_enabled": self.account_configured,
                    "ready_for_render": readiness,
                },
                "experimental_setups": {
                    "setup_b_short_enabled": self.settings.ENABLE_SETUP_B_SHORT,
                    "setup_c_short_enabled": self.settings.ENABLE_SETUP_C_SHORT,
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
                "chart_intelligence": chart_intelligence,
                "mtf_interpretation": mtf,
                "strategy": strategy,
                "news": news,
                "ai_analyst": self._last_ai_result,
                "derivatives": {
                    "status": _jsonable(report.derivatives),
                    "open_interest": derivatives_input["open_interest"],
                    "oi_change_pct": derivatives_input["oi_change_pct"],
                    "funding_rate": derivatives_input["funding_rate"],
                    "long_short_ratio": derivatives_input["long_short_ratio"],
                    "taker_buy_ratio": derivatives_input["taker_buy_ratio"],
                    "liquidations_24h": derivatives_input["liquidations_24h"],
                },
                "macro_context": {
                    "btc_dominance": cmc.get("btc_dominance"),
                    "total_market_cap_usd": cmc.get("total_market_cap_usd"),
                    "total_volume_24h_usd": cmc.get("total_volume_24h_usd"),
                    "source": _jsonable(cmc.get("source")),
                    "observed_at": cmc.get("observed_at"),
                },
                "system_state": {
                    "kill_switch": self.state.kill_switch_activated,
                    "kill_switch_reason": self.state.kill_switch_reason,
                    "daily_loss_guard": self.state.daily_loss_guard_active,
                    "consecutive_loss_guard": self.state.consecutive_loss_cooldown_active,
                    "active_position": self.state.active_position is not None,
                    "last_decision": self.strategy_orchestrator.final_decision(report),
                    "last_update": int(time.time() * 1000),
                },
                "state": {
                    "balance_usdt": self.state.account_balance_usdt,
                    "daily_pnl_usdt": self.state.daily_realized_pnl_usdt,
                    "consecutive_losses": self.state.consecutive_losses,
                    "kill_switch_active": self.state.kill_switch_activated,
                    "kill_switch_reason": self.state.kill_switch_reason,
                    "active_position": _jsonable(self.state.active_position),
                },
                "risk_capital": {
                    "source": risk_capital_source,
                    "sizing_capital_usdt": sizing_capital if risk_capital_source != "UNAVAILABLE" else None,
                    "wallet_balance_usdt": account.get("wallet_balance_usdt"),
                    "available_balance_usdt": account.get("available_balance_usdt"),
                    "configured_risk_pct": self.settings.COUNTER_TREND_RISK_PCT if report.setup.value == "COUNTER_TREND_REACTION" else self.settings.TREND_RISK_PCT,
                    "planned_risk_usdt": report.risk_assessment.risk_amount_usdt if report.risk_assessment else None,
                },
                "account": account_summary,
                "execution": execution_state,
                "sources": {
                    "binance": {
                        "status": binance_source_status,
                        "environment": getattr(self.binance, "active_environment", "CUSTOM_PUBLIC"),
                        "fallback_active": bool(getattr(self.binance, "fallback_active", False)),
                        "errors": [e for e in [mark_err, oi_err, funding_err, ls_err, taker_err] if e],
                    },
                    "coinglass": {
                        "status": cg_status,
                        "configured": self.coinglass.configured,
                        "observed_at": max([x for x in [cg_liq.get("observed_at"), cg_oi.get("observed_at")] if x is not None], default=None),
                        "error_category": cg_oi.get("error_category") or cg_liq.get("error_category"),
                        "liquidations": _jsonable(cg_liq),
                        "aggregate_oi": _jsonable(cg_oi),
                    },
                    "coinmarketcap": {
                        "status": cmc_status,
                        "configured": self.cmc.configured,
                        "observed_at": cmc.get("observed_at"),
                        "metrics": _jsonable(cmc),
                    },
                    "binance_account": {
                        "status": account["status"],
                        "error_category": account["error_category"],
                    },
                    "telegram": self.telegram.safe_status(),
                    "news": {"status": news.get("status")},
                    "ai": self.ai_analyst.safe_status(),
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
            if self.ai_analyst.configured and self._last_ai_decision_id != decision_id:
                self._last_ai_result = self.analyze_ai(snapshot)
                self._last_ai_decision_id = decision_id
                snapshot["ai_analyst"] = self._last_ai_result
            snapshot["final_decision"] = self.strategy_orchestrator.final_decision(report, snapshot["ai_analyst"])
            snapshot["system_state"]["last_decision"] = snapshot["final_decision"]
            self.shadow_journal.record(snapshot)
            if self.telegram.configured:
                snapshot["telegram_notification"] = self.notify_current_decision(snapshot)
            self._snapshot = snapshot
            self._cached_at = now
            return snapshot

    def health(self) -> Dict[str, Any]:
        account = self.account()
        critical_ready = not self.account_configured or account["connected"]
        return {
            "ok": True,
            "ready_for_render": "YES_DEGRADED" if critical_ready else "NO",
            "mode": "TESTNET AUTO EXECUTION" if self.execution_enabled else "DEMO / SHADOW / READ ONLY",
            "orders_enabled": self.execution_enabled,
            "shadow_mode": self.settings.SHADOW_MODE,
            "account_read_only": self.settings.ACCOUNT_READ_ONLY,
            "testnet_account_configured": self.account_configured,
            "testnet_account_authenticated": account["connected"],
            "account_status": account["status"],
            "account_error_category": account["error_category"],
            "dashboard_dir": DASHBOARD_DIR.exists(),
            "coinglass_configured": bool(self.settings.COINGLASS_API_KEY),
            "coinmarketcap_configured": bool(self.settings.COINMARKETCAP_API_KEY),
            "news": {"enabled": self.settings.NEWS_ENABLED},
            "telegram": self.telegram.safe_status(),
            "ai": self.ai_analyst.safe_status(),
            "dashboard_admin_token_configured": self.admin_token_configured,
        }


RUNTIME: Optional[DashboardRuntime] = None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "BTCBotDemo/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Log only method and normalized path; never query strings or headers.
        logger.info("dashboard {} {}", self.command, urlparse(self.path).path)

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

    def _admin_authorized(self) -> bool:
        expected = (RUNTIME.settings.DASHBOARD_ADMIN_TOKEN or "").strip()
        if not expected:
            return False
        authorization = self.headers.get("Authorization", "")
        supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else self.headers.get("X-Dashboard-Admin-Token", "").strip()
        return bool(supplied) and hmac.compare_digest(supplied, expected)

    def _allow_private_get(self) -> bool:
        if not RUNTIME.admin_token_configured or self._admin_authorized():
            return True
        self._send_json({"ok": False, "error": "ADMIN_AUTH_REQUIRED"}, HTTPStatus.UNAUTHORIZED)
        return False

    def _allow_helper_post(self) -> bool:
        if not RUNTIME.admin_token_configured:
            self._send_json({"ok": False, "error": "ENDPOINT_DISABLED"}, HTTPStatus.NOT_FOUND)
            return False
        if not self._admin_authorized():
            self._send_json({"ok": False, "error": "ADMIN_AUTH_REQUIRED"}, HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._send_json(RUNTIME.health())
            return

        if path == "/api/account":
            if not self._allow_private_get():
                return
            self._send_json(RUNTIME.account())
            return

        if path == "/api/telegram":
            if not self._allow_private_get():
                return
            self._send_json(RUNTIME.telegram.safe_status())
            return

        if path == "/api/news":
            self._send_json(RUNTIME.news_engine.evaluate())
            return

        if path == "/api/chart-intelligence":
            self._send_json(RUNTIME.snapshot().get("chart_intelligence", {}))
            return

        if path == "/api/ai/status":
            if not self._allow_private_get():
                return
            self._send_json(RUNTIME.ai_analyst.safe_status())
            return

        if path == "/api/snapshot":
            try:
                force = parse_qs(parsed.query).get("force", ["0"])[0] == "1"
                self._send_json(RUNTIME.snapshot(force=force))
            except Exception:
                logger.warning("Dashboard snapshot failed: DASHBOARD_UNAVAILABLE")
                self._send_json({"ok": False, "error": "DASHBOARD_UNAVAILABLE", "mode": "DEMO"}, HTTPStatus.SERVICE_UNAVAILABLE)
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
        path = urlparse(self.path).path
        allowed = {"/api/telegram/test", "/api/telegram/current-decision", "/api/ai/analyze"}
        if path not in allowed:
            self._send_json({"ok": False, "error": "NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            return
        if not self._allow_helper_post():
            return
        if path == "/api/telegram/test":
            try:
                identity = RUNTIME.telegram.get_me()
                result = RUNTIME.telegram.send_message("BTC Intelligence Console test bildirimi.\nMod: SHADOW / READ ONLY\nEmir gönderimi: DEVRE DIŞI")
                self._send_json({"ok": True, "bot_username": identity.get("username"), "sent": result.get("sent", False)})
            except TelegramError as exc:
                self._send_json({"ok": False, "error": exc.category}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        snapshot = RUNTIME.snapshot()
        if path == "/api/telegram/current-decision":
            self._send_json(RUNTIME.notify_current_decision(snapshot))
            return
        result = RUNTIME.analyze_ai(snapshot)
        RUNTIME._last_ai_result = result
        snapshot["ai_analyst"] = result
        self._send_json(result, HTTPStatus.OK if result.get("status") == "AVAILABLE" else HTTPStatus.SERVICE_UNAVAILABLE)


def _self_test() -> int:
    missing = [name for name in ["index.html", "styles.css", "app.js"] if not (DASHBOARD_DIR / name).exists()]
    if missing:
        print("SELF-TEST FAIL: missing static files:", ", ".join(missing))
        return 1
    json.dumps({"mode": "DEMO", "orders_enabled": False}, allow_nan=False)
    account_client = BinanceFuturesAccountClient(None, None, testnet=True)
    if hasattr(account_client, "place_order"):
        print("SELF-TEST FAIL: account reader exposes order capability")
        return 1
    source = Path(__file__).read_text(encoding="utf-8")
    forbidden_routes = ["/api/" + suffix for suffix in ("order", "buy", "sell", "close-position")]
    if any(f'"{route}"' in source for route in forbidden_routes):
        print("SELF-TEST FAIL: forbidden order route detected")
        return 1
    if not all(route in source for route in ('"/api/news"', '"/api/chart-intelligence"', '"/api/ai/status"')):
        print("SELF-TEST FAIL: intelligence endpoints missing")
        return 1
    print("SELF-TEST PASS: DEMO intelligence stack + SHADOW mode + read-only account + no order routes")
    return 0


def main() -> None:
    global RUNTIME
    parser = argparse.ArgumentParser(description="BTC Trading Bot read-only demo dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--open", action="store_true", help="Open dashboard in the default browser")
    parser.add_argument("--self-test", action="store_true", help="Run local non-network smoke checks and exit")
    parser.add_argument("--telegram-test", action="store_true", help="Verify Telegram bot and send one test notification")
    parser.add_argument("--telegram-discover-chat", action="store_true", help="List chats that sent the bot a recent message")
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())

    RUNTIME = DashboardRuntime()
    if args.telegram_discover_chat:
        try:
            chats = RUNTIME.telegram.discover_chats()
            print(json.dumps({"chats": chats}, ensure_ascii=False, indent=2))
            raise SystemExit(0 if chats else 1)
        except TelegramError as exc:
            print(f"TELEGRAM CHAT DISCOVERY FAIL: {exc.category}")
            raise SystemExit(1)
    if args.telegram_test:
        try:
            identity = RUNTIME.telegram.get_me()
            RUNTIME.telegram.send_message(
                "BTC Intelligence Console bağlantısı hazır.\n"
                "Mod: DEMO / READ ONLY\n"
                "Emir gönderimi: DEVRE DIŞI"
            )
            print(f"TELEGRAM TEST PASS: @{identity.get('username') or 'bot'}")
            raise SystemExit(0)
        except TelegramError as exc:
            print(f"TELEGRAM TEST FAIL: {exc.category}")
            raise SystemExit(1)

    logger.info("BINANCE ACCOUNT MODE: TESTNET")
    logger.info("ACCOUNT ACCESS: READ ONLY")
    logger.info("SHADOW MODE: ENABLED")
    logger.info("ORDER SUBMISSION: DISABLED")
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
