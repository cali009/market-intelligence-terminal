# QUANT INTEL™ Phase 31: Cross-Asset Sovereign Yield Curve & Term Premium Decomposition Architecture

**US Treasuries vs. Government of Canada (GoC) Benchmark Bonds**

---

## 1. EXECUTIVE SUMMARY & OBJECTIVES

Phase 31 extends the platform's macroeconomic foundation from discrete overnight policy rates
(Phase 24 regime detection, Phase 29 interest-rate differentials) into the **full sovereign term
structure of interest rates** across eleven standard maturities (1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y,
10Y, 20Y, 30Y) for both the United States and Canada.

Sovereign yield curves are the discount-rate backbone of every other engine in this platform.
Equity multiples, corporate credit spreads, REIT dividend yields, mortgage resets, and the CAD/USD
exchange rate are all downstream of them. Prior phases consumed policy rates as scalar inputs;
Phase 31 models the *shape* of the curve, separates market rate expectations from the compensation
investors demand for duration risk, and turns curve dynamics into governed, auditable signals.

Four institutional capabilities are delivered:

1. **Nelson-Siegel-Svensson (NSS) parametric curve fitting** — smooth continuous reconstruction of
   the zero-coupon curve from discrete benchmark yields, enabling exact interpolation and
   instantaneous forward-rate extraction.
2. **Adrian-Crump-Moench (ACM) term premium decomposition** — splits 2Y, 5Y, 10Y, and 30Y nominal
   yields into the expected risk-neutral policy-rate path versus the term premium, distinguishing
   *hawkish central banks* from *duration-risk repricing*.
3. **Yield curve slope & regime classification** — 2Y10Y, 3M10Y, and 5Y30Y differentials mapped to
   the six canonical macroeconomic regimes.
4. **Cross-border GoC vs. UST spread term structure** — per-maturity sovereign spreads exposing
   Canada's structurally lower rate path and its currency implications.

Consistent with every prior phase, this is **impersonal decision-support research**. Curve models,
term premium estimates, and regime labels are econometric simulations over public benchmark debt
data. They are not bond brokerage, fixed-income portfolio management, or debt issuance advice.

---

## 2. MATHEMATICAL FORMULATION & QUANTITATIVE TERM STRUCTURE THEORY

### 2.1 Nelson-Siegel-Svensson (NSS) Parametric Yield Curve Model

For maturity $m$ (in years), the continuous spot yield is:

$$
y(m) = \beta_0 + \beta_1 \left( \frac{1 - e^{-m/\tau_1}}{m/\tau_1} \right) + \beta_2 \left( \frac{1 - e^{-m/\tau_1}}{m/\tau_1} - e^{-m/\tau_1} \right) + \beta_3 \left( \frac{1 - e^{-m/\tau_2}}{m/\tau_2} - e^{-m/\tau_2} \right)
$$

The production engine fits the three-parameter Nelson-Siegel specification ($\beta_3 = 0$,
$\tau_2$ inactive), which is sufficient for the monotone-plus-hump shapes observed in current US
and Canadian sovereign curves:

| Parameter | Interpretation |
|:---:|:---|
| $\beta_0$ | Asymptotic long-end level: $\lim_{m \to \infty} y(m) = \beta_0$ |
| $\beta_1$ | Short-end slope component: $y(0) - y(\infty) = \beta_1$ |
| $\beta_2$ | Medium-term curvature / hump amplitude |
| $\tau$ | Decay constant locating the maturity of peak curvature |

Parameters are estimated by non-linear least squares (`scipy.optimize.curve_fit`) with bounded
search: $\beta_0 \in (0, 15)$, $\beta_{1,2} \in (-10, 10)$, $\tau \in (0.1, 10)$. A robust fallback
returns endpoint-derived $\beta_0$/$\beta_1$ with fixed curvature if the optimiser fails to converge.

**Numerical guard.** The vectorized formula evaluates both branches of `np.where`, so the
denominator must be sanitized *before* division; otherwise NumPy raises
`invalid value encountered in divide` and propagates `NaN` at $m \to 0$. The implementation uses a
`safe_tau` mask and the analytic limit $\lim_{x \to 0} (1 - e^{-x})/x = 1$. This path is covered by a
regression test that escalates `RuntimeWarning` to an error.

