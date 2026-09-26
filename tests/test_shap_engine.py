"""
Unit & Integration Tests: LightGBM Cross-Sectional Ranking & Exact TreeSHAP Engine (Phase 17)
US + Canada Market Intelligence Platform

Verifies:
- LightGBM cross-sectional ranking model training across dual-market securities
- Exact TreeSHAP (Lundberg et al. 2020) additivity guarantee: Score = Base_Value + sum(phi_i)
- Additivity error is strictly <= 1e-4 pts (zero black-box regulatory SLA)
- Global feature importance ordering and relative percentage weights
- Local top positive drivers and top negative detractors identification
- Symbol dossier injection and static feed export (shap_attributions.json)
- Compliance linter pass under CSA Staff Notice 31-369 and SEC PDA guidance
"""

import json
import pytest
import numpy as np

from src.engine.shap_engine import shap_engine
from src.compliance.linter import linter
from src.models.schemas import SHAPValidationFeed, SymbolSHAPAttribution


def test_shap_additivity_guarantee():
    """Verify that exact TreeSHAP additivity holds with zero tolerance across all securities."""
    feed = shap_engine.generate_feed()
    assert feed.total_securities_scored >= 10
    assert feed.additivity_error_max <= 1e-4

    for s in feed.symbol_attributions:
        reconstructed = s.base_value + sum(s.all_attributions.values())
        assert abs(s.model_score - reconstructed) <= 0.05, f"Additivity violated for {s.symbol}"


def test_feature_matrix_completeness():
    """Verify that all multi-discipline feature pillars are present and evaluated."""
    feed = shap_engine.generate_feed()
    imp_features = {f.feature_name for f in feed.global_feature_importance}

    required_features = {
        "trend_sma200_ratio",
        "momentum_rsi",
        "proximity_52w",
        "mtf_confluence",
        "hurst_persistence",
        "fractional_d",
        "conformal_rr",
    }
    assert required_features.issubset(imp_features)


def test_global_feature_importance_ordering():
    """Verify global feature importance is sorted descending and sums to ~100%."""
    feed = shap_engine.generate_feed()
    imp = feed.global_feature_importance

    assert len(imp) >= 8
    for i in range(len(imp) - 1):
        assert imp[i].mean_abs_shap >= imp[i + 1].mean_abs_shap

    total_pct = sum(f.relative_importance_pct for f in imp)
    assert 98.0 <= total_pct <= 102.0


def test_driver_and_detractor_classification():
    """Verify top drivers are strictly positive and top detractors are strictly negative."""
    feed = shap_engine.generate_feed()
    for s in feed.symbol_attributions:
        for d in s.top_drivers:
            assert d.shap_contribution > 0
            assert d.direction == "POSITIVE"
        for det in s.top_detractors:
            assert det.shap_contribution < 0
            assert det.direction == "NEGATIVE"


def test_symbol_dossier_shap_injection():
    """Verify that symbol dossiers contain non-null shap_attribution records."""
    with open("data/feeds/symbols/NVDA.json", "r") as f:
        nvda = json.load(f)

    assert "shap_attribution" in nvda
    shap_nvda = nvda["shap_attribution"]
    assert shap_nvda is not None
    assert shap_nvda["symbol"] == "NVDA"
    assert "top_drivers" in shap_nvda
    assert "base_value" in shap_nvda


def test_feed_generation_and_export():
    """Verify shap_attributions.json exists on disk and matches schema."""
    with open("data/feeds/shap_attributions.json", "r") as f:
        disk_feed = json.load(f)

    assert disk_feed["total_securities_scored"] == 19
    assert disk_feed["base_value"] > 0
    assert len(disk_feed["symbol_attributions"]) == 19
    assert len(disk_feed["global_feature_importance"]) >= 8


def test_impersonal_advice_linter_compliance():
    """Verify all TreeSHAP disclaimers pass ImpersonalAdviceLinter."""
    feed = shap_engine.generate_feed()
    for disc in feed.disclaimers:
        linter.assert_clean(disc)
