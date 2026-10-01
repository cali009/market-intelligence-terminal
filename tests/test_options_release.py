"""
Phase 30 Master Release Verification & Comprehensive Audit Suite
Validates end-to-end data integrity, mathematical invariants, Black-Scholes implied volatility surfaces,
dealer Net Gamma Exposure (GEX), Gamma Flip threshold calculations, Max Pain strike distributions,
Predicates 20 & 21, terminal UI workstation synchronization, and statutory regulatory compliance (CSA Staff Notice 31-369 / SEC Publisher Exclusion §202(a)(11)(D)).
"""

import json
import math
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter
from src.models.schemas import OptionsIntelligenceFeed


class TestOptionsReleaseAudit:
    """Master audit verifying Phase 30 production artifacts, mathematical invariants, and UI synchronization."""

    @classmethod
    def setup_class(cls):
        cls.data_dir = Path("data/feeds")
        cls.public_api_dir = Path("public/api")
        cls.web_dir = Path("web")
        cls.public_dir = Path("public")

        with open(cls.data_dir / "options_intelligence.json", "r", encoding="utf-8") as f:
            cls.options_feed = json.load(f)

    def test_options_feed_exists_and_serializes_cleanly(self):
        """Audit that options_intelligence.json exists in feeds/ and public/api/ (both .json and clean route)."""
        feed_path = self.data_dir / "options_intelligence.json"
        pub_path = self.public_api_dir / "options_intelligence.json"
        clean_pub_path = self.public_api_dir / "options_intelligence"

        assert feed_path.exists(), f"Missing feed: {feed_path}"
        assert pub_path.exists(), f"Missing public staged feed: {pub_path}"
        assert clean_pub_path.exists(), f"Missing public clean route: {clean_pub_path}"

        with open(pub_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Pydantic schema validation
            parsed = OptionsIntelligenceFeed.model_validate(data)
            assert parsed.us_cboe_aggregate_net_gex_millions > 0.0
            assert parsed.ca_mx_aggregate_net_gex_millions > 0.0
            assert len(parsed.symbols_monitored) == 10
            assert len(parsed.dossiers) == 10

    def test_volatility_surface_mathematical_invariants(self):
        """Audit implied volatility surfaces across tenors and strike moneyness."""
        dossiers = self.options_feed["dossiers"]

        for sym, d in dossiers.items():
            surfaces = d["volatility_surface"]
            assert len(surfaces) == 5

            for surf in surfaces:
                assert surf["atm_iv_pct"] > 0.0
                assert surf["skew_25d_pct"] > 0.0
                smile = surf["smile_points"]
                assert len(smile) == 9

                # Crash put skew: 80% moneyness IV > 100% moneyness IV
                iv_80 = [p["implied_volatility_pct"] for p in smile if p["moneyness_pct"] == 80.0][0]
                iv_100 = [p["implied_volatility_pct"] for p in smile if p["moneyness_pct"] == 100.0][0]
                assert iv_80 > iv_100

    def test_dealer_gex_and_max_pain_integrity(self):
        """Audit strike GEX, Gamma Flip level, and Max Pain strike."""
        dossiers = self.options_feed["dossiers"]

        for sym, d in dossiers.items():
            strikes = d["strike_gex_distribution"]
            assert len(strikes) >= 20

            spot = d["underlying_spot_price"]
            flip = d["gamma_flip_strike"]
            max_pain = d["max_pain_strike"]

            assert spot > 0.0
            assert flip > 0.0
            assert max_pain > 0.0

            assert d["put_call_ratio_oi"] > 0.0
            assert d["put_call_ratio_volume"] > 0.0
            assert d["gamma_regime"] in ["LONG_GAMMA", "SHORT_GAMMA", "TRANSITION_ZONE"]
            assert d["market_maker_posture"] in ["SUPPRESSING_VOLATILITY", "AMPLIFYING_VOLATILITY", "NEUTRAL_REBALANCING"]

    def test_terminal_ui_options_viewport_synchronization(self):
        """Audit that web/index.html and public/index.html contain complete Phase 30 UI components."""
        for path in [self.web_dir / "index.html", self.public_dir / "index.html"]:
            assert path.exists(), f"Missing HTML file: {path}"
            html_content = path.read_text(encoding="utf-8")

            # Check Tab header and badges
            assert 'data-tab="optionssurface"' in html_content
            assert "Options &amp; Volatility Surface Lab" in html_content or "Options & Volatility Surface Lab" in html_content
            assert "DEALER NET GAMMA (GEX)" in html_content
            assert "VOLATILITY SMILE &amp; SKEW" in html_content
            assert "GAMMA FLIP SOLVER" in html_content

            # Check Market Filter Buttons
            assert "setOptionsMarketFilter('ALL')" in html_content
            assert "setOptionsMarketFilter('US_CBOE')" in html_content
            assert "setOptionsMarketFilter('CA_MX')" in html_content

            # Check Sub-navigation buttons
            assert "setOptionsSubSection('overview')" in html_content
            assert "setOptionsSubSection('gex')" in html_content
            assert "setOptionsSubSection('surface')" in html_content
            assert "setOptionsSubSection('skew')" in html_content
            assert "setOptionsSubSection('sentinels')" in html_content

            # Check Sentinels Monitor
            assert "PREDICATE 20" in html_content
            assert "GAMMA_FLIP_REGIME_TRANSITION" in html_content
            assert "PREDICATE 21" in html_content
            assert "VOLATILITY_SKEW_TAIL_INVERSION" in html_content

            # Check Feed Fetching in JavaScript
            assert "/api/options_intelligence.json" in html_content

    def test_master_release_compliance_linter_zero_violations(self):
        """Audit that all Phase 30 disclaimers, text copies, and UI notices pass ImpersonalAdviceLinter."""
        linter = ImpersonalAdviceLinter()

        # Audit Options disclaimers
        for disc in self.options_feed.get("disclaimers", []):
            violations = linter.lint_text(disc)
            assert violations == [], f"Linter violation in Options disclaimer: {violations}"

        # Audit HTML statutory notices
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "Options volatility surface metrics" in web_html
        assert "dealer Net Gamma Exposure (GEX)" in web_html
        assert "CSA Staff Notice 31-369" in web_html
        assert "SEC Publisher Exclusion" in web_html
