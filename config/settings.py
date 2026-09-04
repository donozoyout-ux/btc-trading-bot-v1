"""Bot configuration and settings loaded from environment variables or hypotheses registry."""

from functools import lru_cache
from typing import Optional, List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.hypotheses import INITIAL_HYPOTHESES


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Environment & Mode
    APP_NAME: str = "BTC-Trading-Bot-V2"
    ENV: str = "development"  # development, testnet, production
    LOG_LEVEL: str = "INFO"
    DASHBOARD_ADMIN_TOKEN: Optional[str] = None

    # Asset & Exchange
    SYMBOL: str = "BTC/USDT"
    BINANCE_SYMBOL: str = "BTCUSDT"
    TIMEFRAMES: List[str] = Field(default=["4h", "1h", "15m", "5m"])

    # API Keys (Binance Futures)
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    BINANCE_TESTNET: bool = True
    BINANCE_RECV_WINDOW_MS: int = 5000
    ACCOUNT_READ_ONLY: bool = True

    # External APIs (Context only)
    COINGLASS_API_KEY: Optional[str] = None
    COINMARKETCAP_API_KEY: Optional[str] = None

    # Telegram notifications
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_NOTIFY_WAIT: bool = False

    # Optional AI analyst. Advisory/explanation only; never execution authority.
    AI_ENABLED: bool = False
    AI_PROVIDER: str = "openai"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-5-mini"
    AI_TIMEOUT_SEC: int = 20

    # News engine. Can be replaced with comma-separated URLs in NEWS_RSS_URLS.
    NEWS_ENABLED: bool = True
    NEWS_MAX_ITEMS: int = 20
    NEWS_LOOKBACK_HOURS: int = 24
    NEWS_RSS_URLS: str = "https://www.coindesk.com/arc/outboundfeeds/rss/,https://cointelegraph.com/rss,https://decrypt.co/feed"

    # Database Configuration
    DB_URL: str = "sqlite:///trading_bot.db"

    # Capital & Account Configuration
    INITIAL_CAPITAL_USDT: float = 10_000.0
    MAX_ACCOUNT_LEVERAGE: int = 5

    # Configurable Strategy Hypotheses (Initialized from INITIAL_HYPOTHESES)
    TREND_RISK_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["trend_risk_per_trade_pct"])
    COUNTER_TREND_RISK_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["counter_trend_risk_pct"])
    MIN_RISK_REWARD: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["min_risk_reward_ratio"])

    WICK_REJECTION_RATIO: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["wick_rejection_ratio"])
    DIRECTIONAL_BODY_RATIO: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["directional_body_ratio"])
    VOLUME_RVOL_THRESHOLD: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["volume_rvol_threshold"])
    SR_CLUSTERING_TOLERANCE_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["sr_clustering_tolerance_pct"])
    LOCATION_PROXIMITY_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["location_proximity_pct"])
    COUNTER_TREND_RSI_OVERSOLD: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["counter_trend_rsi_oversold"])
    COUNTER_TREND_ADX_VETO: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["counter_trend_adx_veto"])

    # Circuit Breakers & Kill Switch Hypotheses
    MAX_DAILY_LOSS_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["max_daily_loss_pct"])
    MAX_CONSECUTIVE_LOSSES: int = Field(default_factory=lambda: INITIAL_HYPOTHESES["max_consecutive_losses"])
    MAX_SLIPPAGE_TOLERANCE_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["max_slippage_tolerance_pct"])
    MAX_API_LATENCY_MS: int = 3000

    # Data-driven Friction Defaults (from hypotheses)
    TAKER_FEE_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["taker_fee_pct"])
    MAKER_FEE_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["maker_fee_pct"])
    SLIPPAGE_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["slippage_pct"])

    # Experimental Exit Policies
    EXIT_POLICY_TP1_CLOSE_PCT: float = Field(default_factory=lambda: INITIAL_HYPOTHESES["exit_policy_tp1_close_pct"])
    EXIT_POLICY_AUTO_BREAKEVEN: bool = Field(default_factory=lambda: INITIAL_HYPOTHESES["exit_policy_auto_breAKEVEN"] if "exit_policy_auto_breAKEVEN" in INITIAL_HYPOTHESES else INITIAL_HYPOTHESES["exit_policy_auto_breakeven"])

    # Warmup & Buffer Configurations
    MIN_WARMUP_BARS_4H: int = 70
    INDICATOR_CONVERGENCE_TOLERANCE: float = 0.0005

    # Storage Paths
    JOURNAL_DIR: str = "journal_logs"

    @property
    def news_rss_urls(self) -> List[str]:
        return [url.strip() for url in self.NEWS_RSS_URLS.split(",") if url.strip()]


@lru_cache
def get_settings() -> BotSettings:
    return BotSettings()
