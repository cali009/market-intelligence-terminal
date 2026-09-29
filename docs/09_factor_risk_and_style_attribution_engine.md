# QUANT INTEL™ Phase 27: Multi-Asset Factor Risk Decomposition, Active Style Tilts & Factor Return Attribution Architecture

**Document Version:** 1.0.0  
**Classification:** Institutional Decision-Support Architecture & Quantitative Specification  
**Jurisdictions Covered:** United States (NYSE / NASDAQ / BATS) & Canada (TSX / TSXV / CSE)  
**Compliance Framework:** CSA Staff Notice 31-369 (Impersonal Research Exemption) & SEC Publisher Exclusion (*Lowe v. SEC*, 472 U.S. 181; Section 202(a)(11)(D))  

---

## 1. EXECUTIVE SUMMARY & OBJECTIVES

Phase 27 introduces institutional **multi-asset factor risk modeling**, **active style tilt analysis**, and **Brinson-Barra factor return attribution** to the dual-market intelligence platform. Modern portfolio management requires transparency into whether investment performance and volatility originate from systematic macro/style factor exposures (e.g., market beta, value, size, momentum, oil, yield curve shifts) or genuine idiosyncratic security-selection conviction (pure alpha).

Phase 27 implements a 10-factor structural risk model spanning 19 dual-market US and Canadian securities, decomposing total portfolio variance into systematic vs. specific risk, quantifying Euler marginal risk contributions, isolating active style tilts against three distinct benchmarks, and executing live factor sentinels within the **QUANT INTEL™ Portfolio Orchestrator**.

### 1.1 Core Subsystem Milestones
1. **Phase 27.1 — Multi-Asset Factor Risk & Variance Decomposition Engine (`src/engine/factor_risk.py`)**:
   - 10-Factor Cross-Border Factor Taxonomy (Market Beta, SMB, HML, UMD, RMW, BAB, ILLIQ, Crude Oil Beta, CAD/USD FX Beta, US Yield Curve Beta).
   - Ordinary Least Squares (OLS) multi-factor regression estimating factor loadings $\boldsymbol{\beta}_i$ and idiosyncratic residual variances $\sigma_{\epsilon,i}^2$ for all 19 securities.
   - Positive semi-definite empirical factor covariance matrix $\boldsymbol{\Omega}_F \in \mathbb{R}^{10 \times 10}$.
   - Exact variance conservation: $\sigma_p^2 = \sigma_{\text{systematic}}^2 + \sigma_{\text{specific}}^2$ (Portfolio Total Volatility: **12.57%**, Systematic Volatility: **12.30%** [95.88% share], Specific Volatility: **2.55%** [4.12% share]).
   - Euler Marginal Contribution to Risk (MCR) verifying exact risk additivity: $\sum_k b_{p,k} \cdot \text{MCR}_k + \text{MCR}_{\text{spec}} = \sigma_p$.
   - Read-only NumPy array resilience under Pandas 2.2+.
2. **Phase 27.2 — Active Style Tilts & Factor Return Attribution Engine (`src/engine/factor_attribution.py`)**:
   - Brinson-Fachler / Barra style factor return attribution against 3 benchmarks:
     1. **Blended Cross-Border Core** (50% SPY / 50% XIU)
     2. **US Large-Cap Core** (100% SPY)
     3. **Canadian TSX 60 Core** (100% XIU)
   - Exact arithmetic return conservation:
     $$R_p - R_b = \sum_{k=1}^K \Delta b_k \cdot f_k + \alpha_{\text{selection}}$$
   - Self-benchmark null invariant proof: Evaluating a benchmark against itself produces identically zero active return, zero factor contributions, and zero tilts.
   - Realized baseline attribution against Blended Benchmark: Active Return **+1.73%** (+173 bps), Factor Return Contribution **+1.05%** (+105 bps), Specific Selection Alpha **+0.68%** (+68 bps), Tracking Error **4.55%**, Information Ratio **0.38**.
   - Automated identification of unintentional style biases and top factor PnL drivers.
3. **Phase 27.3 — QUANT INTEL™ Factor Sentinels & Orchestrator Governance (`src/engine/quant_intel_sentinel.py`)**:
   - Predicate 13 (`FACTOR_CROWDING_BREACH`): Triggers when non-market systematic risk concentration exceeds 45.0% (Action: `REBALANCE_FACTOR_TILT`).
   - Predicate 14 (`FX_COMMODITY_OVEREXPOSURE`): Triggers when crude oil active tilt exceeds 0.40 or CAD/USD active tilt exceeds 0.35 (Action: `REDUCE_COMMODITY_EXPOSURE`).
   - Predicate 15 (`ALPHA_EROSION_WARNING`): Triggers when specific stock selection alpha drops below -1.50% (Action: `AUDIT_SECURITY_SELECTION`).
   - Seamless conversion to system alert taxonomy `PORTFOLIO_CIRCUIT_BREAKER` passing `ImpersonalAdviceLinter` with 0 violations.
