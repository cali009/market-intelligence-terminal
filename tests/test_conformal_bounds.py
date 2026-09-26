"""
Unit & Integration Tests: Adaptive Conformal Prediction (ACI) & Statistical Boundary Engine (Phase 14)
US + Canada Market Intelligence Platform

Verifies:
- Finite-sample marginal coverage calibration (~90.0% nominal target)
- Asymmetric tail non-conformity calibration (lower structural stop < price < upper exhaustion target)
- Adaptive Conformal Inference (ACI) dynamic alpha adaptation (Gibbs & Candès 2021)
- Volatility expansion regime warning threshold (>35% expansion)
- Risk-Reward ratio (R:R) computation from certified statistical bounds
- Feed export (conformal_bounds.json) and symbol dossier injection
- Impersonal advice linter compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import pytest
import numpy as np

from src.engine.conformal_bounds import adaptive_conformal_engine
from src.compliance.linter import linter
from src.models.schemas import AdaptiveConformalBounds


def test_conformal_coverage_guarantee():
    """DoD: Verify that realized empirical coverage calibrates within [85.0%, 95.0%] of nominal 90%."""
    feed = adaptive_conformal_engine.generate_conformal_bounds_feed()
    summary = feed["summary"]

    assert summary["nominal_coverage_target_pct"] == 90.0
    assert 85.0 <= summary["average_realized_coverage_pct"] <= 95.0
    assert summary["coverage_sla_met"] is True


def test_asymmetric_bounds_ordering():
    """Verify that lower stop < current price < upper target across all securities."""
    feed = adaptive_conformal_engine.generate_conformal_bounds_feed()
    bounds = feed["conformal_bounds"]

    assert len(bounds) >= 10
    for b in bounds:
        assert b["conformal_lower_stop"] < b["current_price"], f"Stop >= Price for {b['symbol']}"
        assert b["current_price"] < b["conformal_upper_target"], f"Price >= Target for {b['symbol']}"
        assert b["conformal_lower_stop"] <= b["conformal_median_path"] <= b["conformal_upper_target"]
        assert b["stop_distance_pct"] > 0
        assert b["target_distance_pct"] > 0


def test_adaptive_conformal_inference_step():
    """Verify ACI updates adapted alpha online within bounded interval [0.03, 0.25]."""
    closes = [100.0 * (1.002 ** i) for i in range(80)]
    highs = [p * 1.015 for p in closes]
    lows = [p * 0.985 for p in closes]

    bounds = adaptive_conformal_engine.calibrate_asymmetric_conformal_bounds(
        "TEST", closes, highs, lows
    )

    assert 0.03 <= bounds.adapted_alpha <= 0.25
    assert bounds.nominal_coverage_pct == 90.0
    assert bounds.invalidation_status == "BOUNDS_INTACT"


def test_volatility_expansion_detection():
    """Verify that sudden volatility surges trigger volatility_expansion_warning."""
    # 60 quiet bars followed by sudden high-volatility bars
    quiet_closes = [100.0 + (i * 0.05) for i in range(60)]
    quiet_highs = [p + 0.5 for p in quiet_closes]
    quiet_lows = [p - 0.5 for p in quiet_closes]

    # Add 10 explosive volatility bars
    shock_closes = [quiet_closes[-1] + (i * 2.5) for i in range(10)]
    shock_highs = [p + 8.0 for p in shock_closes]
    shock_lows = [p - 8.0 for p in shock_closes]

    closes = quiet_closes + shock_closes
    highs = quiet_highs + shock_highs
    lows = quiet_lows + shock_lows

    bounds = adaptive_conformal_engine.calibrate_asymmetric_conformal_bounds(
        "VOL_SHOCK", closes, highs, lows
    )

    assert bounds.volatility_expansion_warning is True


def test_conformal_risk_reward_calculation():
    """Verify that conformal R:R ratio is strictly positive and equals target_dist / stop_dist."""
    b = adaptive_conformal_engine.calibrate_bounds_for_symbol("AAPL")
    assert b.conformal_risk_reward_ratio > 0.0
    assert not np.isnan(b.conformal_risk_reward_ratio)

    expected_rr = round(b.target_distance_pct / max(0.1, b.stop_distance_pct), 2)
    assert abs(b.conformal_risk_reward_ratio - expected_rr) < 0.02


def test_conformal_feed_generation_and_export():
    """Verify conformal_bounds.json exists on disk and matches schema."""
    feed = adaptive_conformal_engine.generate_conformal_bounds_feed()
    assert "summary" in feed
    assert "conformal_bounds" in feed

    with open("data/feeds/conformal_bounds.json", "r") as f:
        disk_feed = json.load(f)

    assert len(disk_feed["conformal_bounds"]) == len(feed["conformal_bounds"])
    assert disk_feed["summary"]["total_securities_calibrated"] >= 10


def test_impersonal_advice_linter_compliance():
    """Verify that all conformal disclaimers pass ImpersonalAdviceLinter."""
    feed = adaptive_conformal_engine.generate_conformal_bounds_feed()
    for disc in feed.get("disclaimers", []):
        linter.assert_clean(disc)


def test_symbol_dossier_conformal_bounds_integration():
    """Verify that individual symbol dossiers contain certified conformal_bounds."""
    with open("data/feeds/symbols/NVDA.json", "r") as f:
        nvda = json.load(f)

    assert "conformal_bounds" in nvda
    cb = nvda["conformal_bounds"]
    assert cb["symbol"] == "NVDA"
    assert cb["conformal_lower_stop"] > 0
    assert cb["conformal_upper_target"] > cb["conformal_lower_stop"]
    assert cb["nominal_coverage_pct"] == 90.0
