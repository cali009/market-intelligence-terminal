# QUANT INTEL™ Phase 33: Multi-Horizon Volatility Forecasting & Variance Risk Premium Architecture

**Realized-Volatility Estimation + GARCH-Family MLE + HAR-RV Cascade + VRP Extraction**
**US (NYSE / NASDAQ / AMEX) + Canada (TSX / TSXV / CSE)**

---

## 1. WHY THIS PHASE EXISTS

Every prior volatility surface in the platform is *implied* — Phase 12 inverts option
prices to a Black-Scholes volatility using the Bisection method. Implied volatility
tells you what the market is **pricing**. It says nothing about what volatility has
actually **been**.

Phase 33 supplies the missing half: a model-based forecast of future realized
volatility, estimated from the platform's own daily bars. The difference between the
two is the **Variance Risk Premium (VRP)**, and it is the single most important
volatility signal available to a decision-support system:

```
VRP (variance points) = IV²  −  RV²
VRP (volatility points) = IV   −  RV
```

- **VRP strongly positive** → the options market is pricing more dispersion than the
  asset has delivered. Historically this is the compensation that makes *selling*
  volatility viable.
- **VRP strongly negative** → realized volatility is exceeding what was paid for.
  Short-volatility structures carry uncompensated tail risk. This is a **risk-off**
  condition, not a trading idea.

The platform deliberately reports VRP as **reference and risk information only**. It
does not recommend options strategies, does not size positions from VRP, and does not
present any VRP level as an entry signal.

---

## 2. LOCKED LICENSING DETERMINATION — THE CBOE INDEX FIREWALL

Cboe's Market Data Policies (effective **July 1, 2026**) classify **VIX index levels as
licensed commercial index data**: *"For Cboe Global Indices Feed fees, please contact
IndexData@cboe.com."* Redistribution requires a signed Data Agreement, Data Order Form,
System Description, and prior approval.

**Consequence adopted:** Phase 33 contains **zero** third-party index licensing
exposure, because both sides of the VRP equation are internally derived:

| Input | Source | Licensing posture |
|---|---|---|
| Implied volatility | Phase 30 BSM inversion of the platform's own options chain | Internally derived analytics |
| Realized volatility | Platform-computed from `bar_1d` OHLC | Internally derived analytics |

No Cboe index value, ticker, or name appears in any feed, UI string, or code path.
This is machine-enforced, not merely documented:

- **Schema:** `VolatilityVrpFeed.cboe_index_reference_free: Literal[True]` — a
  Pydantic field that raises `ValidationError` if ever set to `False`, so the posture
  cannot be forged at serialization time.
- **Release test:** `test_no_cboe_index_token_in_any_shipped_artifact` scans the feed,
  the staged `.json` route, and the extensionless clean route for `VIX`, `VIX9D`,
  `VIX3M`, `VIX6M`, `VVIX`, and `Cboe Volatility Index`.
- **UI:** the workstation states *"internally derived inputs only"* and *"No
  third-party index value is referenced."*

The Cboe External Distributor policy's transformation requirement — aggregation,
normalization, enrichment with analytics, or repackaging into a proprietary delivery
mechanism — is satisfied here in the strongest form available: there is no third-party
input to transform in the first place.

---

## 3. REALIZED-VOLATILITY ESTIMATORS

Four estimators, all annualized by `×√252`:

| Estimator | Inputs | Formula | Properties |
|---|---|---|---|
| **Close-to-close** | Close returns | `sd(ln(Pₜ/Pₜ₋₁))` | Unbiased; ignores all intraday information |
| **Parkinson** | High, Low | `√( (1/(4n·ln2)) Σ ln²(H/L) )` | ~5× more efficient than CC; assumes no drift, no overnight gap |
| **Garman–Klass** | O, H, L, C | `√( (1/n) Σ [½ln²(H/L) − (2ln2−1)ln²(C/O)] )` | Uses the open; biased under drift |
| **Rogers–Satchell** | O, H, L, C | `√( (1/n) Σ [ln(H/C)ln(H/O) + ln(L/C)ln(L/O)] )` | Drift-robust; noisier on flat days |
| **Yang–Zhang** | O, H, L, C | Overnight + open-to-close + Rogers–Satchell, weighted by `k = 0.34/(1.34 + (n+1)/(n−1))` | Drift-robust *and* gap-aware; the most complete daily estimator |

