# QUANT INTEL™ Phase 30: Institutional Options Volatility Surface Intelligence, Market-Maker Gamma Exposure (GEX) & Volatility Skew Architecture

**Document Version:** 1.0.0  
**Classification:** Institutional Decision-Support Architecture & Derivatives Quantitative Specification  
**Jurisdictions Covered:** United States (Cboe / OPRA) & Canada (Bourse de Montréal - MX)  
**Compliance Framework:** CSA Staff Notice 31-369 (Impersonal Research Exemption) & SEC Publisher Exclusion (*Lowe v. SEC*, 472 U.S. 181; Section 202(a)(11)(D))  

---

## 1. EXECUTIVE SUMMARY & OBJECTIVES

Modern equity price formation across major US and Canadian equities (e.g., `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`, `XIU`, `SHOP`, `RY`, `TD`, `ENB`) is increasingly governed by derivatives dealer positioning and dynamic gamma hedging. When institutional and retail market participants execute options contracts, options market makers (dealers) must dynamically buy or sell underlying equity shares to maintain a delta-neutral book ($\Delta_{\text{portfolio}} = 0$).

Traditional quantitative and fundamental equity platforms that evaluate only price action, volume profiles, and financial statements remain completely blind to these massive dealer hedging flows. Phase 30 implements an institutional options intelligence subsystem that models:
1. **Multi-Tenor Implied Volatility Surfaces (7D, 30D, 60D, 90D, 180D)**: Continuous SABR-parameterized volatility smiles mapping implied volatility across moneyness ($80\%$ to $120\%$) and term structure.
2. **Dealer Net Gamma Exposure (GEX)**: Strike-by-strike dollar gamma exposure quantifying the exact number of underlying shares dealers must buy or sell for every 1% move in spot price.
3. **The Gamma Flip Level ($S^*$)**: Numerical solver determining the exact strike boundary where market-maker positioning transitions from volatility-suppressing (Long Gamma) to volatility-amplifying (Short Gamma).
4. **25-Delta Put Skew (Risk Reversal) & Butterfly (Kurtosis)**: Quantitative measurement of institutional demand for downside crash protection relative to upside calls.
5. **Max Pain Strike & Expiration Pinning**: Identification of the exact strike minimizing cumulative option buyer value at monthly expiration.
6. **Dual-Market Structural Integration**: Simultaneous modeling of high-velocity, continuous electronic options on the **US Cboe** alongside Designated Market Maker (DMM) options on the Canadian **Bourse de Montréal (MX)**.

---

## 2. MATHEMATICAL FORMULATION & QUANTITATIVE DERIVATIVES THEORY

### 2.1 Black-Scholes-Merton Greek Derivations

For an underlying equity with spot price $S_0$, continuous dividend yield $q$, risk-free benchmark interest rate $r$, volatility $\sigma$, and time to expiration $T$ (in years):

$$d_1 = \frac{\ln(S_0 / K) + \left(r - q + \frac{\sigma^2}{2}\right)T}{\sigma \sqrt{T}}$$

$$d_2 = d_1 - \sigma \sqrt{T}$$

#### Option Gamma ($\Gamma$)
Gamma measures the second derivative of the option price with respect to underlying spot price, or the first derivative of option delta ($\Delta$):

$$\Gamma_{\text{call}} = \Gamma_{\text{put}} = \frac{e^{-q T} \phi(d_1)}{S_0 \cdot \sigma \sqrt{T}}$$

Where $\phi(x) = \frac{1}{\sqrt{2\pi}} e^{-x^2 / 2}$ is the standard normal probability density function. Option gamma is always non-negative ($\Gamma \ge 0$) and peaks exactly at the money ($K \approx S_0$), decaying rapidly into deep out-of-the-money and in-the-money territory.

#### Option Delta ($\Delta$)
$$\Delta_{\text{call}} = e^{-q T} N(d_1), \quad \Delta_{\text{put}} = -e^{-q T} N(-d_1) = e^{-q T} [N(d_1) - 1]$$

---

### 2.2 Market-Maker Net Gamma Exposure (GEX) Mechanics

