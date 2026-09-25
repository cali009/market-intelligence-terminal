"""
Tests for AI Explanation Engine & Reasoning Contract
Verifies FACT / CALCULATION / INFERENCE / UNCERTAINTY structural adherence and compliance.
"""

from src.engine.explanations import explanation_engine, ExplanationBlock


def test_deterministic_explanation_structure():
    sec_info = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "exchange": "NASDAQ",
        "currency": "USD",
    }
    metrics = {
        "close": 335.92,
        "rsi_14": 61.3,
        "rvol_20": 1.25,
        "atr_pct": 2.1,
        "proximity_52w_high": -0.027,
        "volume": 45000000,
        "sma_200": 305.0,
    }
    score_rec = {
        "composite_score": 67,
        "confidence_tier": "B",
        "technical_score": 72,
        "risk_penalty": 0.0,
    }

    block = explanation_engine.generate_deterministic_explanation(
        security_info=sec_info,
        metrics=metrics,
        score_rec=score_rec,
        regime_state="STRONG_BULL",
    )

    assert isinstance(block, ExplanationBlock)
    assert block.symbol == "AAPL"
    assert block.score == 67
    assert block.tier == "B"

    # Verify all four sections are populated
    assert len(block.fact) > 20
    assert len(block.calculation) > 20
    assert len(block.inference) > 20
    assert len(block.uncertainty) > 20

    # Verify key factual numbers exist in text
    assert "335.92" in block.fact
    assert "67/100" in block.calculation
    assert "STRONG_BULL" in block.calculation
    assert "risks" in block.uncertainty.lower()


def test_explanation_export_to_dict():
    sec_info = {"symbol": "RY", "name": "Royal Bank", "exchange": "TSX", "currency": "CAD"}
    metrics = {"close": 282.11, "rsi_14": 42.6, "rvol_20": 0.85, "atr_pct": 1.8, "volume": 2000000}
    score_rec = {"composite_score": 43, "confidence_tier": "C", "technical_score": 50, "risk_penalty": 4.0}

    block = explanation_engine.generate_deterministic_explanation(
        security_info=sec_info,
        metrics=metrics,
        score_rec=score_rec,
        regime_state="WEAK_BULL",
    )
    d = block.to_dict()
    assert "explanation" in d
    assert "FACT" in d["explanation"]
    assert "CALCULATION" in d["explanation"]
    assert "INFERENCE" in d["explanation"]
    assert "UNCERTAINTY" in d["explanation"]