`most_efficient_estimator` is selected per symbol. In the shipped feed **PARKINSON wins
for all 19 symbols** — expected, because daily data has no overnight-gap pathology to
correct and Parkinson's efficiency advantage dominates.

### A discretization-bias finding worth recording

The estimator unit tests initially "failed," returning ≈0.214 against a known true
volatility of 0.25. The estimators were correct; **the test simulator was wrong**.

Range-based estimators consume the daily high and low, which for a continuous process
are the true extrema. A *discretely sampled* path systematically understates that range.
Measured on the simulator at true vol 0.25:

| Intraday steps | Close-to-close | Parkinson | Garman–Klass | Yang–Zhang |
|---:|---:|---:|---:|---:|
| 10 | 0.2510 | 0.2034 | 0.1817 | 0.1812 |
| 40 | 0.2549 | 0.2277 | 0.2162 | 0.2159 |
| 200 | 0.2526 | 0.2387 | 0.2330 | 0.2329 |
| 1000 | 0.2466 | 0.2428 | 0.2412 | 0.2412 |
| 4000 | 0.2474 | 0.2460 | 0.2454 | 0.2454 |

Close-to-close is unbiased at **every** resolution because it uses only endpoints; the
range estimators converge to truth only as sampling resolution rises. The simulator now
defaults to 1200 steps with a resolution-aware tolerance (`RANGE_TOL = 0.06`), and
close-to-close is held to a tighter `rel=0.04`.

**This bias is a property of synthetic data, not production data.** Real daily OHLC
carries the exchange's true intraday extrema.

---

## 4. GARCH-FAMILY MLE WITH AIC SELECTION

Three variants are fit by maximum likelihood to every symbol, and the **minimum AIC
wins**:

```
GARCH(1,1)     σ²ₜ = ω + α·ε²ₜ₋₁ + β·σ²ₜ₋₁                       (k = 4)
EGARCH(1,1)    lnσ²ₜ = ω + α(|zₜ₋₁| − √(2/π)) + γ·zₜ₋₁ + β·lnσ²ₜ₋₁  (k = 5)
GJR-GARCH(1,1) σ²ₜ = ω + α·ε²ₜ₋₁ + γ·ε²ₜ₋₁·1{εₜ₋₁<0} + β·σ²ₜ₋₁      (k = 5)

persistence = α + β + γ/2      half-life = ln(0.5) / ln(persistence)      AIC = 2k − 2·LL
```

EGARCH and GJR both carry a **leverage term γ**: volatility responds more to bad news
than good. A significantly negative EGARCH γ is the standard signature.

AIC genuinely discriminates rather than defaulting to one model — shipped counts are
**EGARCH 10 / GARCH 7 / GJR 2**.

### Reference fit: AAPL (regression baseline)

| Variant | α | β | γ | Persistence | Half-life | Log-lik | AIC |
|---|---:|---:|---:|---:|---:|---:|---:|
| GARCH(1,1) | 0.0863 | 0.8655 | — | 0.9518 | 14.03d | 1342.707 | −2677.414 |
| **EGARCH(1,1)** | 0.1422 | 0.9455 | **−0.1464** | 0.9455 | 12.36d | 1354.238 | **−2698.475** |
| GJR-GARCH(1,1) | 0.0000 | 0.8734 | +0.1266 | 0.9367 | 10.60d | 1350.765 | −2691.531 |

EGARCH wins on AIC and its γ of −0.1464 is the expected negative leverage sign.

### Two numerical bugs found and fixed during bring-up

**(a) EGARCH unconditional seed.** The first EGARCH run returned `LL = −1e12` and
`AIC = 2e12` for every symbol. `omega` was seeded as `log(var_uncond)`, but EGARCH's
unconditional mean of log σ² is `ω/(1−β)`, not `ω`. The recursion collapsed toward e⁻⁵⁴
and tripped the `|log_s2| > 30` guard on every likelihood evaluation. Fixed by seeding
`omega0 = log(var_uncond) × (1 − beta0)` with multi-start Nelder–Mead.