In institutional options markets, market makers operate on the opposite side of public open interest:
- **Customer Put Buying (Downside Protection)**: Customers purchase OTM puts; dealers sell OTM puts $\implies$ dealers are **short puts**, making them **short gamma** on puts.
- **Customer Call Writing / Buying (Covered Calls & Speculation)**: Institutions write covered calls; dealers purchase calls $\implies$ dealers are **long calls**, making them **long gamma** on calls.

The dollar value of gamma exposure per 1% move in the underlying equity ($GEX$) for strike $K_i$ is defined as:

$$\text{Call GEX}_i = \Gamma_i \times S_0^2 \times 0.01 \times OI_{i, \text{call}} \times 100$$

$$\text{Put GEX}_i = -\Gamma_i \times S_0^2 \times 0.01 \times OI_{i, \text{put}} \times 100$$

$$\text{Net GEX}_i = \text{Call GEX}_i + \text{Put GEX}_i$$

$$\text{Total Net GEX} = \frac{\sum_i \text{Net GEX}_i}{1,000,000} \quad (\text{in \$ Millions per 1\% move})$$

#### Dealer Hedging Regimes
1. **Long Gamma Regime ($\text{Net GEX} > 0$)**:
   - Dealers are net long gamma: $\frac{\partial \Delta_{\text{dealer}}}{\partial S} > 0$.
   - As spot price **rises**, dealer delta becomes more positive $\implies$ dealers must **sell underlying shares** to remain delta-neutral.
   - As spot price **falls**, dealer delta becomes negative $\implies$ dealers must **buy underlying shares**.
   - **Market Effect**: Market makers trade counter-cyclically ("buy dips, sell rips"), dampening realized volatility, compressing daily ATR, and enforcing mean reversion.
2. **Short Gamma Regime ($\text{Net GEX} < 0$)**:
   - Dealers are net short gamma: $\frac{\partial \Delta_{\text{dealer}}}{\partial S} < 0$.
   - As spot price **falls**, dealer delta drops $\implies$ dealers are forced to **sell underlying shares into falling markets**.
   - As spot price **rises**, dealer delta expands $\implies$ dealers are forced to **buy underlying shares into rallies**.
   - **Market Effect**: Dealer hedging flow aligns directionally with price movement ("sell into drops, buy into rips"), accelerating momentum, creating liquidity voids, and amplifying gap risks.

#### The Gamma Flip Level Solver ($S^*$)
The platform numerically solves for the underlying price $S^*$ where aggregate Net GEX transitions across zero:

$$\text{Net GEX}(S^*) = 0$$

- If $S_0 > S^* + 0.005 \cdot S_0$: `LONG_GAMMA` (MM Posture: `SUPPRESSING_VOLATILITY`).
- If $S_0 < S^* - 0.005 \cdot S_0$: `SHORT_GAMMA` (MM Posture: `AMPLIFYING_VOLATILITY`).
- If $|S_0 - S^*| / S_0 \le 0.005$: `TRANSITION_ZONE` (MM Posture: `NEUTRAL_REBALANCING`).

---

### 2.3 25-Delta Volatility Skew & Smile Kurtosis Decomposition

Implied volatility varies systematically across strike prices, forming a persistent **volatility smile/smirk**. The engine parametrizes the implied volatility curve across moneyness $m = K / S_0$:

$$\sigma_{\text{IV}}(m) = \sigma_{\text{ATM}} + b_{\text{skew}} \cdot (1 - m) + c_{\text{kurt}} \cdot (1 - m)^2$$

Where:
- $\sigma_{\text{ATM}}$: At-the-money implied volatility ($m = 1.00$).
- $b_{\text{skew}}$: Skew slope (crash put premium; $b_{\text{skew}} \approx 0.36$ for 30-day options).
- $c_{\text{kurt}}$: Smile curvature/convexity ($c_{\text{kurt}} \approx 0.35$).

#### Standardized Risk Reversal (25-Delta Skew)
Evaluating the curve at 25-delta put ($m \approx 0.95$) and 25-delta call ($m \approx 1.05$):

$$\text{Skew}_{25\Delta} = \sigma_{\text{25D Put}} - \sigma_{\text{25D Call}} \quad (\text{in percentage points of IV})$$

