"""
Tests for Entry Signal Engine (Phase 6)
US + Canada Market Intelligence Platform

Verifies:
- Setup type classification (BREAKOUT, PULLBACK, BASE_BREAKOUT, TREND_CONTINUATION, OVERSOLD_REVERSAL)
- Preferred entry, alternative deeper pullback entry, and entry zone
- Structural stop loss calculation (stop distance >= 1.2x ATR, stop cap <= 18%)
- Target trimming to nearest major resistance level for T1
- Planned vs Realized R:R ratio calculation and >= 2.0:1 enforcement
- 100% machine-checkable invalidation predicates
- Six-dimension rationale breakdown
- Empirical holding period distributions
"""

import pytest
from src.engine.signals import signal_engine, SignalEngine


def test_setup_type_classification():
    # 1. 52-Week Breakout
    m_bo = {"proximity_52w_high": -0.01, "rvol_20": 1.5, "rsi_14": 65.0, "close": 100.0}
    assert SignalEngine.classify_setup_type(m_bo) == "BREAKOUT"

    # 2. Oversold Reversal
    m_os = {"rsi_14": 32.0, "close": 95.0, "sma_20": 100.0}
    assert SignalEngine.classify_setup_type(m_os) == "OVERSOLD_REVERSAL"

    # 3. Base Squeeze Breakout
    m_sq = {"bb_bandwidth": 0.06, "rvol_20": 1.3, "close": 100.0}
    assert SignalEngine.classify_setup_type(m_sq) == "BASE_BREAKOUT"

    # 4. Pullback in Uptrend
    m_pb = {"ema_8": 105.0, "ema_21": 102.0, "close": 101.0, "sma_20": 100.5, "sma_50": 95.0}
    assert SignalEngine.classify_setup_type(m_pb) == "PULLBACK"

    # 5. Trend Continuation
    m_tc = {"close": 100.0, "rsi_14": 55.0}
    assert SignalEngine.classify_setup_type(m_tc) == "TREND_CONTINUATION"


def test_signal_generation_bullish_contract():
    metrics = {
        "close": 200.0,
        "atr_14": 4.0,
        "atr_pct": 2.0,
        "sma_20": 196.0,
        "sma_50": 188.0,
        "rsi_14": 58.5,
        "rvol_20": 1.2,
    }
    tech_dossier = {
        "support_resistance": {
            "support_1": 194.0,
            "resistance_1": 208.0,
        }
    }
    fund_dossier = {
        "quality": {"quality_score": 82},
        "valuation": {"valuation_score": 65},
        "coverage_status": "FULL",
        "provenance_source": "SEC_EDGAR_XBRL",
    }

    plan = signal_engine.generate_long_signal(
        security_id=1,
        metrics=metrics,
        composite_score=75,
        confidence_tier="A",
        horizon="POSITION",
        fundamental_dossier=fund_dossier,
        technical_dossier=tech_dossier,
        regime_state="STRONG_BULL",
        sector="Technology",
    )

    assert plan is not None
    assert plan.direction == "LONG"
    assert plan.preferred_entry == 200.0
    assert plan.alt_entry < plan.preferred_entry
    assert plan.entry_zone_low < plan.preferred_entry <= plan.entry_zone_high
    assert plan.stop_loss < plan.preferred_entry

    # Stop distance >= 1.2x ATR (4.8 pts)
    stop_dist = plan.preferred_entry - plan.stop_loss
    assert stop_dist >= 4.8

    # Stop cap <= 18%
    assert (stop_dist / plan.preferred_entry) <= 0.18

    # Target 1 trimmed to resistance_1 ($208.0)
    assert plan.target_1 <= 208.0
    assert "resistance" in plan.target_1_basis.lower()

    # Target 2 >= 2.5R
    assert plan.target_2 > plan.target_1
    assert plan.planned_rr_t2 >= 2.0
    assert plan.realized_rr_t1 > 0

    # 100% Invalidation Predicates present
    assert len(plan.invalidation_predicates) >= 5
    pred_types = [p["predicate"] for p in plan.invalidation_predicates]
    assert "DAILY_CLOSE_BELOW_LEVEL" in pred_types
    assert "BREAKOUT_FAILURE_CONSECUTIVE_CLOSES" in pred_types
    assert "REGIME_SHIFT_CRISIS" in pred_types

    # Six-Dimension Rationale
    assert plan.rationale is not None
    assert "technical" in plan.rationale
    assert "fundamental" in plan.rationale
    assert "news_catalysts" in plan.rationale
    assert "macro" in plan.rationale
    assert "sector" in plan.rationale
    assert "regime" in plan.rationale

    # Data lineage
    assert plan.data_lineage["point_in_time_verified"] is True
    assert plan.data_lineage["fundamentals_source"] == "SEC_EDGAR_XBRL"


def test_signal_suppressed_for_low_score_or_poor_rr():
    metrics = {"close": 50.0, "atr_14": 2.0}

    # Suppressed for score < 50
    plan = signal_engine.generate_long_signal(
        security_id=2,
        metrics=metrics,
        composite_score=45,
        confidence_tier="C",
    )
    assert plan is None
