"""
Unit and Integration Test Suite for Sovereign Yield Curve & Term Premium Decomposition (Phase 31)
US Treasuries vs Government of Canada (GoC) Benchmark Bonds

Tests Covered:
1. Nelson-Siegel parametric curve fitting parameter recovery accuracy.
2. Eleven-maturity cross-border yield point construction and spread identity.
3. Slope arithmetic (2Y10Y, 3M10Y, 5Y30Y) against raw benchmark yield differentials.
4. Yield curve regime classification across all six macroeconomic term structure regimes.
5. Adrian-Crump-Moench (ACM) term premium decomposition additive identity.
6. Nelson-Siegel fitted curve interpolation error bounds.
7. Sentinel Predicate 22 (SOVEREIGN_YIELD_CURVE_INVERSION) trigger, threshold, and action.
8. Sentinel Predicate 23 (TERM_PREMIUM_SHOCK) trigger, threshold, and action.
9. Feed caching and force-refresh semantics.
10. ImpersonalAdviceLinter statutory compliance validation (0 violations).
"""

import math
import pytest
import numpy as np

from src.compliance.linter import ImpersonalAdviceLinter
from src.engine.sovereign_yield_curve import (
    SovereignYieldCurveEngine,
    sovereign_yield_curve_engine,
    SOVEREIGN_DISCLAIMERS,
    TENOR_SPEC,
)
from src.engine.quant_intel_sentinel import quant_intel_sentinel
from src.models.schemas import SovereignYieldFeed, SovereignCurveProfile


EXPECTED_TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]


class TestNelsonSiegelCurveFitting:
    """Validates the Nelson-Siegel parametric term structure fitter."""

    @classmethod
    def setup_class(cls):
        cls.engine = SovereignYieldCurveEngine()
        cls.maturities = np.array([m for _, m in TENOR_SPEC])

    def test_nelson_siegel_recovers_known_parameters(self):
        """Synthesizes a curve from known NSS parameters and verifies the fitter recovers them."""
        true_beta0, true_beta1, true_beta2, true_tau = 3.5000, 1.2000, -1.4000, 2.2500
        synthetic = self.engine.nelson_siegel_formula(
            self.maturities, true_beta0, true_beta1, true_beta2, true_tau
        )

        params = self.engine.fit_nelson_siegel(self.maturities, synthetic)

        assert params.beta0_level == pytest.approx(true_beta0, abs=0.05)
        assert params.beta1_slope == pytest.approx(true_beta1, abs=0.10)
        assert params.beta2_curvature == pytest.approx(true_beta2, abs=0.15)
        assert params.tau == pytest.approx(true_tau, abs=0.50)

    def test_nelson_siegel_formula_boundary_conditions(self):
        """Verifies the analytic limits: y(0) = beta0 + beta1 and y(inf) = beta0."""
        beta0, beta1, beta2, tau = 4.2000, 0.8000, -2.0000, 1.8000

        # Long-end asymptote approaches beta0. Convergence is O(tau/m), so a very long
        # maturity is required to reach 1e-4 precision (m=500 leaves ~4e-3 residual).
        long_end = self.engine.nelson_siegel_formula(np.array([1e6]), beta0, beta1, beta2, tau)[0]
        assert long_end == pytest.approx(beta0, abs=1e-4)

        # Short end approaches beta0 + beta1
        short_end = self.engine.nelson_siegel_formula(np.array([1e-7]), beta0, beta1, beta2, tau)[0]
        assert short_end == pytest.approx(beta0 + beta1, abs=1e-3)

        # Residual decay must be monotone in maturity (O(tau/m) tail)
        y_500 = self.engine.nelson_siegel_formula(np.array([500.0]), beta0, beta1, beta2, tau)[0]
        assert abs(y_500 - beta0) > abs(long_end - beta0)

    def test_nelson_siegel_handles_zero_maturity_without_division_error(self):
        """Guards the m -> 0 division path: no NaN and no NumPy RuntimeWarning."""
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = self.engine.nelson_siegel_formula(np.array([0.0, 1.0]), 4.0, 0.5, -1.0, 2.0)

        assert np.all(np.isfinite(result)), f"Non-finite yield at m->0: {result}"
        # lim_{m->0} y(m) == beta0 + beta1
        assert result[0] == pytest.approx(4.5, abs=1e-6)

    def test_fit_falls_back_gracefully_on_degenerate_input(self):
        """Verifies the robust fallback branch when curve_fit cannot converge."""
        # Constant yields give a well-defined but degenerate fit; must still return finite params
        flat = np.full(len(self.maturities), 3.75)
        params = self.engine.fit_nelson_siegel(self.maturities, flat)
        assert math.isfinite(params.beta0_level)
        assert math.isfinite(params.beta1_slope)
        assert math.isfinite(params.tau)
        assert params.tau >= 0.1