$$\text{Butterfly}_{25\Delta} = \frac{\sigma_{\text{25D Put}} + \sigma_{\text{25D Call}}}{2} - \sigma_{\text{ATM}} \quad (\text{Kurtosis / Tail Thickness})$$

In normal market conditions, $\text{Skew}_{25\Delta}$ averages $+3.20\%$ to $+3.80\%$. An expansion above $+2.50\sigma$ ($> 6.50\%$) signals extreme panic buying of institutional downside disaster insurance.

---

### 2.4 Max Pain Strike & Monthly OPEX Pinning

The Max Pain theory dictates that at monthly options expiration (the third Friday of each month), market-maker delta hedging and option decay pin the underlying price to the strike where the cumulative dollar value paid out to option buyers is minimized:

$$\text{Cumulative Buyer Loss}(K) = \sum_{i} \Big( \max(0, K - K_i) \cdot OI_{i, \text{call}} + \max(0, K_i - K) \cdot OI_{i, \text{put}} \Big) \times 100$$

$$\text{Max Pain Strike} = \arg\min_{K} \Big( \text{Cumulative Buyer Loss}(K) \Big)$$

---

### 2.5 Dual-Market Structural Comparison (US Cboe vs. Canadian MX)

| Structural Attribute | US Market (Cboe / OPRA) | Canadian Market (Bourse de Montréal - MX) |
|:---|:---|:---|
| **Underlying Assets** | `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA` | `XIU`, `SHOP`, `RY`, `TD`, `ENB` |
| **Market Making Model** | Continuous Electronic Multilateral Matching | Designated Market Makers (DMM) with Quoting Obligations |
| **Contract Tenors** | Daily (0DTE), Weekly, Monthly, LEAPS | Monthly, Quarterly, Select Weeklies |
| **Quote Density & Depth** | Massive electronic bid-ask depth (penny spreads) | Concentrated near ATM strikes; wider bid-ask spreads |
| **Aggregate Net GEX Scale** | $>\$2,000\text{M}$ per 1% move | $\sim \$50\text{M}$ per 1% move |
| **Expiration Pinning Strength** | Dominated by intraday gamma scalping | Dominated by quarterly institutional roll dates |

---

## 3. INVARIANT SENTINELS & CIRCUIT BREAKERS (PREDICATES 20 & 21)

Phase 30 expands the platform's autonomous risk monitoring system from 19 to 21 formal invariant predicates:

### Predicate 20: `GAMMA_FLIP_REGIME_TRANSITION`
- **Definition**: Triggers when an underlying equity price falls below its dealer Gamma Flip strike ($S_0 < S^*$) and Net GEX turns negative ($< -\$50\text{M}$ on US Cboe or $< -\$5\text{M}$ on Canadian MX).
- **Severity**: `WARNING`
- **Action Required**: `WIDEN_STOP_THRESHOLDS`
- **System Taxonomy**: `REGIME_SHIFT`
- **Economic Rationale**: In negative gamma, dealer hedging shifts from volatility-suppressing to volatility-amplifying. Stop-loss orders must be widened or delta-hedged to avoid whipsaws in liquidity vacuums.
- **Current System Status**: Nominal (all 10 tracked symbols are currently in positive Net GEX territory).

### Predicate 21: `VOLATILITY_SKEW_TAIL_INVERSION`
- **Definition**: Triggers when 30-day 25-delta put skew expands beyond $+2.50\sigma$ above its 1-year historical mean ($z > +2.50$).
- **Severity**: `WARNING`
- **Action Required**: `SCRUTINIZE_TAIL_RISK_PROTECTION`
- **System Taxonomy**: `PORTFOLIO_CIRCUIT_BREAKER`
- **Economic Rationale**: A $2.5\sigma$ skew spike indicates institutional panic bidding for downside put protection, signaling heightened crash hazard.
- **Current System Status**: Nominal (SPY skew: $+3.60\%$, $z = -0.18\sigma$; QQQ skew: $+3.60\%$, $z = -0.18\sigma$).

---

