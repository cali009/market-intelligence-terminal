"""
Phase 28 Master Release Verification & Comprehensive Audit Suite
Validates end-to-end data integrity, mathematical invariants, Black-Litterman Bayesian view blending,
turnover-constrained rebalancing, Predicates 16 and 17, terminal UI workstation synchronization,
and statutory regulatory compliance (CSA Staff Notice 31-369 / SEC Publisher Exclusion §202(a)(11)(D)).
"""

import json
import math
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter


class TestBayesianReleaseAudit:
    """Master audit verifying Phase 28 production artifacts, mathematical invariants, and UI synchronization."""

    @classmethod
    def setup_class(cls):
        cls.data_dir = Path("data/feeds")
        cls.public_api_dir = Path("public/api")
        cls.web_dir = Path("web")
        cls.public_dir = Path("public")

        with open(cls.data_dir / "portfolio_bayesian.json", "r", encoding="utf-8") as f:
            cls.bayesian_feed = json.load(f)

    def test_bayesian_feed_exists_and_serializes_cleanly(self):
        """Audit that portfolio_bayesian.json exists in feeds/ and public/api/ (both .json and clean route)."""
        feed_path = self.data_dir / "portfolio_bayesian.json"
        pub_path = self.public_api_dir / "portfolio_bayesian.json"
        clean_pub_path = self.public_api_dir / "portfolio_bayesian"

        assert feed_path.exists(), f"Missing feed: {feed_path}"
        assert pub_path.exists(), f"Missing public staged feed: {pub_path}"
        assert clean_pub_path.exists(), f"Missing public clean route: {clean_pub_path}"

        with open(pub_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert isinstance(data, dict)
            assert "views" in data
            assert "allocations" in data
            assert "turnover_frontier" in data
            assert "disclaimers" in data

    def test_black_litterman_mathematical_invariants(self):
        """Audit that Black-Litterman outputs conserve capital, respect caps, and obey trace bounds."""
        bf = self.bayesian_feed
        allocations = bf["allocations"]
        assert len(allocations) == 19

        constrained_weights = [a["constrained_weight"] for a in allocations.values()]
        prior_weights = [a["prior_weight"] for a in allocations.values()]

        # Weight conservation
        assert math.isclose(sum(constrained_weights), 1.0, rel_tol=1e-3)
        assert math.isclose(sum(prior_weights), 1.0, rel_tol=1e-3)

        # 18% concentration ceiling
        for sym, a in allocations.items():
            assert a["constrained_weight"] <= 0.1801, f"{sym} weight exceeded 18%: {a['constrained_weight']}"
            assert a["constrained_weight"] >= 0.0, f"{sym} negative weight: {a['constrained_weight']}"

        # Turnover cap and trace ratio bounds
        assert bf["total_one_way_turnover_pct"] <= 20.01
        assert 1.00 <= bf["estimation_error_trace_ratio"] <= 1.40

    def test_subjective_views_structure_and_coverage(self):
        """Audit that Quant Intel views contain both absolute and relative pair convictions."""
        views = self.bayesian_feed["views"]
        assert len(views) >= 4

        view_types = {v["view_type"] for v in views}
        assert "ABSOLUTE" in view_types
        assert "RELATIVE" in view_types

        for v in views:
            assert v["confidence_pct"] > 0.0
            assert "attribution_source" in v
            assert isinstance(v["view_return_pct"], (int, float))
            assert isinstance(v["posterior_expected_return_pct"], (int, float))

    def test_turnover_frontier_integrity(self):
        """Audit that turnover frontier points contain complete institutional metrics."""
        frontier = self.bayesian_feed["turnover_frontier"]
        assert len(frontier) >= 6

        for p in frontier:
            assert p["turnover_limit_pct"] > 0.0
            assert p["portfolio_expected_return_pct"] > 0.0
            assert p["portfolio_volatility_pct"] > 0.0
            assert p["sharpe_ratio"] > 0.0
            assert p["estimated_transaction_cost_bps"] >= 0.0
            assert p["active_share_pct"] >= 0.0

    def test_terminal_ui_bayesian_viewport_synchronization(self):
        """Audit that web/index.html and public/index.html contain complete Phase 28 UI components."""
        for path in [self.web_dir / "index.html", self.public_dir / "index.html"]:
            assert path.exists(), f"Missing HTML file: {path}"
            html_content = path.read_text(encoding="utf-8")

            # Check Tab header and badges
            assert "data-tab=\"portfoliohrp\"" in html_content
            assert "HRP Risk &amp; Bayesian Lab" in html_content or "HRP Risk & Bayesian Lab" in html_content
            assert "BLACK-LITTERMAN BAYESIAN BLENDING" in html_content
            assert "TURNOVER-CONSTRAINED REBALANCER" in html_content

            # Check Sub-navigation button
            assert "setHrpSection('bayesian')" in html_content
            assert "Black-Litterman Bayesian Rebalancer" in html_content

            # Check Views and Allocations Table elements
            assert "Quant Intel Active Subjective Views" in html_content
            assert "Prior HRP vs Bayesian Constrained Allocations" in html_content
            assert "Efficient Turnover vs Return Frontier" in html_content

            # Check Sentinels Monitor
            assert "Predicate 16" in html_content
            assert "Predicate 17" in html_content

            # Check Feed Fetching in JavaScript
            assert "/api/portfolio_bayesian.json" in html_content

    def test_master_release_compliance_linter_zero_violations(self):
        """Audit that all Phase 28 Bayesian disclaimers, text copies, and UI notices pass ImpersonalAdviceLinter."""
        linter = ImpersonalAdviceLinter()

        # Audit Bayesian disclaimers
        for disc in self.bayesian_feed.get("disclaimers", []):
            violations = linter.lint_text(disc)
            assert violations == [], f"Linter violation in Bayesian disclaimer: {violations}"

        # Audit HTML statutory notices
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "Black-Litterman Bayesian rebalancing" in web_html
        assert "turnover regularizers" in web_html
        assert "CSA Staff Notice 31-369" in web_html
        assert "SEC Publisher Exclusion" in web_html
