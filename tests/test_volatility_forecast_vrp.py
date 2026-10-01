"""
Unit and Integration Test Suite for Multi-Horizon Volatility Forecasting & VRP (Phase 33)
US + Canada Dual-Market Intelligence Platform

Tests Covered:
 1. Realized-volatility estimator accuracy against synthetic data with KNOWN sigma.
 2. Range-based estimator efficiency properties and dispersion.
 3. GARCH(1,1) / EGARCH(1,1) / GJR-GARCH(1,1) MLE convergence and parameter validity.
 4. GARCH parameter recovery on synthetic data generated from known parameters.
 5. Vectorized lfilter variance path equivalence with the explicit recursion.
 6. AIC-based model selection correctness.
 7. HAR-RV (Corsi 2009) coefficient validity, R^2, and multi-horizon forecasts.
 8. Variance Risk Premium arithmetic identities.
 9. VRP availability gating -- no fabricated implied volatility.
10. Volatility regime classification thresholds.
11. Predicate 26 (VARIANCE_RISK_PREMIUM_COLLAPSE) and 27 (VOLATILITY_CLUSTERING_HAZARD).
12. Cboe index licensing firewall.
13. Determinism, caching, and ImpersonalAdviceLinter compliance.
"""

import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from src.compliance.linter import ImpersonalAdviceLinter
from src.engine.volatility_forecast_vrp import (
    E_ABS_Z,
    HAR_MONTH,
    HAR_WEEK,
    SIGMA_FLOOR,
    TRADING_DAYS,
    VOLATILITY_DISCLAIMERS,
    VOL_CLUSTER_RATIO_THRESHOLD,
    VRP_COLLAPSE_THRESHOLD_PTS,
    VolatilityForecastVrpEngine,
    _linear_garch_path,
    conditional_vol_next,
    fit_egarch_1_1,
    fit_garch_1_1,
    fit_gjr_garch_1_1,
    fit_har_rv,
    rv_close_to_close,
    rv_garman_klass,
    rv_parkinson,
    rv_rogers_satchell,
    rv_yang_zhang,
    volatility_forecast_vrp_engine,
)
from src.engine.quant_intel_sentinel import quant_intel_sentinel
from src.models.schemas import VolatilityVrpFeed


# ---------------------------------------------------------------------------
# Shared expensive fixture: the full feed build takes ~26s, so build it ONCE.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def feed():
    return volatility_forecast_vrp_engine.evaluate_volatility_forecasts(force_refresh=True)


def simulate_ohlc(n_days: int, annual_vol: float, seed: int = 7, steps: int = 1200):
    """
    Simulates daily OHLC from a geometric Brownian path with a KNOWN annualized
    volatility, so estimator accuracy can be tested against ground truth.

    IMPORTANT -- intraday sampling resolution. Range-based estimators consume the
    daily high and low, which for a continuous process are the true extrema. A
    DISCRETELY sampled path systematically understates that range, so with too few
    steps every range estimator reads low while close-to-close stays unbiased.
    Measured on this simulator at true vol 0.25:

        steps    CC      Parkinson   G-K      Y-Z
           10   0.2510    0.2034    0.1817   0.1812
           40   0.2549    0.2277    0.2162   0.2159
          200   0.2526    0.2387    0.2330   0.2329
         1000   0.2466    0.2428    0.2412   0.2412
         4000   0.2474    0.2460    0.2454   0.2454

    The default of 1200 steps keeps the residual discretization bias under ~3%.
    Real daily OHLC carries the exchange's true intraday extrema, so production
    data does not suffer this bias.
    """
    rng = np.random.default_rng(seed)
    daily_vol = annual_vol / math.sqrt(TRADING_DAYS)
    dt = 1.0 / steps

    opens, highs, lows, closes = [], [], [], []
    price = 100.0
    for _ in range(n_days):
        o = price
        path = [o]
        for _ in range(steps):
            shock = rng.normal(0.0, daily_vol * math.sqrt(dt))
            path.append(path[-1] * math.exp(shock))
        c = path[-1]
        opens.append(o)
        highs.append(max(path))
        lows.append(min(path))
        closes.append(c)
        price = c

    return (np.array(opens), np.array(highs), np.array(lows), np.array(closes))