4. **Phase 27.4 — Terminal UI Integration & Interactive Factor Viewport (`web/index.html` & `public/index.html`)**:
   - Dedicated "🔬 Multi-Asset Factor Risk & Style Attribution" workstation tab.
   - Interactive 3-benchmark toggle bar with live active return and alpha decomposition ribbons.
   - 10-factor waterfall attribution table with active tilt indicators and PnL impact.
   - 19-security multi-asset factor loadings heatmap matrix with color-coded intensity cells.
   - Real-time 3-predicate factor sentinels monitor strip.
5. **Phase 27.5 — Master Release Audit & Comprehensive Test Suite (`tests/test_factor_release.py`)**:
   - Verification of 127 total unit, integration, and release audit tests across 15 test suites with 100% pass rate.
   - Clean route API endpoints `/api/factor_risk` and `/api/factor_attribution` staged in public distribution bundle (160 total files).

---

## 2. MATHEMATICAL & FACTOR PRICING FORMULATIONS

### 2.1 The 10-Factor Structural Cross-Border Model
The realized return $R_{i,t}$ of security $i \in \{1, \dots, N\}$ is decomposed into systematic factor premiums and an idiosyncratic residual component:

$$R_{i,t} = \alpha_i + \sum_{k=1}^K \beta_{i,k} F_{k,t} + \epsilon_{i,t}$$

Where:
- $F_{k,t}$ is the return of factor $k$ at time $t$.
- $\beta_{i,k} = \frac{\text{Cov}(R_i, F_k)}{\text{Var}(F_k)}$ is the loading (beta) of asset $i$ on factor $k$.
- $\alpha_i$ is the intercept (pricing error / selection alpha).
- $\epsilon_{i,t} \sim \mathcal{N}(0, \sigma_{\epsilon,i}^2)$ is the idiosyncratic residual, satisfying $\mathbb{E}[\epsilon_{i,t}] = 0$ and $\text{Cov}(\epsilon_{i,t}, F_{k,t}) = 0$.

### 2.2 Factor Taxonomy & Specification

| # | Factor Key | Factor Name | Category | Economic Mechanism & Definition |
|---|---|---|---|---|
| 1 | `market_beta` | Market Beta | MACRO | Sensitivity to broad equity market return (S&P 500 / TSX Composite blend). Captures broad equity risk premium. |
| 2 | `size_smb` | Size (SMB) | STYLE | Small Minus Big (Fama-French). Captures small-cap illiquidity and growth premium vs mega-caps. |
| 3 | `value_hml` | Value (HML) | STYLE | High Minus Low (Fama-French). High book-to-market and low P/E value discount vs high-multiple growth equities. |
| 4 | `momentum_umd` | Momentum (UMD) | STYLE | Up Minus Down (Carhart). 12-month trailing price momentum minus 1-month reversal. |
| 5 | `quality_rmw` | Quality (RMW) | STYLE | Robust Minus Weak operating profitability (Fama-French 5). Balance sheet quality and return on capital. |
| 6 | `low_volatility_bab` | Low Volatility (BAB) | STYLE | Betting Against Beta (Frazzini-Pedersen). Low-beta leverage-constrained risk anomaly. |
| 7 | `liquidity_illiq` | Illiquidity (ILLIQ) | STYLE | Amihud (2002) price impact per dollar volume. Captures market liquidity friction premium. |
| 8 | `crude_oil_beta` | Crude Oil Beta | MACRO | Sensitivity to WTI/WCS crude oil front-month futures returns. Critical for Canadian TSX energy producers & midstream. |
| 9 | `cad_usd_fx_beta` | CAD/USD FX Beta | MACRO | Sensitivity to Bank of Canada CAD/USD spot exchange rate changes. Measures cross-border revenue currency exposure. |
| 10 | `yield_curve_beta` | US Yield Curve Beta | MACRO | Sensitivity to US 10Y-2Y Treasury yield curve slope ($\Delta (y_{10} - y_2)$). Drives banking net interest margins and duration risk. |

### 2.3 Total Portfolio Variance Decomposition
Let $\mathbf{w} \in \mathbb{R}^N$ be the vector of portfolio asset weights ($\sum_i w_i = 1$). The portfolio's factor exposure vector $\mathbf{b}_p \in \mathbb{R}^K$ is defined as:

$$\mathbf{b}_p = \mathbf{B}^T \mathbf{w} = \sum_{i=1}^N w_i \boldsymbol{\beta}_i$$

Where $\mathbf{B} \in \mathbb{R}^{N \times K}$ is the matrix of asset factor loadings.

The total portfolio variance $\sigma_p^2$ is partitioned into systematic variance $\sigma_{\text{sys}}^2$ and specific (idiosyncratic) variance $\sigma_{\text{spec}}^2$:

$$\sigma_p^2 = \mathbf{w}^T \boldsymbol{\Sigma} \mathbf{w} = \underbrace{\mathbf{b}_p^T \boldsymbol{\Omega}_F \mathbf{b}_p}_{\sigma_{\text{systematic}}^2} + \underbrace{\mathbf{w}^T \mathbf{D}_{\epsilon} \mathbf{w}}_{\sigma_{\text{specific}}^2}$$

Where:
- $\boldsymbol{\Omega}_F \in \mathbb{R}^{K \times K}$ is the positive semi-definite factor covariance matrix.
- $\mathbf{D}_{\epsilon} = \text{diag}(\sigma_{\epsilon,1}^2, \dots, \sigma_{\epsilon,N}^2) \in \mathbb{R}^{N \times N}$ is the diagonal matrix of specific residual variances.

#### Numerical Baseline:
- $\sigma_p = 12.57\%$ (annualized total volatility)
- $\sigma_{\text{systematic}} = 12.30\%$ ($\sigma_{\text{sys}}^2 / \sigma_p^2 = 95.88\%$ systematic variance ratio)
- $\sigma_{\text{specific}} = 2.55\%$ ($\sigma_{\text{spec}}^2 / \sigma_p^2 = 4.12\%$ specific variance ratio)

### 2.4 Euler Marginal Contribution to Factor Risk
Applying Euler's theorem on homogeneous functions of degree 1 to total risk $\sigma_p(\mathbf{b}_p, \mathbf{w})$:

$$\sigma_p = \sum_{k=1}^K b_{p,k} \frac{\partial \sigma_p}{\partial b_{p,k}} + \sum_{i=1}^N w_i \frac{\partial \sigma_p}{\partial w_i}$$

The marginal contribution of factor $k$ to total risk is:

$$\text{MCR}_{F,k} = \frac{\partial \sigma_p}{\partial b_{p,k}} = \frac{(\boldsymbol{\Omega}_F \mathbf{b}_p)_k}{\sigma_p}$$

The factor's total percentage contribution to systematic risk is given by:

$$\% \text{Risk}_k = \frac{b_{p,k} \cdot (\boldsymbol{\Omega}_F \mathbf{b}_p)_k}{\sigma_{\text{sys}}^2} \times 100\%$$

---

## 3. BRINSON-BARRA ACTIVE STYLE RETURN ATTRIBUTION

### 3.1 Active Return Arithmetic Decomposition
Let $R_p$ be the realized annualized return of the portfolio and $R_b$ the return of the benchmark. The active return $\Delta R = R_p - R_b$ is decomposed into:

$$R_p - R_b = \sum_{k=1}^K \underbrace{\Delta b_k \cdot f_k}_{\text{Factor Return Contribution}} + \underbrace{\alpha_{\text{specific}}}_{\text{Stock Selection Alpha}}$$

Where:
- $\Delta b_k = b_{p,k} - b_{b,k}$ is the active style tilt on factor $k$.
- $f_k$ is the realized annualized return of factor $k$.
- $\alpha_{\text{specific}} = \Delta R - \sum_{k=1}^K \Delta b_k \cdot f_k$ is the pure idiosyncratic stock selection alpha.

### 3.2 Realized Attribution vs Blended Benchmark (50% SPY / 50% XIU)

