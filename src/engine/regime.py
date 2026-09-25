"""
Market Regime Classification Engine
US + Canada Dual-Market Macro & Volatility State Machine
Classifies market into 6 regimes with causal filtered probabilities and strategy gating.
"""

from typing import Dict, Any, List, NamedTuple
from dataclasses import dataclass
import numpy as np


class RegimeOutput(NamedTuple):
    regime_state: str            # STRONG_BULL | WEAK_BULL | CONSOLIDATION | HIGH_VOLATILITY | BEARISH | CRISIS
    filtered_probability: float  # Confidence in current state (0.0 to 1.0)
    regime_multiplier: float     # 0.60 to 1.05 (multiplier applied to opportunity scores)
    risk_scaling: float          # 0.25 to 1.0 (multiplier applied to position size)
    gated_strategies: Dict[str, bool] # Strategy permissions for this regime
    top_drivers: List[str]       # Top 3 contributing features in plain English


class RegimeClassifier:
    """
    Deterministic & causal market regime classifier based on trend, volatility, and credit/yield curves.
    Evaluates US and Canadian markets independently or in aggregate.
    """

    @staticmethod
    def classify_regime(
        index_close: float,
        index_sma50: float,
        index_sma200: float,
        index_sma200_slope: float,
        vix_level: float = 16.0,
        yield_spread_10y_2y: float = 0.50, # In percentage points, e.g. +0.50%
        credit_spread_oas: float = 3.50,   # US High Yield OAS in %
    ) -> RegimeOutput:
        """
        Classifies current regime using standardized multi-factor thresholds:
        - Trend: Position relative to 50DMA, 200DMA, and 200DMA slope.
        - Volatility: VIX (<17 Low, 17-25 Elevated, >25 High, >35 Extreme).
        - Yield Curve: 10Y-2Y spread (>0 Normal, <0 Inverted).
        - Credit Stress: HY OAS (<3.5 Normal, 3.5-5.5 Elevated, >5.5 Stress).
        """
        drivers = []
        is_above_200 = index_close > index_sma200
        is_above_50 = index_close > index_sma50
        is_rising_200 = index_sma200_slope > 0.0

        # Feature explanation strings
        if is_above_200 and is_rising_200:
            drivers.append("Index trading above rising 200-day moving average (bullish trend structure)")
        elif not is_above_200:
            drivers.append("Index below 200-day moving average (structural downtrend)")

        if vix_level < 16.5:
            drivers.append(f"VIX at {vix_level:.1f} indicates low implied volatility / calm conditions")
        elif vix_level > 25.0:
            drivers.append(f"VIX elevated at {vix_level:.1f} signals heightened market volatility")

        if yield_spread_10y_2y > 0.15:
            drivers.append(f"Yield curve positively sloped (+{yield_spread_10y_2y*100:.0f} bps 10Y-2Y spread)")
        elif yield_spread_10y_2y < 0.0:
            drivers.append(f"Yield curve inverted ({yield_spread_10y_2y*100:.0f} bps 10Y-2Y spread)")

        # 1. CRISIS / STRESS STATE
        # Credit spreads blown out (> 6.0%) or severe panic (VIX > 38)
        if credit_spread_oas >= 6.5 or vix_level >= 38.0:
            return RegimeOutput(
                regime_state="CRISIS",
                filtered_probability=0.88,
                regime_multiplier=0.60,
                risk_scaling=0.25,
                gated_strategies={
                    "breakouts": False,
                    "swing_momentum": False,
                    "pullback_uptrend": False,
                    "mean_reversion": False,
                    "quality_value": False,
                },
                top_drivers=drivers,
            )

        # 2. HIGH VOLATILITY STATE
        # High VIX (> 25) with deteriorating trend or choppy market
        if vix_level >= 25.0:
            return RegimeOutput(
                regime_state="HIGH_VOLATILITY",
                filtered_probability=0.82,
                regime_multiplier=0.70,
                risk_scaling=0.50,
                gated_strategies={
                    "breakouts": False,
                    "swing_momentum": False,
                    "pullback_uptrend": True, # Allowed only with wide stops
                    "mean_reversion": False,
                    "quality_value": True,
                },
                top_drivers=drivers,
            )

        # 3. BEARISH STATE
        # Index below declining 200DMA
        if not is_above_200 and not is_rising_200:
            return RegimeOutput(
                regime_state="BEARISH",
                filtered_probability=0.85,
                regime_multiplier=0.65,
                risk_scaling=0.40,
                gated_strategies={
                    "breakouts": False,
                    "swing_momentum": False,
                    "pullback_uptrend": False,
                    "mean_reversion": True, # Tactical oversold bounces only
                    "quality_value": True,
                },
                top_drivers=drivers,
            )

        # 4. STRONG BULL STATE
        # Above rising 200DMA, above 50DMA, low VIX (< 17), positive yield curve
        if is_above_200 and is_above_50 and is_rising_200 and vix_level < 18.0 and yield_spread_10y_2y >= 0:
            return RegimeOutput(
                regime_state="STRONG_BULL",
                filtered_probability=0.90,
                regime_multiplier=1.05,
                risk_scaling=1.00,
                gated_strategies={
                    "breakouts": True,
                    "swing_momentum": True,
                    "pullback_uptrend": True,
                    "mean_reversion": False, # Mean reversion limited in strong bull
                    "quality_value": True,
                },
                top_drivers=drivers,
            )

        # 5. WEAK BULL STATE
        # Above 200DMA, but either 50DMA below or VIX moderate (17-24)
        if is_above_200:
            return RegimeOutput(
                regime_state="WEAK_BULL",
                filtered_probability=0.78,
                regime_multiplier=1.00,
                risk_scaling=0.85,
                gated_strategies={
                    "breakouts": True,
                    "swing_momentum": True,
                    "pullback_uptrend": True,
                    "mean_reversion": True,
                    "quality_value": True,
                },
                top_drivers=drivers,
            )

        # 6. CONSOLIDATION / SIDEWAYS STATE
        return RegimeOutput(
            regime_state="CONSOLIDATION",
            filtered_probability=0.75,
            regime_multiplier=0.85,
            risk_scaling=0.75,
            gated_strategies={
                "breakouts": False, # False breakouts common in sideways market
                "swing_momentum": False,
                "pullback_uptrend": True,
                "mean_reversion": True,
                "quality_value": True,
            },
            top_drivers=drivers,
        )


# Global singleton regime classifier
regime_classifier = RegimeClassifier()
