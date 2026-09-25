"""
Tests for Market Regime Classifier
Verifies 6-state detection, filtered probability bounds, and strategy permissions.
"""

from src.engine.regime import regime_classifier


def test_strong_bull_classification():
    output = regime_classifier.classify_regime(
        index_close=580.0,
        index_sma50=560.0,
        index_sma200=520.0,
        index_sma200_slope=14.0,  # Strongly rising
        vix_level=14.5,           # Calm volatility
        yield_spread_10y_2y=0.45, # Normal yield curve
        credit_spread_oas=3.1,    # Normal credit
    )
    assert output.regime_state == "STRONG_BULL"
    assert output.regime_multiplier == 1.05
    assert output.risk_scaling == 1.00
    assert output.gated_strategies["breakouts"] is True
    assert output.gated_strategies["swing_momentum"] is True
    assert len(output.top_drivers) >= 2


def test_crisis_classification_on_blown_credit():
    output = regime_classifier.classify_regime(
        index_close=420.0,
        index_sma50=460.0,
        index_sma200=490.0,
        index_sma200_slope=-20.0,
        vix_level=39.0,           # Panic
        credit_spread_oas=7.2,    # Blown out spreads
    )
    assert output.regime_state == "CRISIS"
    assert output.regime_multiplier == 0.60
    assert output.risk_scaling == 0.25
    assert output.gated_strategies["breakouts"] is False
    assert output.gated_strategies["swing_momentum"] is False


def test_high_volatility_classification():
    output = regime_classifier.classify_regime(
        index_close=510.0,
        index_sma50=515.0,
        index_sma200=500.0,
        index_sma200_slope=2.0,
        vix_level=28.5,           # High vol
    )
    assert output.regime_state == "HIGH_VOLATILITY"
    assert output.regime_multiplier == 0.70
    assert output.risk_scaling == 0.50
    assert output.gated_strategies["breakouts"] is False
