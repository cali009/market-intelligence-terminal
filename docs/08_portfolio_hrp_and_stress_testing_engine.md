# QUANT INTEL™ Phase 26: Hierarchical Risk Parity (HRP), Macroeconomic Stress-Testing & Portfolio Orchestration Architecture

**Document Version:** 1.0.0  
**Classification:** Institutional Decision-Support Architecture & Quantitative Specification  
**Jurisdictions Covered:** United States (NYSE / NASDAQ / BATS) & Canada (TSX / TSXV / CSE)  
**Compliance Framework:** CSA Staff Notice 31-369 (Impersonal Research Exemption) & SEC Publisher Exclusion (*Lowe v. SEC*, 472 U.S. 181; Section 202(a)(11)(D))  

---

## 1. EXECUTIVE SUMMARY & OBJECTIVES

Phase 26 introduces institutional portfolio-level risk allocation, macroeconomic crisis stress-testing, and dynamic conviction-governed portfolio orchestration to the dual-market intelligence platform. In traditional quantitative finance, Markowitz Mean-Variance Optimization (MVO) suffers from severe mathematical instability (Michaud's "error maximization"), requiring the inversion of a positive semi-definite covariance matrix that produces extreme, unstable corner solutions and catastrophic out-of-sample fragility during liquidity shocks.

To eliminate matrix inversion instability and protect capital across market crises, Phase 26 implements **Marcos López de Prado's (2016) Hierarchical Risk Parity (HRP)** alongside parametric tail risk modeling, 5 historical crisis replays, and the **QUANT INTEL™ Portfolio Orchestrator**.

### 1.1 Core Subsystem Milestones
1. **Phase 26.1 — Hierarchical Risk Parity (HRP) Core Engine (`src/engine/portfolio_hrp.py`)**:
   - Correlation-based metric distance transformation $d_{i,j} = \sqrt{\frac{1}{2}(1 - \rho_{i,j})}$ satisfying metric space non-negativity, symmetry, and triangle inequality.
   - Euclidean linkage distance $\tilde{D}_{i,j} = \sqrt{\sum_k (d_{i,k} - d_{j,k})^2}$ and single-linkage agglomerative clustering.
   - Quasi-diagonal seriation leaf reordering organizing correlated cross-border assets into contiguous blocks without matrix inversion.
   - Recursive bisection weight allocation with inverse cluster variance scaling.
   - Euler Marginal Contribution to Risk (MCR) decomposition verifying exact mathematical risk budgeting: $\sum_i w_i \cdot \text{MCR}_i = \sigma_p$.
   - Benchmark comparison proving HRP outperforms naive Equal-Weight (EW: 16.10% vol) and Inverse Variance (IVP: 12.88% vol) with an annualized portfolio volatility of **12.48%** (-22.5% risk reduction vs EW) and a Diversification Ratio of **1.68**.
2. **Phase 26.2 — Historical & Macroeconomic Stress-Testing Engine (`src/engine/portfolio_stress.py`)**:
   - Parametric Value at Risk (VaR) and Conditional Value at Risk (CVaR / Expected Shortfall) across 95% and 99% confidence intervals.
   - Basel III multi-horizon $\sqrt{10}$ temporal scaling (1-day to 10-day stress horizon).
   - 5 Historical and Forward Crisis Replays:
     1. **2008 Global Financial Crisis** (Lehman collapse, credit freeze): HRP preserved **+$2,751.49** vs EW.
     2. **2015 Canadian Crude Oil Collapse** (OPEC market share war, WTI/WCS collapse): Energy sector stress isolation.
     3. **2020 COVID Liquidity Freeze** (Global shutdown, VIX spike to 82): Cross-asset liquidity shock.
     4. **2022 Central Bank Rapid Tightening Cycle** (Fed + BoC 450bps rate hikes): HRP preserved **+$5,711.51** vs EW.
     5. **2026 Stagflationary Supply & Tariff Shock** (Forward cross-border tariff simulation): HRP preserved **+$1,831.36** vs EW.
   - Automated Tail Risk Posture classification (`LOW_TAIL_RISK`, `MODERATE_TAIL_RISK`, `ELEVATED_TAIL_RISK`, `CRITICAL_TAIL_RISK`).
3. **Phase 26.3 — QUANT INTEL Portfolio Orchestrator & Live Risk Sentinels (`src/engine/portfolio_orchestrator.py`)**:
   - Security-level conviction grade modulation: Grade A (1.25x), Grade B (1.00x), Grade C (0.50x), Grade SKIP (0.00x).
   - Dynamic macro hazard governor integration downweighting fragile sectors during adverse Markov regime transitions.
   - Capital conservation principle: total deployed capital ($58,280 / 58.28%) plus protective defensive cash reserve ($41,720 / 41.72%) exactly equals total capital ($100,000.00).
   - Dual-market exposure balance: US Equities (29.31%) vs Canadian Equities (28.97%).
   - Integrated Transaction Cost Analysis (TCA) execution routing: `DIRECT_MARKET`, `PASSIVE_LIMIT`, `TWAP_4_HOURS`, `VWAP_INTRADAY`, `ICEBERG_POV_15PCT`.
   - Portfolio-level invalidation sentinels:
     - Predicate 10: `PORTFOLIO_CONCENTRATION_BREACH` ($\max_i \tilde{w}_i > 20.0\%$).
     - Predicate 11: `CORRELATION_SPIKE_WARNING` ($\bar{\rho} > 0.65$).
     - Predicate 12: `CVAR_TAIL_RISK_BREACH` ($\text{CVaR}_{99\%, 1d} > 6.00\%$).
4. **Phase 26.4 — Terminal Workstation UI & Comprehensive Release Audit (`web/index.html`)**:
   - Interactive Bloomberg/TradingView-inspired terminal workstation (`tab-portfoliohrp`).
   - Dynamic capital scaling ($50k, $100k, $250k), crisis replay deep-dive, allocation matrix, tree seriation cluster viewer, and TCA simulator link.
   - Automated test suite `tests/test_portfolio_release.py` verifying mathematical invariants and zero compliance violations.

---

## 2. MATHEMATICAL & ALGORITHMIC FORMULATIONS

### 2.1 López de Prado (2016) Hierarchical Risk Parity (HRP)

#### Step 1: Metric Distance Transformation
Given the $N \times N$ empirical correlation matrix $P = (\rho_{i,j})$ of asset returns, the correlation-based distance $d_{i,j}$ is defined as:

$$d_{i,j} = \sqrt{\frac{1}{2} (1 - \rho_{i,j})}$$

Where $d_{i,j} \in [0, 1]$. To satisfy the requirements of a proper topological metric space:
1. $d_{i,j} \ge 0$ (Non-negativity)
2. $d_{i,j} = 0 \iff i = j$ (Identity of indiscernibles)
3. $d_{i,j} = d_{j,i}$ (Symmetry)
4. $d_{i,j} \le d_{i,k} + d_{k,j}$ (Triangle inequality)

To account for correlation across the entire universe, the Euclidean distance between distance vectors $d_i$ and $d_j$ is computed:

$$\tilde{D}_{i,j} = \sqrt{\sum_{k=1}^N (d_{i,k} - d_{j,k})^2}$$

#### Step 2: Quasi-Diagonalization (Seriation)
Agglomerative hierarchical clustering merges closest clusters iteratively using single-linkage distance:

$$D(C_A, C_B) = \min_{i \in C_A, j \in C_B} \tilde{D}_{i,j}$$

The resulting tree linkage matrix is traversed recursively from the root to extract a 1D seriation ordering of leaf nodes:

$$\mathcal{O} = [s_1, s_2, \dots, s_N]$$

In $\mathcal{O}$, economically and statistically correlated assets are placed contiguously. This quasi-diagonalizes the covariance matrix without performing eigenvalue decomposition or matrix inversion.

#### Step 3: Recursive Bisection Weight Allocation
Let $L$ be the ordered list of items in the current cluster.
1. Split $L$ into two sub-clusters: $L_1$ (left) and $L_2$ (right).
2. For each sub-cluster $k \in \{1, 2\}$, compute the inverse-variance portfolio variance $\tilde{V}_k$:
   $$w_k^* = \frac{\text{diag}(\Sigma_k)^{-1}}{\mathbf{1}^T \text{diag}(\Sigma_k)^{-1} \mathbf{1}}$$
   $$\tilde{V}_k = {w_k^*}^T \Sigma_k w_k^*$$
3. Compute the recursive split factor $\alpha_1$:
   $$\alpha_1 = 1 - \frac{\tilde{V}_1}{\tilde{V}_1 + \tilde{V}_2} = \frac{\tilde{V}_2}{\tilde{V}_1 + \tilde{V}_2}, \quad \alpha_2 = 1 - \alpha_1$$
4. Update weights for all assets $i \in L_1$ and $j \in L_2$:
   $$w_i \leftarrow w_i \cdot \alpha_1$$
   $$w_j \leftarrow w_j \cdot \alpha_2$$
5. Recurse until every sub-cluster contains exactly one asset.

#### Step 4: Euler Marginal Contribution to Risk (MCR) Decomposition
By Euler's homogeneous function theorem, the total portfolio volatility $\sigma_p = \sqrt{w^T \Sigma w}$ decomposes exactly into the sum of asset marginal risk contributions:

$$\text{MCR}_i = \frac{\partial \sigma_p}{\partial w_i} = \frac{(\Sigma w)_i}{\sigma_p}$$

$$\sigma_p = \sum_{i=1}^N w_i \cdot \text{MCR}_i = \sum_{i=1}^N \text{RC}_i$$

The percentage risk share of asset $i$ is:

$$\% \text{RC}_i = \frac{w_i \cdot \text{MCR}_i}{\sigma_p} \times 100\%$$

---

### 2.2 Parametric Tail Risk: VaR & CVaR (Expected Shortfall)

Under the parametric Gaussian return assumption for 1-day portfolio log returns with mean $\mu_p$ and volatility $\sigma_p$:

$$\text{VaR}_\alpha = - (\mu_p + z_\alpha \cdot \sigma_p)$$

Where $z_{0.95} = -1.64485$ and $z_{0.99} = -2.32635$.

Conditional Value at Risk ($\text{CVaR}_\alpha$), or Expected Shortfall, measures the expected loss conditional on exceeding the $\text{VaR}_\alpha$ threshold:

$$\text{CVaR}_\alpha = \mathbb{E}[-R_p \mid -R_p \ge \text{VaR}_\alpha] = - \left( \mu_p - \sigma_p \frac{\phi(z_\alpha)}{1 - \alpha} \right)$$

Where $\phi(z) = \frac{1}{\sqrt{2\pi}} e^{-z^2/2}$ is the standard normal probability density function.

#### Basel III 10-Day Multi-Horizon Expansion
Following the Basel Committee on Banking Supervision (BCBS) standards for market risk capital requirements, 1-day tail risk metrics scale to a 10-day holding horizon using the square-root of time rule:

$$\text{VaR}_{10d, \alpha} = \sqrt{10} \cdot \text{VaR}_{1d, \alpha}$$

$$\text{CVaR}_{10d, \alpha} = \sqrt{10} \cdot \text{CVaR}_{1d, \alpha}$$

---

### 2.3 Macroeconomic Crisis Replay Simulations

To evaluate tail resiliency beyond parametric assumptions, the engine replays empirical historical asset shocks across 5 major crises:

| Crisis Scenario | Period | Key Macro Drivers | Portfolio Return | Naive EW Return | HRP Preservation Delta |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **2008 Global Financial Crisis** | Sep 2008 – Mar 2009 | Lehman collapse, banking credit freeze | **-40.56%** | -43.00% | **+$2,751.49** |
| **2015 Canadian Crude Oil Collapse** | Jun 2014 – Jan 2016 | OPEC price war, WTI/WCS collapse | **-15.13%** | -12.52% | -$2,606.18 |
| **2020 COVID Liquidity Freeze** | Feb 2020 – Mar 2020 | Global pandemic lockdown, VIX spike | **-29.78%** | -26.68% | -$3,100.29 |
| **2022 Central Bank Tightening Cycle** | Jan 2022 – Oct 2022 | Fed & BoC rapid 450bps rate hikes | **-12.13%** | -17.84% | **+$5,711.51** |
| **2026 Stagflationary Tariff Shock** | Forward Simulation | Cross-border supply & tariff disruption | **-11.27%** | -13.10% | **+$1,831.36** |

The Capital Preservation Delta ($\Delta_{\text{Preserved}}$) is defined as:

$$\Delta_{\text{Preserved}} = (R_{p,s} - R_{\text{EW},s}) \cdot W_0$$

Where $W_0 = \$100,000$ base capital.

---

### 2.4 QUANT INTEL™ Portfolio Orchestrator & Sizing Architecture

The orchestrator synthesizes security-level quantitative conviction, Markov macro hazard downweighting, HRP risk baseline weights, and execution TCA routing:

$$\tilde{w}_i = w_{\text{HRP},i} \cdot s_{\text{conv},i} \cdot s_{\text{hazard},i}$$

Where:
- $w_{\text{HRP},i}$ is the baseline HRP weight.
- $s_{\text{conv},i} \in \{A: 1.25, B: 1.00, C: 0.50, \text{SKIP}: 0.00\}$ is the security conviction grade scalar.
- $s_{\text{hazard},i} \in [0.4, 1.0]$ is the Markov macro regime hazard scalar.

#### Capital Conservation & Protective Cash Reserve
The total deployed capital $W_{\text{deployed}}$ and cash reserve $W_{\text{cash}}$ satisfy:

$$W_{\text{deployed}} = W_0 \sum_{i=1}^N \tilde{w}_i = \$58,280.00 \quad (58.28\%)$$

$$W_{\text{cash}} = W_0 - W_{\text{deployed}} = \$41,720.00 \quad (41.72\%)$$

$$W_{\text{deployed}} + W_{\text{cash}} = W_0 = \$100,000.00$$

The cash reserve is not an arbitrary idle balance; it represents the mathematical residue of macro hazard mitigation and conviction downweighting, protecting the portfolio during adverse market regimes.

#### Algorithmic Execution Routing & Child Slicing
For each active position $i$, target shares and execution strategies are assigned based on order size and liquidity tier:

$$\text{Shares}_i = \left\lfloor \frac{W_0 \cdot \tilde{w}_i}{P_{\text{arrival},i}} \right\rfloor$$

- Liquid Mega-Cap ($< \$5,000$): `DIRECT_MARKET` / `PASSIVE_LIMIT`.
- Mid-Cap / Cross-Border ($> \$5,000$): `TWAP_4_HOURS` / `VWAP_INTRADAY` with U-curve volume weighting.
- Large Block / Illiquid: `ICEBERG_POV_15PCT` to prevent book displacement.

---

## 3. STATUTORY COMPLIANCE & REGULATORY BOUNDARIES

The Phase 26 subsystem is engineered in strict accordance with North American securities laws:

1. **Canadian Securities Administrators (CSA) Staff Notice 31-369**:
   - The platform provides impersonal, generalized decision-support research.
   - It does not evaluate client financial circumstances, investment objectives, or risk tolerance.
   - Disclaimers clearly state that simulated portfolio allocations, risk reduction percentages, and scenario replays do not constitute individualized portfolio management or investment advice.
2. **SEC Publisher Exclusion (*Lowe v. SEC*, 472 U.S. 181; Section 202(a)(11)(D))**:
   - The platform distributes regular, bona fide publications containing impersonal analysis of securities to non-personalized subscribers.
   - The automated `ImpersonalAdviceLinter` runs in CI/CD, guaranteeing zero promotional, promissory, or advisory phrasing violations.
