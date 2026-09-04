"""Support and Resistance Confluence Engine clustering multi-timeframe levels and Fibs."""

from typing import List, Dict, Optional, Tuple
from core.models import Candle, SwingPoint, ConfluenceZone
from datetime import datetime, timezone


class SupportResistanceEngine:
    """
    Identifies key horizontal levels across 4H, 1H, 15M,
    Previous Day/Week Highs and Lows (PDH/PDL, PWH/PWL),
    Fibonacci retracements (0.382, 0.50, 0.618, 0.786),
    and clusters them into Confluence Zones.
    """

    FIB_LEVELS = [0.382, 0.50, 0.618, 0.786]

    def __init__(self, cluster_tolerance_pct: float = 0.004):  # 0.40% zone width tolerance
        self.cluster_tolerance_pct = cluster_tolerance_pct

    def extract_day_and_week_levels(
        self, candles_1h: List[Candle]
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        Extracts Previous Day High (PDH), Previous Day Low (PDL),
        Previous Week High (PWH), and Previous Week Low (PWL).
        """
        if len(candles_1h) < 24:
            return None, None, None, None

        # Group by UTC day and week
        days_map: Dict[str, List[Candle]] = {}
        weeks_map: Dict[str, List[Candle]] = {}

        for c in candles_1h:
            dt = c.dt
            day_str = dt.strftime("%Y-%m-%d")
            week_str = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"

            days_map.setdefault(day_str, []).append(c)
            weeks_map.setdefault(week_str, []).append(c)

        sorted_days = sorted(days_map.keys())
        pdh, pdl = None, None
        if len(sorted_days) >= 2:
            prev_day_candles = days_map[sorted_days[-2]]
            pdh = max(c.high for c in prev_day_candles)
            pdl = min(c.low for c in prev_day_candles)

        sorted_weeks = sorted(weeks_map.keys())
        pwh, pwl = None, None
        if len(sorted_weeks) >= 2:
            prev_week_candles = weeks_map[sorted_weeks[-2]]
            pwh = max(c.high for c in prev_week_candles)
            pwl = min(c.low for c in prev_week_candles)

        return pdh, pdl, pwh, pwl

    def calculate_fibonacci_levels(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]
    ) -> List[Tuple[float, str]]:
        """
        Calculates Fib levels between the most recent major swing high and swing low.
        Returns list of (price, label).
        """
        levels: List[Tuple[float, str]] = []
        if not swing_highs or not swing_lows:
            return levels

        last_sh = swing_highs[-1]
        last_sl = swing_lows[-1]

        high = max(last_sh.price, last_sl.price)
        low = min(last_sh.price, last_sl.price)
        diff = high - low

        if diff <= 0:
            return levels

        # If swing low happened after swing high -> retracement upwards
        # If swing high happened after swing low -> retracement downwards
        if last_sh.swing_time < last_sl.swing_time:
            # Bearish leg: price dropped from high to low; fibs are resistance on bounce
            for fib in self.FIB_LEVELS:
                price = low + diff * fib
                levels.append((price, f"Fib {fib:.3f}"))
        else:
            # Bullish leg: price rallied from low to high; fibs are support on pullback
            for fib in self.FIB_LEVELS:
                price = high - diff * fib
                levels.append((price, f"Fib {fib:.3f}"))

        return levels

    def cluster_levels_into_zones(
        self, raw_levels: List[Tuple[float, str]], current_price: float
    ) -> List[ConfluenceZone]:
        """
        Clusters nearby price levels within cluster_tolerance_pct into Confluence Zones.
        """
        if not raw_levels:
            return []

        # Sort raw levels by price
        sorted_levels = sorted(raw_levels, key=lambda x: x[0])

        clusters: List[List[Tuple[float, str]]] = []
        current_cluster: List[Tuple[float, str]] = [sorted_levels[0]]

        for i in range(1, len(sorted_levels)):
            price, label = sorted_levels[i]
            cluster_center = sum(p for p, _ in current_cluster) / len(current_cluster)

            if abs(price - cluster_center) / cluster_center <= self.cluster_tolerance_pct:
                current_cluster.append((price, label))
            else:
                clusters.append(current_cluster)
                current_cluster = [(price, label)]

        if current_cluster:
            clusters.append(current_cluster)

        zones: List[ConfluenceZone] = []
        for cl in clusters:
            prices = [p for p, _ in cl]
            sources = [s for _, s in cl]
            min_p = min(prices)
            max_p = max(prices)
            center = sum(prices) / len(prices)

            level_type = "SUPPORT" if center < current_price else "RESISTANCE"

            zones.append(
                ConfluenceZone(
                    level_type=level_type,
                    price_min=min_p,
                    price_max=max_p,
                    center=center,
                    strength=len(cl),
                    sources=sources,
                )
            )

        return zones

    def evaluate_sr_zones(
        self,
        current_price: float,
        swings_4h: Tuple[List[SwingPoint], List[SwingPoint]],
        swings_1h: Tuple[List[SwingPoint], List[SwingPoint]],
        swings_15m: Tuple[List[SwingPoint], List[SwingPoint]],
        candles_1h: List[Candle],
    ) -> List[ConfluenceZone]:
        """
        Builds raw level list from all multi-timeframe sources and clusters them.
        """
        raw_levels: List[Tuple[float, str]] = []

        # 4H Swings
        sh_4h, sl_4h = swings_4h
        for sh in sh_4h[-4:]:
            raw_levels.append((sh.price, "4H Swing High"))
        for sl in sl_4h[-4:]:
            raw_levels.append((sl.price, "4H Swing Low"))

        # 1H Swings
        sh_1h, sl_1h = swings_1h
        for sh in sh_1h[-6:]:
            raw_levels.append((sh.price, "1H Swing High"))
        for sl in sl_1h[-6:]:
            raw_levels.append((sl.price, "1H Swing Low"))

        # 15M Swings
        sh_15m, sl_15m = swings_15m
        for sh in sh_15m[-6:]:
            raw_levels.append((sh.price, "15M Swing High"))
        for sl in sl_15m[-6:]:
            raw_levels.append((sl.price, "15M Swing Low"))

        # PDH / PDL / PWH / PWL
        pdh, pdl, pwh, pwl = self.extract_day_and_week_levels(candles_1h)
        if pdh is not None:
            raw_levels.append((pdh, "PDH (Prev Day High)"))
        if pdl is not None:
            raw_levels.append((pdl, "PDL (Prev Day Low)"))
        if pwh is not None:
            raw_levels.append((pwh, "PWH (Prev Week High)"))
        if pwl is not None:
            raw_levels.append((pwl, "PWL (Prev Week Low)"))

        # Fibonacci from 1H Swings
        fib_levels = self.calculate_fibonacci_levels(sh_1h, sl_1h)
        raw_levels.extend(fib_levels)

        # Cluster into zones
        return self.cluster_levels_into_zones(raw_levels, current_price)