### 2.2 Adrian-Crump-Moench (ACM) Term Premium Decomposition

For an $n$-year zero-coupon sovereign bond:

$$
y_t^{(n)} = \underbrace{\mathbb{E}_t\left[\frac{1}{n} \sum_{i=0}^{n-1} r_{t+i}\right]}_{\text{Risk-neutral rate path}} + \underbrace{TP_t^{(n)}}_{\text{Term premium}}
$$

- **Risk-neutral rate path** — the average future overnight policy rate implied by the market over
  $n$ years under the expectations hypothesis.
- **Term premium** — excess compensation demanded by risk-averse investors for bearing duration,
  inflation-volatility, and fiscal debt-supply risk over $n$ years.

The additive identity $\text{nominal} = \text{path} + TP$ is asserted at every maturity in both the
unit and release test suites.

#### Why the decomposition matters for equities

A 10Y yield rising from 4.00% to 4.35% can mean two very different things:

| Driver | Interpretation | Equity consequence |
|:---|:---|:---|
| **Rate path ↑** | Central bank expected to stay tighter for longer | Cyclicals and financials often benefit; discount-rate drag partially offset by growth |
| **Term premium ↑** | Investors demanding more compensation for duration/fiscal risk | Pure discount-rate drag; long-duration high-P/E equities compress with no earnings offset |

The platform translates term premium movement into valuation drag:

$$
\Delta \text{Valuation Drag} \approx -\text{Duration}_{\text{equity}} \times \Delta TP_{10Y}
$$

A sharp term premium expansion compresses multiples on long-duration assets even when earnings
growth is intact — which is precisely why Predicate 23 routes to
`COMPRESS_EQUITY_VALUATION_MULTIPLES` rather than a growth signal.

### 2.3 Yield Curve Slope Differentials & Regime Taxonomy

Three slope differentials are computed in basis points:

$$
\text{Slope}_{2Y10Y} = (y_{10Y} - y_{2Y}) \times 100, \quad
\text{Slope}_{3M10Y} = (y_{10Y} - y_{3M}) \times 100, \quad
\text{Slope}_{5Y30Y} = (y_{30Y} - y_{5Y}) \times 100
$$

Each profile is classified into one of six canonical regimes:

| Regime | Condition | Macro interpretation |
|:---|:---|:---|
| `INVERTED` | $2Y10Y < -50$ bps **or** $3M10Y < -75$ bps | Late-cycle recession warning |
| `BEAR_STEEPENER` | $2Y10Y > 0$ and $5Y30Y > +40$ bps | Long-end fiscal supply / term premium pressure |
| `BULL_STEEPENER` | $2Y10Y > 0$ and $3M10Y > 0$ | Easing cycle, early recovery |
| `BEAR_FLATTENER` | $2Y10Y \le 0$ and $3M10Y < 0$ | Late-cycle tightening |
| `NORMAL` | $2Y10Y > 0$, otherwise unclassified | Conventional upward slope |
| `BULL_FLATTENER` | residual | Flight to quality, recession pricing |

Boundary conditions are **exclusive**: slopes exactly at $-50.0$ / $-75.0$ bps do **not** classify as
inverted. This is asserted explicitly in tests to prevent off-by-one hazard drift.

### 2.4 Cross-Border Sovereign Spread Term Structure

For each maturity $m$:

$$
\text{Spread}_m = \big( y_{\text{GoC}}(m) - y_{\text{UST}}(m) \big) \times 100 \ \text{bps}
$$

A persistently negative spread across the 2Y–5Y segment indicates the Bank of Canada must run a
lower policy path than the Federal Reserve — in Canada's case driven by mortgage-reset sensitivity
and household leverage — creating structural CAD depreciation pressure that Phase 29's
minimum-variance hedge ratio engine consumes.

---

## 3. INVARIANT SENTINELS & CIRCUIT BREAKERS (PREDICATES 22 & 23)

### Predicate 22: `SOVEREIGN_YIELD_CURVE_INVERSION`

