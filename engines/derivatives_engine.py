"""Derivatives Engine analyzing Open Interest, Funding Rate, Long/Short Crowding, and Liquidations."""

from typing import Dict, Any, Optional
from core.models import DerivativesState, DerivativesField
from config.constants import (
    DerivativesStatus,
    FundingClass,
    CrowdingStatus,
    TradeDirection,
    SetupType,
    DataSource,
)


class DerivativesEngine:
    """
    Evaluates derivatives market context from Binance Futures and CoinGlass.
    Strictly distinguishes UNAVAILABLE (data missing) from NEUTRAL (balanced data).
    Tracks field-level provenance metadata.
    """

    FUNDING_ELEVATED_POS = 0.0002   # +0.02%
    FUNDING_EXTREME_POS = 0.0005    # +0.05%
    FUNDING_ELEVATED_NEG = -0.0002  # -0.02%
    FUNDING_EXTREME_NEG = -0.0005   # -0.05%

    CROWDING_LONG_THRESHOLD = 2.20
    CROWDING_SHORT_THRESHOLD = 0.75

    def __init__(self, oi_material_change_pct: float = 0.005, bearish_taker_ratio: float = 0.80, bullish_taker_ratio: float = 1.20):
        self.oi_material_change_pct = oi_material_change_pct
        self.bearish_taker_ratio = bearish_taker_ratio
        self.bullish_taker_ratio = bullish_taker_ratio

    def classify_funding(self, funding_rate: Optional[float]) -> FundingClass:
        """Categorizes funding rate into NORMAL, ELEVATED, EXTREME."""
        if funding_rate is None:
            return FundingClass.NORMAL
        if funding_rate >= self.FUNDING_EXTREME_POS or funding_rate <= self.FUNDING_EXTREME_NEG:
            return FundingClass.EXTREME
        elif funding_rate >= self.FUNDING_ELEVATED_POS or funding_rate <= self.FUNDING_ELEVATED_NEG:
            return FundingClass.ELEVATED
        return FundingClass.NORMAL

    def classify_crowding(self, long_short_ratio: Optional[float]) -> CrowdingStatus:
        """Categorizes account long-short ratio."""
        if long_short_ratio is None:
            return CrowdingStatus.BALANCED
        if long_short_ratio >= self.CROWDING_LONG_THRESHOLD:
            return CrowdingStatus.LONG_CROWDING
        elif long_short_ratio <= self.CROWDING_SHORT_THRESHOLD:
            return CrowdingStatus.SHORT_CROWDING
        return CrowdingStatus.BALANCED

    def evaluate_derivatives(
        self,
        candidate_direction: TradeDirection,
        setup_type: SetupType,
        price_change_pct: float,
        oi_field: Optional[DerivativesField] = None,
        oi_change_field: Optional[DerivativesField] = None,
        funding_field: Optional[DerivativesField] = None,
        ls_field: Optional[DerivativesField] = None,
        taker_field: Optional[DerivativesField] = None,
        liquidation_field: Optional[DerivativesField] = None,
    ) -> DerivativesState:
        """
        Synthesizes derivatives signals without synthesizing mock numbers.
        If all sources are unavailable, returns status=UNAVAILABLE.
        If partial sources are available, returns status=DEGRADED or normal interpretation.
        """
        # Ensure default field containers if None
        oi_f = oi_field or DerivativesField()
        oi_ch_f = oi_change_field or DerivativesField()
        funding_f = funding_field or DerivativesField()
        ls_f = ls_field or DerivativesField()
        taker_f = taker_field or DerivativesField()
        liq_f = liquidation_field or DerivativesField()

        available_sources = [
            f.source for f in [oi_f, funding_f, ls_f, taker_f, liq_f]
            if f.source != DataSource.UNAVAILABLE and f.value is not None
        ]

        if not available_sources:
            return DerivativesState(
                status=DerivativesStatus.UNAVAILABLE,
                open_interest=oi_f,
                oi_change_pct=oi_ch_f,
                funding_rate=funding_f,
                funding_class=FundingClass.NORMAL,
                long_short_ratio=ls_f,
                crowding=CrowdingStatus.BALANCED,
                liquidations_24h_usdt=liq_f,
                taker_buy_volume_ratio=taker_f,
                reason="All derivatives data sources unavailable",
            )

        funding_val = funding_f.value
        funding_class = self.classify_funding(funding_val)
        crowding = self.classify_crowding(ls_f.value)

        # Evaluate signals
        reasons = []
        is_degraded = len(available_sources) < 3
        status = DerivativesStatus.DEGRADED if is_degraded else DerivativesStatus.NEUTRAL

        oi_ch_val = oi_ch_f.value
        is_oi_up = oi_ch_val is not None and oi_ch_val > self.oi_material_change_pct
        is_oi_down = oi_ch_val is not None and oi_ch_val < -self.oi_material_change_pct
        is_price_up = price_change_pct > 0
        is_price_down = price_change_pct < 0

        if candidate_direction == TradeDirection.LONG:
            if funding_class == FundingClass.EXTREME and funding_val and funding_val > 0 and crowding == CrowdingStatus.LONG_CROWDING:
                if taker_f.value is not None and taker_f.value <= self.bearish_taker_ratio:
                    status = DerivativesStatus.REJECT
                    reasons.append("Extreme positive funding and heavy long crowding with bearish taker participation")
                else:
                    status = DerivativesStatus.WARN
                    reasons.append("Extreme positive funding with heavy long crowding")

            if status != DerivativesStatus.REJECT and setup_type == SetupType.COUNTER_TREND_REACTION:
                if is_price_down and is_oi_up and taker_f.value is not None and taker_f.value <= self.bearish_taker_ratio:
                    status = DerivativesStatus.REJECT
                    reasons.append("Counter-trend long veto: falling price, expanding OI and bearish taker participation")
                elif is_price_down and is_oi_down:
                    status = DerivativesStatus.CONFIRM
                    reasons.append("Exhaustion flush: Price down with OI decreasing")
                elif is_price_down and is_oi_up:
                    status = DerivativesStatus.WARN
                    reasons.append("Caution: Price falling with aggressive new short OI")
            elif status != DerivativesStatus.REJECT and setup_type == SetupType.TREND_PULLBACK:
                if is_price_up and is_oi_up:
                    status = DerivativesStatus.CONFIRM
                    reasons.append("Bullish trend participation: Price and OI rising")
                elif taker_f.value is not None and taker_f.value >= self.bullish_taker_ratio:
                    status = DerivativesStatus.CONFIRM
                    reasons.append(f"Taker buy pressure dominant: {taker_f.value:.2f}")

        elif candidate_direction == TradeDirection.SHORT:
            if funding_class == FundingClass.EXTREME and funding_val and funding_val < 0 and crowding == CrowdingStatus.SHORT_CROWDING:
                if taker_f.value is not None and taker_f.value >= self.bullish_taker_ratio:
                    status = DerivativesStatus.REJECT
                    reasons.append("Extreme negative funding and heavy short crowding with bullish taker participation")
                else:
                    status = DerivativesStatus.WARN
                    reasons.append("Extreme negative funding with heavy short crowding")

            if status != DerivativesStatus.REJECT and setup_type == SetupType.COUNTER_TREND_REACTION:
                if is_price_up and is_oi_up and taker_f.value is not None and taker_f.value >= self.bullish_taker_ratio:
                    status = DerivativesStatus.REJECT
                    reasons.append("Counter-trend short veto: rising price, expanding OI and bullish taker participation")
            elif status != DerivativesStatus.REJECT and setup_type == SetupType.TREND_PULLBACK:
                if is_price_down and is_oi_up:
                    status = DerivativesStatus.CONFIRM
                    reasons.append("Bearish participation: Price down with OI rising")
                elif taker_f.value is not None and taker_f.value <= self.bearish_taker_ratio:
                    status = DerivativesStatus.CONFIRM
                    reasons.append(f"Taker sell pressure dominant: {taker_f.value:.2f}")

        reason_str = "; ".join(reasons) if reasons else ("Partial derivatives available" if is_degraded else "Derivatives normal")

        return DerivativesState(
            status=status,
            open_interest=oi_f,
            oi_change_pct=oi_ch_f,
            funding_rate=funding_f,
            funding_class=funding_class,
            long_short_ratio=ls_f,
            crowding=crowding,
            liquidations_24h_usdt=liq_f,
            taker_buy_volume_ratio=taker_f,
            reason=reason_str,
        )
