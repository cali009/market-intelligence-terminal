"""
Tests for AI Explanation Engine & Numeral Verification Gate (Phase 6)
US + Canada Market Intelligence Platform

Verifies:
- Strict FACT / CALCULATION / INFERENCE / UNCERTAINTY 4-layer contract
- Numeral-Verification Gate: auditing all numbers in INFERENCE & UNCERTAINTY
- Rejection of ungrounded/hallucinated numerals
- Compliance advice-language linter validation (CSA 31-369 & SEC Publisher Exclusion)
"""

from src.engine.explanations import explanation_engine, ExplanationBlock, ExplanationEngine
from src.compliance.linter import linter


def test_deterministic_explanation_and_numeral_verification():
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
        "fundamental_score": 75,
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
    assert block.numeral_verification_passed is True

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

    # Ensure clean compliance
    full_text = f"{block.fact}\n{block.calculation}\n{block.inference}\n{block.uncertainty}"
    violations = linter.lint_text(full_text)
    assert len(violations) == 0
    linter.assert_clean(full_text)


def test_numeral_verification_gate_detects_hallucinations():
    fact = "AAPL closed at $200.00 with volume 1000000."
    calc = "Score computed at 75/100."
    # Grounded inference with approved empirical constant 64%
    inf_clean = "Historical setups during bull regimes had positive forward expectancy across 64% of folds."
    unc_clean = "A close below 190.00 invalidates the thesis."

    passed, unauth = ExplanationEngine.verify_numerals(
        fact_text=fact,
        calc_text=calc,
        inference_text=inf_clean,
        uncertainty_text=unc_clean,
        known_inputs={"close": 200.0, "sma_200": 190.0},
    )
    assert passed is True
    assert len(unauth) == 0

    # Hallucinated inference with invented target price $999.99 and ungrounded 87.5% win rate
    inf_hallucinated = "This stock will surge to $999.99 with an 87.5% guaranteed win rate."
    passed_hallucinated, unauth_hallucinated = ExplanationEngine.verify_numerals(
        fact_text=fact,
        calc_text=calc,
        inference_text=inf_hallucinated,
        uncertainty_text=unc_clean,
        known_inputs={"close": 200.0, "sma_200": 190.0},
    )
    assert passed_hallucinated is False
    assert "999.99" in unauth_hallucinated
    assert "87.5" in unauth_hallucinated