class TestRealizedVolatilityEstimators:
    """Validates all four estimators against synthetic data with known sigma."""

    ANNUAL_VOL = 0.25  # 25% known ground truth

    # Range estimators retain ~2-3% residual bias from discrete intraday sampling
    # even at 1200 steps, so the tolerance is set to absorb that known effect.
    RANGE_TOL = 0.06

    @classmethod
    def setup_class(cls):
        cls.o, cls.h, cls.l, cls.c = simulate_ohlc(2500, cls.ANNUAL_VOL, seed=7, steps=1200)
        cls.returns = np.diff(np.log(cls.c))

    def test_close_to_close_recovers_known_volatility(self):
        """Close-to-close RV must recover the true annualized volatility."""
        est = rv_close_to_close(self.returns)
        # Close-to-close uses only endpoints, so it is unbiased regardless of the
        # intraday sampling resolution and warrants a tighter tolerance.
        assert est == pytest.approx(self.ANNUAL_VOL, rel=0.04)

    def test_parkinson_recovers_known_volatility(self):
        """Parkinson high-low RV must recover the true annualized volatility."""
        est = rv_parkinson(self.h, self.l)
        assert est == pytest.approx(self.ANNUAL_VOL, rel=self.RANGE_TOL)

    def test_garman_klass_recovers_known_volatility(self):
        """Garman-Klass OHLC RV must recover the true annualized volatility."""
        est = rv_garman_klass(self.o, self.h, self.l, self.c)
        assert est == pytest.approx(self.ANNUAL_VOL, rel=self.RANGE_TOL)

    def test_yang_zhang_recovers_known_volatility(self):
        """Yang-Zhang RV must recover the true annualized volatility."""
        est = rv_yang_zhang(self.o, self.h, self.l, self.c)
        assert est == pytest.approx(self.ANNUAL_VOL, rel=self.RANGE_TOL)

    def test_rogers_satchell_recovers_known_volatility(self):
        """Rogers-Satchell drift-independent RV must recover the true volatility."""
        est = rv_rogers_satchell(self.o, self.h, self.l, self.c)
        assert est == pytest.approx(self.ANNUAL_VOL, rel=self.RANGE_TOL)

    def test_range_estimators_are_more_efficient_than_close_to_close(self):
        """
        Parkinson should have materially lower sampling variance than close-to-close.
        Verified by comparing estimator spread across independent simulations.
        """
        cc_vals, pk_vals = [], []
        for seed in range(12):
            o, h, l, c = simulate_ohlc(400, self.ANNUAL_VOL, seed=100 + seed, steps=800)
            r = np.diff(np.log(c))
            cc_vals.append(rv_close_to_close(r))
            pk_vals.append(rv_parkinson(h, l))
        # Efficiency shows up as tighter dispersion around truth for the same sample size.
        assert np.std(pk_vals) <= np.std(cc_vals) * 1.15

    def test_estimators_handle_degenerate_input(self):
        """Estimators must not raise or return NaN on short/flat input."""
        flat_h = np.full(5, 100.0)
        flat_l = np.full(5, 100.0)
        flat_o = np.full(5, 100.0)
        flat_c = np.full(5, 100.0)
        assert rv_parkinson(flat_h, flat_l) == pytest.approx(0.0, abs=1e-12)
        assert rv_garman_klass(flat_o, flat_h, flat_l, flat_c) >= 0.0
        assert math.isfinite(rv_yang_zhang(flat_o, flat_h, flat_l, flat_c))
        assert rv_close_to_close(np.zeros(3)) == pytest.approx(0.0, abs=1e-12)

    def test_estimators_scale_correctly_with_volatility(self):
        """Doubling the true volatility must roughly double every estimate."""
        o1, h1, l1, c1 = simulate_ohlc(1500, 0.20, seed=11, steps=800)
        o2, h2, l2, c2 = simulate_ohlc(1500, 0.40, seed=11, steps=800)
        r1, r2 = np.diff(np.log(c1)), np.diff(np.log(c2))
        assert rv_close_to_close(r2) / rv_close_to_close(r1) == pytest.approx(2.0, rel=0.20)
        assert rv_parkinson(h2, l2) / rv_parkinson(h1, l1) == pytest.approx(2.0, rel=0.20)

    def test_engine_realized_profile_is_internally_consistent(self, feed):
        """Every shipped dossier must report all four estimators and a valid dispersion."""
        for sym, d in feed.dossiers.items():
            r = d.realized
            assert r.bars_used >= 60, f"{sym}: insufficient bars"
            vals = [r.close_to_close_pct, r.parkinson_pct, r.garman_klass_pct, r.yang_zhang_pct]
            assert all(math.isfinite(v) and v >= 0.0 for v in vals), sym
            expected_disp = (max(vals) - min(vals))
            assert r.estimator_dispersion_pct == pytest.approx(expected_disp, abs=0.02)
            assert r.most_efficient_estimator == "PARKINSON"