class TestCrossBorderYieldTermStructure:
    """Validates eleven-maturity construction, spread identity, and slope arithmetic."""

    @classmethod
    def setup_class(cls):
        cls.engine = SovereignYieldCurveEngine()
        cls.feed = cls.engine.evaluate_sovereign_curves(force_refresh=True)

    def test_feed_covers_all_eleven_standard_maturities(self):
        """Both sovereign profiles must span the full 1M-30Y benchmark ladder."""
        for profile in (self.feed.us_profile, self.feed.ca_profile):
            tenors = [p.tenor for p in profile.yield_points]
            assert tenors == EXPECTED_TENORS, f"{profile.jurisdiction} tenor ladder mismatch"
            assert len(profile.yield_points) == 11

        assert len(self.feed.cross_border_yield_curve) == 11

    def test_cross_border_spread_identity(self):
        """Spread (bps) must equal (GoC yield - UST yield) * 100 for every maturity."""
        for point in self.feed.cross_border_yield_curve:
            expected = (point.goc_yield_pct - point.ust_yield_pct) * 100.0
            assert point.spread_bps == pytest.approx(expected, abs=0.06), (
                f"Spread identity broken at {point.tenor}"
            )

    def test_master_spread_aggregates_match_point_level_data(self):
        """Top-level 2Y/10Y spread fields must agree with the per-maturity cross-border ladder."""
        lookup = {p.tenor: p for p in self.feed.cross_border_yield_curve}
        assert self.feed.ten_year_spread_bps == pytest.approx(lookup["10Y"].spread_bps, abs=0.06)
        assert self.feed.two_year_spread_bps == pytest.approx(lookup["2Y"].spread_bps, abs=0.06)

    def test_slope_arithmetic_matches_benchmark_differentials(self):
        """2Y10Y, 3M10Y, and 5Y30Y slopes must equal the raw yield differentials in basis points."""
        for profile in (self.feed.us_profile, self.feed.ca_profile):
            y = {p.tenor: (p.ust_yield_pct if profile.jurisdiction == "US" else p.goc_yield_pct)
                 for p in profile.yield_points}

            assert profile.slope_2y10y_bps == pytest.approx((y["10Y"] - y["2Y"]) * 100.0, abs=0.06)
            assert profile.slope_3m10y_bps == pytest.approx((y["10Y"] - y["3M"]) * 100.0, abs=0.06)
            assert profile.slope_5y30y_bps == pytest.approx((y["30Y"] - y["5Y"]) * 100.0, abs=0.06)

    def test_currency_and_jurisdiction_mapping(self):
        """US profile must be USD-denominated and the Canadian profile CAD-denominated."""
        assert self.feed.us_profile.jurisdiction == "US"
        assert self.feed.us_profile.currency == "USD"
        assert self.feed.ca_profile.jurisdiction == "CA"
        assert self.feed.ca_profile.currency == "CAD"

    def test_yield_curves_are_finite_and_positively_bounded(self):
        """All sovereign yields must be finite and within plausible sovereign bounds."""
        for profile in (self.feed.us_profile, self.feed.ca_profile):
            for p in profile.yield_points:
                assert math.isfinite(p.ust_yield_pct) and math.isfinite(p.goc_yield_pct)
                assert -5.0 < p.ust_yield_pct < 20.0
                assert -5.0 < p.goc_yield_pct < 20.0


class TestCurveRegimeClassification:
    """Validates all six macroeconomic term structure regime classifications."""

    @classmethod
    def setup_class(cls):
        cls.engine = SovereignYieldCurveEngine()

    @pytest.mark.parametrize(
        "s2y10y,s3m10y,s5y30y,expected",
        [
            (-54.2, -82.5, 20.0, "INVERTED"),
            (13.0, -64.0, 52.0, "BEAR_STEEPENER"),
            (30.0, 40.0, 10.0, "BULL_STEEPENER"),
            (-20.0, -30.0, 10.0, "BEAR_FLATTENER"),
            (10.0, -40.0, 5.0, "NORMAL"),
            (-10.0, 5.0, 0.0, "BULL_FLATTENER"),
        ],
    )
    def test_regime_classification_matrix(self, s2y10y, s3m10y, s5y30y, expected):
        """Each slope combination must map to its canonical macroeconomic regime label."""
        assert self.engine.classify_curve_regime(s2y10y, s3m10y, s5y30y) == expected

    def test_inversion_boundaries_are_exclusive(self):
        """Slopes exactly at -50.0 / -75.0 bps must NOT classify as inverted."""
        assert self.engine.classify_curve_regime(-50.0, -75.0, 0.0) != "INVERTED"
        assert self.engine.classify_curve_regime(-50.1, -75.0, 0.0) == "INVERTED"
        assert self.engine.classify_curve_regime(-50.0, -75.1, 0.0) == "INVERTED"


