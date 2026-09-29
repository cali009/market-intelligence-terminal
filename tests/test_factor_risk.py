"""
Unit and Integration Tests for Cross-Border Multi-Asset Factor Risk Engine (Phase 27.1)
US + Canada Dual-Market Intelligence Platform
"""

import json
from pathlib import Path
import numpy as np
import pytest

from src.compliance.linter import linter
from src.engine.factor_risk import (
    factor_risk_engine,
    FACTOR_KEYS,
    FACTOR_NAMES,
    FACTOR_RISK_DISCLAIMERS,
)


class TestFactorRiskEngine:
    """Test suite validating factor loadings, covariance semi-definiteness, and variance conservation."""

    @pytest.fixture(scope="class", autouse=True)
    def setup_factor_engine(self):
        type(self).result = factor_risk_engine.evaluate_factor_risk(
            total_capital=100000.0, force_refresh=True
        )

    def test_universe_coverage_and_factor_loadings_presence(self):
        """Asserts all 19 dual-market symbols have valid factor loadings with high explanatory power."""
        loadings = self.result.factor_loadings
        assert len(loadings) == 19, f"Expected 19 symbols, got {len(loadings)}"

        expected_symbols = [
            "AAPL", "AMZN", "BAM", "BN", "CNQ", "CNR", "CP", "ENB", "GOOGL",
            "JPM", "MSFT", "NVDA", "QQQ", "RY", "SHOP", "SPY", "TD", "XIU", "XOM"
        ]
        for sym in expected_symbols:
            assert sym in loadings, f"Missing factor loading for {sym}"
            l = loadings[sym]
            assert l.symbol == sym
            assert l.market in ["US", "CA"]
            # All 10 factor loadings must be finite floats
            for k in FACTOR_KEYS:
                val = getattr(l, k)
                assert np.isfinite(val), f"Factor {k} for {sym} is not finite: {val}"
            # Specific variance must be positive
            assert l.specific_residual_variance > 0.0
            # Model R-squared must be >= 0.50 for all securities
            assert 0.50 <= l.r_squared <= 1.0, f"R-squared for {sym} is {l.r_squared}, expected >= 0.50"

    def test_systematic_and_specific_variance_conservation(self):
        """Asserts that total portfolio variance equals systematic variance plus specific variance."""
        port_vol = self.result.portfolio_annualized_volatility
        sys_vol = self.result.systematic_volatility
        spec_vol = self.result.specific_volatility

        total_var = port_vol ** 2
        reconstructed_var = (sys_vol ** 2) + (spec_vol ** 2)

        assert np.isclose(total_var, reconstructed_var, atol=1e-4), (
            f"Variance conservation failed: total {total_var} vs sys+spec {reconstructed_var}"
        )

        sys_pct = self.result.systematic_risk_share_pct
        spec_pct = self.result.specific_risk_share_pct
        assert np.isclose(sys_pct + spec_pct, 100.0, atol=0.1), (
            f"Risk share sum failed: {sys_pct}% + {spec_pct}% != 100%"
        )

        # In a diversified portfolio, systematic risk should dominate (> 80%)
        assert sys_pct > 80.0, f"Systematic risk share too low: {sys_pct}%"
        assert spec_pct < 20.0, f"Specific risk share too high: {spec_pct}%"

    def test_factor_covariance_matrix_properties(self):
        """Asserts factor covariance matrix is symmetric and positive semi-definite."""
        cov_dict = self.result.factor_covariance_matrix
        assert len(cov_dict) == 10, f"Expected 10x10 covariance matrix, got {len(cov_dict)}"

        # Convert to numpy array
        cov_mat = np.zeros((10, 10))
        for i, row_k in enumerate(FACTOR_KEYS):
            for j, col_k in enumerate(FACTOR_KEYS):
                cov_mat[i, j] = cov_dict[row_k][col_k]

        # Symmetry test: Cov = Cov^T
        assert np.allclose(cov_mat, cov_mat.T, atol=1e-5), "Factor covariance matrix is not symmetric"

        # Diagonal elements must be positive variances
        diag = np.diag(cov_mat)
        assert np.all(diag > 0.0), f"Non-positive diagonal variances found: {diag}"

        # Eigenvalues must be >= -1e-6 (positive semi-definite)
        eigvals = np.linalg.eigvalsh(cov_mat)
        assert np.all(eigvals >= -1e-6), f"Negative eigenvalues found in factor covariance: {eigvals}"

    def test_economic_factor_loading_monotonicity(self):
        """Asserts that factor loadings align with cross-border economic intuition."""
        loadings = self.result.factor_loadings

        # 1. Crude Oil Beta: Energy producers (CNQ, XOM, ENB) > Tech growth (NVDA, MSFT, AAPL)
        avg_energy_oil_beta = np.mean([loadings["CNQ"].crude_oil_beta, loadings["XOM"].crude_oil_beta, loadings["ENB"].crude_oil_beta])
        avg_tech_oil_beta = np.mean([loadings["NVDA"].crude_oil_beta, loadings["MSFT"].crude_oil_beta, loadings["AAPL"].crude_oil_beta])
        assert avg_energy_oil_beta > avg_tech_oil_beta + 0.5, (
            f"Energy oil beta ({avg_energy_oil_beta}) not sufficiently higher than tech ({avg_tech_oil_beta})"
        )

        # 2. Yield Curve Slope: Financials (RY, TD, JPM) have strong positive sensitivity
        avg_fin_curve_beta = np.mean([loadings["RY"].yield_curve_beta, loadings["TD"].yield_curve_beta, loadings["JPM"].yield_curve_beta])
        avg_tech_curve_beta = np.mean([loadings["AAPL"].yield_curve_beta, loadings["SHOP"].yield_curve_beta])
        assert avg_fin_curve_beta > avg_tech_curve_beta, (
            f"Financials yield curve beta ({avg_fin_curve_beta}) not higher than tech ({avg_tech_curve_beta})"
        )

        # 3. Market Beta: Core ETFs (SPY, XIU) have beta near 1.0
        assert np.isclose(loadings["SPY"].market_beta, 1.0, atol=0.25)
        assert np.isclose(loadings["XIU"].market_beta, 1.0, atol=0.25)

    def test_marginal_contribution_to_risk_euler_sum(self):
        """Asserts Euler marginal risk contributions sum to total systematic risk."""
        summaries = self.result.factor_risk_summaries
        assert len(summaries) == 10

        # Percent of systematic risk must sum to ~100%
        total_sys_pct = sum(s.percent_of_systematic_risk for s in summaries.values())
        assert np.isclose(total_sys_pct, 100.0, atol=0.5), (
            f"Sum of percent_of_systematic_risk is {total_sys_pct}%, expected 100.0%"
        )

        # Marginal contribution to total portfolio variance must sum to systematic risk share
        total_mcr_pct = sum(s.marginal_contribution_to_risk_pct for s in summaries.values())
        assert np.isclose(total_mcr_pct, self.result.systematic_risk_share_pct, atol=0.5), (
            f"Sum of MCR% ({total_mcr_pct}%) does not match systematic risk share ({self.result.systematic_risk_share_pct}%)"
        )

    def test_read_only_array_resilience_in_factor_calculations(self):
        """Asserts calculate_portfolio_factor_risk accepts and processes read-only numpy arrays without throwing ValueError."""
        # Create mock read-only arrays
        weights = np.ones(5) / 5.0
        weights.flags.writeable = False

        B = np.random.randn(5, 10)
        B.flags.writeable = False

        factor_cov = np.eye(10) * 0.04
        factor_cov.flags.writeable = False

        spec_vars = np.ones(5) * 0.02
        spec_vars.flags.writeable = False

        port_vol, sys_vol, spec_vol, sys_pct, spec_pct, summaries = (
            factor_risk_engine.calculate_portfolio_factor_risk(
                weights=weights,
                B=B,
                factor_cov=factor_cov,
                spec_vars=spec_vars,
                total_capital=100000.0,
            )
        )
        assert port_vol > 0.0
        assert sys_vol > 0.0
        assert spec_vol > 0.0
        assert np.isclose(sys_pct + spec_pct, 100.0, atol=0.1)

    def test_factor_risk_feed_export_and_json_serialization(self):
        """Asserts factor risk engine exports valid JSON feed matching schema."""
        out_path = factor_risk_engine.export_feed()
        assert out_path.exists()
        assert out_path.name == "factor_risk.json"

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "portfolio_annualized_volatility" in data
        assert "systematic_risk_share_pct" in data
        assert "factor_loadings" in data
        assert "factor_covariance_matrix" in data
        assert "factor_risk_summaries" in data
        assert len(data["factor_loadings"]) == 19
        assert len(data["factor_risk_summaries"]) == 10

    def test_compliance_linter_zero_violations(self):
        """Asserts all factor risk disclaimers strictly conform to CSA 31-369 and SEC rules."""
        for disc in FACTOR_RISK_DISCLAIMERS:
            assert len(linter.lint_text(disc)) == 0
