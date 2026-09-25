"""
Unit and Integration Tests for News, SEC Filings & Catalysts Intelligence Engine
"""

import pytest
from src.engine.news_engine import news_engine, SEC_8K_ITEM_TAXONOMY, UNIVERSE_CIK_MAP
from src.models.schemas import CatalystEvent


def test_sec_8k_taxonomy_completeness():
    assert "2.02" in SEC_8K_ITEM_TAXONOMY
    assert SEC_8K_ITEM_TAXONOMY["2.02"]["category"] == "EARNINGS_ANNOUNCEMENT"
    assert SEC_8K_ITEM_TAXONOMY["2.02"]["materiality_level"] == "HIGH"
    assert SEC_8K_ITEM_TAXONOMY["4.02"]["hard_gate"] is True
    assert SEC_8K_ITEM_TAXONOMY["1.03"]["hard_gate"] is True


def test_sentiment_evaluation_bounds():
    bullish = news_engine.evaluate_sentiment("Record revenue and profit surge beat expectations", "Strong demand drove cash flow record")
    assert bullish["score"] > 0
    assert bullish["label"] in ("BULLISH", "LEAN_BULLISH")
    assert len(bullish["drivers"]) > 0

    bearish = news_engine.evaluate_sentiment("Missed estimates and lowered guidance", "Subpoena received regarding accounting irregularity")
    assert bearish["score"] < 0
    assert bearish["label"] in ("BEARISH", "LEAN_BEARISH")

    neutral = news_engine.evaluate_sentiment("Submission of matters to a vote", "Routine annual shareholder meeting disclosure")
    assert -0.25 <= neutral["score"] <= 0.25


def test_score_impact_bounds():
    pts_high = news_engine.compute_score_impact(5, 1.0, "2026-09-25")
    assert -12.0 <= pts_high <= 12.0
    pts_neg = news_engine.compute_score_impact(5, -1.0, "2026-09-25")
    assert -12.0 <= pts_neg <= 12.0


def test_macro_catalysts_generation():
    macro_events = news_engine.generate_macro_catalysts()
    assert len(macro_events) > 0
    for e in macro_events:
        assert isinstance(e, CatalystEvent)
        assert e.market == "MACRO"
        assert e.source == "BOC_VALET"
        assert e.materiality_score >= 3
        assert -1.0 <= e.sentiment_score <= 1.0


def test_compile_master_catalyst_feed():
    events = news_engine.compile_master_catalyst_feed()
    assert len(events) >= 10
    summary = news_engine.get_summary_metrics(events)
    assert summary["total_catalysts_analyzed"] == len(events)
    assert summary["high_materiality_count"] >= 1
    assert summary["source_breakdown"]["sec_edgar_filings"] > 0

    for e in events:
        assert isinstance(e, CatalystEvent)
        assert e.materiality_level in ("HIGH", "MEDIUM", "LOW")
        assert 1 <= e.materiality_score <= 5
        assert -1.0 <= e.sentiment_score <= 1.0
        assert e.horizon in ("IMMEDIATE", "SHORT_TERM", "MEDIUM_TERM", "STRUCTURAL")
        assert e.priced_in_status in ("FRESH", "PARTIAL", "PRICED_IN")
        assert -12.0 <= e.score_impact_pts <= 12.0
        assert e.source_url.startswith("http")
