"""Configurable hypotheses registry isolating all unverified strategy and risk numbers.

All values defined here are initial hypotheses for empirical backtesting and forward testing.
None of these values are production-locked.
"""

from typing import Dict, Any


INITIAL_HYPOTHESES: Dict[str, Any] = {
    # Candlestick trigger hypotheses (Section 28)
    "wick_rejection_ratio": 0.35,          # Min wick ratio for rejection candle (35%)
    "directional_body_ratio": 0.65,        # Min body ratio for directional marubozu (65%)
    
    # Volume hypotheses (Section 29)
    "volume_rvol_threshold": 1.50,         # Relative volume threshold for expansion
    "volume_spike_threshold": 2.00,        # RVOL spike threshold
    
    # Support / Resistance & Location hypotheses (Section 19, 20, 21)
    "sr_clustering_tolerance_pct": 0.004,  # S/R zone clustering proximity (0.40%)
    "location_proximity_pct": 0.005,       # Proximity threshold to S/R zone (0.50%)
    
    # Setup C & Momentum hypotheses (Section 24, 25, 26)
    "counter_trend_rsi_oversold": 30.0,    # 5M RSI threshold for mean-reversion long
    "counter_trend_rsi_overbought": 70.0,  # 5M RSI threshold for mean-reversion short
    "counter_trend_adx_veto": 35.0,        # 4H/1H ADX ceiling above which counter-trend is vetoed
    "bollinger_band_period": 20,           # BB period for Setup C
    "bollinger_band_std_dev": 2.0,         # BB standard deviation

    # Derivatives veto hypotheses. A veto requires all relevant real fields.
    "derivatives_oi_material_change_pct": 0.005,  # 0.50% OI expansion/contraction
    "derivatives_bearish_taker_ratio": 0.80,      # Taker buy/sell at or below = bearish participation
    "derivatives_bullish_taker_ratio": 1.20,      # Taker buy/sell at or above = bullish participation
    
    # Risk & Sizing hypotheses (Section 37, 38, 40)
    "min_risk_reward_ratio": 1.50,         # Minimum acceptable structural R:R filter
    "trend_risk_per_trade_pct": 0.0050,    # 0.50% account equity risk for trend trades
    "counter_trend_risk_pct": 0.0025,      # 0.25% account equity risk for counter-trend trades
    
    # Kill Switch hypotheses (Section 41)
    "max_daily_loss_pct": 0.020,           # 2.0% daily equity kill switch limit
    "max_consecutive_losses": 3,           # Consecutive loss kill switch limit
    "max_slippage_tolerance_pct": 0.0005,  # 0.05% execution slippage cap
    
    # Friction & Fees (data-driven)
    "taker_fee_pct": 0.0004,        # 0.04% default taker fee
    "maker_fee_pct": 0.0002,        # 0.02% default maker fee
    "slippage_pct": 0.0002,         # 0.02% default execution slippage

    # Experimental Exit Policies (Section 34, 36) - NOT production locked
    "exit_policy_tp1_close_pct": 0.50,     # Partial position exit fraction at TP1 (e.g. 50%)
    "exit_policy_auto_breakeven": True,    # Move stop loss to break-even after TP1
    "exit_policy_trailing_stop": False,    # Trailing stop policy toggle
}