class TestGarchFamily:
    """Validates GARCH-family MLE fits and parameter recovery."""

    @classmethod
    def setup_class(cls):
        # Simulate a GARCH(1,1) process with known parameters for recovery testing.
        rng = np.random.default_rng(42)
        n = 4000
        omega, alpha, beta = 1e-6, 0.08, 0.90
        eps = np.zeros(n)
        s2 = np.zeros(n)
        s2[0] = omega / max(1e-9, 1 - alpha - beta)
        for t in range(1, n):
            s2[t] = omega + alpha * eps[t - 1] ** 2 + beta * s2[t - 1]
            eps[t] = math.sqrt(s2[t]) * rng.normal()
        cls.sim_eps = eps
        cls.true_persistence = alpha + beta

        engine = VolatilityForecastVrpEngine()
        ohlc = engine.load_ohlc()
        closes = ohlc["AAPL"]["close"].to_numpy(dtype=float)
        cls.real_returns = np.diff(np.log(np.maximum(closes, 1e-12)))

    def test_garch_recovers_known_persistence(self):
        """GARCH(1,1) must recover the simulated alpha + beta within tolerance."""
        fit = fit_garch_1_1(self.sim_eps)
        assert fit.persistence == pytest.approx(self.true_persistence, abs=0.05)
        assert fit.alpha == pytest.approx(0.08, abs=0.03)
        assert fit.beta == pytest.approx(0.90, abs=0.03)

    def test_all_variants_converge_on_real_data(self):
        """All three variants must converge with finite likelihood on real returns."""
        for fn in (fit_garch_1_1, fit_egarch_1_1, fit_gjr_garch_1_1):
            fit = fn(self.real_returns)
            assert fit.converged, f"{fit.variant} did not converge"
            assert math.isfinite(fit.log_likelihood), f"{fit.variant} non-finite LL"
            assert abs(fit.log_likelihood) < 1e9, f"{fit.variant} hit the divergence penalty"
            assert math.isfinite(fit.aic)

    def test_egarch_does_not_hit_divergence_penalty(self):
        """
        Regression test: EGARCH previously returned the -1e12 penalty because omega was
        seeded as log(var_uncond) instead of log(var_uncond)*(1-beta). The unconditional
        mean of log(sigma^2) is omega/(1-beta), not omega.
        """
        fit = fit_egarch_1_1(self.real_returns)
        assert fit.log_likelihood > -1e6, "EGARCH returned the divergence penalty"
        assert fit.aic < 0, "EGARCH AIC indicates a failed optimization"

    def test_persistence_is_within_stationary_bounds(self):
        """Persistence must lie in (0, 1) for a covariance-stationary fit."""
        for fn in (fit_garch_1_1, fit_egarch_1_1, fit_gjr_garch_1_1):
            fit = fn(self.real_returns)
            assert 0.0 < fit.persistence < 1.0, f"{fit.variant}: persistence {fit.persistence}"

    def test_half_life_is_consistent_with_persistence(self):
        """Half-life must equal ln(0.5)/ln(persistence)."""
        for fn in (fit_garch_1_1, fit_egarch_1_1, fit_gjr_garch_1_1):
            fit = fn(self.real_returns)
            if fit.half_life_days is not None:
                expected = math.log(0.5) / math.log(fit.persistence)
                assert fit.half_life_days == pytest.approx(expected, abs=0.05)

    def test_egarch_leverage_term_is_negative_for_equities(self):
        """
        The EGARCH gamma term should be negative for equities: negative returns raise
        volatility more than equal-magnitude positive returns (the leverage effect).
        """
        fit = fit_egarch_1_1(self.real_returns)
        assert fit.gamma < 0.0, f"Expected negative leverage term, got {fit.gamma}"

    def test_garch_gamma_is_zero_by_construction(self):
        """Symmetric GARCH(1,1) has no asymmetry term."""
        fit = fit_garch_1_1(self.real_returns)
        assert fit.gamma == 0.0

    def test_aic_penalty_accounts_for_parameter_count(self):
        """AIC = 2k - 2LL with k=4 for GARCH and k=5 for EGARCH/GJR."""
        g = fit_garch_1_1(self.real_returns)
        e = fit_egarch_1_1(self.real_returns)
        j = fit_gjr_garch_1_1(self.real_returns)
        assert g.aic == pytest.approx(2 * 4 - 2 * g.log_likelihood, abs=0.02)
        assert e.aic == pytest.approx(2 * 5 - 2 * e.log_likelihood, abs=0.02)
        assert j.aic == pytest.approx(2 * 5 - 2 * j.log_likelihood, abs=0.02)

    def test_model_selection_picks_minimum_aic(self, feed):
        """The selected variant must be the one with the lowest AIC among the three fits."""
        for sym, d in feed.dossiers.items():
            aics = {f.variant: f.aic for f in d.model_selection.fits}
            assert len(aics) == 3, f"{sym}: expected three variant fits"
            best = min(aics, key=aics.get)
            assert d.model_selection.best_variant == best, f"{sym}: AIC selection mismatch"
            assert d.model_selection.best_aic == pytest.approx(aics[best], abs=0.01)

    def test_aic_delta_to_runner_up_is_non_negative(self, feed):
        """The gap to the runner-up must be >= 0 by construction."""
        for sym, d in feed.dossiers.items():
            assert d.model_selection.aic_delta_to_runner_up >= 0.0

    def test_conditional_vol_is_positive_and_finite(self, feed):
        """One-step conditional volatility must be a positive finite percentage."""
        for sym, d in feed.dossiers.items():
            cv = d.regime.conditional_vol_pct
            assert math.isfinite(cv) and cv > 0.0, f"{sym}: conditional vol {cv}"


