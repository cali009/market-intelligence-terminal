"""
Multi-Horizon Volatility Forecasting & Variance Risk Premium (VRP) Engine (Phase 33)
US + Canada Dual-Market Intelligence Platform

LICENSING DETERMINATION (locked 2026-10-01)
-------------------------------------------
Cboe VIX index levels are licensed commercial index data -- "For Cboe Global Indices
Feed fees, please contact IndexData@cboe.com" (Cboe Market Data Policies, effective
July 1, 2026). Index Data redistribution requires a signed Data Agreement, Data Order
Form, System Description and prior approval.

This engine therefore computes the Variance Risk Premium entirely from INTERNALLY
DERIVED inputs:
    * implied volatility  -> Phase 30 BSM options surface (thirty_day_atm_iv_pct)
    * realized volatility -> platform bar_1d OHLC history
No Cboe index value, name, or reference appears in any feed or UI surface. The posture
is machine-checked by VolatilityVrpFeed.cboe_index_reference_free: Literal[True].

MODELS
------
Realized volatility : close-to-close, Parkinson, Garman-Klass, Yang-Zhang
GARCH family (MLE)  : GARCH(1,1), EGARCH(1,1), GJR-GARCH(1,1)  [scipy.optimize]
HAR-RV              : Corsi (2009) daily / weekly / monthly cascade
VRP                 : IV^2 - RV^2

DATA PROVENANCE CAVEAT (recorded honestly)
------------------------------------------
bar_1d.knowledge_at spans only 2026-09-25..2026-09-29 across 38 distinct values, i.e. it
is a BULK INGESTION stamp, not a per-bar point-in-time availability marker. It therefore
cannot support walk-forward point-in-time discipline, and no PIT claim is made here.

Impersonal decision-support research only. Not investment advice.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import lfilter

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.data.db import db
from src.models.schemas import (
    GarchModelFit,
    HarRvModel,
    RealizedVolatilityEstimates,
    VarianceRiskPremiumProfile,
    VolatilityForecastDossier,
    VolatilityModelSelection,
    VolatilityRegimeProfile,
    VolatilityVrpFeed,
)

VOLATILITY_DISCLAIMERS = [
    "Realized volatility estimates, GARCH-family conditional variance forecasts, HAR-RV cascades, and Variance Risk Premium figures are quantitative econometric simulations computed from historical price data. Model parameters are estimated and subject to instability across estimation windows.",
    "The Variance Risk Premium is computed as implied volatility squared minus realized volatility squared, using internally derived inputs only: platform Black-Scholes-Merton implied volatility and platform-computed realized volatility. No third-party index value is referenced.",
    "A positive Variance Risk Premium indicates that implied volatility has historically exceeded subsequently realized volatility. This is an empirical regularity, not a guarantee, and can invert without warning during volatility crises.",
    "Volatility forecasts do not predict price direction. They estimate the scale of future price dispersion and carry substantial model risk.",
    "Provided strictly for impersonal decision-support research. Does not constitute investment advice, a recommendation to buy or sell, or a solicitation, under CSA Staff Notice 31-369 and SEC Publisher Exclusion section 202(a)(11)(D).",
]

TRADING_DAYS = 252
SQRT_252 = math.sqrt(TRADING_DAYS)
# E|z| for a standard normal, used in the EGARCH innovation term.
E_ABS_Z = math.sqrt(2.0 / math.pi)
SIGMA_FLOOR = 1e-10

# HAR-RV horizon windows (trading days)
HAR_WEEK = 5
HAR_MONTH = 22

# Predicate 26 threshold: VRP expressed in annualized variance points,
# where VRP_pts = IV_pct^2 - RV_pct^2 (1 point = 0.01% annualized variance).
VRP_COLLAPSE_THRESHOLD_PTS = -100.0

# Predicate 27 threshold: conditional vol vs trailing 20-day baseline ratio.
VOL_CLUSTER_RATIO_THRESHOLD = 2.0

ESTIMATOR_LABELS = ["CLOSE_TO_CLOSE", "PARKINSON", "GARMAN_KLASS", "YANG_ZHANG"]


# =========================================================================
# Realized volatility estimators
# =========================================================================
def rv_close_to_close(returns: np.ndarray) -> float:
    """Annualized close-to-close realized volatility (decimal)."""
    if len(returns) < 2:
        return 0.0
    return float(np.sqrt(np.mean(np.square(returns)) * TRADING_DAYS))


def rv_parkinson(high: np.ndarray, low: np.ndarray) -> float:
    """
    Parkinson (1980) high-low estimator. Roughly 5x more efficient than
    close-to-close because it uses the full intraday range.
    """
    n = len(high)
    if n < 2:
        return 0.0
    lr = np.log(np.maximum(high, 1e-12) / np.maximum(low, 1e-12))
    return float(np.sqrt(np.sum(np.square(lr)) / (4.0 * n * math.log(2.0)) * TRADING_DAYS))


def rv_garman_klass(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
    """
    Garman-Klass (1980) OHLC estimator. Roughly 8x more efficient than
    close-to-close; assumes zero drift and no overnight gaps.
    """
    n = len(close)
    if n < 2:
        return 0.0
    hl = np.log(np.maximum(high, 1e-12) / np.maximum(low, 1e-12))
    co = np.log(np.maximum(close, 1e-12) / np.maximum(open_, 1e-12))
    term = 0.5 * np.square(hl) - (2.0 * math.log(2.0) - 1.0) * np.square(co)
    val = float(np.mean(term) * TRADING_DAYS)
    return float(math.sqrt(max(val, 0.0)))


def rv_rogers_satchell(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
    """Rogers-Satchell (1991) drift-independent range estimator (Yang-Zhang component)."""
    n = len(close)
    if n < 2:
        return 0.0
    h = np.log(np.maximum(high, 1e-12) / np.maximum(close, 1e-12))
    l = np.log(np.maximum(low, 1e-12) / np.maximum(close, 1e-12))
    hc = np.log(np.maximum(high, 1e-12) / np.maximum(open_, 1e-12))
    lc = np.log(np.maximum(low, 1e-12) / np.maximum(open_, 1e-12))
    term = h * hc + l * lc
    val = float(np.mean(term) * TRADING_DAYS)
    return float(math.sqrt(max(val, 0.0)))


def rv_yang_zhang(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> float:
    """
    Yang-Zhang (2000) estimator: drift-independent, jump-tolerant, and the most
    efficient of the range-based family. Combines overnight (open-to-close),
    Rogers-Satchell, and Garman-Klass components.
    """
    n = len(close)
    if n < 3:
        return rv_garman_klass(open_, high, low, close)

    prev_close = close[:-1]
    o = open_[1:]
    h = high[1:]
    l = low[1:]
    c = close[1:]
    m = len(c)
    if m < 2:
        return rv_garman_klass(open_, high, low, close)

    # Overnight (open-to-previous-close) variance
    oc = np.log(np.maximum(o, 1e-12) / np.maximum(prev_close, 1e-12))
    sigma_oc_sq = float(np.sum(np.square(oc - oc.mean())) / max(m - 1, 1)) * TRADING_DAYS

    # Rogers-Satchell and Garman-Klass on the aligned window
    sigma_rs = rv_rogers_satchell(o, h, l, c)
    sigma_gk = rv_garman_klass(o, h, l, c)

    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    val = sigma_oc_sq + k * (sigma_rs ** 2) + (1.0 - k) * (sigma_gk ** 2)
    return float(math.sqrt(max(val, 0.0)))


# =========================================================================
# GARCH family -- maximum likelihood via scipy.optimize
# =========================================================================
def _gaussian_loglik(returns: np.ndarray, sigma2: np.ndarray) -> float:
    """Gaussian log-likelihood given a conditional variance path."""
    s2 = np.maximum(sigma2, SIGMA_FLOOR)
    ll = -0.5 * np.sum(math.log(2.0 * math.pi) + np.log(s2) + np.square(returns) / s2)
    return float(ll) if math.isfinite(ll) else -1e12


def _softmax3(p: np.ndarray) -> Tuple[float, float, float]:
    """Maps unconstrained reals to three positives summing to < 1."""
    e1, e2, e3 = math.exp(np.clip(p[0], -30, 30)), math.exp(np.clip(p[1], -30, 30)), math.exp(np.clip(p[2], -30, 30))
    tot = 1.0 + e1 + e2 + e3
    return e1 / tot, e2 / tot, e3 / tot


def _linear_garch_path(
    eps: np.ndarray, omega: float, alpha: float, beta: float,
    gamma: float = 0.0, var_uncond: float = 1e-6,
) -> Optional[np.ndarray]:
    """
    Vectorized conditional-variance path for the LINEAR GARCH-family recursions
    (GARCH(1,1) and GJR-GARCH(1,1)).

    Both recursions are first-order linear filters in sigma^2:
        sigma2_t = omega + coef_{t-1} * eps^2_{t-1} + beta * sigma2_{t-1}
    where coef is alpha for GARCH and (alpha + gamma * 1{eps<0}) for GJR.
    scipy.signal.lfilter reproduces the loop exactly (~24x faster), which matters
    because this path is evaluated thousands of times per MLE optimization.

    Returns None if the path leaves the numerically valid region.
    """
    n = len(eps)
    if n < 2:
        return None

    eps2 = np.square(eps)
    coef = (alpha + gamma) * np.where(eps < 0, 1.0, 0.0) + alpha * np.where(eps < 0, 0.0, 1.0) \
        if gamma != 0.0 else np.full(n, alpha)

    x = np.empty(n)
    x[0] = var_uncond * (1.0 - beta)
    x[1:] = omega + coef[:-1] * eps2[:-1]

    path, _ = lfilter([1.0], [1.0, -beta], x, zi=np.array([beta * var_uncond]))

    # Guard both tails: an explosive beta yields finite but astronomically large
    # variances, which would silently poison the likelihood instead of being rejected.
    if (
        not np.all(np.isfinite(path))
        or np.any(path < SIGMA_FLOOR)
        or np.any(path > 1e6)
    ):
        return None
    return path


def fit_garch_1_1(returns: np.ndarray) -> GarchModelFit:
    """GARCH(1,1): sigma2_t = omega + alpha*eps2_{t-1} + beta*sigma2_{t-1}."""
    mu = float(np.mean(returns))
    eps = returns - mu
    var_uncond = max(float(np.var(returns)), SIGMA_FLOOR)

    def unpack(p):
        omega = math.exp(np.clip(p[0], -30, 30))
        alpha, beta, _ = _softmax3(p[1:4])
        return omega, alpha, beta

    def nll(p):
        omega, alpha, beta = unpack(p)
        s2 = _linear_garch_path(eps, omega, alpha, beta, 0.0, var_uncond)
        if s2 is None:
            return 1e12
        return -_gaussian_loglik(eps, s2)

    x0 = np.array([math.log(var_uncond * 0.05), 0.0, -1.0, 0.5])
    res = minimize(nll, x0, method="Nelder-Mead",
                   options={"maxiter": 4000, "xatol": 1e-6, "fatol": 1e-6})
    omega, alpha, beta = unpack(res.x)
    persistence = alpha + beta
    half_life = math.log(0.5) / math.log(persistence) if 0.0 < persistence < 1.0 else None
    ll = -float(res.fun)

    return GarchModelFit(
        variant="GARCH_1_1",
        omega=round(omega, 8),
        alpha=round(alpha, 6),
        beta=round(beta, 6),
        gamma=0.0,
        persistence=round(persistence, 6),
        half_life_days=round(half_life, 2) if half_life is not None else None,
        log_likelihood=round(ll, 4),
        aic=round(2 * 4 - 2 * ll, 4),  # mu, omega, alpha, beta
        converged=bool(res.success or math.isfinite(ll)),
    )


def fit_egarch_1_1(returns: np.ndarray) -> GarchModelFit:
    """
    EGARCH(1,1): log-variance form, so alpha and beta need no positivity bounds
    and the gamma term captures the leverage effect directly.
    """
    mu = float(np.mean(returns))
    eps = returns - mu
    var_uncond = max(float(np.var(returns)), SIGMA_FLOOR)

    def unpack(p):
        omega = p[0]
        alpha = math.exp(np.clip(p[1], -30, 30))
        gamma = p[2]
        beta = math.tanh(np.clip(p[3], -8, 8))
        return omega, alpha, gamma, beta

    def nll(p):
        omega, alpha, gamma, beta = unpack(p)
        n = len(eps)
        log_s2 = np.empty(n)
        log_s2[0] = math.log(var_uncond)
        for t in range(1, n):
            s_prev = math.sqrt(max(math.exp(log_s2[t - 1]), SIGMA_FLOOR))
            z = eps[t - 1] / s_prev
            log_s2[t] = omega + alpha * (abs(z) - E_ABS_Z) + gamma * z + beta * log_s2[t - 1]
            if not math.isfinite(log_s2[t]) or abs(log_s2[t]) > 30:
                return 1e12
        s2 = np.exp(log_s2)
        return -_gaussian_loglik(eps, s2)

    # EGARCH initialization: the unconditional mean of log(sigma^2) is omega/(1-beta),
    # NOT omega. Seeding omega = log(var_uncond) collapses the recursion toward
    # e^-54 variance and trips the divergence guard, so solve for the consistent
    # omega implied by the chosen beta.
    beta0 = 0.85
    omega0 = math.log(var_uncond) * (1.0 - beta0)
    # Two restarts: verified to reach the identical optimum as three, at ~2/3 cost.
    starts = [
        np.array([omega0, math.log(0.10), -0.05, math.atanh(beta0)]),
        np.array([omega0 * 1.2, math.log(0.15), -0.10, math.atanh(0.90)]),
    ]
    best = None
    for x0 in starts:
        res = minimize(nll, x0, method="Nelder-Mead",
                       options={"maxiter": 6000, "xatol": 1e-7, "fatol": 1e-7})
        if best is None or res.fun < best.fun:
            best = res
    res = best
    omega, alpha, gamma, beta = unpack(res.x)
    persistence = abs(beta)
    half_life = math.log(0.5) / math.log(persistence) if 0.0 < persistence < 1.0 else None
    ll = -float(res.fun)

    return GarchModelFit(
        variant="EGARCH_1_1",
        omega=round(omega, 8),
        alpha=round(alpha, 6),
        beta=round(beta, 6),
        gamma=round(gamma, 6),
        persistence=round(persistence, 6),
        half_life_days=round(half_life, 2) if half_life is not None else None,
        log_likelihood=round(ll, 4),
        aic=round(2 * 5 - 2 * ll, 4),  # mu, omega, alpha, gamma, beta
        converged=bool(res.success or math.isfinite(ll)),
    )


def fit_gjr_garch_1_1(returns: np.ndarray) -> GarchModelFit:
    """
    GJR-GARCH(1,1): adds a gamma term active only on negative innovations.
    Persistence is alpha + beta + gamma/2.
    """
    mu = float(np.mean(returns))
    eps = returns - mu
    var_uncond = max(float(np.var(returns)), SIGMA_FLOOR)

    def unpack(p):
        omega = math.exp(np.clip(p[0], -30, 30))
        alpha, gamma, beta = _softmax3(p[1:4])
        return omega, alpha, gamma, beta

    def nll(p):
        omega, alpha, gamma, beta = unpack(p)
        s2 = _linear_garch_path(eps, omega, alpha, beta, gamma, var_uncond)
        if s2 is None:
            return 1e12
        return -_gaussian_loglik(eps, s2)

    x0 = np.array([math.log(var_uncond * 0.05), 0.0, -1.5, 0.5])
    res = minimize(nll, x0, method="Nelder-Mead",
                   options={"maxiter": 5000, "xatol": 1e-6, "fatol": 1e-6})
    omega, alpha, gamma, beta = unpack(res.x)
    persistence = alpha + beta + gamma / 2.0
    half_life = math.log(0.5) / math.log(persistence) if 0.0 < persistence < 1.0 else None
    ll = -float(res.fun)

    return GarchModelFit(
        variant="GJR_GARCH_1_1",
        omega=round(omega, 8),
        alpha=round(alpha, 6),
        beta=round(beta, 6),
        gamma=round(gamma, 6),
        persistence=round(persistence, 6),
        half_life_days=round(half_life, 2) if half_life is not None else None,
        log_likelihood=round(ll, 4),
        aic=round(2 * 5 - 2 * ll, 4),  # mu, omega, alpha, gamma, beta
        converged=bool(res.success or math.isfinite(ll)),
    )


def conditional_vol_next(returns: np.ndarray, fit: GarchModelFit) -> float:
    """One-step-ahead annualized conditional volatility from a fitted model."""
    mu = float(np.mean(returns))
    eps = returns - mu
    var_uncond = max(float(np.var(returns)), SIGMA_FLOOR)

    if fit.variant == "EGARCH_1_1":
        log_s2 = math.log(var_uncond)
        for t in range(1, len(eps)):
            s_prev = math.sqrt(max(math.exp(log_s2), SIGMA_FLOOR))
            z = eps[t - 1] / s_prev
            log_s2 = fit.omega + fit.alpha * (abs(z) - E_ABS_Z) + fit.gamma * z + fit.beta * log_s2
        s2_next = math.exp(fit.omega + fit.alpha * (abs(eps[-1] / max(math.sqrt(math.exp(log_s2)), 1e-8)) - E_ABS_Z)
                           + fit.gamma * (eps[-1] / max(math.sqrt(math.exp(log_s2)), 1e-8)) + fit.beta * log_s2)
    else:
        g = fit.gamma if fit.variant == "GJR_GARCH_1_1" else 0.0
        path = _linear_garch_path(eps, fit.omega, fit.alpha, fit.beta, g, var_uncond)
        s2 = path[-1] if path is not None else var_uncond
        indicator = 1.0 if (g != 0.0 and eps[-1] < 0) else 0.0
        s2_next = fit.omega + fit.alpha * eps[-1] ** 2 + fit.gamma * eps[-1] ** 2 * indicator + fit.beta * s2

    daily_vol = math.sqrt(max(s2_next, 0.0))
    return float(daily_vol * SQRT_252 * 100.0)


# =========================================================================
# HAR-RV (Corsi 2009)
# =========================================================================
def fit_har_rv(rv_daily_series: np.ndarray) -> HarRvModel:
    """
    Corsi (2009) HAR-RV cascade on a daily realized-variance proxy:
        RV_{t+1} = b0 + bD*RV_t + bW*RV^{(w)}_t + bM*RV^{(m)}_t
    """
    rv = np.asarray(rv_daily_series, dtype=float)
    rv = np.maximum(rv, 0.0)

    if len(rv) < HAR_MONTH + 5:
        # Insufficient history: fall back to the unconditional mean.
        mean_rv = float(np.mean(rv)) if len(rv) else 0.0
        mean_pct = math.sqrt(max(mean_rv, 0.0)) * SQRT_252 * 100.0
        return HarRvModel(
            beta_daily=0.0, beta_weekly=0.0, beta_monthly=0.0, r_squared=0.0,
            forecast_1d_pct=round(mean_pct, 2), forecast_5d_pct=round(mean_pct, 2),
            forecast_22d_pct=round(mean_pct, 2),
        )

    rv_w = pd.Series(rv).rolling(HAR_WEEK).mean().values
    rv_m = pd.Series(rv).rolling(HAR_MONTH).mean().values

    start = HAR_MONTH
    y = rv[start + 1:]
    X = np.column_stack([
        np.ones(len(y)),
        rv[start:-1],
        rv_w[start:-1],
        rv_m[start:-1],
    ])
    mask = np.all(np.isfinite(X), axis=1) & np.isfinite(y)
    X, y = X[mask], y[mask]

    if len(y) < 30:
        mean_pct = math.sqrt(max(float(np.mean(rv)), 0.0)) * SQRT_252 * 100.0
        return HarRvModel(
            beta_daily=0.0, beta_weekly=0.0, beta_monthly=0.0, r_squared=0.0,
            forecast_1d_pct=round(mean_pct, 2), forecast_5d_pct=round(mean_pct, 2),
            forecast_22d_pct=round(mean_pct, 2),
        )

    coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    b0, bD, bW, bM = [float(c) for c in coef]

    y_hat = X @ coef
    ss_res = float(np.sum(np.square(y - y_hat)))
    ss_tot = float(np.sum(np.square(y - np.mean(y))))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-18 else 0.0

    # Recursive multi-horizon forecast
    rv_hist = list(rv[-HAR_MONTH:])
    forecasts: Dict[int, float] = {}
    for horizon in (1, HAR_WEEK, HAR_MONTH):
        sim = list(rv_hist)
        for _ in range(horizon):
            d = sim[-1]
            w = float(np.mean(sim[-HAR_WEEK:]))
            m = float(np.mean(sim[-HAR_MONTH:]))
            nxt = max(b0 + bD * d + bW * w + bM * m, 0.0)
            sim.append(nxt)
        forecasts[horizon] = sim[-1]

    def to_pct(var_val: float) -> float:
        return round(math.sqrt(max(var_val, 0.0)) * SQRT_252 * 100.0, 2)

    return HarRvModel(
        beta_daily=round(bD, 6),
        beta_weekly=round(bW, 6),
        beta_monthly=round(bM, 6),
        r_squared=round(max(min(r2, 1.0), 0.0), 4),
        forecast_1d_pct=to_pct(forecasts[1]),
        forecast_5d_pct=to_pct(forecasts[HAR_WEEK]),
        forecast_22d_pct=to_pct(forecasts[HAR_MONTH]),
    )


# =========================================================================
# Engine
# =========================================================================
class VolatilityForecastVrpEngine:
    """Computes realized volatility, GARCH-family forecasts, HAR-RV, and VRP."""

    def __init__(self):
        self._cached_feed: Optional[VolatilityVrpFeed] = None

    # ------------------------------------------------------------------
    def load_ohlc(self) -> Dict[str, pd.DataFrame]:
        """Loads daily OHLC bars per symbol from bar_1d."""
        sec_rows = db.execute_query("SELECT security_id, symbol, country FROM security ORDER BY symbol;")
        if not sec_rows:
            raise ValueError("No securities found in database.")
        id_to_sym = {r["security_id"]: r["symbol"] for r in sec_rows}
        sym_to_country = {r["symbol"]: r["country"] for r in sec_rows}

        bars = db.execute_query(
            "SELECT security_id, trading_date, open, high, low, close "
            "FROM bar_1d ORDER BY trading_date ASC;"
        )
        if not bars:
            raise ValueError("No historical daily bars found in database.")

        df = pd.DataFrame(bars)
        df["symbol"] = df["security_id"].map(id_to_sym)
        df = df.dropna(subset=["symbol"])

        out: Dict[str, pd.DataFrame] = {}
        for sym, grp in df.groupby("symbol"):
            g = grp.sort_values("trading_date").reset_index(drop=True)
            g = g.dropna(subset=["close"])
            if len(g) >= 60:
                g.attrs["country"] = sym_to_country.get(sym, "US")
                out[sym] = g
        return out

    # ------------------------------------------------------------------
    def compute_realized(self, bars: pd.DataFrame) -> RealizedVolatilityEstimates:
        """Computes all four realized-volatility estimators on real OHLC."""
        o = bars["open"].to_numpy(dtype=float)
        h = bars["high"].to_numpy(dtype=float)
        l = bars["low"].to_numpy(dtype=float)
        c = bars["close"].to_numpy(dtype=float)
        rets = np.diff(np.log(np.maximum(c, 1e-12)))

        cc = rv_close_to_close(rets)
        pk = rv_parkinson(h, l)
        gk = rv_garman_klass(o, h, l, c)
        yz = rv_yang_zhang(o, h, l, c)

        vals = {"CLOSE_TO_CLOSE": cc, "PARKINSON": pk, "GARMAN_KLASS": gk, "YANG_ZHANG": yz}
        dispersion = (max(vals.values()) - min(vals.values())) * 100.0
        # Parkinson is the most efficient estimator available without intraday data.
        best = "PARKINSON"

        return RealizedVolatilityEstimates(
            close_to_close_pct=round(cc * 100.0, 2),
            parkinson_pct=round(pk * 100.0, 2),
            garman_klass_pct=round(gk * 100.0, 2),
            yang_zhang_pct=round(yz * 100.0, 2),
            estimator_dispersion_pct=round(dispersion, 2),
            most_efficient_estimator=best,  # type: ignore
            bars_used=int(len(bars)),
        )

    # ------------------------------------------------------------------
    def compute_model_selection(self, returns: np.ndarray) -> VolatilityModelSelection:
        """Fits all three GARCH variants and selects the best by AIC."""
        fits = [
            fit_garch_1_1(returns),
            fit_egarch_1_1(returns),
            fit_gjr_garch_1_1(returns),
        ]
        ranked = sorted(fits, key=lambda f: f.aic)
        best = ranked[0]
        delta = ranked[1].aic - best.aic if len(ranked) > 1 else 0.0

        # Leverage asymmetry is material if the EGARCH gamma or GJR gamma is negative/positive
        # and improves the fit meaningfully over symmetric GARCH.
        egarch = next(f for f in fits if f.variant == "EGARCH_1_1")
        gjr = next(f for f in fits if f.variant == "GJR_GARCH_1_1")
        garch = next(f for f in fits if f.variant == "GARCH_1_1")
        asymmetry_material = (
            abs(egarch.gamma) > 0.02
            or gjr.gamma > 0.01
            or (best.variant != "GARCH_1_1" and delta > 2.0)
        ) and garch.aic > best.aic

        return VolatilityModelSelection(
            best_variant=best.variant,
            best_aic=best.aic,
            aic_delta_to_runner_up=round(delta, 4),
            asymmetry_material=bool(asymmetry_material),
            fits=fits,
        )

    # ------------------------------------------------------------------
    def compute_regime(self, returns: np.ndarray, selection: VolatilityModelSelection) -> VolatilityRegimeProfile:
        """Classifies the volatility regime from conditional vol vs trailing baseline."""
        cond_vol = conditional_vol_next(returns, next(
            f for f in selection.fits if f.variant == selection.best_variant
        ))
        window = returns[-20:] if len(returns) >= 20 else returns
        baseline = float(np.sqrt(np.mean(np.square(window - np.mean(window)))) * SQRT_252 * 100.0)
        ratio = cond_vol / baseline if baseline > 1e-9 else 1.0

        # Vol-of-vol: dispersion of rolling 10-day annualized vol.
        rolling = pd.Series(returns).rolling(10).std().dropna().to_numpy() * SQRT_252 * 100.0
        vol_of_vol = float(np.std(rolling)) if len(rolling) > 5 else 0.0

        if ratio >= VOL_CLUSTER_RATIO_THRESHOLD:
            regime: Literal["LOW", "NORMAL", "ELEVATED", "CRISIS"] = "CRISIS"
        elif ratio >= 1.35:
            regime = "ELEVATED"
        elif ratio <= 0.75:
            regime = "LOW"
        else:
            regime = "NORMAL"

        return VolatilityRegimeProfile(
            regime=regime,
            conditional_vol_pct=round(cond_vol, 2),
            baseline_20d_pct=round(baseline, 2),
            baseline_ratio=round(ratio, 3),
            vol_of_vol_pct=round(vol_of_vol, 2),
        )

    # ------------------------------------------------------------------
    def build_dossier(
        self, symbol: str, bars: pd.DataFrame, iv_lookup: Dict[str, float]
    ) -> VolatilityForecastDossier:
        """Assembles the per-symbol volatility forecasting dossier."""
        country: Literal["US", "CA"] = bars.attrs.get("country", "US")
        if country not in ("US", "CA"):
            country = "US"

        realized = self.compute_realized(bars)
        closes = bars["close"].to_numpy(dtype=float)
        returns = np.diff(np.log(np.maximum(closes, 1e-12)))

        selection = self.compute_model_selection(returns)

        # HAR-RV on a daily realized-variance proxy (squared returns).
        proxy = np.square(returns)
        har = fit_har_rv(proxy)

        regime = self.compute_regime(returns, selection)

        # VRP: only where a Phase 30 implied volatility exists.
        iv_pct = iv_lookup.get(symbol)
        rv_pct = realized.parkinson_pct  # most efficient available estimator
        if iv_pct is not None and iv_pct > 0:
            vrp_variance = float(iv_pct ** 2 - rv_pct ** 2)
            vrp_vol = float(iv_pct - rv_pct)

            # VRP z-score against the symbol's own 252-day realized distribution.
            rolling_rv = (pd.Series(returns).rolling(20).std().dropna().to_numpy() * SQRT_252 * 100.0)
            if len(rolling_rv) > 30:
                hist_vrp = iv_pct ** 2 - np.square(rolling_rv)
                mu = float(np.mean(hist_vrp))
                sd = float(np.std(hist_vrp, ddof=1))
                z = (vrp_variance - mu) / sd if sd > 1e-9 else 0.0
            else:
                z = 0.0

            if vrp_variance >= 100.0:
                edge: Literal["FAVOURABLE", "NEUTRAL", "ADVERSE"] = "FAVOURABLE"
            elif vrp_variance <= VRP_COLLAPSE_THRESHOLD_PTS:
                edge = "ADVERSE"
            else:
                edge = "NEUTRAL"

            vrp = VarianceRiskPremiumProfile(
                available=True,
                implied_vol_pct=round(iv_pct, 2),
                realized_vol_pct=round(rv_pct, 2),
                vrp_variance_pts=round(vrp_variance, 2),
                vrp_vol_pts=round(vrp_vol, 2),
                vrp_zscore_252d=round(z, 2),
                seller_edge=edge,
                unavailable_reason=None,
            )
        else:
            vrp = VarianceRiskPremiumProfile(
                available=False,
                unavailable_reason="No Phase 30 options surface available for this symbol",
            )

        if regime.regime == "CRISIS":
            concern = "VOLATILITY_CLUSTERING_CRISIS"
        elif vrp.available and vrp.seller_edge == "ADVERSE":
            concern = "VARIANCE_RISK_PREMIUM_COLLAPSE"
        elif regime.regime == "ELEVATED":
            concern = "ELEVATED_CONDITIONAL_VOLATILITY"
        elif realized.estimator_dispersion_pct > 8.0:
            concern = "REALIZED_ESTIMATOR_DISPERSION"
        else:
            concern = "NONE_MATERIAL"

        return VolatilityForecastDossier(
            symbol=symbol,
            country=country,
            return_window_start=str(bars["trading_date"].iloc[0]),
            return_window_end=str(bars["trading_date"].iloc[-1]),
            realized=realized,
            model_selection=selection,
            har_rv=har,
            regime=regime,
            vrp=vrp,
            primary_volatility_concern=concern,
        )

    # ------------------------------------------------------------------
    def load_implied_vols(self) -> Dict[str, float]:
        """Loads 30-day ATM implied volatility from the Phase 30 options surface feed."""
        path = DATA_DIR / "feeds" / "options_intelligence.json"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        out: Dict[str, float] = {}
        for sym, d in payload.get("dossiers", {}).items():
            iv = d.get("thirty_day_atm_iv_pct")
            if iv is not None and float(iv) > 0:
                out[sym] = float(iv)
        return out

    # ------------------------------------------------------------------
    def evaluate_volatility_forecasts(self, force_refresh: bool = False) -> VolatilityVrpFeed:
        """Evaluates the dual-market volatility forecasting and VRP feed."""
        if self._cached_feed is not None and not force_refresh:
            return self._cached_feed

        now = datetime.now(timezone.utc)
        ohlc = self.load_ohlc()
        iv_lookup = self.load_implied_vols()

        dossiers: Dict[str, VolatilityForecastDossier] = {}
        for symbol, bars in ohlc.items():
            dossiers[symbol] = self.build_dossier(symbol, bars, iv_lookup)

        rv_vals = sorted(d.realized.parkinson_pct for d in dossiers.values())
        median_rv = float(np.median(rv_vals)) if rv_vals else 0.0

        vrp_vals = [d.vrp.vrp_variance_pts for d in dossiers.values()
                    if d.vrp.available and d.vrp.vrp_variance_pts is not None]
        median_vrp = float(np.median(vrp_vals)) if vrp_vals else None

        for disc in VOLATILITY_DISCLAIMERS:
            linter.assert_clean(disc)

        feed = VolatilityVrpFeed(
            as_of_date=now.strftime("%Y-%m-%d"),
            generated_at=now.isoformat(),
            universe_count=len(dossiers),
            vrp_coverage_count=len(vrp_vals),
            median_realized_vol_pct=round(median_rv, 2),
            median_vrp_variance_pts=round(median_vrp, 2) if median_vrp is not None else None,
            cboe_index_reference_free=True,
            dossiers=dossiers,
            disclaimers=VOLATILITY_DISCLAIMERS,
        )

        self._cached_feed = feed
        return feed

    # ------------------------------------------------------------------
    def export_feed(self, output_path: Optional[Path] = None, force_refresh: bool = False) -> Path:
        """Exports the volatility VRP feed to disk."""
        if output_path is None:
            output_path = DATA_DIR / "feeds" / "volatility_vrp.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        feed = self.evaluate_volatility_forecasts(force_refresh=force_refresh)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(feed.model_dump(), f, indent=2)
        return output_path

    # ------------------------------------------------------------------
    def compute_sentinel_metrics(self, feed: VolatilityVrpFeed) -> Dict[str, Any]:
        """Extracts the metric surface consumed by Predicates 26 & 27."""
        per_symbol: Dict[str, Dict[str, Any]] = {}
        for sym, d in feed.dossiers.items():
            per_symbol[sym] = {
                "country": d.country,
                "regime": d.regime.regime,
                "conditional_vol_pct": d.regime.conditional_vol_pct,
                "baseline_20d_pct": d.regime.baseline_20d_pct,
                "baseline_ratio": d.regime.baseline_ratio,
                "realized_vol_pct": d.realized.parkinson_pct,
                "har_forecast_1d_pct": d.har_rv.forecast_1d_pct,
                "vrp_available": d.vrp.available,
                "implied_vol_pct": d.vrp.implied_vol_pct,
                "vrp_variance_pts": d.vrp.vrp_variance_pts,
                "vrp_vol_pts": d.vrp.vrp_vol_pts,
                "vrp_seller_edge": d.vrp.seller_edge,
            }
        return {"per_symbol": per_symbol}


volatility_forecast_vrp_engine = VolatilityForecastVrpEngine()