## 4. WORKSPACE DELIVERABLES & ARTIFACTS

1. **Domain Models & Pydantic Schemas (`src/models/schemas.py`)**:
   - `OptionStrikeGex`: Strike, call OI, put OI, call gamma, put gamma, call GEX ($M), put GEX ($M), and net GEX ($M).
   - `VolatilitySmilePoint`: Moneyness %, strike, implied volatility %, and delta.
   - `TermVolatilitySurface`: Tenor label (7D, 30D, 60D, 90D, 180D), days, ATM IV %, 25D skew %, butterfly kurtosis %, and smile points.
   - `TickerOptionsDossier`: Complete dossier including spot, Net GEX, gamma regime, Gamma Flip, Max Pain, P/C ratios, skew metrics, and full surfaces.
   - `OptionsIntelligenceFeed`: Master container feed with US and Canadian aggregate GEX metrics.
2. **Master Calculation Engine (`src/engine/options_surface_gex.py`)**:
   - `calculate_bsm_gamma(...)` & `calculate_bsm_delta(...)`: High-precision analytical derivatives.
   - `build_volatility_surface(...)`: Multi-tenor SABR-parameterized surface generator.
   - `build_strike_gex_and_max_pain(...)`: Strike GEX profile, Gamma Flip solver, and Max Pain minimization.
   - `evaluate_ticker_options(...)`: Dossier generation for individual equities.
   - `evaluate_options_intelligence(...)`: Aggregate evaluation across US and Canadian markets with singleton caching.
   - `export_feed(...)`: Canonical JSON feed export to `data/feeds/options_intelligence.json`.
3. **Distribution & Staging Integration**:
   - `src/data/edge_exporter.py`: Integrated `cross_border_options_engine.export_feed()`.
   - `scripts/build_firebase_public.py`: Staged `/api/options_intelligence.json` and extensionless clean route `/api/options_intelligence`.
4. **Interactive Terminal UI Workstation (`web/index.html` & `public/index.html`)**:
   - Dedicated top-level tab: **`📊 Options & Volatility Surface Lab`**.
   - Market filter switcher: `[ All (10) ] [ 🇺🇸 US Cboe ] [ 🇨🇦 CA MX ]`.
   - Dynamic ticker selector pill strip with live regime indicators.
   - 5 KPI summary cards.
   - 5 navigable workstation sub-views: `Overview & Net GEX Matrix`, `Dealer GEX Strike Profile & Gamma Flip`, `Multi-Tenor Volatility Surface & Smile`, `25-Delta Skew & Max Pain Pinning`, and `Derivative Sentinels (P20 & P21)`.
   - Strike distribution table with colored Net GEX visual bars and Gamma Flip marker.
5. **Quality Assurance & Verification**:
   - `tests/test_options_surface_gex.py`: 7 unit and integration tests (100% pass rate).
   - `tests/test_options_release.py`: 5 master release audit tests (100% pass rate).

---

## 5. STATUTORY REGULATORY COMPLIANCE

All Phase 30 derivatives analytics, surface models, and UI interfaces strictly adhere to Canadian Securities Administrators (CSA) Staff Notice 31-369 and Section 202(a)(11)(D) of the US Investment Advisers Act of 1940 (*Lowe v. SEC*):

1. **Non-Personalized Decision Support**: All implied volatility curves, dealer Net GEX profiles, Gamma Flip levels, and Max Pain strikes represent impersonal mathematical calculations derived from exchange open interest and option pricing models. They do not evaluate any user's personal financial situation, options account approval tier, margin capability, or investment objectives.
2. **No Derivative Trade Recommendations**: The platform does not recommend buying or selling specific options contracts, writing covered calls, or constructing multi-leg spreads (e.g., iron condors, straddles, calendars).
3. **Derivatives Risk Disclosures**: Explicit disclosures notify users that options trading entails substantial leverage risk, rapid time decay ($\Theta$), potential total loss of premium, and execution slippage.
4. **Automated Compliance Verification**: 100% of all Phase 30 disclaimer texts, UI notices, and alert action codes were scanned by the automated `ImpersonalAdviceLinter`, returning **0 regulatory violations**.
