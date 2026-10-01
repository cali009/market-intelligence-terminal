"""
Unit and Integration Tests for Black-Litterman Bayesian View Blending & Turnover Rebalancer (Phase 28)
US + Canada Dual-Market Intelligence Platform

Validates:
- Implied market equilibrium returns (Pi = lambda * Sigma * w_mkt)
- Quant Intel subjective view extraction (P matrix, Q vector, Omega uncertainty)
- Black-Litterman posterior combined distribution (mu_BL, Sigma_BL)
- Null-view convergence to prior equilibrium
- Turnover-constrained quadratic rebalancer (L1 turnover penalty & L2 market impact)
- Efficient turnover frontier monotonicity
- Predicate 16 (EXCESSIVE_TURNOVER_BREACH) & Predicate 17 (ESTIMATION_ERROR_SPIKE)
- Statutory regulatory compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import math
from pathlib import Path
import numpy as np
import pytest

from src.compliance.linter import linter
from src.engine.portfolio_bayesian import portfolio_bayesian_engine
from src.engine.quant_intel_sentinel import quant_intel_sentinel


class TestPortfolioBayesianEngine:
    """Test suite validating Black-Litterman Bayesian allocation and turnover rebalancing."""

    @pytest.fixture(scope="class", autouse=True)
    def setup_data(self):
        type(self).res = portfolio_bayesian_engine.evaluate_bayesian_portfolio(
            total_capital=100000.0, force_refresh=True
        )

    def test_load_equilibrium_prior_properties(self):
        """Asserts that equilibrium returns Pi are positive semi-definite and prior weights sum to 1.0."""
        symbols, cov, w_prior, pi_ret, mkt_map = portfolio_bayesian_engine.load_equilibrium_prior()

        assert len(symbols) == 19
        assert cov.shape == (19, 19)
        assert np.allclose(cov, cov.T, atol=1e-7), "Covariance matrix must be symmetric"

        # Check positive semi-definiteness via eigenvalues
        eigvals = np.linalg.eigvalsh(cov)
        assert np.all(eigvals >= -1e-6), "Covariance matrix must be positive semi-definite"

        # Weight conservation
        assert math.isclose(float(np.sum(w_prior)), 1.0, rel_tol=1e-5)
        assert np.all(w_prior >= 0.0), "Prior weights must be non-negative"

        # Equilibrium returns Pi = lambda * Sigma * w_prior
        expected_pi = portfolio_bayesian_engine.risk_aversion * (cov @ w_prior)
        assert np.allclose(pi_ret, expected_pi, atol=1e-5)

    def test_extract_quant_intel_views_properties(self):
        """Asserts that subjective views reflect active Quant Intel conviction grades."""
        symbols, cov, w_prior, pi_ret, mkt_map = portfolio_bayesian_engine.load_equilibrium_prior()
        sym_to_idx = {s: i for i, s in enumerate(symbols)}
        P, Q, C, views = portfolio_bayesian_engine.extract_quant_intel_views(symbols, pi_ret, sym_to_idx)

        assert len(views) >= 4, "Should extract at least 4 active views"
        assert P.shape == (len(views), len(symbols))
        assert len(Q) == len(views)
        assert len(C) == len(views)

        # Check confidences are bounded in (0, 1]
        assert np.all(C > 0.0) and np.all(C <= 1.0)

        # Check that both absolute and relative views are present
        view_types = {v.view_type for v in views}
        assert "ABSOLUTE" in view_types
        assert "RELATIVE" in view_types

    def test_black_litterman_posterior_updating(self):
        """Asserts that Black-Litterman posterior returns shift toward views proportionally to confidence."""
        res = self.res
        assert res.views_count >= 4

        # For high-conviction outperform views (AAPL, NVDA), posterior expected return must shift upward
        view_map = {v.symbol: v for v in res.views if v.view_type == "ABSOLUTE"}
        if "AAPL" in view_map:
            assert view_map["AAPL"].posterior_expected_return_pct > view_map["AAPL"].implied_prior_return_pct
        if "NVDA" in view_map:
            assert view_map["NVDA"].posterior_expected_return_pct > view_map["NVDA"].implied_prior_return_pct

        # For uncorrelated underweight views (ENB), posterior expected return must shift downward
        if "ENB" in view_map:
            assert view_map["ENB"].posterior_expected_return_pct < view_map["ENB"].implied_prior_return_pct

        # For relative views (NVDA vs GOOGL), posterior spread must widen in favor of the outperforming asset
        rel_views = {v.symbol: v for v in res.views if v.view_type == "RELATIVE"}
        if "NVDA" in rel_views:
            assert rel_views["NVDA"].posterior_expected_return_pct > rel_views["NVDA"].implied_prior_return_pct

        # Trace ratio should reflect parameter estimation uncertainty: Tr(Sigma_BL) >= Tr(Sigma)
        assert res.estimation_error_trace_ratio >= 1.00

    def test_null_views_convergence_to_prior(self):
        """Asserts that when views are empty, Black-Litterman posterior collapses identically to the market prior."""
        symbols, cov, w_prior, pi_ret, mkt_map = portfolio_bayesian_engine.load_equilibrium_prior()
        empty_P = np.zeros((0, len(symbols)), dtype=float)
        empty_Q = np.zeros(0, dtype=float)
        empty_C = np.zeros(0, dtype=float)

        mu_bl, sigma_bl, w_unc, trace_ratio = portfolio_bayesian_engine.compute_black_litterman_posterior(
            cov, pi_ret, empty_P, empty_Q, empty_C
        )

        assert np.allclose(mu_bl, pi_ret, atol=1e-6), "Null views must return identical prior returns"
        assert np.allclose(sigma_bl, cov, atol=1e-6), "Null views must return identical prior covariance"
        assert math.isclose(trace_ratio, 1.0, rel_tol=1e-5)

    def test_turnover_constrained_rebalancing_invariants(self):
        """Asserts that rebalanced weights conserve capital, respect single-asset caps, and limit turnover."""
        res = self.res
        weights = [a.constrained_weight for a in res.allocations.values()]

        # Weight conservation: sum(w) == 1.0
        assert math.isclose(sum(weights), 1.0, rel_tol=1e-3)

        # Single asset concentration ceiling <= 18.0%
        for sym, alloc in res.allocations.items():
            assert alloc.constrained_weight <= 0.1801, f"{sym} exceeded 18% concentration: {alloc.constrained_weight}"
            assert alloc.constrained_weight >= 0.0, f"{sym} negative weight: {alloc.constrained_weight}"

        # Turnover cap respect
        assert res.total_one_way_turnover_pct <= 20.01, f"Turnover exceeded cap: {res.total_one_way_turnover_pct}"

        # Transaction cost drag
        assert res.total_transaction_cost_bps > 0.0
        assert res.total_transaction_cost_dollars > 0.0

    def test_turnover_frontier_monotonicity(self):
        """Asserts that efficient turnover frontier shows non-decreasing active share as turnover limits widen."""
        frontier = self.res.turnover_frontier
        assert len(frontier) >= 6

        turnovers = [p.one_way_turnover_pct for p in frontier]
        # Verify that realized turnover is non-decreasing with turnover limit
        for i in range(len(turnovers) - 1):
            assert turnovers[i] <= turnovers[i + 1] + 0.10, "Turnover should increase with looser constraints"

        # Check that all points have valid Sharpe ratios
        for p in frontier:
            assert p.portfolio_expected_return_pct > 0.0
            assert p.portfolio_volatility_pct > 0.0

    def test_predicate_16_excessive_turnover_sentinel(self):
        """Asserts that Predicate 16 triggers when 1-way turnover exceeds 25.0%."""
        mock_bayesian = self.res.model_dump()
        mock_bayesian["total_one_way_turnover_pct"] = 28.5

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={"cvar_99_1d_pct": 2.0},
            bayesian_metrics=mock_bayesian,
        )

        turnover_alerts = [a for a in alerts if a.predicate_type == "EXCESSIVE_TURNOVER_BREACH"]
        assert len(turnover_alerts) == 1
        a = turnover_alerts[0]
        assert "Excessive Portfolio Rebalancing Turnover" in a.headline
        assert a.severity == "WARNING"
        assert a.action_required == "THROTTLE_PORTFOLIO_TURNOVER"
        assert a.current_price == 28.5
        assert linter.lint_text(a.headline) == []
        assert linter.lint_text(a.body) == []

    def test_predicate_17_estimation_error_spike_sentinel(self):
        """Asserts that Predicate 17 triggers when trace inflation exceeds 1.40x."""
        mock_bayesian = self.res.model_dump()
        mock_bayesian["estimation_error_trace_ratio"] = 1.48

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={"cvar_99_1d_pct": 2.0},
            bayesian_metrics=mock_bayesian,
        )

        error_alerts = [a for a in alerts if a.predicate_type == "ESTIMATION_ERROR_SPIKE"]
        assert len(error_alerts) == 1
        a = error_alerts[0]
        assert "Bayesian Estimation Error Uncertainty Spike" in a.headline
        assert a.severity == "WARNING"
        assert a.action_required == "INCREASE_PRIOR_SHRINKAGE"
        assert a.current_price == 1.48
        assert linter.lint_text(a.headline) == []
        assert linter.lint_text(a.body) == []

    def test_feed_export_and_json_serialization(self):
        """Asserts that portfolio_bayesian.json exports and serializes cleanly."""
        feed_path = portfolio_bayesian_engine.export_feed(force_refresh=True)
        assert feed_path.exists()
        with open(feed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert "views" in data
            assert "allocations" in data
            assert "turnover_frontier" in data
            assert len(data["views"]) >= 4

    def test_compliance_linter_zero_violations(self):
        """Asserts that all Bayesian disclaimers and alert phrases pass ImpersonalAdviceLinter."""
        for disc in self.res.disclaimers:
            violations = linter.lint_text(disc)
            assert violations == [], f"Linter violation in Bayesian disclaimer: {violations}"
