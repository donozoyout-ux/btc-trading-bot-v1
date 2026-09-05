"""Core Pydantic data models for the BTC Trading Bot — Master Specification V2.1 FINAL."""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from config.constants import (
    MarketRegime,
    TradeDirection,
    DecisionStatus,
    SetupType,
    LocationQuality,
    VolatilityLevel,
    StructureType,
    TriggerState,
    DerivativesStatus,
    GlobalContextStatus,
    SourceHealthStatus,
    DataSafetyStatus,
    DataSource,
    FundingClass,
    CrowdingStatus,
    RiskDecision,
    GuardType,
    RiskReasonCode,
)


class Candle(BaseModel):
    """Represents a single OHLCV candlestick with strictly mandatory is_closed validation."""
    timestamp: int            # Epoch timestamp in milliseconds (candle open time)
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool           # Mandatory: Must be explicitly provided, no latent default!

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp / 1000, tz=timezone.utc)

    @property
    def hl2(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def hlc3(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def total_range(self) -> float:
        return max(self.high - self.low, 1e-8)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


class SwingPoint(BaseModel):
    """
    Confirmed structural swing point with dual timestamps.
    Guarantees that when simulation_clock < confirmed_at, this swing cannot be observed.
    """
    swing_time: int           # Timestamp (ms) of bar t where swing high/low formed
    confirmed_at: int         # Exact close timestamp (ms) of second right confirmation bar t+2
    price: float
    is_high: bool             # True for Swing High, False for Swing Low
    candle_index: int         # Position in series


class MarketStructure(BaseModel):
    """Output of Market Structure Engine."""
    timeframe: str
    structure: StructureType
    swing_highs: List[SwingPoint] = Field(default_factory=list)
    swing_lows: List[SwingPoint] = Field(default_factory=list)
    last_bos: Optional[str] = None    # "BULLISH_BOS", "BEARISH_BOS", or None
    last_choch: Optional[str] = None  # "BULLISH_CHOCH", "BEARISH_CHOCH", or None
    recent_hh: bool = False
    recent_hl: bool = False
    recent_lh: bool = False
    recent_ll: bool = False


class RegimeResult(BaseModel):
    """Output of Market Regime Engine (Derived exclusively from BTC market inputs)."""
    regime: MarketRegime
    score: float                      # Score between -100 and +100
    confidence: str                   # "HIGH", "MEDIUM", "LOW"
    volatility: VolatilityLevel
    is_transition: bool = False
    overextended_up: bool = False
    overextended_down: bool = False
    stability_confirmed: bool = True  # Verified across 2 consecutive closed 4H candles
    details: Dict[str, Any] = Field(default_factory=dict)


class GlobalContextState(BaseModel):
    """Output of Global Context Engine (CoinMarketCap macro context)."""
    status: GlobalContextStatus = GlobalContextStatus.UNAVAILABLE
    btc_dominance: Optional[float] = None
    total_market_cap_usd: Optional[float] = None
    global_volume_24h_usd: Optional[float] = None
    source: DataSource = DataSource.UNAVAILABLE
    is_stale: bool = False
    reason: str = ""


class ConfluenceZone(BaseModel):
    """Identified horizontal Support or Resistance zone."""
    level_type: str                   # "SUPPORT" or "RESISTANCE"
    price_min: float
    price_max: float
    center: float
    strength: int = 1                 # Confluence count
    sources: List[str] = Field(default_factory=list)


class LocationResult(BaseModel):
    """Output of Trade Location Engine."""
    quality: LocationQuality
    current_price: float
    nearest_support: Optional[ConfluenceZone] = None
    nearest_resistance: Optional[ConfluenceZone] = None
    distance_to_support_pct: float = 0.0
    distance_to_resistance_pct: float = 0.0
    is_bad_location: bool = False
    reason: str = ""


class SetupSignal(BaseModel):
    """Output of Setup Detection Engine."""
    setup_type: SetupType
    direction: TradeDirection
    detected: bool = False
    timeframe: str = "15m"
    invalidation_level: float = 0.0
    target_level: float = 0.0
    zone: Optional[ConfluenceZone] = None
    reason: str = ""
    breakout_timestamp: Optional[int] = None
    retest_timestamp: Optional[int] = None
    breakout_level: Optional[float] = None
    breakout_quality: Optional[str] = None
    retest_hold: bool = False
    retest_confirmation: Optional[str] = None
    setup_invalidated: bool = False


class TriggerResult(BaseModel):
    """Output of 5M Entry Trigger Engine."""
    state: TriggerState
    is_triggered: bool = False
    direction: TradeDirection = TradeDirection.WAIT
    pattern: str = ""
    trigger_price: float = 0.0
    reason: str = ""


class DerivativesField(BaseModel):
    """Container for individual derivatives metric carrying provenance and freshness."""
    value: Optional[float] = None
    source: DataSource = DataSource.UNAVAILABLE
    observed_at: Optional[int] = None
    is_stale: bool = False


class DerivativesState(BaseModel):
    """Output of Derivatives Confirmation Engine with field-level provenance metadata."""
    status: DerivativesStatus = DerivativesStatus.UNAVAILABLE
    open_interest: DerivativesField = Field(default_factory=DerivativesField)
    oi_change_pct: DerivativesField = Field(default_factory=DerivativesField)
    funding_rate: DerivativesField = Field(default_factory=DerivativesField)
    funding_class: FundingClass = FundingClass.NORMAL
    long_short_ratio: DerivativesField = Field(default_factory=DerivativesField)
    crowding: CrowdingStatus = CrowdingStatus.BALANCED
    liquidations_24h_usdt: DerivativesField = Field(default_factory=DerivativesField)
    taker_buy_volume_ratio: DerivativesField = Field(default_factory=DerivativesField)
    reason: str = ""


class TradePlan(BaseModel):
    """Pre-Trade Plan generated by Trade Plan Engine prior to Risk Engine evaluation."""
    setup_type: SetupType
    direction: TradeDirection
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    invalidation: float
    risk_reward: float
    is_valid: bool = True
    invalidation_reason: str = ""


class RiskAssessment(BaseModel):
    """Output of Risk Engine & Position Sizer evaluating TradePlan compliance."""
    decision: RiskDecision
    direction: TradeDirection
    entry_price: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    risk_reward: float = 0.0
    position_size_btc: float = 0.0
    position_size_usdt: float = 0.0
    risk_amount_usdt: float = 0.0
    risk_pct_used: float = 0.0
    rejection_reason: str = ""
    reason_code: RiskReasonCode = RiskReasonCode.OTHER
    guard_type: GuardType = GuardType.OTHER_RISK_CONTROL_BLOCK
    candidate_id: str = ""
    trade_plan: Optional[TradePlan] = None


class EntryQualityAssessment(BaseModel):
    decision: str
    reason_codes: List[str] = Field(default_factory=list)
    direction: TradeDirection
    entry_price: float
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    distance_to_support_pct: Optional[float] = None
    distance_to_resistance_pct: Optional[float] = None
    atr_extension_5m: Optional[float] = None
    atr_extension_15m: Optional[float] = None
    rsi_5m: Optional[float] = None
    rsi_15m: Optional[float] = None
    opposing_bos: bool = False
    opposing_choch: bool = False
    price_basis_deviation_pct: Optional[float] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class DataHealthResult(BaseModel):
    """Comprehensive data health status covering overall safety and source-level states."""
    overall_safety: DataSafetyStatus = DataSafetyStatus.SAFE
    source_health: Dict[str, SourceHealthStatus] = Field(default_factory=dict)
    details: Dict[str, str] = Field(default_factory=dict)
    reason: str = ""


class DecisionReport(BaseModel):
    """Master Decision Output logged every 5M cycle per Section 42."""
    timestamp: int
    evaluation_id: str = ""
    symbol: str = "BTC/USDT"
    price: float
    regime: MarketRegime
    regime_score: float
    confidence: str
    volatility: VolatilityLevel
    vol_percentile: float = 50.0
    atr_distance_atrs: float = 0.0
    current_rsi: float = 50.0
    structure_4h: StructureType
    structure_1h: StructureType
    location: LocationQuality
    setup: SetupType
    setup_direction: TradeDirection = TradeDirection.WAIT
    trigger_state: TriggerState
    derivatives: DerivativesStatus
    overextended_up: bool = False
    overextended_down: bool = False
    kill_switch_active: bool = False
    guard_type: GuardType = GuardType.OTHER_RISK_CONTROL_BLOCK
    global_context: GlobalContextStatus = GlobalContextStatus.UNAVAILABLE
    risk_status: RiskDecision
    final_decision: DecisionStatus
    reason: str
    trade_plan: Optional[TradePlan] = None
    risk_assessment: Optional[RiskAssessment] = None
    entry_quality_assessment: Optional[EntryQualityAssessment] = None
    setup_evidence: Dict[str, Any] = Field(default_factory=dict)


class TradeRecord(BaseModel):
    """Completed or active trade record for journaling and audit."""
    trade_id: str
    symbol: str = "BTC/USDT"
    setup_type: SetupType
    direction: TradeDirection
    entry_time: int
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    size_btc: float
    size_usdt: float
    pnl_usdt: float = 0.0
    pnl_pct: float = 0.0
    r_multiple: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    is_closed: bool = False
    fees_paid_usdt: float = 0.0
    # Phase 2A entry-context telemetry (observability only, never gates decisions)
    evaluation_id: str = ""
    candidate_id: str = ""
    entry_regime: str = ""
    entry_volatility: str = ""
    entry_vol_percentile: float = 0.0
    entry_overextended: str = "NONE"
    entry_atr_distance_atrs: float = 0.0
    entry_rsi: float = 0.0
