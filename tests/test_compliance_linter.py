"""
Tests for Impersonal Advice Linter
Verifies compliance with CSA Staff Notice 31-369 and SEC Publisher Exclusion.
"""

import pytest
from src.compliance.linter import linter, LintViolation


def test_clean_impersonal_text_passes():
    compliant_text = (
        "The quantitative composite score for Royal Bank of Canada (RY.TO) is 82/100, "
        "ranking in the 94th percentile of the Canadian financial sector. "
        "The 14-day RSI is currently 54.2, and 20-day RVOL is 1.2x. "
        "The stock is consolidating within an ATR band of 1.8%."
    )
    violations = linter.lint_text(compliant_text)
    assert len(violations) == 0
    # Should not raise
    linter.assert_clean(compliant_text)


def test_catches_direct_instruction():
    violating_text = "The score is high, so you should buy RY.TO immediately at the open."
    violations = linter.lint_text(violating_text)
    assert len(violations) == 1
    assert violations[0].rule_category == "DIRECT_INSTRUCTION"
    assert "you should buy" in violations[0].matched_phrase.lower()


def test_catches_recommendation_and_guarantee():
    violating_text = (
        "We recommend buying Shopify stock today.\n"
        "It provides a guaranteed profit based on our proprietary neural net."
    )
    violations = linter.lint_text(violating_text)
    assert len(violations) == 2
    categories = [v.rule_category for v in violations]
    assert "RECOMMENDATION_CLAIM" in categories
    assert "PROMISSORY_CLAIM" in categories


def test_assert_clean_raises_value_error():
    violating_text = "Our advice is to allocate 25% of your portfolio to gold ETFs."
    with pytest.raises(ValueError) as exc_info:
        linter.assert_clean(violating_text, source_label="TestReport")
    assert "Compliance Linter Failed" in str(exc_info.value)
    assert "ADVICE_CLAIM" in str(exc_info.value)