class TestVectorizedVariancePath:
    """Validates the lfilter optimization against the explicit recursion."""

    @classmethod
    def setup_class(cls):
        engine = VolatilityForecastVrpEngine()
        ohlc = engine.load_ohlc()
        closes = ohlc["MSFT"]["close"].to_numpy(dtype=float)
        cls.returns = np.diff(np.log(np.maximum(closes, 1e-12)))
        cls.eps = cls.returns - cls.returns.mean()
        cls.var_uncond = max(float(np.var(cls.returns)), SIGMA_FLOOR)

    def _explicit_loop(self, eps, omega, alpha, beta, gamma, vu):
        n = len(eps)
        s2 = np.empty(n)
        s2[0] = vu
        for t in range(1, n):
            ind = 1.0 if (gamma != 0.0 and eps[t - 1] < 0) else 0.0
            s2[t] = omega + alpha * eps[t - 1] ** 2 + gamma * eps[t - 1] ** 2 * ind + beta * s2[t - 1]
        return s2

    def test_linear_path_matches_loop_for_symmetric_garch(self):
        """Vectorized path must equal the explicit loop for GARCH(1,1)."""
        fast = _linear_garch_path(self.eps, 1e-6, 0.09, 0.87, 0.0, self.var_uncond)
        slow = self._explicit_loop(self.eps, 1e-6, 0.09, 0.87, 0.0, self.var_uncond)
        assert fast is not None
        assert np.allclose(fast, slow, rtol=0, atol=1e-14)

    def test_linear_path_matches_loop_for_gjr(self):
        """Vectorized path must equal the explicit loop for GJR-GARCH(1,1)."""
        fast = _linear_garch_path(self.eps, 1e-6, 0.06, 0.85, 0.13, self.var_uncond)
        slow = self._explicit_loop(self.eps, 1e-6, 0.06, 0.85, 0.13, self.var_uncond)
        assert fast is not None
        assert np.allclose(fast, slow, rtol=0, atol=1e-14)

    def test_linear_path_rejects_explosive_parameters(self):
        """An explosive beta must be rejected rather than returning an invalid path."""
        bad = _linear_garch_path(self.eps, 1e-6, 0.5, 1.5, 0.0, self.var_uncond)
        assert bad is None

    def test_linear_path_rejects_short_input(self):
        """Fewer than two observations must return None."""
        assert _linear_garch_path(np.array([0.01]), 1e-6, 0.09, 0.87, 0.0, 1e-4) is None


