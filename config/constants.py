"""Constants and Enums for the BTC Trading Bot — Master Specification V2.1 FINAL."""

from enum import Enum


class MarketRegime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    RANGE = "RANGE"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


class PositionManagementState(str, Enum):
    HOLD = "HOLD"
    RECOVERY_WAIT = "RECOVERY_WAIT"
    PROTECT = "PROTECT"
    TIGHTEN_STOP = "TIGHTEN_STOP"
    TAKE_PARTIAL = "TAKE_PARTIAL"
    TARGET_REPLAN = "TARGET_REPLAN"
    EXIT_EARLY = "EXIT_EARLY"
    NO_CHANGE = "NO_CHANGE"


class ManagementProfile(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    TREND_RUNNER = "TREND_RUNNER"


class DecisionStatus(str, Enum):
    LONG_ENTRY = "LONG_ENTRY"
    SHORT_ENTRY = "SHORT_ENTRY"
    LONG_WATCH = "LONG_WATCH"
    SHORT_WATCH = "SHORT_WATCH"
    NO_TRADE = "NO_TRADE"


class SetupType(str, Enum):
    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    COUNTER_TREND_REACTION = "COUNTER_TREND_REACTION"
    NONE = "NONE"


class LocationQuality(str, Enum):
    STRONG_LONG_LOCATION = "STRONG_LONG_LOCATION"
    GOOD_LONG_LOCATION = "GOOD_LONG_LOCATION"
    NEUTRAL = "NEUTRAL"
    GOOD_SHORT_LOCATION = "GOOD_SHORT_LOCATION"
    STRONG_SHORT_LOCATION = "STRONG_SHORT_LOCATION"
    BAD_LOCATION = "BAD_LOCATION"


class VolatilityLevel(str, Enum):
    LOW = "LOW"            # 0 - 20 percentile
    NORMAL = "NORMAL"      # 20 - 80 percentile
    HIGH = "HIGH"          # 80 - 95 percentile
    EXTREME = "EXTREME"    # 95 - 100 percentile


class StructureType(str, Enum):
    BULLISH = "BULLISH"    # Higher High + Higher Low
    BEARISH = "BEARISH"    # Lower High + Lower Low
    MIXED = "MIXED"        # Inconsistent swings


class TriggerState(str, Enum):
    NO_SETUP = "NO_SETUP"
    WATCH = "WATCH"
    SETUP_DETECTED = "SETUP_DETECTED"
    WAITING_TRIGGER = "WAITING_TRIGGER"
    ENTRY_READY = "ENTRY_READY"
    IN_POSITION = "IN_POSITION"


class DerivativesStatus(str, Enum):
    CONFIRM = "CONFIRM"
    NEUTRAL = "NEUTRAL"
    WARN = "WARN"
    REJECT = "REJECT"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class GlobalContextStatus(str, Enum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"
    UNAVAILABLE = "UNAVAILABLE"


class SourceHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    OFFLINE = "OFFLINE"


class DataSafetyStatus(str, Enum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"


# Backward compatibility alias
DataHealthStatus = DataSafetyStatus


class DataSource(str, Enum):
    BINANCE = "BINANCE"
    COINGLASS = "COINGLASS"
    COINMARKETCAP = "COINMARKETCAP"
    SIMULATION = "SIMULATION"
    UNAVAILABLE = "UNAVAILABLE"


class FundingClass(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    EXTREME = "EXTREME"


class CrowdingStatus(str, Enum):
    LONG_CROWDING = "LONG_CROWDING"
    BALANCED = "BALANCED"
    SHORT_CROWDING = "SHORT_CROWDING"


class RiskDecision(str, Enum):
    ACCEPT_TRADE = "ACCEPT_TRADE"
    REJECT_TRADE = "REJECT_TRADE"


class GuardType(str, Enum):
    """Explicit risk-control guard categories — no overlap, no double-count."""
    DAILY_LOSS_GUARD = "DAILY_LOSS_GUARD"
    CONSECUTIVE_LOSS_GUARD = "CONSECUTIVE_LOSS_GUARD"
    EMERGENCY_LATCH = "EMERGENCY_LATCH"
    DATA_SAFETY_BLOCK = "DATA_SAFETY_BLOCK"
    POSITION_STATE_BLOCK = "POSITION_STATE_BLOCK"
    OTHER_RISK_CONTROL_BLOCK = "OTHER_RISK_CONTROL_BLOCK"


class RiskReasonCode(str, Enum):
    """Explicit rejection reason codes for full observability."""
    BAD_RISK_REWARD = "BAD_RISK_REWARD"
    INVALID_STOP = "INVALID_STOP"
    STOP_DISTANCE_TOO_SMALL = "STOP_DISTANCE_TOO_SMALL"
    STOP_DISTANCE_TOO_LARGE = "STOP_DISTANCE_TOO_LARGE"
    INVALID_POSITION_SIZE = "INVALID_POSITION_SIZE"
    MAX_EXPOSURE = "MAX_EXPOSURE"
    DAILY_LOSS_GUARD = "DAILY_LOSS_GUARD"
    CONSECUTIVE_LOSS_GUARD = "CONSECUTIVE_LOSS_GUARD"
    EMERGENCY_LATCH = "EMERGENCY_LATCH"
    VOLATILITY_REJECTION = "VOLATILITY_REJECTION"
    DATA_UNSAFE = "DATA_UNSAFE"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    DERIVATIVES_REJECTION = "DERIVATIVES_REJECTION"
    INVALID_TRADE_PLAN = "INVALID_TRADE_PLAN"
    OTHER = "OTHER"