| Factor Name | Category | Portfolio Beta ($b_p$) | Benchmark Beta ($b_b$) | Active Tilt ($\Delta b$) | Factor Return 1Y ($f_k$) | PnL Contribution | Systematic Risk Share |
|---|---|---|---|---|---|---|---|
| **Market Beta** | MACRO | 0.987 | 0.995 | -0.008 | +16.39% | -0.13% (-13 bps) | 103.95% |
| **Size (SMB)** | STYLE | -0.057 | 0.000 | -0.057 | -10.10% | **+0.58% (+58 bps)** | -0.32% |
| **Value (HML)** | STYLE | -0.178 | 0.000 | -0.178 | -0.27% | +0.05% (+5 bps) | -0.21% |
| **Momentum (UMD)** | STYLE | +0.271 | +0.214 | +0.057 | +3.14% | **+0.18% (+18 bps)** | +0.12% |
| **Quality (RMW)** | STYLE | +0.279 | +0.211 | +0.068 | -6.67% | -0.45% (-45 bps) | +0.48% |
| **Low Volatility (BAB)** | STYLE | +0.655 | +0.722 | -0.067 | +9.51% | -0.64% (-64 bps) | -0.89% |
| **Liquidity (ILLIQ)** | STYLE | -0.730 | -0.627 | -0.103 | +14.20% | -1.46% (-146 bps) | +1.64% |
| **Crude Oil Beta** | MACRO | +0.419 | +0.136 | **+0.283** | -0.95% | -0.27% (-27 bps) | +4.82% |
| **CAD/USD FX Beta** | MACRO | +0.144 | +0.071 | **+0.073** | -2.10% | -0.15% (-15 bps) | +0.18% |
| **Yield Curve Beta** | MACRO | +0.478 | +0.186 | **+0.292** | +11.50% | **+3.36% (+336 bps)** | +32.51% |
| **TOTAL FACTOR CONTRIBUTION** | — | — | — | — | — | **+1.05% (+105 bps)** | — |
| **SPECIFIC SELECTION ALPHA ($\alpha$)** | — | — | — | — | — | **+0.68% (+68 bps)** | — |
| **TOTAL ACTIVE RETURN ($R_p - R_b$)** | — | — | — | — | — | **+1.73% (+173 bps)** | — |

**Attribution Metrics:**
- **Tracking Error ($\text{TE}$):** $4.55\%$
- **Information Ratio ($\text{IR}$):** $\frac{+1.73\%}{4.55\%} = \mathbf{0.38}$

---

## 4. QUANT INTEL™ FACTOR SENTINELS & GOVERNANCE INVARIANTS

To protect investor capital from hidden factor crowding and macroeconomic transmission shocks, the orchestrator evaluates three live factor sentinels alongside concentration and tail risk predicates:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PORTFOLIO ORCHESTRATOR FACTOR GOVERNANCE                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  Predicate 10: Portfolio Concentration Breach (Max single weight > 18%)     │
│  Predicate 11: Cross-Asset Correlation Spike (Avg correlation > 0.65)       │
│  Predicate 12: Parametric CVaR Tail Limit (1d 99% CVaR > 3.50%)             │
│                                                                             │
│  [PHASE 27 FACTOR RISK & ATTRIBUTION INVARIANTS]                            │
│  Predicate 13: FACTOR_CROWDING_BREACH                                       │
│    Trigger: max_{k != market} |% Risk_k| > 45.0%                            │
│    Severity: WARNING | Action: REBALANCE_FACTOR_TILT                        │
│    Nominal Status: PASS (Yield Curve Beta at 32.51%)                        │
│                                                                             │
│  Predicate 14: FX_COMMODITY_OVEREXPOSURE                                    │
│    Trigger: |Δb_{crude}| > 0.40 OR |Δb_{fx}| > 0.35                         │
│    Severity: WARNING | Action: REDUCE_COMMODITY_EXPOSURE                    │
│    Nominal Status: PASS (Crude tilt: +0.28, FX tilt: +0.07)                 │
│                                                                             │
│  Predicate 15: ALPHA_EROSION_WARNING                                        │
│    Trigger: \alpha_{specific} < -1.50%                                      │
│    Severity: WARNING | Action: AUDIT_SECURITY_SELECTION                     │
│    Nominal Status: PASS (Specific Alpha: +0.68%)                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. STATUTORY REGULATORY COMPLIANCE

All factor risk summaries, style tilt attributions, and sentinel alerts strictly adhere to **CSA Staff Notice 31-369** and the **SEC Publisher Exclusion (§202(a)(11)(D))**:
- **Impersonal Quantitative Analytics**: All factor loadings and return decompositions are calculated via standardized, algorithmic ordinary least squares regressions and linear algebra formulations. No client-specific or bespoke advice is produced.
- **Mandatory Statutory Disclaimer**: Prominently displayed across all factor UI views and JSON feeds:
  > *"Statutory Regulatory Notice (CSA Staff Notice 31-369 / SEC Publisher Exclusion §202(a)(11)(D)): Hierarchical Risk Parity allocations, multi-asset factor models, style return attributions, parametric tail risk metrics (VaR / CVaR), and historical scenario stress replays are impersonal quantitative mathematical simulations. They do not constitute individualized investment advice, financial planning, or fiduciary asset management. Simulated performance, factor exposures, risk reduction metrics, and crisis replay drawdowns are subject to statistical estimation error and market regime instability. Past performance is no guarantee of future returns."*
- **Continuous Automated Linting**: All headlines, alert messages, and user-facing cards pass `ImpersonalAdviceLinter` verifying 0 compliance violations.
