"""
Unit & Integration Tests for Phase 23.2: Machine Learning Meta-Labeling Model & Trade Gating Engine
US + Canada Market Intelligence Platform
"""

import json
from pathlib import Path
import pytest
import numpy as np

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.meta_label_classifier import meta_label_classifier, DISCLAIMERS
from src.models.schemas import MetaLabelModelMetrics, MetaGatingVerdict


class TestMetaLabelClassifier:
    """Test suite covering meta-labeling training, calibrated probabilities, gating, and compliance."""

    def test_train_meta_classifier(self):
        """Validates ensemble model training, metrics schema, and sample counts."""
        metrics = meta_label_classifier.train_model(force_refresh=True)

        assert isinstance(metrics, MetaLabelModelMetrics)
        assert metrics.model_id == "ML_META_ENSEMBLE_V1"
        assert "Ensemble" in metrics.model_architecture
        assert metrics.total_training_samples == 944
        assert metrics.train_auc > 0.55
        assert 0.0 <= metrics.brier_score <= 1.0
        assert len(metrics.top_features) == 20

    def test_feature_importances_ranking(self):
        """Verifies feature importances sum to ~1.0 and directional impacts are valid."""
        metrics = meta_label_classifier.train_model()
        weights = [f.importance_weight for f in metrics.top_features]

        assert sum(weights) == pytest.approx(1.0, abs=0.05)
        for f in metrics.top_features:
            assert f.importance_weight >= 0.0
            assert f.directional_impact in ["POSITIVE", "NEGATIVE"]

        # Confirm critical factors are present in ranked features
        feat_names = {f.feature_name for f in metrics.top_features}
        assert "cmf_20" in feat_names
        assert "atr_pct" in feat_names
        assert "dist_sma200" in feat_names
        assert "hurst_exponent" in feat_names

    def test_predict_probability_clamped_bounds(self):
        """Verifies that predicted probabilities remain bounded within [0.05, 0.95]."""
        extreme_bullish = {name: 10.0 for name in meta_label_classifier.feature_names}
        p_bull = meta_label_classifier.predict_probability(extreme_bullish)
        assert 0.05 <= p_bull <= 0.95

        extreme_bearish = {name: -10.0 for name in meta_label_classifier.feature_names}
        p_bear = meta_label_classifier.predict_probability(extreme_bearish)
        assert 0.05 <= p_bear <= 0.95

    def test_evaluate_gating_actions_and_drivers(self):
        """Verifies trade gating decisions, size multipliers, and driver attribution string."""
        sample_features = {
            "cmf_20": 0.22,
            "sma200_slope": 2.5,
            "dist_sma50": 0.01,
            "hurst_exponent": 0.65,
            "quality_score": 90.0,
            "macro_regime_score": 1.0,
            "atr_pct": 0.025,
        }

        verdict = meta_label_classifier.evaluate_gating("NVDA", sample_features)
        assert isinstance(verdict, MetaGatingVerdict)
        assert verdict.symbol == "NVDA"
        assert 0.05 <= verdict.meta_probability <= 0.95
        assert verdict.action in ["HIGH_CONVICTION_PROCEED", "MODERATE_PROCEED", "CAUTION_THROTTLE", "GATED_EXCLUDE"]
        assert 0.0 <= verdict.size_multiplier <= 1.0
        assert "impact" in verdict.primary_driver

    def test_feed_export_and_content(self, tmp_path):
        """Verifies that exporting feed produces valid JSON matching Pydantic schema."""
        feed_file = meta_label_classifier.export_feed(tmp_path)
        assert feed_file.exists()

        with open(feed_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "metrics" in data
        assert "disclaimers" in data
        assert data["metrics"]["model_id"] == "ML_META_ENSEMBLE_V1"
        assert len(data["metrics"]["top_features"]) == 20
        assert len(data["disclaimers"]) >= 3

    def test_disclaimers_compliance_clean(self):
        """Asserts zero promissory or regulatory violations in disclaimers."""
        for disc in DISCLAIMERS:
            linter.assert_clean(disc)
            assert "guarantee" not in disc.lower() or "do not guarantee" in disc.lower()