| Field | Value |
|:---|:---|
| **Condition** | $2Y10Y < -50.0$ bps **or** $3M10Y < -75.0$ bps, per jurisdiction |
| **Severity** | `WARNING` |
| **Action Required** | `DEFENSIVE_DURATION_BIAS` |
| **Taxonomy** | `REGIME_SHIFT` |
| **Symbol** | `US_SOVEREIGN` / `CA_SOVEREIGN` |

Deep sovereign curve inversion reflects severe market anticipation of late-cycle over-tightening
and elevated recession risk over the following 12–18 months. Both legs are tested independently, so
a positive 2Y10Y with a deeply inverted 3M10Y still trips the sentinel.

### Predicate 23: `TERM_PREMIUM_SHOCK`

| Field | Value |
|:---|:---|
| **Condition** | 10Y ACM term premium change $> +35.0$ bps over a 10-session rolling window |
| **Severity** | `WARNING` |
| **Action Required** | `COMPRESS_EQUITY_VALUATION_MULTIPLES` |
| **Taxonomy** | `PORTFOLIO_CIRCUIT_BREAKER` |
| **Symbol** | `US_SOVEREIGN` / `CA_SOVEREIGN` |

A sharp term premium expansion raises the equity cost of capital without a commensurate increase in
corporate cash-flow growth, triggering multiple compression across technology, consumer growth, and
high-P/E equities.

Both predicates are evaluated inside
`QuantIntelSentinel.evaluate_portfolio_level_sentinels(..., sovereign_yield_metrics=...)` and accept
either plain dicts or serialized `SovereignCurveProfile` models.

---

## 4. WORKSPACE DELIVERABLES & ARTIFACTS

| Artifact | Path | Purpose |
|:---|:---|:---|
| Domain schemas | `src/models/schemas.py` | `SovereignYieldPoint`, `NelsonSiegelParameters`, `TermPremiumDecomposition`, `SovereignCurveProfile`, `SovereignYieldFeed` |
| Core engine | `src/engine/sovereign_yield_curve.py` | NSS fitting, ACM decomposition, slope/regime classification, cross-border spreads |
| Sentinel engine | `src/engine/quant_intel_sentinel.py` | Predicates 22 & 23 |
| Orchestrator | `src/engine/portfolio_orchestrator.py` | Passes sovereign metrics into portfolio-level sentinel governance |
| Exporter | `src/data/edge_exporter.py` | Writes `sovereign_yield_curve.json` to `feeds/` and `dist/` |
| Build script | `scripts/build_firebase_public.py` | Stages `.json` plus extensionless clean route |
| Feed (source) | `data/feeds/sovereign_yield_curve.json` | Engine-generated master payload |
| Feed (staged) | `public/api/sovereign_yield_curve.json` + `public/api/sovereign_yield_curve` | Firebase distribution copies |
| Terminal UI | `web/index.html` / `public/index.html` | `📈 Sovereign Curve & Term Premium` workstation tab |
| Unit tests | `tests/test_sovereign_yield_curve.py` | 32 tests across fitting, spreads, regimes, ACM, sentinels, compliance |
| Release audit | `tests/test_yield_release.py` | 14 artifact/invariant/UI/statutory audit tests |
| Specification | `docs/13_sovereign_yield_curve_term_premium.md` | This document |

### Terminal UI Workstation

The `📈 Sovereign Curve & Term Premium` tab renders:

- **Slope monitor KPIs** — 2Y10Y, 3M10Y, 5Y30Y with hazard-aware colour coding, plus the classified
  regime label and its macro interpretation.
- **Interactive SVG curve overlay** — UST (solid) vs. GoC (dashed) across all eleven maturities on a
  log-maturity axis, drawn inline with no external dependencies.
- **ACM term premium bars** — stacked risk-neutral rate path vs. term premium for 2Y/5Y/10Y/30Y with
  10-day change flags.
- **Cross-border spread table** — per-maturity UST, GoC, and spread in basis points.
- **Jurisdiction toggle** — US Treasuries (USD) / Government of Canada (CAD).
- **Live sentinel badge** — flips from `CURVE NOMINAL` to `SENTINEL TRIGGERED (P22/P23)` when either
  predicate breaches.

---

## 5. CURRENT SHIPPED STATE (as of 2026-10-01)