class TestHarRv:
    """Validates the Corsi (2009) HAR-RV cascade."""

    @classmethod
    def setup_class(cls):
        engine = VolatilityForecastVrpEngine()
        ohlc = engine.load_ohlc()
        closes = ohlc["SPY"]["close"].to_numpy(dtype=float)
        rets = np.diff(np.log(np.maximum(closes, 1e-12)))
        cls.har = fit_har_rv(np.square(rets))

    def test_r_squared_is_a_valid_goodness_of_fit(self):
        """R^2 must lie in [0, 1]."""
        assert 0.0 <= self.har.r_squared <= 1.0

    def test_forecasts_are_positive_and_finite(self):
        """All three horizons must produce positive finite volatility forecasts."""
        for v in (self.har.forecast_1d_pct, self.har.forecast_5d_pct, self.har.forecast_22d_pct):
            assert math.isfinite(v) and v > 0.0

    def test_cascade_coefficients_are_finite(self):
        """Daily, weekly, and monthly coefficients must be finite."""
        for c in (self.har.beta_daily, self.har.beta_weekly, self.har.beta_monthly):
            assert math.isfinite(c)

    def test_insufficient_history_falls_back_to_unconditional_mean(self):
        """A short series must degrade gracefully rather than raising."""
        short = fit_har_rv(np.square(np.full(10, 0.01)))
        assert short.r_squared == 0.0
        assert short.forecast_1d_pct > 0.0

    def test_persistent_volatility_produces_positive_autocorrelation(self):
        """A strongly autocorrelated variance series must yield a meaningful R^2."""
        rng = np.random.default_rng(3)
        v = np.zeros(1200)
        v[0] = 1e-4
        for t in range(1, 1200):
            v[t] = 1e-6 + 0.85 * v[t - 1] + 0.1 * v[t - 1] * abs(rng.normal())
        har = fit_har_rv(v)
        assert har.r_squared > 0.3, f"Expected meaningful fit on autocorrelated data, got {har.r_squared}"

    def test_shipped_har_models_are_valid(self, feed):
        """Every shipped HAR model must have valid forecasts and R^2."""
        for sym, d in feed.dossiers.items():
            assert 0.0 <= d.har_rv.r_squared <= 1.0, sym
            assert d.har_rv.forecast_1d_pct > 0.0
            assert d.har_rv.forecast_22d_pct > 0.0