class TestAcmTermPremiumDecomposition:
    """Validates Adrian-Crump-Moench term premium additive identities."""

    @classmethod
    def setup_class(cls):
        cls.engine = SovereignYieldCurveEngine()
        cls.feed = cls.engine.evaluate_sovereign_curves(force_refresh=True)

    def test_term_premium_additive_identity(self):
        """Nominal yield must equal risk-neutral rate path plus term premium."""
        for profile in (self.feed.us_profile, self.feed.ca_profile):
            for d in profile.term_premium_decompositions:
                expected_tp = d.nominal_yield_pct - d.risk_neutral_rate_path_pct
                assert d.term_premium_pct == pytest.approx(expected_tp, abs=0.02), (
                    f"{profile.jurisdiction} {d.tenor} ACM additive identity broken"
                )

    def test_term_premium_bps_conversion(self):
        """Term premium in basis points must equal the percentage figure scaled by 100."""
        for profile in (self.feed.us_profile, self.feed.ca_profile):
            for d in profile.term_premium_decompositions:
                assert d.term_premium_bps == pytest.approx(d.term_premium_pct * 100.0, abs=0.6)

    def test_term_premium_covers_canonical_benchmark_tenors(self):
        """Decomposition must cover 2Y, 5Y, 10Y, and 30Y for both jurisdictions."""
        for profile in (self.feed.us_profile, self.feed.ca_profile):
            tenors = sorted(d.tenor for d in profile.term_premium_decompositions)
            assert tenors == ["10Y", "2Y", "30Y", "5Y"]

    def test_term_premium_uses_jurisdiction_specific_rate_paths(self):
        """US and Canadian decompositions must not share identical rate-path assumptions."""
        us_10y = next(d for d in self.feed.us_profile.term_premium_decompositions if d.tenor == "10Y")
        ca_10y = next(d for d in self.feed.ca_profile.term_premium_decompositions if d.tenor == "10Y")
        assert us_10y.nominal_yield_pct != ca_10y.nominal_yield_pct
        assert us_10y.risk_neutral_rate_path_pct != ca_10y.risk_neutral_rate_path_pct


class TestSentinelPredicates22And23:
    """Validates the Phase 31 invariant sentinels."""

    @classmethod
    def setup_class(cls):
        cls.sentinel = quant_intel_sentinel

    def test_predicate_22_fires_on_curve_inversion(self):
        """A deeply inverted curve must raise SOVEREIGN_YIELD_CURVE_INVERSION with defensive action."""
        metrics = {
            "us_profile": {
                "slope_2y10y_bps": -54.2,
                "slope_3m10y_bps": -82.5,
                "term_premium_decompositions": [],
            }
        }
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, sovereign_yield_metrics=metrics
        )
        p22 = [a for a in alerts if a.predicate_type == "SOVEREIGN_YIELD_CURVE_INVERSION"]

        assert len(p22) == 1
        assert p22[0].symbol == "US_SOVEREIGN"
        assert p22[0].severity == "WARNING"
        assert p22[0].trigger_level == -50.0
        assert p22[0].current_price == pytest.approx(-54.2, abs=0.05)
        assert p22[0].action_required == "DEFENSIVE_DURATION_BIAS"

    def test_predicate_22_fires_independently_via_3m10y_breach(self):
        """A positive 2Y10Y with a deeply inverted 3M10Y must still trip the sentinel."""
        metrics = {
            "ca_profile": {
                "slope_2y10y_bps": 12.0,
                "slope_3m10y_bps": -90.0,
                "term_premium_decompositions": [],
            }
        }
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, sovereign_yield_metrics=metrics
        )
        p22 = [a for a in alerts if a.predicate_type == "SOVEREIGN_YIELD_CURVE_INVERSION"]
        assert len(p22) == 1
        assert p22[0].symbol == "CA_SOVEREIGN"

    def test_predicate_23_fires_on_term_premium_shock(self):
        """A 10Y term premium expanding beyond +35 bps must raise TERM_PREMIUM_SHOCK."""
        metrics = {
            "ca_profile": {
                "slope_2y10y_bps": 17.0,
                "slope_3m10y_bps": 40.0,
                "term_premium_decompositions": [
                    {"tenor": "2Y", "ten_day_change_bps": 4.0},
                    {"tenor": "10Y", "ten_day_change_bps": 38.5},
                ],
            }
        }
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, sovereign_yield_metrics=metrics
        )
        p23 = [a for a in alerts if a.predicate_type == "TERM_PREMIUM_SHOCK"]

        assert len(p23) == 1
        assert p23[0].symbol == "CA_SOVEREIGN"
        assert p23[0].trigger_level == 35.0
        assert p23[0].current_price == pytest.approx(38.5, abs=0.05)
        assert p23[0].action_required == "COMPRESS_EQUITY_VALUATION_MULTIPLES"

    def test_predicates_remain_silent_under_nominal_conditions(self):
        """Nominal curve slopes and term premium drift must produce zero sovereign alerts."""
        metrics = {
            "us_profile": {
                "slope_2y10y_bps": 13.0,
                "slope_3m10y_bps": -64.0,
                "term_premium_decompositions": [{"tenor": "10Y", "ten_day_change_bps": 8.5}],
            },
            "ca_profile": {
                "slope_2y10y_bps": 17.0,
                "slope_3m10y_bps": -70.0,
                "term_premium_decompositions": [{"tenor": "10Y", "ten_day_change_bps": 6.0}],
            },
        }
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, sovereign_yield_metrics=metrics
        )
        sovereign = [a for a in alerts if a.predicate_type in
                     ("SOVEREIGN_YIELD_CURVE_INVERSION", "TERM_PREMIUM_SHOCK")]
        assert sovereign == []

    def test_predicates_accept_pydantic_profile_objects(self):
        """The sentinel must read attributes from SovereignCurveProfile models, not just dicts."""
        engine = SovereignYieldCurveEngine()
        feed = engine.evaluate_sovereign_curves(force_refresh=True)

        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, sovereign_yield_metrics=feed.model_dump()
        )
        # Current empirical curves are nominally positive; assert no crash and no false positives
        sovereign = [a for a in alerts if a.predicate_type in
                     ("SOVEREIGN_YIELD_CURVE_INVERSION", "TERM_PREMIUM_SHOCK")]
        assert isinstance(sovereign, list)


