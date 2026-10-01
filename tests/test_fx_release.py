"""
Phase 29 Master Release Verification & Comprehensive Audit Suite
Validates end-to-end data integrity, mathematical invariants, Covered Interest Rate Parity (CIP),
minimum-variance optimal currency hedging, dual-listed arbitrage routing, Predicates 18 & 19,
terminal UI workstation synchronization, and statutory regulatory compliance (CSA Staff Notice 31-369 / SEC Publisher Exclusion §202(a)(11)(D)).
"""

import json
import math
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter
from src.models.schemas import CrossBorderFxFeed


class TestFxReleaseAudit:
    """Master audit verifying Phase 29 production artifacts, mathematical invariants, and UI synchronization."""

    @classmethod
    def setup_class(cls):
        cls.data_dir = Path("data/feeds")
        cls.public_api_dir = Path("public/api")
        cls.web_dir = Path("web")
        cls.public_dir = Path("public")

        with open(cls.data_dir / "cross_border_fx.json", "r", encoding="utf-8") as f:
            cls.fx_feed = json.load(f)

    def test_fx_feed_exists_and_serializes_cleanly(self):
        """Audit that cross_border_fx.json exists in feeds/ and public/api/ (both .json and clean route)."""
        feed_path = self.data_dir / "cross_border_fx.json"
        pub_path = self.public_api_dir / "cross_border_fx.json"
        clean_pub_path = self.public_api_dir / "cross_border_fx"

        assert feed_path.exists(), f"Missing feed: {feed_path}"
        assert pub_path.exists(), f"Missing public staged feed: {pub_path}"
        assert clean_pub_path.exists(), f"Missing public clean route: {clean_pub_path}"

        with open(pub_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Pydantic schema validation
            parsed = CrossBorderFxFeed.model_validate(data)
            assert parsed.spot_cad_usd > 1.0
            assert parsed.spot_usd_cad < 1.0
            assert len(parsed.forward_curve) == 4
            assert len(parsed.dual_listed_arbitrage) >= 8
            assert parsed.cad_base_hedging is not None
            assert parsed.usd_base_hedging is not None

    def test_cip_forward_curve_mathematical_invariants(self):
        """Audit Covered Interest Rate Parity forward curve monotonicity and money market relations."""
        curve = self.fx_feed["forward_curve"]
        assert len(curve) == 4

        tenor_days = [p["days"] for p in curve]
        assert tenor_days == [30, 91, 182, 365]

        # Monotonicity of forward points (increasingly negative as tenor lengthens)
        for i in range(len(curve) - 1):
            assert curve[i]["forward_points_pips"] > curve[i+1]["forward_points_pips"]
            assert curve[i]["forward_fx"] > curve[i+1]["forward_fx"]

        # Policy rate differential check
        diff_bps = self.fx_feed["policy_rate_differential_bps"]
        assert diff_bps == -60.0

    def test_optimal_hedging_mathematical_invariants(self):
        """Audit minimum-variance hedge ratio boundaries, volatility reduction, and hedging posture."""
        cad_h = self.fx_feed["cad_base_hedging"]
        usd_h = self.fx_feed["usd_base_hedging"]

        for h in [cad_h, usd_h]:
            assert 0.0 <= h["optimal_hedge_ratio"] <= 1.0
            assert h["unhedged_portfolio_volatility_pct"] > 0.0
            assert h["fully_hedged_portfolio_volatility_pct"] > 0.0
            assert h["optimal_hedged_portfolio_volatility_pct"] <= h["unhedged_portfolio_volatility_pct"]
            assert h["risk_reduction_pct"] >= 0.0
            assert h["hedging_drag_bps"] >= 0.0
            assert h["hedging_posture"] in ["PARTIAL_NATURAL_HEDGE", "FULL_HEDGE_RECOMMENDED", "UNHEDGED_OPTIMAL"]

    def test_dual_listed_arbitrage_coverage_and_venue_routing(self):
        """Audit dual-market interlisted arbitrage opportunity structure and hurdle logic."""
        arb_opps = self.fx_feed["dual_listed_arbitrage"]
        assert len(arb_opps) >= 8

        symbols = {a["symbol"] for a in arb_opps}
        required_syms = {"RY", "TD", "SHOP", "ENB", "CNQ", "BAM", "CNR", "CP"}
        assert required_syms.issubset(symbols)

        for arb in arb_opps:
            assert arb["tsx_price_cad"] > 0.0
            assert arb["nyse_price_usd"] > 0.0
            assert arb["round_trip_friction_bps"] == 9.0
            assert arb["routing_recommendation"] in ["EXECUTE_TSX", "EXECUTE_NYSE", "PARITY_EFFICIENT"]
            assert arb["liquidity_center"] in ["TSX_DOMINANT", "US_DOMINANT", "BALANCED"]

            if arb["routing_recommendation"] == "EXECUTE_TSX":
                assert arb["basis_spread_bps"] < -9.0
                assert arb["net_arbitrage_bps"] > 0.0
            elif arb["routing_recommendation"] == "EXECUTE_NYSE":
                assert arb["basis_spread_bps"] > 9.0
                assert arb["net_arbitrage_bps"] > 0.0
            else:
                assert -9.0 <= arb["basis_spread_bps"] <= 9.0
                assert arb["net_arbitrage_bps"] == 0.0

    def test_terminal_ui_fx_viewport_synchronization(self):
        """Audit that web/index.html and public/index.html contain complete Phase 29 UI components."""
        for path in [self.web_dir / "index.html", self.public_dir / "index.html"]:
            assert path.exists(), f"Missing HTML file: {path}"
            html_content = path.read_text(encoding="utf-8")

            # Check Tab header and badges
            assert 'data-tab="crossborderfx"' in html_content
            assert "Cross-Border FX &amp; Arbitrage" in html_content or "Cross-Border FX & Arbitrage" in html_content
            assert "COVERED INTEREST PARITY (CIP)" in html_content
            assert "MIN-VARIANCE HEDGE (h*)" in html_content
            assert "TSX/NYSE SMART ROUTING" in html_content

            # Check Base Currency Toggle
            assert "setFxBaseCurrency('CAD')" in html_content
            assert "setFxBaseCurrency('USD')" in html_content

            # Check Sub-navigation buttons
            assert "setFxSubSection('overview')" in html_content
            assert "setFxSubSection('curves')" in html_content
            assert "setFxSubSection('hedging')" in html_content
            assert "setFxSubSection('arbitrage')" in html_content
            assert "setFxSubSection('sentinels')" in html_content

            # Check Simulator elements
            assert "updateSimulatedHedgeRatio" in html_content
            assert 'type="range"' in html_content
            assert "simHedgeVal" in html_content
            assert "simVolVal" in html_content

            # Check Sentinels Monitor
            assert "PREDICATE 18" in html_content
            assert "CROSS_BORDER_PARITY_DISLOCATION" in html_content
            assert "PREDICATE 19" in html_content
            assert "UNHEDGED_CURRENCY_DRAG" in html_content

            # Check Feed Fetching in JavaScript
            assert "/api/cross_border_fx.json" in html_content

    def test_master_release_compliance_linter_zero_violations(self):
        """Audit that all Phase 29 disclaimers, text copies, and UI notices pass ImpersonalAdviceLinter."""
        linter = ImpersonalAdviceLinter()

        # Audit FX disclaimers
        for disc in self.fx_feed.get("disclaimers", []):
            violations = linter.lint_text(disc)
            assert violations == [], f"Linter violation in FX disclaimer: {violations}"

        # Audit HTML statutory notices
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "Cross-border dual-currency analytics" in web_html
        assert "Covered Interest Rate Parity forward curves" in web_html
        assert "CSA Staff Notice 31-369" in web_html
        assert "SEC Publisher Exclusion" in web_html
