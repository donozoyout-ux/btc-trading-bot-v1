"""Trade Location Engine rating entry quality and filtering bad locations."""

from typing import List, Optional, Tuple
from core.models import ConfluenceZone, LocationResult, RegimeResult
from config.constants import LocationQuality, MarketRegime


class TradeLocationEngine:
    """
    Evaluates where current price sits relative to S/R Confluence Zones
    and macro regime overextension status. Prevents buying resistance or selling support.
    """

    def __init__(self, proximity_threshold_pct: float = 0.005):  # 0.50% proximity to zone
        self.proximity_threshold_pct = proximity_threshold_pct

    def find_nearest_zones(
        self, current_price: float, zones: List[ConfluenceZone]
    ) -> Tuple[Optional[ConfluenceZone], Optional[ConfluenceZone]]:
        """Finds closest support zone (below price) and resistance zone (above price)."""
        supports = [z for z in zones if z.center < current_price]
        resistances = [z for z in zones if z.center > current_price]

        nearest_support = max(supports, key=lambda z: z.center) if supports else None
        nearest_resistance = min(resistances, key=lambda z: z.center) if resistances else None

        return nearest_support, nearest_resistance

    def evaluate_location(
        self,
        current_price: float,
        zones: List[ConfluenceZone],
        regime_result: RegimeResult,
    ) -> LocationResult:
        """
        Calculates location quality and identifies BAD_LOCATION traps.
        """
        nearest_sup, nearest_res = self.find_nearest_zones(current_price, zones)

        dist_sup_pct = (
            (current_price - nearest_sup.center) / current_price if nearest_sup else 999.0
        )
        dist_res_pct = (
            (nearest_res.center - current_price) / current_price if nearest_res else 999.0
        )

        at_support = dist_sup_pct <= self.proximity_threshold_pct
        at_resistance = dist_res_pct <= self.proximity_threshold_pct

        quality = LocationQuality.NEUTRAL
        is_bad = False
        reason = ""

        # Section 21: Trap Check - Bad Long Location
        if at_resistance and regime_result.overextended_up:
            quality = LocationQuality.BAD_LOCATION
            is_bad = True
            reason = "BAD LONG LOCATION: Price at major resistance while overextended up."
        # Trap Check - Bad Short Location
        elif at_support and regime_result.overextended_down:
            quality = LocationQuality.BAD_LOCATION
            is_bad = True
            reason = "BAD SHORT LOCATION: Price at major support while overextended down."
        elif at_support:
            if nearest_sup and nearest_sup.strength >= 2:
                quality = LocationQuality.STRONG_LONG_LOCATION
                reason = f"STRONG LONG LOCATION: At confluence support with {nearest_sup.strength} levels ({', '.join(nearest_sup.sources[:3])})"
            else:
                quality = LocationQuality.GOOD_LONG_LOCATION
                reason = "GOOD LONG LOCATION: At support zone"
        elif at_resistance:
            if nearest_res and nearest_res.strength >= 2:
                quality = LocationQuality.STRONG_SHORT_LOCATION
                reason = f"STRONG SHORT LOCATION: At confluence resistance with {nearest_res.strength} levels ({', '.join(nearest_res.sources[:3])})"
            else:
                quality = LocationQuality.GOOD_SHORT_LOCATION
                reason = "GOOD SHORT LOCATION: At resistance zone"
        else:
            quality = LocationQuality.NEUTRAL
            reason = "NEUTRAL LOCATION: Price between key S/R zones"

        return LocationResult(
            quality=quality,
            current_price=current_price,
            nearest_support=nearest_sup,
            nearest_resistance=nearest_res,
            distance_to_support_pct=dist_sup_pct,
            distance_to_resistance_pct=dist_res_pct,
            is_bad_location=is_bad,
            reason=reason,
        )