> **Lesson:** when a volatility model is parameterized on log-variance, derive the
> consistent unconditional seed from that parameterization. Do not reuse the
> linear-model seed. Symptom to watch for: `LL == -1e12` / absurd AIC.

**(b) Missing upper-bound guard on the variance path.** `_linear_garch_path` rejected
non-finite and sub-floor variances but never large ones, so an explosive `beta`
produced a path peaking at ~5e+84 that silently poisoned the likelihood. The guard now
rejects `path > 1e6` as well.

### Vectorization, verified exact

The GARCH and GJR variance recursions were originally explicit Python loops. They are
now a single `scipy.signal.lfilter` call:

```python
path, _ = lfilter([1.0], [1.0, -beta], x, zi=np.array([beta * var_uncond]))
```

Two mistakes were made and corrected on the way: omitting `zi` leaves the filter
unseeded (path off by 2.835e-04), and `lfilter` returns `(y, zf)` so unpacking is
required. The corrected version matches the explicit loop to **max abs diff
4.337e-19** with a **23.8× speedup**.

**Full build: 52.9s → 26.5s**, byte-identical AIC and parameters.

> **Lesson:** verify vectorized replacements against the explicit recursion before
> trusting them. A clean run is not a correct run.

---

## 5. HAR-RV CASCADE

The Heterogeneous AutoRegressive model captures volatility persistence across three
horizons — the empirical basis of volatility's long memory:

```
RVₜ₊₁ = β₀ + β_D·RVₜ + β_W·RVₜ^(w) + β_M·RVₜ^(m)
```

with 5-day and 22-day aggregation windows, solved by `np.linalg.lstsq`, then applied
recursively to produce 1-day, 5-day, and 22-day forecasts. Shipped median in-sample
`R²` is reported per symbol; forecasts are required positive by test.

---

## 6. VOLATILITY REGIME CLASSIFICATION

Driven by the ratio of GARCH conditional volatility to the trailing 20-day baseline:

| Band | Regime |
|---|---|
| ratio ≥ 2.0 | `CRISIS` |
| ratio ≥ 1.35 | `ELEVATED` |
| ratio ≤ 0.75 | `LOW` |
| otherwise | `NORMAL` |

Shipped distribution: **17 NORMAL / 2 ELEVATED / 0 CRISIS / 0 LOW**.

Seller edge: `FAVOURABLE` at VRP ≥ +100, `ADVERSE` at VRP ≤ −100, else `NEUTRAL`.
Shipped: **7 NEUTRAL / 2 FAVOURABLE / 1 ADVERSE**.

`primary_volatility_concern` shipped: **14 NONE_MATERIAL / 2
ELEVATED_CONDITIONAL_VOLATILITY / 2 REALIZED_ESTIMATOR_DISPERSION / 1
VARIANCE_RISK_PREMIUM_COLLAPSE**.

### VRP unit calibration caught before implementation

A drafted threshold of "−5.0 variance points" was **two orders of magnitude wrong**.
With `VRP = IV² − RV²`, a 25% IV against 20% RV yields 225 points, not 1.25. The
implemented threshold is **−100.0 variance points**.

---

## 7. PREDICATES 26 & 27

The sentinel now evaluates **27 distinct predicate types**.

**Predicate 26 — `VARIANCE_RISK_PREMIUM_COLLAPSE`**
`vrp_variance_pts < −100.0` → `WARNING`, action `AVOID_SHORT_VOLATILITY`,
`trigger_level = −100.0`. Fires when realized volatility materially exceeds what the
options market priced.

**Predicate 27 — `VOLATILITY_CLUSTERING_HAZARD`**
`baseline_ratio > 2.0` → `WARNING`, action `SCALE_DOWN_EQUITY_SIZING`,
`trigger_level = 2.0`. Fires when GARCH conditional volatility more than doubles the
trailing baseline.

Boundaries are **exclusive**: exactly −100.0 or exactly 2.0 does not fire.