| Metric | US Treasuries | GoC Bonds |
|:---|:---:|:---:|
| Curve regime | `BEAR_STEEPENER` | `BEAR_STEEPENER` |
| 2Y10Y slope | +13.0 bps | +17.0 bps |
| 3M10Y slope | −64.0 bps | −70.0 bps |
| 5Y30Y slope | +52.0 bps | +54.0 bps |
| NSS $\beta_0$ (level) | 4.6405% | 3.9420% |
| NSS $\beta_1$ (slope) | 0.3515 | 0.4108 |
| NSS $\beta_2$ (curvature) | −2.9683 | −3.1942 |
| NSS $\tau$ (decay) | 1.9004 | 1.8132 |
| 10Y term premium | +46.0 bps | +20.0 bps |
| 30Y term premium | +77.0 bps | +42.0 bps |

**Cross-border spreads:** 10Y GoC − UST = **−73.0 bps**; 2Y GoC − UST = **−77.0 bps**.

**Honest read of the current shape.** Both curves classify as `BEAR_STEEPENER` because 2Y10Y is
positive while the 5Y30Y long end is steep. However, the 3M10Y slope is negative in both
jurisdictions (−64.0 bps US, −70.0 bps CA): the 3-month bill yields more than the 10-year bond, a
genuine front-end inversion. It sits **above** the −75 bps Predicate 22 hazard boundary and
therefore does not fire. This is a real, disclosed limitation of threshold-based classification —
the label describes the dominant 2Y10Y/5Y30Y shape, not the front end. Both sentinels are currently
nominal across the monitored universes.

---

## 6. STATUTORY REGULATORY COMPLIANCE

In strict adherence to **CSA Staff Notice 31-369** and **SEC Publisher Exclusion §202(a)(11)(D)**:

1. **Impersonal decision support.** All yield curves, NSS parameters, term premium estimates, and
   slope regimes are quantitative econometric simulations derived from public benchmark debt
   securities. They do not constitute sovereign bond brokerage, fixed-income portfolio management,
   or debt issuance advice.
2. **Model-risk disclosure.** Statutory notices state explicitly that term premium decompositions
   rely on affine term-structure specifications subject to estimation error and regime shift.
3. **No fabricated data.** Yields are sourced from official public series; the engine never
   synthesizes market data outside documented empirical baselines.
4. **Automated linter verification.** Every disclaimer, sentinel headline, body, and action code
   passes `ImpersonalAdviceLinter` with **0 violations**, enforced in both test suites.

---

## 7. DEFINITION OF DONE — VERIFICATION RESULTS

| Criterion | Status |
|:---|:---:|
| Pydantic schemas added to `src/models/schemas.py` | ✅ |
| `SovereignYieldCurveEngine` implemented (NSS, ACM, regimes, spreads) | ✅ |
| Predicates 22 & 23 wired into sentinel + orchestrator | ✅ |
| Exporter and build script stage `.json` and clean routes | ✅ |
| Terminal UI tab built in `web/index.html`, synced byte-identical to `public/index.html` | ✅ |
| Unit + release suites authored (46 tests) | ✅ |
| Documentation completed | ✅ |
| **Full repository suite: 420 passed, 1 skipped, 0 failed** | ✅ |
| Zero `ImpersonalAdviceLinter` violations | ✅ |
| Inline JS validated via `node --check` | ✅ |

---

## 8. KNOWN LIMITATIONS & ROADMAP

1. **Empirical baseline yields.** The shipped engine uses documented static benchmark baselines.
   Production deployment should ingest live series from the US Treasury yield API and the Bank of
   Canada Valet API (both free for commercial use with a declared User-Agent).
2. **Static ACM calibration.** Rate-path expectations and term premia use fixed affine baselines
   rather than a rolling re-estimated state-space model. A full ACM re-estimation against realized
   excess returns is a natural Phase 32+ extension.
3. **Three-parameter Nelson-Siegel.** The Svensson second-curvature term ($\beta_3$, $\tau_2$) is
   present in the documented formulation but inactive in the fit. It should be enabled if either
   sovereign curve develops a double-hump shape.
4. **Front-end inversion blind spot.** As disclosed in §5, the regime label is driven by 2Y10Y and
   5Y30Y; a front-end-only inversion below the −75 bps boundary is not separately surfaced. A
   dedicated short-end inversion predicate is a candidate addition.
