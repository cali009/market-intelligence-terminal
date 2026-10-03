"""
Phase 31 Master Release Verification & Comprehensive Audit Suite

Validates end-to-end data integrity, Nelson-Siegel term structure invariants,
Adrian-Crump-Moench (ACM) term premium additive identities, cross-border GoC vs UST
spread arithmetic, Predicates 22 & 23 sentinel wiring, terminal UI workstation
synchronization, and statutory regulatory compliance
(CSA Staff Notice 31-369 / SEC Publisher Exclusion §202(a)(11)(D)).
"""

import json
import math
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter
from src.models.schemas import SovereignYieldFeed

EXPECTED_TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]


class TestSovereignYieldReleaseAudit:
    """Master audit verifying Phase 31 production artifacts, mathematical invariants, and UI synchronization."""

    @classmethod
    def setup_class(cls):
        cls.data_dir = Path("data/feeds")
        cls.public_api_dir = Path("public/api")
        cls.web_dir = Path("web")
        cls.public_dir = Path("public")

        with open(cls.data_dir / "sovereign_yield_curve.json", "r", encoding="utf-8") as f:
            cls.sovereign_feed = json.load(f)

    # ------------------------------------------------------------------
    # 1. Production artifact existence and schema validation
    # ------------------------------------------------------------------
    def test_sovereign_feed_exists_and_serializes_cleanly(self):
        """Audit that sovereign_yield_curve.json exists in feeds/ and public/api/ (both .json and clean route)."""
        feed_path = self.data_dir / "sovereign_yield_curve.json"
        pub_path = self.public_api_dir / "sovereign_yield_curve.json"
        clean_pub_path = self.public_api_dir / "sovereign_yield_curve"

        assert feed_path.exists(), f"Missing feed: {feed_path}"
        assert pub_path.exists(), f"Missing public staged feed: {pub_path}"
        assert clean_pub_path.exists(), f"Missing public clean route: {clean_pub_path}"

        with open(pub_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            parsed = SovereignYieldFeed.model_validate(data)
            assert parsed.us_profile.jurisdiction == "US"
            assert parsed.ca_profile.jurisdiction == "CA"
            assert len(parsed.cross_border_yield_curve) == 11

    def test_public_clean_route_matches_json_route_byte_for_byte(self):
        """The extensionless clean route must be an exact copy of the .json route."""
        json_bytes = (self.public_api_dir / "sovereign_yield_curve.json").read_bytes()
        clean_bytes = (self.public_api_dir / "sovereign_yield_curve").read_bytes()
        assert json_bytes == clean_bytes, "Clean route drifted from .json route"

    def test_staged_feed_matches_source_feed(self):
        """The staged public feed must carry the same sovereign data as the engine-generated source feed.

        Comparison ignores BOTH wall-clock stamps, `generated_at` and `as_of_date`:
        other suites (e.g. test_edge_exporter -> export_all()) legitimately regenerate the
        source feed mid-run, and `as_of_date` additionally rolls at UTC midnight, so a
        byte-for-byte check would be order- and time-dependent. Sovereign curve content is
        the property that actually matters.
        """
        source = json.loads((self.data_dir / "sovereign_yield_curve.json").read_text(encoding="utf-8"))
        staged = json.loads((self.public_api_dir / "sovereign_yield_curve.json").read_text(encoding="utf-8"))

        src_as_of = source.get("as_of_date")
        stg_as_of = staged.get("as_of_date")
        for payload in (source, staged):
            payload.pop("generated_at", None)
            payload.pop("as_of_date", None)

        assert source == staged, "Staged feed content drifted from source feed"

        # The staged copy may legitimately trail by a UTC-midnight roll, but must not be
        # stale. A bound catches a genuinely abandoned deploy without failing at midnight.
        from datetime import date as _date
        gap = (_date.fromisoformat(src_as_of) - _date.fromisoformat(stg_as_of)).days
        assert 0 <= gap <= 1, (
            f"Staged sovereign feed is stale: source as_of {src_as_of} vs staged {stg_as_of}"
        )

    # ------------------------------------------------------------------
    # 2. Mathematical invariants
    # ------------------------------------------------------------------
    def test_eleven_maturity_ladder_is_complete(self):
        """Both sovereign profiles and the cross-border ladder must span 1M through 30Y."""
        parsed = SovereignYieldFeed.model_validate(self.sovereign_feed)
        for profile in (parsed.us_profile, parsed.ca_profile):
            assert [p.tenor for p in profile.yield_points] == EXPECTED_TENORS
        assert [p.tenor for p in parsed.cross_border_yield_curve] == EXPECTED_TENORS

    def test_cross_border_spread_identity_holds_across_feed(self):
        """Spread (bps) must equal (GoC - UST) * 100 at every maturity in the shipped feed."""
        for p in self.sovereign_feed["cross_border_yield_curve"]:
            expected = (p["goc_yield_pct"] - p["ust_yield_pct"]) * 100.0
            assert abs(p["spread_bps"] - expected) < 0.06, f"Spread identity broken at {p['tenor']}"

    def test_acm_term_premium_additive_identity_holds_across_feed(self):
        """Nominal yield = rate path + term premium must hold for every shipped decomposition."""
        for key in ("us_profile", "ca_profile"):
            for d in self.sovereign_feed[key]["term_premium_decompositions"]:
                expected = d["nominal_yield_pct"] - d["risk_neutral_rate_path_pct"]
                assert abs(d["term_premium_pct"] - expected) < 0.02, (
                    f"ACM identity broken: {key} {d['tenor']}"
                )
                assert abs(d["term_premium_bps"] - d["term_premium_pct"] * 100.0) < 0.6

    def test_slope_fields_match_benchmark_differentials_in_feed(self):
        """Shipped slope fields must equal the raw yield differentials from the yield ladder."""
        parsed = SovereignYieldFeed.model_validate(self.sovereign_feed)
        for profile in (parsed.us_profile, parsed.ca_profile):
            y = {p.tenor: (p.ust_yield_pct if profile.jurisdiction == "US" else p.goc_yield_pct)
                 for p in profile.yield_points}
            assert profile.slope_2y10y_bps == pytest.approx((y["10Y"] - y["2Y"]) * 100.0, abs=0.06)
            assert profile.slope_3m10y_bps == pytest.approx((y["10Y"] - y["3M"]) * 100.0, abs=0.06)
            assert profile.slope_5y30y_bps == pytest.approx((y["30Y"] - y["5Y"]) * 100.0, abs=0.06)

    def test_nelson_siegel_parameters_are_finite_and_bounded(self):
        """Fitted NSS parameters must be finite and inside plausible sovereign ranges."""
        for key in ("us_profile", "ca_profile"):
            nss = self.sovereign_feed[key]["nss_params"]
            assert 0.0 < nss["beta0_level"] < 15.0
            assert math.isfinite(nss["beta1_slope"])
            assert math.isfinite(nss["beta2_curvature"])
            assert 0.1 <= nss["tau"] <= 10.0

    def test_yield_regime_labels_are_from_canonical_taxonomy(self):
        """Curve regime labels must come from the six-regime macroeconomic taxonomy."""
        allowed = {"BEAR_STEEPENER", "BULL_STEEPENER", "BEAR_FLATTENER", "BULL_FLATTENER", "INVERTED", "NORMAL"}
        for key in ("us_profile", "ca_profile"):
            assert self.sovereign_feed[key]["curve_regime"] in allowed

    # ------------------------------------------------------------------
    # 3. Sentinel and orchestrator wiring
    # ------------------------------------------------------------------
    def test_predicates_22_and_23_are_wired_into_sentinel_engine(self):
        """The sentinel engine must expose the sovereign_yield_metrics parameter and both predicates."""
        import inspect
        from src.engine.quant_intel_sentinel import quant_intel_sentinel

        sig = inspect.signature(quant_intel_sentinel.evaluate_portfolio_level_sentinels)
        assert "sovereign_yield_metrics" in sig.parameters

        source = inspect.getsource(quant_intel_sentinel.evaluate_portfolio_level_sentinels)
        assert "SOVEREIGN_YIELD_CURVE_INVERSION" in source
        assert "TERM_PREMIUM_SHOCK" in source
        assert "DEFENSIVE_DURATION_BIAS" in source
        assert "COMPRESS_EQUITY_VALUATION_MULTIPLES" in source

    def test_portfolio_orchestrator_consumes_sovereign_curve_engine(self):
        """The orchestrator must import, evaluate, and pass sovereign metrics to the sentinel."""
        src = (Path("src") / "engine" / "portfolio_orchestrator.py").read_text(encoding="utf-8")
        assert "from src.engine.sovereign_yield_curve import sovereign_yield_curve_engine" in src
        assert "sovereign_yield_curve_engine.evaluate_sovereign_curves()" in src
        assert "sovereign_yield_metrics=sovereign_res.model_dump()" in src

    def test_edge_exporter_stages_sovereign_feed(self):
        """The edge exporter and Firebase build script must both handle the new feed."""
        exporter = (Path("src") / "data" / "edge_exporter.py").read_text(encoding="utf-8")
        assert "sovereign_yield_curve_engine.export_feed" in exporter

        build = (Path("scripts") / "build_firebase_public.py").read_text(encoding="utf-8")
        assert '("sovereign_yield_curve.json", "sovereign_yield_curve.json")' in build
        assert '("sovereign_yield_curve.json", "sovereign_yield_curve")' in build

    # ------------------------------------------------------------------
    # 4. Terminal UI workstation integration and synchronization
    # ------------------------------------------------------------------
    def test_terminal_ui_workstation_integration_and_synchronization(self):
        """web/index.html and public/index.html must both contain the Phase 31 workstation, byte-identical."""
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        pub_html = (self.public_dir / "index.html").read_text(encoding="utf-8")

        assert web_html == pub_html, "public/index.html drifted from web/index.html"

        for html_content in (web_html, pub_html):
            # Tab button and panel
            assert 'data-tab="sovereigncurve"' in html_content
            assert 'id="tab-sovereigncurve"' in html_content
            assert "Sovereign Curve" in html_content
            assert "'sovereigncurve'" in html_content  # registered in switchTab tabIds

            # Workstation header taxonomy tags
            assert "NELSON-SIEGEL FIT" in html_content
            assert "ACM TERM PREMIUM" in html_content
            assert "SLOPE REGIMES" in html_content

            # Jurisdiction filter controls
            assert "setYieldJurisdiction('US')" in html_content
            assert "setYieldJurisdiction('CA')" in html_content

            # Renderer and loader functions
            assert "loadSovereignCurveAndRender" in html_content
            assert "renderSovereignCurveView" in html_content
            assert "buildYieldCurveSvg" in html_content
            assert "buildTermPremiumBars" in html_content
            assert "refreshSovereignData" in html_content

            # Feed fetching (both .json and extensionless fallback)
            assert "/api/sovereign_yield_curve.json" in html_content
            assert "/api/sovereign_yield_curve'" in html_content

            # Sentinel predicate surfacing
            assert "SENTINEL TRIGGERED (P22/P23)" in html_content

    # ------------------------------------------------------------------
    # 5. Statutory compliance
    # ------------------------------------------------------------------
    def test_master_release_compliance_linter_zero_violations(self):
        """All Phase 31 disclaimers, text copies, and UI notices must pass ImpersonalAdviceLinter."""
        linter = ImpersonalAdviceLinter()

        for disc in self.sovereign_feed.get("disclaimers", []):
            violations = linter.lint_text(disc)
            assert violations == [], f"Linter violation in sovereign disclaimer: {violations}"

        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "Nelson-Siegel-Svensson parametric curve fitting" in web_html
        assert "STATUTORY RESEARCH NOTICE" in web_html

        # No personalized advisory language anywhere in the Phase 31 UI copy
        for phrase in ("you should buy", "we recommend you", "buy this bond", "recommended allocation"):
            assert phrase not in web_html.lower()
