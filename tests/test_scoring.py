"""
Tests for 8-Factor Quantitative Scoring Engine
Verifies scoring formulas, risk deduction, horizon weighting, and structural gating.
"""

from src.engine.scoring import scoring_engine


def test_bullish_setup_scoring():
    bullish_metrics = {
        "close": 150.0,
        "sma_20": 147.0,
        "sma_50": 142.0,
        "sma_200": 130.0,
        "sma_200_slope_20d": 12.5,
        "adx_14": 28.0,
        "hh_hl_ratio_20": 0.65,
        "proximity_52w_high": -0.02,  # 2% from high
        "return_3m": 0.15,            # +15% in 3m
        "momentum_12_1": 0.22,
        "bb_bandwidth": 0.07,         # Squeeze
        "rsi_14": 58.0,               # Constructive
        "rvol_20": 1.6,               # High RVOL
        "obv_slope_20": 500000.0,
        "cmf_20": 0.12,
        "ema_8": 149.0,
        "ema_21": 146.0,
        "atr_pct": 2.2,               # Ideal volatility fit
        "data_confidence": "HIGH",
    }

    score_record = scoring_engine.evaluate_security(
        security_id=1,
        metrics=bullish_metrics,
        fundamental_score=75,
        regime_state="STRONG_BULL",
        regime_multiplier=1.05,
        news_contribution=4.0,
        sector_contribution=5.0,
        horizon="POSITION",
    )

    assert score_record.composite_score >= 75
    assert score_record.confidence_tier == "A"
    assert score_record.technical_score >= 70

    attribution = score_record.factor_attribution_json
    assert "trend_score" in attribution
    assert "risk_penalty" in attribution
    assert attribution["risk_penalty"] == 0.0  # Clean trend, no penalty


def test_bearish_breakdown_hard_gate():
    bearish_metrics = {
        "close": 85.0,
        "sma_20": 92.0,
        "sma_50": 105.0,
        "sma_200": 120.0,
        "sma_200_slope_20d": -15.0,   # Declining 200DMA
        "adx_14": 35.0,
        "hh_hl_ratio_20": 0.20,
        "proximity_52w_high": -0.42,  # Down 42% from highs
        "return_3m": -0.25,
        "momentum_12_1": -0.30,
        "bb_bandwidth": 0.25,
        "rsi_14": 32.0,
        "rvol_20": 2.1,
        "obv_slope_20": -800000.0,
        "cmf_20": -0.18,
        "ema_8": 87.0,
        "ema_21": 95.0,
        "atr_pct": 6.8,               # Wild volatility
        "data_confidence": "HIGH",
    }

    score_record = scoring_engine.evaluate_security(
        security_id=2,
        metrics=bearish_metrics,
        fundamental_score=30,
        regime_state="BEARISH",
        regime_multiplier=0.60,
        horizon="POSITION",
    )

    # Structural Gate: Below declining 200DMA and below 20DMA MUST cap score at 40
    assert score_record.composite_score <= 40
    assert score_record.risk_penalty > 10.0  # High risk penalty deducted