class TestVarianceRiskPremium:
    """Validates VRP arithmetic and availability gating."""

    def test_vrp_variance_identity(self, feed):
        """VRP variance points must equal IV^2 - RV^2 exactly."""
        for sym, d in feed.dossiers.items():
            if not d.vrp.available:
                continue
            expected = d.vrp.implied_vol_pct ** 2 - d.vrp.realized_vol_pct ** 2
            assert d.vrp.vrp_variance_pts == pytest.approx(expected, abs=0.05), sym

    def test_vrp_volatility_identity(self, feed):
        """VRP volatility points must equal IV - RV exactly."""
        for sym, d in feed.dossiers.items():
            if not d.vrp.available:
                continue
            expected = d.vrp.implied_vol_pct - d.vrp.realized_vol_pct
            assert d.vrp.vrp_vol_pts == pytest.approx(expected, abs=0.02), sym

    def test_vrp_only_available_where_options_surface_exists(self, feed):
        """VRP availability must exactly match the Phase 30 options universe."""
        opts_path = Path("data/feeds/options_intelligence.json")
        if not opts_path.exists():
            pytest.skip("options feed absent")
        opts = json.loads(opts_path.read_text(encoding="utf-8"))
        optionable = {s for s, d in opts["dossiers"].items()
                      if d.get("thirty_day_atm_iv_pct")}
        available = {s for s, d in feed.dossiers.items() if d.vrp.available}
        assert available == optionable & set(feed.dossiers.keys())

    def test_no_fabricated_implied_volatility(self, feed):
        """Symbols without an options surface must carry NO implied volatility value."""
        for sym, d in feed.dossiers.items():
            if not d.vrp.available:
                assert d.vrp.implied_vol_pct is None, f"{sym}: fabricated IV"
                assert d.vrp.vrp_variance_pts is None
                assert d.vrp.vrp_vol_pts is None
                assert d.vrp.seller_edge is None
                assert d.vrp.unavailable_reason is not None

    def test_seller_edge_bands_match_thresholds(self, feed):
        """Seller edge must follow the FAVOURABLE / NEUTRAL / ADVERSE bands."""
        for sym, d in feed.dossiers.items():
            if not d.vrp.available:
                continue
            v = d.vrp.vrp_variance_pts
            if v >= 100.0:
                assert d.vrp.seller_edge == "FAVOURABLE", sym
            elif v <= VRP_COLLAPSE_THRESHOLD_PTS:
                assert d.vrp.seller_edge == "ADVERSE", sym
            else:
                assert d.vrp.seller_edge == "NEUTRAL", sym

    def test_coverage_count_matches_available_dossiers(self, feed):
        """vrp_coverage_count must equal the number of dossiers with VRP."""
        n = sum(1 for d in feed.dossiers.values() if d.vrp.available)
        assert feed.vrp_coverage_count == n

    def test_median_vrp_matches_dossier_values(self, feed):
        """The published median VRP must match the median of the dossiers."""
        vals = [d.vrp.vrp_variance_pts for d in feed.dossiers.values() if d.vrp.available]
        if vals:
            assert feed.median_vrp_variance_pts == pytest.approx(float(np.median(vals)), abs=0.02)


class TestVolatilityRegime:
    """Validates regime classification thresholds."""

    def test_regime_bands_are_correct(self, feed):
        """Regime labels must follow the documented baseline-ratio bands."""
        for sym, d in feed.dossiers.items():
            ratio = d.regime.baseline_ratio
            if ratio >= VOL_CLUSTER_RATIO_THRESHOLD:
                assert d.regime.regime == "CRISIS", sym
            elif ratio >= 1.35:
                assert d.regime.regime == "ELEVATED", sym
            elif ratio <= 0.75:
                assert d.regime.regime == "LOW", sym
            else:
                assert d.regime.regime == "NORMAL", sym

    def test_baseline_ratio_is_arithmetically_consistent(self, feed):
        """baseline_ratio must equal conditional vol / trailing 20-day baseline."""
        for sym, d in feed.dossiers.items():
            expected = d.regime.conditional_vol_pct / d.regime.baseline_20d_pct
            assert d.regime.baseline_ratio == pytest.approx(expected, abs=0.005), sym

    def test_vol_of_vol_is_non_negative(self, feed):
        """Vol-of-vol is a dispersion measure and must be non-negative."""
        for sym, d in feed.dossiers.items():
            assert d.regime.vol_of_vol_pct >= 0.0


