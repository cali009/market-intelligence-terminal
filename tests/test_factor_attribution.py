"""
Unit and Integration Tests for Active Style Tilts & Factor Return Attribution Engine (Phase 27.2)
US + Canada Dual-Market Intelligence Platform
"""

import json
from pathlib import Path
import numpy as np
import pytest

from src.compliance.linter import linter
from src.engine.factor_attribution import (
    factor_attribution_engine,
    FACTOR_ATTRIBUTION_DISCLAIMERS,
)
from src.engine.factor_risk import FACTOR_KEYS


class TestFactorAttributionEngine:
    """Test suite validating Brinson-Barra PnL attribution, active factor tilts, and tracking error."""

    @pytest.fixture(scope="class", autouse=True)
    def setup_attribution_engine(self):
        type(self).result = factor_attribution_engine.evaluate_attribution(
            total_capital=100000.0, force_refresh=True
        )

    def test_all_three_benchmarks_evaluated(self):
        """Asserts blended, US, and Canadian benchmarks are computed with finite metrics."""
        benchmarks = self.result.benchmarks
        assert len(benchmarks) == 3
        expected_keys = ["BLENDED_CROSS_BORDER", "US_SP500_CORE", "CA_TSX60_CORE"]
        for k in expected_keys:
            assert k in benchmarks, f"Missing benchmark attribution for {k}"
            b = benchmarks[k]
            assert np.isfinite(b.portfolio_return_pct)
            assert np.isfinite(b.benchmark_return_pct)
            assert np.isfinite(b.active_return_pct)
            assert np.isfinite(b.tracking_error_pct)
            assert np.isfinite(b.information_ratio)
            assert np.isfinite(b.factor_return_contribution_pct)
            assert np.isfinite(b.specific_alpha_pct)

    def test_active_return_arithmetic_decomposition_exact(self):
        """Asserts Active Return equals Factor Contribution + Specific Alpha across all benchmarks."""
        for b_key, b in self.result.benchmarks.items():
            active_ret = b.active_return_pct
            factor_contrib = b.factor_return_contribution_pct
            spec_alpha = b.specific_alpha_pct
            reconstructed_active = factor_contrib + spec_alpha

            assert np.isclose(active_ret, reconstructed_active, atol=0.05), (
                f"Active return decomposition failed for {b_key}: {active_ret}% != {factor_contrib}% + {spec_alpha}%"
            )

    def test_self_benchmark_attribution_zero_active_return_and_tilts(self):
        """Asserts that evaluating portfolio against itself yields zero active return, tilts, and tracking error."""
        # Run attribution against w_b == w_p
        symbols = ["AAPL", "SPY", "XIU"]
        w_p = np.array([0.3, 0.4, 0.3])
        w_b = np.array([0.3, 0.4, 0.3])
        B = np.random.randn(3, 10)

        # Mock daily return data
        ret_df = np.random.randn(50, 3) * 0.01
        import pandas as pd
        ret_df_pd = pd.DataFrame(ret_df, columns=symbols)
        factors_df_pd = pd.DataFrame(np.random.randn(50, 10) * 0.01, columns=FACTOR_KEYS)

        self_attrib = factor_attribution_engine.compute_single_benchmark_attribution(
            benchmark_name="SELF_TEST",
            w_p=w_p,
            w_b=w_b,
            B=B,
            ret_df=ret_df_pd,
            factors_df=factors_df_pd,
        )

        assert np.isclose(self_attrib.active_return_pct, 0.0, atol=1e-4)
        assert np.isclose(self_attrib.tracking_error_pct, 0.0, atol=1e-4)
        assert np.isclose(self_attrib.factor_return_contribution_pct, 0.0, atol=1e-4)
        assert np.isclose(self_attrib.specific_alpha_pct, 0.0, atol=1e-4)

        for tilt in self_attrib.factor_tilts.values():
            assert np.isclose(tilt.active_tilt, 0.0, atol=1e-4)
            assert tilt.tilt_direction == "NEUTRAL"
            assert np.isclose(tilt.pnl_contribution_pct, 0.0, atol=1e-4)

    def test_all_ten_factors_present_in_tilts(self):
        """Asserts all 10 factor tilts are properly populated and directional classifications are consistent."""
        primary_bench = self.result.benchmarks["BLENDED_CROSS_BORDER"]
        tilts = primary_bench.factor_tilts
        assert len(tilts) == 10

        for k in FACTOR_KEYS:
            assert k in tilts, f"Missing factor tilt {k}"
            t = tilts[k]
            assert t.factor_key == k
            # Directional consistency check
            if t.active_tilt >= 0.05:
                assert t.tilt_direction == "OVERWEIGHT"
            elif t.active_tilt <= -0.05:
                assert t.tilt_direction == "UNDERWEIGHT"
            else:
                assert t.tilt_direction == "NEUTRAL"

    def test_tracking_error_and_information_ratio_monotonicity(self):
        """Asserts tracking error is non-negative and Information Ratio equals active return / tracking error."""
        for b_key, b in self.result.benchmarks.items():
            assert b.tracking_error_pct > 0.0, f"Tracking error must be positive for {b_key}"
            expected_ir = round(b.active_return_pct / b.tracking_error_pct, 2)
            assert np.isclose(b.information_ratio, expected_ir, atol=0.05)

    def test_unintentional_biases_detection(self):
        """Asserts that active factor tilts with magnitude >= 0.15 are detected as unintentional biases."""
        primary = self.result.benchmarks["BLENDED_CROSS_BORDER"]
        biases = self.result.unintentional_biases
        bias_keys = {b["factor_key"] for b in biases}

        for factor_key, tilt in primary.factor_tilts.items():
            if abs(tilt.active_tilt) >= 0.15:
                assert factor_key in bias_keys, f"Expected {factor_key} with tilt {tilt.active_tilt} in biases"
            else:
                assert factor_key not in bias_keys, f"Unexpected {factor_key} with tilt {tilt.active_tilt} in biases"

    def test_read_only_array_resilience(self):
        """Asserts compute_single_benchmark_attribution handles read-only numpy arrays without throwing ValueError."""
        symbols = ["AAPL", "SPY"]
        w_p = np.array([0.5, 0.5])
        w_p.flags.writeable = False

        w_b = np.array([0.0, 1.0])
        w_b.flags.writeable = False

        B = np.random.randn(2, 10)
        B.flags.writeable = False

        import pandas as pd
        ret_df = pd.DataFrame(np.random.randn(30, 2) * 0.01, columns=symbols)
        factors_df = pd.DataFrame(np.random.randn(30, 10) * 0.01, columns=FACTOR_KEYS)

        attrib = factor_attribution_engine.compute_single_benchmark_attribution(
            benchmark_name="RO_TEST",
            w_p=w_p,
            w_b=w_b,
            B=B,
            ret_df=ret_df,
            factors_df=factors_df,
        )
        assert np.isfinite(attrib.active_return_pct)
        assert np.isfinite(attrib.tracking_error_pct)

    def test_factor_attribution_feed_export_and_json_serialization(self):
        """Asserts export_feed produces valid JSON feed matching schema."""
        out_path = factor_attribution_engine.export_feed()
        assert out_path.exists()
        assert out_path.name == "factor_attribution.json"

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "benchmarks" in data
        assert "top_positive_factors" in data
        assert "top_negative_factors" in data
        assert "unintentional_biases" in data
        assert len(data["benchmarks"]) == 3
        assert len(data["disclaimers"]) > 0

    def test_compliance_linter_zero_violations(self):
        """Asserts all factor attribution disclaimers conform to CSA 31-369 and SEC rules."""
        for disc in FACTOR_ATTRIBUTION_DISCLAIMERS:
            assert len(linter.lint_text(disc)) == 0
