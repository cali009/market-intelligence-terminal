"""
Entry & Exit Signal Calculation Engine
US + Canada Market Intelligence Platform
Calculates multi-target entry zones, structural stops, and machine-checkable invalidation predicates.
"""

from typing import Dict, Any, Optional, List, NamedTuple
from dataclasses import dataclass
import json
from src.compliance.disclaimers import DISCLAIMER_VERSION


@dataclass
class SignalPlan:
    direction: str                     # LONG | NONE | EXIT
    entry_zone_low: float
    entry_zone_high: float
    preferred_entry: float
    alt_entry: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    risk_reward_ratio: float
    holding_period: str                # SWING | POSITION
    confidence_tier: str               # A | B | C | D
    strength: int                      # 0 to 100
    invalidation_predicates: List[Dict[str, Any]]
    risk_notes: List[str]
    disclaimer_version: str


class SignalEngine:
    """
    Computes deterministic execution parameters for validated setups.
    Enforces minimum Risk-to-Reward ratio (R:R >= 2.0:1) and machine-verifiable invalidation rules.
    """

    @staticmethod
    def generate_long_signal(
        security_id: int,
        metrics: Dict[str, Any],
        composite_score: int,
        confidence_tier: str,
        horizon: str = "POSITION",
    ) -> Optional[SignalPlan]:
        """
        Evaluate whether current technical posture warrants a research signal plan.
        Requires composite_score >= 50 and valid structural stop.
        """
        # Minimum score threshold to publish an actionable research plan
        if composite_score < 50:
            return None

        close = float(metrics.get("close", 0.0))
        atr = float(metrics.get("atr_14", close * 0.02))
        sma_20 = float(metrics.get("sma_20") or close)
        sma_50 = float(metrics.get("sma_50") or close)

        # 1. Entry Zone Calculation
        # Preferred entry is current close; entry zone accommodates 0.4x ATR
        preferred = round(close, 2)
        entry_low = round(max(preferred - (0.4 * atr), sma_20 * 0.99), 2)
        entry_high = round(preferred + (0.2 * atr), 2)
        alt_entry = round(sma_20, 2) if sma_20 < preferred else round(preferred * 0.985, 2)

        # 2. Structural Stop Loss
        # Swing stop: 1.5x ATR below entry or just below 20DMA
        # Position stop: 2.2x ATR below entry or just below 50DMA
        if horizon == "SWING":
            stop_candidate = min(preferred - (1.6 * atr), sma_20 - (0.5 * atr))
        else:
            stop_candidate = min(preferred - (2.2 * atr), sma_50 - (0.5 * atr))

        stop_loss = round(max(stop_candidate, preferred * 0.82), 2)
        risk_per_share = preferred - stop_loss
        if risk_per_share <= 0:
            return None

        # 3. Three Profit Targets (1.5R, 2.5R, 4.0R)
        t1 = round(preferred + (1.6 * risk_per_share), 2)
        t2 = round(preferred + (2.6 * risk_per_share), 2)
        t3 = round(preferred + (4.0 * risk_per_share), 2)

        rr_ratio = round((t2 - preferred) / risk_per_share, 2)
        if rr_ratio < 2.0:
            return None  # Enforce minimum 2.0:1 R:R gate

        # 4. Machine-Checkable Invalidation Predicates
        predicates = [
            {
                "predicate": "DAILY_CLOSE_BELOW_LEVEL",
                "level": stop_loss,
                "description": f"Daily closing price finishes below structural stop loss of {stop_loss}",
            },
            {
                "predicate": "RVOL_COLLAPSE",
                "threshold": 0.40,
                "description": "20-day Relative Volume drops below 0.40x indicating liquidity desertion",
            },
            {
                "predicate": "EARNINGS_ANNOUNCEMENT_EVENT",
                "buffer_sessions": 2,
                "description": "Filing within 2 trading sessions triggers event risk de-risking protocol",
            },
        ]

        # 5. Risk Notes
        atr_pct = metrics.get("atr_pct", 2.0)
        risk_notes = [
            f"Daily ATR volatility is {atr_pct:.1f}%; position sizing must calibrate to ${risk_per_share:.2f}/share risk.",
            f"Stop loss is positioned {((preferred-stop_loss)/preferred)*100:.1f}% below preferred entry price.",
        ]

        return SignalPlan(
            direction="LONG",
            entry_zone_low=entry_low,
            entry_zone_high=entry_high,
            preferred_entry=preferred,
            alt_entry=alt_entry,
            stop_loss=stop_loss,
            target_1=t1,
            target_2=t2,
            target_3=t3,
            risk_reward_ratio=rr_ratio,
            holding_period=horizon,
            confidence_tier=confidence_tier,
            strength=composite_score,
            invalidation_predicates=predicates,
            risk_notes=risk_notes,
            disclaimer_version=DISCLAIMER_VERSION,
        )


# Global singleton signal engine
signal_engine = SignalEngine()
