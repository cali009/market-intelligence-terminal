"""
Tests for Entry & Exit Signal Engine
Verifies entry zones, structural stop-loss calculation, R:R >= 2.0 gate, and invalidation predicates.
"""

from src.engine.signals import signal_engine


def test_signal_generation_bullish():
    metrics = {
        "close": 200.0,
        "atr_14": 4.0,
        "atr_pct": 2.0,
        "sma_20": 196.0,
        "sma_50": 188.0,
    }

    plan = signal_engine.generate_long_signal(
        security_id=1,
        metrics=metrics,
        composite_score=75,
        confidence_tier="A",
        horizon="POSITION",
    )

    assert plan is not None
    assert plan.direction == "LONG"
    assert plan.preferred_entry == 200.0
    assert plan.entry_zone_low < plan.preferred_entry
    assert plan.stop_loss < plan.preferred_entry
    assert plan.target_1 > plan.preferred_entry
    assert plan.target_2 > plan.target_1
    assert plan.risk_reward_ratio >= 2.0
    assert len(plan.invalidation_predicates) >= 2
    assert plan.disclaimer_version is not None


def test_signal_suppressed_for_low_score():
    metrics = {
        "close": 50.0,
        "atr_14": 2.0,
    }

    # Low opportunity score should suppress signal generation
    plan = signal_engine.generate_long_signal(
        security_id=2,
        metrics=metrics,
        composite_score=35,
        confidence_tier="C",
        horizon="POSITION",
    )

    assert plan is None