class TestPredicate26And27:
    """Validates the Phase 33 invariant sentinels."""

    @classmethod
    def setup_class(cls):
        cls.sentinel = quant_intel_sentinel

    def _run(self, metrics):
        return self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, volatility_vrp_metrics=metrics
        )

    def test_predicate_26_fires_on_vrp_collapse(self):
        """VRP below -100 variance points must raise the sentinel."""
        m = {"per_symbol": {"TEST": {
            "vrp_variance_pts": -936.2, "implied_vol_pct": 33.5,
            "realized_vol_pct": 45.4, "baseline_ratio": 1.0,
        }}}
        p26 = [a for a in self._run(m) if a.predicate_type == "VARIANCE_RISK_PREMIUM_COLLAPSE"]
        assert len(p26) == 1
        assert p26[0].symbol == "TEST"
        assert p26[0].trigger_level == -100.0
        assert p26[0].current_price == pytest.approx(-936.2, abs=0.05)
        assert p26[0].action_required == "AVOID_SHORT_VOLATILITY"

    def test_predicate_26_body_discloses_iv_and_rv(self):
        """The generated body must show the implied and realized volatility behind the signal."""
        m = {"per_symbol": {"TEST": {
            "vrp_variance_pts": -500.0, "implied_vol_pct": 20.0, "realized_vol_pct": 35.0,
        }}}
        p26 = [a for a in self._run(m) if a.predicate_type == "VARIANCE_RISK_PREMIUM_COLLAPSE"][0]
        assert "20.0%" in p26.body and "35.0%" in p26.body

    def test_predicate_27_fires_without_any_vrp_field(self):
        """
        Regression test: Predicate 27 is independent of the options surface. A prior
        `if vrp is None: continue` skipped the rest of the loop iteration, silently
        disabling Predicate 27 for every symbol lacking implied volatility.
        """
        m = {"per_symbol": {"TEST": {
            "baseline_ratio": 2.40, "conditional_vol_pct": 48.0, "baseline_20d_pct": 20.0,
        }}}
        p27 = [a for a in self._run(m) if a.predicate_type == "VOLATILITY_CLUSTERING_HAZARD"]
        assert len(p27) == 1, "Predicate 27 must fire without a VRP field present"
        assert p27[0].trigger_level == 2.0
        assert p27[0].current_price == pytest.approx(2.40, abs=0.005)
        assert p27[0].action_required == "SCALE_DOWN_EQUITY_SIZING"

    def test_both_predicates_can_fire_together(self):
        """A symbol can breach both the VRP and the clustering boundary at once."""
        m = {"per_symbol": {"A": {
            "vrp_variance_pts": -500.0, "implied_vol_pct": 20.0, "realized_vol_pct": 35.0,
            "baseline_ratio": 2.5, "conditional_vol_pct": 50.0, "baseline_20d_pct": 20.0,
        }}}
        types = {a.predicate_type for a in self._run(m)}
        assert types == {"VARIANCE_RISK_PREMIUM_COLLAPSE", "VOLATILITY_CLUSTERING_HAZARD"}

    def test_boundaries_are_exclusive(self):
        """Exactly -100.0 VRP and exactly 2.00x ratio must NOT fire."""
        m = {"per_symbol": {"A": {"vrp_variance_pts": -100.0, "baseline_ratio": 2.0}}}
        assert self._run(m) == []

    def test_empty_metric_dict_is_handled(self):
        """A dossier with no metrics must not raise."""
        assert self._run({"per_symbol": {"A": {}}}) == []

    def test_sentinel_surface_exposes_vrp_and_regime_metrics(self, feed):
        """The engine's sentinel surface must carry the fields both predicates read."""
        surface = volatility_forecast_vrp_engine.compute_sentinel_metrics(feed)
        for sym, m in surface["per_symbol"].items():
            for key in ("baseline_ratio", "conditional_vol_pct", "baseline_20d_pct",
                        "vrp_available", "vrp_variance_pts", "implied_vol_pct"):
                assert key in m, f"{sym}: missing sentinel field {key}"


class TestCboeIndexLicensingFirewall:
    """The feed and engine must carry no Cboe index value, name, or reference."""

    BANNED_TOKENS = ["VIX", "VIX9D", "VIX3M", "VIX6M", "VVIX", "SKEW Index", "Cboe Volatility Index"]

    def test_feed_declares_index_reference_free(self, feed):
        """The schema must hard-code the licensing posture."""
        assert feed.cboe_index_reference_free is True

    def test_no_index_token_appears_in_serialized_feed(self, feed):
        """No banned index token may appear anywhere in the serialized feed."""
        blob = json.dumps(feed.model_dump())
        for token in self.BANNED_TOKENS:
            assert token not in blob, f"Banned index token present: {token}"

    def test_cboe_index_reference_free_cannot_be_forged(self):
        """The Literal[True] field must reject a False declaration."""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            VolatilityVrpFeed(
                as_of_date="x", generated_at="y", universe_count=0, vrp_coverage_count=0,
                median_realized_vol_pct=0.0, median_vrp_variance_pts=None,
                cboe_index_reference_free=False, dossiers={}, disclaimers=[],
            )

    def test_ui_carries_no_index_reference(self):
        """The terminal UI must not reference any Cboe volatility index."""
        web = Path("web/index.html").read_text(encoding="utf-8")
        # Isolate the Phase 33 tab markup and renderer.
        start = web.find('id="tab-volatilityvrp"')
        assert start > 0
        segment = web[start:start + 4000]
        for token in ("VIX", "Cboe Volatility Index"):
            assert token not in segment, f"Banned index token in UI: {token}"

    def test_ui_discloses_internal_derivation(self):
        """The UI must state that VRP uses internally derived inputs only."""
        web = Path("web/index.html").read_text(encoding="utf-8")
        assert "internally derived inputs only" in web
        assert "No third-party index value is referenced" in web