### A third real bug: a `continue` that swallowed a predicate

The original per-symbol loop read:

```python
vrp = m.get("vrp_variance_pts")
if vrp is None:
    continue          # ← silently disabled Predicate 27
```

Predicate 27 depends only on `baseline_ratio`, not on VRP. That `continue` skipped the
rest of the iteration, so **Predicate 27 never evaluated the 9 symbols without an
options surface** — exactly the coverage hole that matters most, since those are the
names with the least risk transparency. Fixed by guarding each predicate independently.

> **Lesson:** a `continue` placed before an independent check silently disables it for
> the uncovered subset. Guard each predicate separately, and prove the uncovered path
> fires.

A regression test now asserts this structurally *and* behaviourally:
`test_predicate_27_is_not_gated_behind_vrp_availability` scans the Predicate 26 block
for a live `continue` statement (comments stripped, so the explanatory comment
describing this pitfall is not mistaken for code), and
`test_predicate_27_fires_for_symbol_without_implied_volatility` feeds a symbol with
`vrp_available=False` and `baseline_ratio=2.5` and requires the alert.

**Live behaviour:** Predicate 26 fires once (SHOP, −936.19 pts); Predicate 27 fires
zero times in the current market.

---

## 8. SHIPPED FEED (`data/feeds/volatility_vrp.json`, 49,622 bytes)

Universe **19**, VRP coverage **10/19**, as of **2026-10-01**.
Median realized vol **21.17%** (Parkinson); median VRP **+24.35** variance points.

| Symbol | RV (Parkinson) | HAR 1d | IV | VRP (var pts) | Seller edge |
|---|---:|---:|---:|---:|---|
| SPY | 12.90% | 13.34 | 15.5 | **+73.8** | NEUTRAL |
| AAPL | 23.74% | 22.33 | 22.5 | −57.3 | NEUTRAL |
| XIU | 10.31% | — | 12.8 | **+57.5** | NEUTRAL |
| RY | 13.92% | — | 13.8 | −3.3 | NEUTRAL |
| SHOP | 45.37% | 60.16 | 33.5 | **−936.2** | **ADVERSE** |

---

## 9. LIMITATIONS — STATED, NOT BURIED

1. **VRP coverage is partial: 10 of 19 symbols.** Nine symbols have no options surface
   in the Phase 30 feed, so they receive realized-volatility and GARCH analysis only.
   Every such dossier carries `vrp.available = false` with an explicit
   `unavailable_reason`, and `implied_vol_pct`, `vrp_variance_pts`, `vrp_vol_pts`, and
   `seller_edge` are **all null**. Implied volatility is never imputed, defaulted, or
   fabricated.

2. **No intraday realized volatility.** HAR-RV here aggregates *daily* range-based RV.
   The original Heterogeneous AutoRegressive model is built on intraday 5-minute RV.
   This version captures the multi-horizon structure but is a weaker estimator than
   the published original, and the coefficients should not be compared to academic
   HAR-RV estimates.

3. **GARCH parameters are unstable across estimation windows.** Single-parameter
   changes in α or β move persistence and half-life materially. For this reason the
   log-likelihood and persistence are surfaced in the feed rather than hidden, and
   model selection is by AIC across three variants rather than trusting one fit.

4. **Build cost is ~26s for the full universe.** Tests must share a single
   module-scoped fixture; re-running the build per test is prohibitively slow.

5. **No point-in-time or walk-forward claim is made for this phase.** This is the most
   important limitation. `bar_1d.knowledge_at` is a **bulk-ingestion timestamp**, not a
   per-bar availability marker: it holds only 38 distinct values spanning
   2026-09-25 → 2026-09-29 against trading dates reaching back to 2024-09-25. It cannot
   support walk-forward point-in-time discipline. Every GARCH, HAR-RV, and VRP number
   in this phase is therefore **in-sample, full-history, and informational**. It is not
   a validated out-of-sample forecast and must never be described as one.

---

## 10. COMPLIANCE POSTURE

Consistent with the platform's standing posture as **unregistered impersonal
investment research** under CSA/CIRO Staff Notice 31-369 and the SEC section
202(a)(11)(D) publisher exclusion:

