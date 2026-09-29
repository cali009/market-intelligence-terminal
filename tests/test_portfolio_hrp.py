"""
Unit and Integration Tests for Hierarchical Risk Parity (HRP) Portfolio Engine (Phase 26.1)
US + Canada Dual-Market Intelligence Platform
"""

import numpy as np
import pytest

from src.compliance.linter import linter
from src.engine.portfolio_hrp import hrp_portfolio_engine, HRP_DISCLAIMERS


class TestHrpPortfolioEngine:
    """Test suite validating correlation distance, dendrogram seriation, recursive bisection, and Euler risk budgeting."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.result = hrp_portfolio_engine.generate_hrp_allocation(total_capital=100000.0, force_refresh=True)

    def test_universe_coverage_and_dual_market_representation(self):
        """Asserts that all 19 dual-market securities are covered with both US and Canadian presence."""
        assert self.result.universe_size == 19
        assert len(self.result.allocations) == 19
        assert len(self.result.ordered_symbols) == 19

        us_allocs = [a for a in self.result.allocations.values() if a.country == "US"]
        ca_allocs = [a for a in self.result.allocations.values() if a.country == "CA"]

        assert len(us_allocs) >= 8
        assert len(ca_allocs) >= 8
        assert sum(a.weight for a in us_allocs) > 0.20
        assert sum(a.weight for a in ca_allocs) > 0.20

    def test_correlation_distance_properties(self):
        """Asserts correlation distance d_{i,j} satisfies metric bounds [0, 1], symmetry, and zero diagonal."""
        returns_df, _ = hrp_portfolio_engine.load_universe_returns()
        corr, dist = hrp_portfolio_engine.compute_correlation_distance(returns_df)

        n = corr.shape[0]
        # Diagonal must be 0
        assert np.allclose(np.diag(dist), np.zeros(n), atol=1e-6)
        # Symmetry
        assert np.allclose(dist, dist.T, atol=1e-6)
        # Bounds [0, 1]
        assert np.all(dist >= 0.0)
        assert np.all(dist <= 1.0)

    def test_quasi_diagonalize_completeness(self):
        """Asserts dendrogram leaf ordering preserves all indices with zero duplicates."""
        returns_df, _ = hrp_portfolio_engine.load_universe_returns()
        _, dist = hrp_portfolio_engine.compute_correlation_distance(returns_df)
        linkage = hrp_portfolio_engine.build_hierarchical_tree(dist, method="single")
        order = hrp_portfolio_engine.quasi_diagonalize(linkage)

        n = len(returns_df.columns)
        assert len(order) == n
        assert sorted(order) == list(range(n))
        assert len(set(order)) == n

    def test_recursive_bisection_weight_conservation(self):
        """Asserts recursive bisection generates non-negative weights strictly summing to 1.0."""
        returns_df, _ = hrp_portfolio_engine.load_universe_returns()
        cov = returns_df.cov().values
        _, dist = hrp_portfolio_engine.compute_correlation_distance(returns_df)
        linkage = hrp_portfolio_engine.build_hierarchical_tree(dist, method="single")
        order = hrp_portfolio_engine.quasi_diagonalize(linkage)

        weights = hrp_portfolio_engine.recursive_bisection(cov, order)
        assert len(weights) == len(order)
        assert np.isclose(np.sum(weights), 1.0, atol=1e-6)
        assert np.all(weights > 0.0)

    def test_hrp_outperforms_equal_weight_in_volatility(self):
        """Asserts that HRP produces lower annualized volatility than naive Equal-Weight allocation."""
        bench = self.result.benchmark_comparisons
        hrp_vol = bench["HIERARCHICAL_RISK_PARITY"]["portfolio_volatility_annualized"]
        ew_vol = bench["EQUAL_WEIGHT"]["portfolio_volatility_annualized"]

        # HRP must have lower portfolio volatility than Equal-Weight
        assert hrp_vol < ew_vol
        # HRP diversification ratio must be greater than or equal to Equal-Weight
        assert bench["HIERARCHICAL_RISK_PARITY"]["diversification_ratio"] >= bench["EQUAL_WEIGHT"]["diversification_ratio"]

    def test_risk_budgeting_monotonicity(self):
        """Asserts higher-volatility growth assets receive lower HRP weight than low-volatility anchors."""
        allocs = self.result.allocations
        # Low volatility anchors: TD, ENB, SPY
        # High volatility growth: NVDA, SHOP
        low_vol_weight = np.mean([allocs["TD"].weight, allocs["ENB"].weight, allocs["SPY"].weight])
        high_vol_weight = np.mean([allocs["NVDA"].weight, allocs["SHOP"].weight])

        assert low_vol_weight > high_vol_weight

    def test_euler_marginal_risk_shares_sum(self):
        """Asserts that Euler percentage risk shares sum to 100% within numerical tolerance."""
        total_risk_share = sum(a.risk_share_pct for a in self.result.allocations.values())
        assert np.isclose(total_risk_share, 100.0, atol=0.5)

    def test_compliance_linter_zero_violations(self):
        """Asserts statutory impersonal research disclaimers have zero compliance violations."""
        for disc in HRP_DISCLAIMERS:
            assert len(linter.lint_text(disc)) == 0