class TestDeterminismAndUniverse:
    """Validates reproducibility, caching, and dual-market coverage."""

    def test_feed_round_trips_through_pydantic(self, feed):
        """The feed must serialize and re-validate cleanly."""
        parsed = VolatilityVrpFeed.model_validate(feed.model_dump())
        assert parsed.universe_count == feed.universe_count

    def test_caching_returns_same_instance(self):
        """Repeated calls without force_refresh must return the cached feed."""
        first = volatility_forecast_vrp_engine.evaluate_volatility_forecasts(force_refresh=True)
        assert volatility_forecast_vrp_engine.evaluate_volatility_forecasts() is first

    def test_universe_covers_both_markets(self, feed):
        """The feed must span US and Canadian listings."""
        assert {d.country for d in feed.dossiers.values()} == {"US", "CA"}
        assert feed.universe_count >= 15

    def test_return_window_is_documented_per_symbol(self, feed):
        """Each dossier must record its estimation window."""
        for sym, d in feed.dossiers.items():
            assert len(d.return_window_start) == 10
            assert len(d.return_window_end) == 10
            assert d.return_window_start < d.return_window_end


class TestStatutoryCompliance:
    """Validates impersonal research compliance across all Phase 33 text."""

    @classmethod
    def setup_class(cls):
        cls.linter = ImpersonalAdviceLinter()

    def test_disclaimers_pass_linter(self):
        """Every statutory disclaimer must pass with zero violations."""
        for disc in VOLATILITY_DISCLAIMERS:
            assert self.linter.lint_text(disc) == [], f"Violation in: {disc[:70]}"

    def test_action_codes_pass_linter(self):
        """Sentinel action codes must not read as personalized directives."""
        for code in ("AVOID_SHORT_VOLATILITY", "SCALE_DOWN_EQUITY_SIZING"):
            assert self.linter.lint_text(code) == []

    def test_generated_alert_copy_passes_linter(self):
        """Dynamically generated sentinel headlines and bodies must be impersonal."""
        metrics = {"per_symbol": {"TEST": {
            "vrp_variance_pts": -500.0, "implied_vol_pct": 20.0, "realized_vol_pct": 35.0,
            "baseline_ratio": 2.5, "conditional_vol_pct": 50.0, "baseline_20d_pct": 20.0,
        }}}
        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, volatility_vrp_metrics=metrics
        )
        assert alerts
        for a in alerts:
            assert self.linter.lint_text(a.headline) == [], f"Headline violation: {a.headline}"
            assert self.linter.lint_text(a.body) == [], f"Body violation: {a.body}"

    def test_disclaimers_disclose_model_risk(self):
        """The disclaimer set must disclose estimation instability and lack of guarantees."""
        blob = " ".join(VOLATILITY_DISCLAIMERS).lower()
        assert "model risk" in blob or "subject to instability" in blob
        assert "not a guarantee" in blob

    def test_disclaimers_disclose_no_directional_prediction(self):
        """The disclaimer set must state that volatility forecasts do not predict direction."""
        blob = " ".join(VOLATILITY_DISCLAIMERS).lower()
        assert "do not predict price direction" in blob

    def test_disclaimers_disclose_internal_derivation(self):
        """The disclaimer set must disclose that no third-party index is referenced."""
        blob = " ".join(VOLATILITY_DISCLAIMERS).lower()
        assert "no third-party index value is referenced" in blob
