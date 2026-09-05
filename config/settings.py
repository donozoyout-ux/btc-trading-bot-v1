"""Bot configuration and settings loaded from environment variables or hypotheses registry."""

from functools import lru_cache
from typing import Optional, List
from pydantic import Field, model_validator
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

    # Asset & Exchange
    SYMBOL: str = "BTC/USDT"
    BINANCE_SYMBOL: str = "BTCUSDT"
    TIMEFRAMES: List[str] = Field(default=["4h", "1h", "15m", "5m"])

    # API Keys (Binance Futures)
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    BINANCE_TESTNET: bool = True
    BINANCE_RECV_WINDOW: int = Field(default=5000, ge=1000, le=60000)
    ACCOUNT_READ_ONLY: bool = True
    ORDER_SUBMISSION_ENABLED: bool = False
    RUN_EXECUTION_SMOKE_TEST: bool = False
    TEST_ORDER_NOTIONAL_USDT: float = Field(default=10.0, gt=0, le=100.0)
    TEST_ORDER_MAX_NOTIONAL_USDT: float = Field(default=100.0, ge=50.0, le=250.0)
    MAX_OPEN_POSITIONS: int = Field(default=1, ge=1, le=1)
    EXECUTION_POLL_SECONDS: int = Field(default=15, ge=5, le=300)

    # External APIs (Context only)
    COINGLASS_API_KEY: Optional[str] = None
    COINMARKETCAP_API_KEY: Optional[str] = None

    # Telegram notifications (backend-only, no trading commands)
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_DEDUPE_TTL_SECONDS: int = Field(default=3600, ge=60, le=86400)

    # Context-only intelligence integrations
    NEWS_ENABLED: bool = True
    NEWS_RSS_URLS: str = "https://www.coindesk.com/arc/outboundfeeds/rss/,https://cointelegraph.com/rss"
    NEWS_CACHE_SECONDS: int = Field(default=300, ge=60, le=3600)
    AI_ENABLED: bool = False
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-5"

    # Kept only for backwards-compatible env parsing. Interactive dashboard auth
    # is intentionally disabled for the unattended 24/7 TESTNET runtime.
    DASHBOARD_ADMIN_TOKEN: Optional[str] = None

    # Defaults remain observation-only; explicit TESTNET execution flags may unlock orders.
    SHADOW_MODE: bool = True

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
    EXIT_POLICY_AUTO_BREAKEVEN: bool = Field(default_factory=lambda: INITIAL_HYPOTHESES["exit_policy_auto_breakeven"])

    # Warmup & Buffer Configurations
    MIN_WARMUP_BARS_4H: int = 70
    INDICATOR_CONVERGENCE_TOLERANCE: float = 0.0005

    # Storage Paths
    JOURNAL_DIR: str = "journal_logs"

    @model_validator(mode="after")
    def enforce_demo_observation_mode(self):
        """Only an explicit TESTNET-only configuration may unlock execution."""
        execution_boundary = self.BINANCE_TESTNET and self.ENV.strip().lower() == "testnet"
        if not execution_boundary or not self.ORDER_SUBMISSION_ENABLED:
            self.ORDER_SUBMISSION_ENABLED = False
            self.RUN_EXECUTION_SMOKE_TEST = False
            self.ACCOUNT_READ_ONLY = True
            self.SHADOW_MODE = True
        elif self.ACCOUNT_READ_ONLY or self.SHADOW_MODE:
            # Conflicting flags fail closed instead of being silently relaxed.
            self.ORDER_SUBMISSION_ENABLED = False
            self.RUN_EXECUTION_SMOKE_TEST = False

        # Unattended 24/7 dashboard: no interactive admin-token wall.
        # This also keeps helper POST endpoints disabled because no admin token exists.
        self.DASHBOARD_ADMIN_TOKEN = None
        return self

    @property
    def testnet_execution_enabled(self) -> bool:
        return bool(
            self.BINANCE_TESTNET
            and self.ENV.strip().lower() == "testnet"
            and self.ORDER_SUBMISSION_ENABLED
            and not self.ACCOUNT_READ_ONLY
            and not self.SHADOW_MODE
        )


@lru_cache
def get_settings() -> BotSettings:
    return BotSettings()