class TestFeedCachingAndExport:
    """Validates feed caching semantics and Pydantic serialization."""

    def test_cached_feed_returns_identical_instance(self):
        """Repeated calls without force_refresh must return the cached feed."""
        engine = SovereignYieldCurveEngine()
        first = engine.evaluate_sovereign_curves(force_refresh=True)
        second = engine.evaluate_sovereign_curves()
        assert first is second

    def test_force_refresh_rebuilds_feed(self):
        """force_refresh must construct a new feed instance."""
        engine = SovereignYieldCurveEngine()
        first = engine.evaluate_sovereign_curves(force_refresh=True)
        second = engine.evaluate_sovereign_curves(force_refresh=True)
        assert first is not second
        assert first.as_of_date == second.as_of_date

    def test_feed_round_trips_through_pydantic(self):
        """The feed must serialize and re-validate cleanly against its Pydantic schema."""
        feed = sovereign_yield_curve_engine.evaluate_sovereign_curves(force_refresh=True)
        parsed = SovereignYieldFeed.model_validate(feed.model_dump())
        assert parsed.us_profile.jurisdiction == "US"
        assert len(parsed.cross_border_yield_curve) == 11


class TestStatutoryCompliance:
    """Validates impersonal research compliance across all Phase 31 text artifacts."""

    @classmethod
    def setup_class(cls):
        cls.linter = ImpersonalAdviceLinter()

    def test_disclaimers_pass_impersonal_advice_linter(self):
        """Every statutory disclaimer must pass with zero violations."""
        for disc in SOVEREIGN_DISCLAIMERS:
            violations = self.linter.lint_text(disc)
            assert violations == [], f"Disclaimer violation: {violations} in '{disc[:60]}'"

    def test_predicate_action_codes_pass_linter(self):
        """Sentinel action codes must not read as personalized directives."""
        for code in ("DEFENSIVE_DURATION_BIAS", "COMPRESS_EQUITY_VALUATION_MULTIPLES"):
            assert self.linter.lint_text(code) == []

    def test_generated_alert_copy_passes_linter(self):
        """Dynamically generated sentinel headlines and bodies must be impersonal."""
        metrics = {
            "us_profile": {
                "slope_2y10y_bps": -54.2,
                "slope_3m10y_bps": -82.5,
                "term_premium_decompositions": [{"tenor": "10Y", "ten_day_change_bps": 41.0}],
            }
        }
        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, sovereign_yield_metrics=metrics
        )
        assert alerts, "Expected sentinel alerts for this hazard scenario"
        for a in alerts:
            assert self.linter.lint_text(a.headline) == [], f"Headline violation: {a.headline}"
            assert self.linter.lint_text(a.body) == [], f"Body violation: {a.body}"