- VRP, GARCH, HAR-RV, and regime outputs are **descriptive analytics about volatility**,
  not recommendations. Volatility forecasts **do not predict direction**.
- No language in the feed or UI recommends buying, selling, or holding any security,
  and no position sizing is derived from any Phase 33 output.
- All five statutory disclaimers are published in the feed under `disclaimers` and
  machine-validated against `ImpersonalAdviceLinter` with **zero violations**.
- Predicate actions are risk-management directives (`AVOID_SHORT_VOLATILITY`,
  `SCALE_DOWN_EQUITY_SIZING`), not trade recommendations.

---

## 11. TEST COVERAGE

| Suite | File | Result |
|---|---|---|
| Unit | `tests/test_volatility_forecast_vrp.py` | **62 passed** (77.86s) |
| Release audit | `tests/test_volatility_release.py` | **22 passed** (0.61s) |

Unit coverage spans ten classes: realized estimators against known ground truth,
GARCH-family MLE convergence and stationarity, the vectorized variance path against
the explicit recursion, HAR-RV, VRP arithmetic, regime bands, Predicates 26 & 27, the
Cboe licensing firewall, determinism/universe invariants, and statutory compliance.

The release suite audits artifact existence, clean-route byte equality, schema
validation, the VRP identities, absence of fabricated implied volatility, VRP coverage
matching the options universe exactly, three-variant presence with stationary
persistence and no divergence penalties, AIC-selection consistency, sentinel /
orchestrator / exporter / build-script wiring, terminal UI synchronization, and the
compliance linter.

### A brittle pre-existing assertion exposed by Predicate 26

`tests/test_portfolio_release.py::test_portfolio_risk_sentinels_integrity` asserted
`len(portfolio_alerts) == 0` "under nominal allocation." That assertion is unsound, and
Predicate 26 is what exposed it.

The 19 portfolio-level predicates fall into two disjoint families:

- **Allocation-construction** predicates (concentration, correlation, CVaR, factor
  crowding, FX/commodity overexposure, alpha erosion, turnover, estimation error,
  unhedged currency drag) are a function of the weights the orchestrator chose. Under a
  nominal allocation, silence *is* a true invariant.
- **Market-condition** predicates (parity dislocation, gamma flip, skew inversion,
  sovereign yield inversion, term premium shock, dark-pool distribution, short-volume
  squeeze, and now VRP collapse and volatility clustering) are a function of live market
  state. They fire when the market warrants it, regardless of allocation.

Requiring the second family to be silent makes the suite depend on a specific market
regime. At the time of this phase it genuinely broke: the CAD/USD move put all nine
dual-listed names at −53 to −78 bps parity spreads (Predicate 18, pre-existing), and
SHOP's −936 variance-point VRP correctly triggered Predicate 26.

**Attribution was verified rather than assumed.** Re-running the orchestration with only
`volatility_forecast_vrp_engine.compute_sentinel_metrics` patched to return `{}` still
produced **9 alerts, all `CROSS_BORDER_PARITY_DISLOCATION`** — proving Phase 33
contributed exactly one alert, the correct one, and that the parity alerts were live
market drift.

The test now asserts the invariant it actually means: zero allocation-construction
breaches, every emitted alert well-formed and belonging to a known predicate, and any
firing alert drawn from the market-condition family. A companion test,
`test_sentinel_predicate_families_cover_every_emitted_type`, parses the engine source and
fails if a future predicate is added without being classified, so the two families
cannot silently drift out of sync.

> **Lesson:** a test that asserts "nothing fired" is only an invariant if nothing in it
> depends on external state. Split market-condition alerts from construction breaches
> before treating silence as a regression signal.

---

## 12. TEST RESULTS

```
tests/test_volatility_forecast_vrp.py   62 passed
tests/test_volatility_release.py        22 passed
Full suite                             590 passed, 1 skipped  (337.37s)
Prior baseline                         505 passed, 1 skipped
```

Delta: **+85 new tests** (62 unit + 22 release + 1 predicate-family guard), with the
brittle Phase 26 assertion rewritten rather than deleted.
