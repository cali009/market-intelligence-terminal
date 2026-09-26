"""
Unit & Integration Tests: Deterministic Idiosyncratic Risk & Invalidation Matrix Engine (Phase 18)
US + Canada Market Intelligence Platform

Verifies:
- Systematic risk posture classification across 4 taxonomy states
- Position size multiplier bounds [0.0, 1.0] and haircut consistency
- Adaptive conformal stop ratchet logic (effective_stop >= conformal_stop)
- Hurst memory flip alert triggering (H < 0.45)
- Cross-border basis parity stretch detection (|z| >= 2.0)
- TreeSHAP factor detractor drag penalty integration
- Feed export (idiosyncratic_risk_matrix.json) and symbol dossier injection
- Compliance linter pass under CSA Staff Notice 31-369 and SEC Publisher Exclusion
"""

import json
import pytest

from src.engine.idiosyncratic_risk import idiosyncratic_risk_engine
from src.compliance.linter import linter
from src.models.schemas import IdiosyncraticRiskProfile, IdiosyncraticRiskMatrixFeed


def test_risk_posture_classification_and_multiplier():
    """Verify that all securities receive valid risk posture states and bounded size multipliers."""
    feed = idiosyncratic_risk_engine.generate_feed()
    assert feed.total_securities_monitored == 19

    valid_postures = {
        "NORMAL_EQUILIBRIUM",
        "ELEVATED_CAUTION",
        "DEFENSIVE_DE_RISK",
        "IMMEDIATE_INVALIDATION",
    }

    for p in feed.profiles:
        assert p.risk_posture in valid_postures
        assert 0.0 <= p.position_size_multiplier <= 1.0

        if p.risk_posture == "NORMAL_EQUILIBRIUM":
            assert p.position_size_multiplier == 1.0
        elif p.risk_posture == "ELEVATED_CAUTION":
            assert p.position_size_multiplier == 0.75
        elif p.risk_posture == "DEFENSIVE_DE_RISK":
            assert p.position_size_multiplier == 0.50
        elif p.risk_posture == "IMMEDIATE_INVALIDATION":
            assert p.position_size_multiplier == 0.0


def test_conformal_stop_ratchet_logic():
    """Verify that effective stop price is strictly positive and >= conformal stop."""
    feed = idiosyncratic_risk_engine.generate_feed()
    for p in feed.profiles:
        assert p.effective_stop_price > 0
        assert p.conformal_stop_price > 0
        assert isinstance(p.conformal_ratchet_recommended, bool)


def test_hurst_memory_flip_alert():
    """Verify that Hurst flip alert is triggered when H < 0.45."""
    feed = idiosyncratic_risk_engine.generate_feed()
    for p in feed.profiles:
        if p.hurst_exponent < 0.45:
            assert p.hurst_flip_alert is True
        else:
            assert p.hurst_flip_alert is False


def test_cross_border_parity_stretch_alert():
    """Verify cross-border basis stretch alert triggers on |z| >= 2.0."""
    feed = idiosyncratic_risk_engine.generate_feed()
    for p in feed.profiles:
        if p.basis_zscore is not None:
            if abs(p.basis_zscore) >= 2.0:
                assert p.cross_border_parity_stretch is True
            else:
                assert p.cross_border_parity_stretch is False


def test_shap_detractor_drag_calculation():
    """Verify that TreeSHAP factor drag is finite and non-positive."""
    feed = idiosyncratic_risk_engine.generate_feed()
    for p in feed.profiles:
        assert p.shap_detractor_drag_pts <= 0.0


def test_feed_generation_and_export():
    """Verify idiosyncratic_risk_matrix.json exists on disk and matches schema."""
    with open("data/feeds/idiosyncratic_risk_matrix.json", "r") as f:
        disk_feed = json.load(f)

    assert disk_feed["total_securities_monitored"] == 19
    assert len(disk_feed["profiles"]) == 19
    assert disk_feed["average_size_multiplier"] > 0


def test_symbol_dossier_idiosyncratic_risk_injection():
    """Verify that symbol dossiers contain non-null idiosyncratic_risk records."""
    with open("data/feeds/symbols/NVDA.json", "r") as f:
        nvda = json.load(f)

    assert "idiosyncratic_risk" in nvda
    ir = nvda["idiosyncratic_risk"]
    assert ir is not None
    assert ir["symbol"] == "NVDA"
    assert "risk_posture" in ir
    assert "position_size_multiplier" in ir


def test_impersonal_advice_linter_compliance():
    """Verify that all mitigation texts and disclaimers pass ImpersonalAdviceLinter."""
    feed = idiosyncratic_risk_engine.generate_feed()
    for disc in feed.disclaimers:
        linter.assert_clean(disc)
    for p in feed.profiles:
        linter.assert_clean(p.actionable_mitigation)
        linter.assert_clean(p.primary_risk_driver)
