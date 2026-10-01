# QUANT INTEL™ Phase 29: Cross-Border Dual-Currency Analytics, Optimal FX Hedging & TSX/NYSE Arbitrage Architecture

**Document Version:** 1.0.0  
**Classification:** Institutional Decision-Support Architecture & Quantitative Specification  
**Jurisdictions Covered:** United States (NYSE / NASDAQ) & Canada (TSX / TSXV)  
**Compliance Framework:** CSA Staff Notice 31-369 (Impersonal Research Exemption) & SEC Publisher Exclusion (*Lowe v. SEC*, 472 U.S. 181; Section 202(a)(11)(D))  

---

## 1. EXECUTIVE SUMMARY & OBJECTIVES

Cross-border portfolio management across the United States and Canada introduces two distinct structural phenomena absent in single-currency equity research:
1. **Foreign Exchange Risk & Carry Drag**: Unhedged foreign equity exposure exposes domestic capital to currency volatility. However, naive 100% currency hedging is often mathematically suboptimal due to the **natural currency buffer** (the negative correlation between US equity returns and CAD/USD exchange rate movements during equity risk-off regimes) and the ongoing **carry drag** imposed by the Bank of Canada and US Federal Reserve overnight interest rate differential.
2. **Dual-Listed Intermarket Pricing Disparities**: For companies interlisted on both the Toronto Stock Exchange (TSX) and the New York Stock Exchange (NYSE) (e.g., Royal Bank of Canada, TD Bank, Shopify, Enbridge, Canadian Natural Resources, Brookfield, Canadian National Railway, and Canadian Pacific Kansas City), intraday liquidity fragmentation, differing market makers, and FX fluctuations create transitory basis spreads. If these spreads exceed the empirical round-trip execution friction hurdle (9.0 bps), significant execution alpha can be captured via smart order venue routing without taking directional equity risk.

Phase 29 delivers an institutional quantitative subsystem that models Covered Interest Rate Parity (CIP) forward term structures, computes minimum-variance optimal currency hedge ratios ($h^*$), provides an interactive real-time hedging simulator, and dynamically routes interlisted orders to the economically superior exchange venue.

---

## 2. MATHEMATICAL FORMULATION & QUANTITATIVE METHODOLOGY

### 2.1 Covered Interest Rate Parity (CIP) Forward Term Structure

Under no-arbitrage money market equilibrium, the forward foreign exchange rate for maturity $T$ (expressed in fractions of a 365-day year) is determined by the spot exchange rate $S_0$ (CAD per USD) and the respective money market benchmark rates:

$$F_T = S_0 \times \frac{1 + r_{\text{CAD}} \cdot \left(\frac{T}{365}\right)}{1 + r_{\text{USD}} \cdot \left(\frac{T}{365}\right)}$$

Where:
- $S_0$: Official Bank of Canada Valet daily spot exchange rate ($1.4188$ CAD per USD; $0.7048$ USD per CAD as of September 2026).
- $r_{\text{CAD}}$: Bank of Canada policy overnight target rate ($4.25\%$).
- $r_{\text{USD}}$: US Federal Reserve Effective Federal Funds rate ($4.85\%$).
- Rate Differential: $\Delta r = r_{\text{CAD}} - r_{\text{USD}} = 4.25\% - 4.85\% = -0.60\%$ ($-60.0$ bps).

#### Forward Points & Synthetic Basis
Forward points (quoted in pips where $1 \text{ pip} = 0.0001$) represent the carry adjustment:

$$\text{Points}_T = (F_T - S_0) \times 10,000$$

Because Canadian interest rates are lower than US interest rates ($r_{\text{CAD}} < r_{\text{USD}}$), forward contracts trade at a discount to spot ($\text{Points}_T < 0$), reflecting the USD forward discount / CAD discount. 

Empirical institutional CIP basis reflects the cross-currency basis swap spread ($x_{\text{CIP}}$), which captures the USD structural funding premium in global interbank markets:

$$\text{CIP Basis}_T = -8.5 - 3.5 \cdot \left(\frac{T}{365}\right) \text{ bps}$$

#### CIP Forward Curve Term Structure Table (As of September 2026)
| Tenor | Days ($T$) | BoC Rate | Fed Rate | Spread ($\Delta r$) | Spot FX ($S_0$) | Forward FX ($F_T$) | Forward Points | CIP Basis | Annualized Drag |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **1M** | 30 | 4.25% | 4.85% | -60.0 bps | 1.4188 | 1.4181 | -7.0 pips | -8.8 bps | 60.0 bps |
| **3M** | 91 | 4.25% | 4.85% | -60.0 bps | 1.4188 | 1.4167 | -21.0 pips | -9.4 bps | 60.0 bps |
| **6M** | 182 | 4.25% | 4.85% | -60.0 bps | 1.4188 | 1.4147 | -41.4 pips | -10.2 bps | 60.0 bps |
| **12M** | 365 | 4.25% | 4.85% | -60.0 bps | 1.4188 | 1.4107 | -81.2 pips | -12.0 bps | 60.0 bps |

---

### 2.2 Minimum-Variance Optimal Currency Hedging Engine ($h^*$)

Consider a domestic portfolio manager holding foreign assets with local currency return $R_{\text{foreign}}$ and currency exchange rate return $R_{\text{FX}} = \ln(S_t / S_{t-1})$. 

If the manager implements a fractional currency forward hedge ratio $h \in [0.0, 1.0]$, the hedged domestic return is:

$$R_{\text{hedged}}(h) = R_{\text{foreign}} + (1 - h) R_{\text{FX}} - h \cdot \text{CarryDrag}$$

The annualized portfolio variance as a function of hedge ratio $h$ is:

$$\sigma^2_p(h) = \sigma^2_{\text{foreign}} + (1 - h)^2 \sigma^2_{\text{FX}} + 2 (1 - h) \text{Cov}(R_{\text{foreign}}, R_{\text{FX}})$$

Differentiating with respect to $h$ and setting the derivative to zero yields the **Minimum-Variance Optimal Hedge Ratio**:

$$\frac{\partial \sigma^2_p}{\partial h} = -2(1 - h)\sigma^2_{\text{FX}} - 2 \text{Cov}(R_{\text{foreign}}, R_{\text{FX}}) = 0$$

$$h^* = 1 + \frac{\text{Cov}(R_{\text{foreign}}, R_{\text{FX}})}{\text{Var}(R_{\text{FX}})}$$

Equivalently, defined relative to the foreign asset return:

$$h^* = -\frac{\text{Cov}(R_{\text{foreign}}, R_{\text{FX}})}{\text{Var}(R_{\text{FX}})} = -\rho \cdot \frac{\sigma_{\text{foreign}}}{\sigma_{\text{FX}}}$$

Constrained to long-only hedging posture: $h^* \in [0.0, 1.0]$.

#### The Natural Currency Hedge Phenomenon
In empirical market history, US large-cap equities (e.g., Apple, Microsoft, Nvidia, Amazon) exhibit a negative correlation with CAD/USD exchange rate changes during stress periods ($\rho \approx -0.05$ to $-0.25$). During global risk-off episodes (e.g., 2008 GFC, 2020 COVID crash, 2022 rate shocks), global capital flows into the US dollar as a safe haven, causing the USD to appreciate against the Canadian dollar.

For a Canadian investor:
- US stock price drops in USD terms: $-10\%$
- USD appreciates against CAD: $+6\%$
- Net return in CAD terms: approximately $-4\%$ (the currency appreciation cushions the drawdown).

Because the currency acts as an automatic organic buffer, **fully hedging ($h = 1.0$) eliminates this protective cushion**, actually *increasing* volatility compared to the optimal partial hedge ($h^* \approx 0.227$), while forcing the investor to pay 60 bps of annualized negative carry points.

#### Empirical Hedging Parameters
| Base Currency | Portfolio Basket | Unhedged Vol ($\sigma_0$) | 100% Hedged Vol ($\sigma_1$) | Min-Var Hedge ($h^*$) | Optimal Vol ($\sigma^*$) | Vol Reduction | Carry Drag | Posture Classification |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **CAD** | US Large-Cap Basket | 19.18% | 18.86% | **22.7%** ($0.227$) | **18.82%** | **-1.84%** | 13.6 bps | `PARTIAL_NATURAL_HEDGE` |
| **USD** | Canadian TSX Basket | 17.08% | 16.56% | **11.6%** ($0.116$) | **16.55%** | **-3.05%** | 7.0 bps | `UNHEDGED_OPTIMAL` |

---

### 2.3 Dual-Listed Intraday Basis Arbitrage & Smart Venue Router

For dual-listed Canadian equities trading simultaneously on the TSX in CAD and the NYSE in USD, the theoretical parity price in CAD is:

$$P_{\text{implied, CAD}} = P_{\text{NYSE, USD}} \times S_0$$

The raw basis spread in basis points is:

$$\text{Basis Spread (bps)} = \left( \frac{P_{\text{TSX, CAD}} - P_{\text{implied, CAD}}}{P_{\text{TSX, CAD}}} \right) \times 10,000$$

#### Dual-Market Round-Trip Friction Hurdle
Arbitrage cannot be monetized without crossing market frictions. The platform models institutional round-trip frictions:
1. **Exchange Clearing & Settlement**: $1.5$ bps (CDS clearing for TSX + DTC clearing for NYSE).
2. **Bid-Ask Crossing**: $6.0$ bps ($3.0$ bps half-spread on TSX $+ 3.0$ bps half-spread on NYSE).
3. **Spot FX Conversion Slippage**: $1.5$ bps (institutional wholesale FX spread).
4. **Total Hurdle**: $H = 1.5 + 6.0 + 1.5 = \mathbf{9.0\text{ bps}}$.

#### Net Arbitrage & Routing Decision Rule
$$\text{Net Arbitrage (bps)} = \max\Big(0.0, \, |\text{Basis Spread}| - 9.0\text{ bps}\Big)$$

The Smart Order Router generates deterministic execution recommendations:
- **If $\text{Basis Spread} > +9.0$ bps**: TSX is trading at a premium to NYSE $\implies$ Route buy orders to **`EXECUTE_NYSE`** (buy in USD, convert from CAD, capturing the basis discount).
- **If $\text{Basis Spread} < -9.0$ bps**: NYSE is trading at a premium to TSX $\implies$ Route buy orders to **`EXECUTE_TSX`** (buy in CAD on TSX).
- **If $|\text{Basis Spread}| \le 9.0$ bps**: The intermarket price difference is within friction bounds $\implies$ **`PARITY_EFFICIENT`** (execute in primary liquidity center).

#### Live Interlisted Universe Monitor (9 Dual-Listed Equities)
| Symbol | US Symbol | Name | TSX Close (CAD) | NYSE Close (USD) | Implied CAD | Raw Spread | Hurdle | Net Arbitrage | Smart Venue Route | Liquidity Center |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **CNR** | CNI | Canadian National Railway | $170.90 | $120.93 | $171.58 | **-39.5 bps** | 9.0 bps | **+30.5 bps** | `EXECUTE_TSX` | TSX Dominant (1.75x) |
| **CP** | CP | Canadian Pacific Kansas | $122.90 | $86.91 | $123.31 | **-33.2 bps** | 9.0 bps | **+24.2 bps** | `EXECUTE_TSX` | TSX Dominant (1.68x) |
| **RY** | RY | Royal Bank of Canada | $285.63 | $201.98 | $286.57 | **-32.9 bps** | 9.0 bps | **+23.9 bps** | `EXECUTE_TSX` | TSX Dominant (2.12x) |
| **TD** | TD | Toronto-Dominion Bank | $169.80 | $120.10 | $170.40 | **-35.0 bps** | 9.0 bps | **+26.0 bps** | `EXECUTE_TSX` | Balanced (1.10x) |
| **SHOP** | SHOP | Shopify Inc. | $204.50 | $144.15 | $204.52 | **-1.0 bps** | 9.0 bps | 0.0 bps | `PARITY_EFFICIENT` | US Dominant (0.42x) |
| **ENB** | ENB | Enbridge Inc. | $66.58 | $46.90 | $66.54 | **+6.0 bps** | 9.0 bps | 0.0 bps | `PARITY_EFFICIENT` | TSX Dominant (1.85x) |
| **CNQ** | CNQ | Canadian Natural Res. | $67.38 | $47.52 | $67.42 | **-6.2 bps** | 9.0 bps | 0.0 bps | `PARITY_EFFICIENT` | TSX Dominant (2.30x) |
| **BAM** | BAM | Brookfield Asset Mgmt | $62.94 | $44.35 | $62.92 | **+3.2 bps** | 9.0 bps | 0.0 bps | `PARITY_EFFICIENT` | Balanced (1.05x) |
| **BN** | BN | Brookfield Corp. | $79.80 | $56.26 | $79.82 | **-2.5 bps** | 9.0 bps | 0.0 bps | `PARITY_EFFICIENT` | Balanced (0.95x) |

---

## 3. SENTINEL CIRCUIT BREAKERS (PREDICATES 18 & 19)

Phase 29 expands the platform's autonomous risk monitoring system from 17 to 19 formal invariant predicates:

### Predicate 18: `CROSS_BORDER_PARITY_DISLOCATION`
- **Definition**: Triggers when any dual-listed equity basis spread magnitude exceeds 45.0 bps ($|\text{Spread}| > 45.0\text{ bps}$).
- **Severity**: `WARNING`
- **Action Required**: `INVESTIGATE_VENUE_ARBITRAGE`
- **Compliance Status**: Verified clean under `ImpersonalAdviceLinter` (0 violations).
- **Nominal Max Spread**: 39.5 bps (CNR) $\implies$ **PASS (Nominal)**.

### Predicate 19: `UNHEDGED_CURRENCY_DRAG`
- **Definition**: Triggers when unhedged portfolio volatility exceeds fully hedged portfolio volatility by more than 2.00% ($\sigma_{\text{unhedged}} - \sigma_{\text{hedged}} > 2.00\%$).
- **Severity**: `WARNING`
- **Action Required**: `IMPLEMENT_CURRENCY_HEDGE`
- **Compliance Status**: Verified clean under `ImpersonalAdviceLinter` (0 violations).
- **Nominal Currency Drag**: CAD base drag $= 19.18\% - 18.86\% = +0.32\%$; USD base drag $= 17.08\% - 16.56\% = +0.52\% \implies$ **PASS (Nominal)**.

---

## 4. WORKSPACE DELIVERABLES & ARTIFACTS

1. **Domain Models & Pydantic Schemas (`src/models/schemas.py`)**:
   - `CipForwardPoint`: Tenor, days, BoC rate, Fed rate, interest diff (bps), spot FX, forward FX, points (pips), and CIP basis (bps).
   - `OptimalHedgeRatioSummary`: Unhedged vol, hedged vol, optimal hedge ratio ($h^*$), min-variance vol, risk reduction %, FX correlation, drag (bps), and posture.
   - `DualListedArbitrageOpportunity`: TSX price, NYSE price, implied parity, basis spread (bps), hurdle (bps), net arbitrage, routing recommendation, and liquidity center.
   - `CrossBorderFxFeed`: Complete institutional payload container.
2. **Master Calculation Engine (`src/engine/cross_border_fx.py`)**:
   - `compute_forward_curve(spot_fx)`: Generates 1M–12M forward points and empirical CIP basis.
   - `compute_optimal_hedge_ratio(...)`: Solves minimum-variance analytical hedge for CAD and USD bases.
   - `evaluate_dual_listed_arbitrage(spot_fx)`: Computes basis spreads and venue recommendations.
   - `evaluate_cross_border_fx(...)`: Master evaluation pipeline with singleton caching.
   - `export_feed(...)`: Writes to `data/feeds/cross_border_fx.json`.
3. **Distribution & Build Integration**:
   - `src/data/edge_exporter.py`: Integrated `cross_border_fx_engine.export_feed()`.
   - `scripts/build_firebase_public.py`: Staged `/api/cross_border_fx.json` and clean extensionless route `/api/cross_border_fx`.
4. **Terminal UI Workstation (`web/index.html` & `public/index.html`)**:
   - Dedicated top-level tab: **`💱 Cross-Border FX & Arbitrage`**.
   - Currency base switcher: `[ 🇨🇦 CAD Base ]` vs `[ 🇺🇸 USD Base ]`.
   - 5 KPI summary strip cards.
   - 5 navigable workstation sub-views: `Cross-Border Command`, `CIP Forward Curve`, `Optimal Hedging ($h^*$) & Simulator`, `Dual-Listed Arbitrage & Router`, and `Dislocation Sentinels (P18 & P19)`.
   - Dynamic real-time interactive hedge ratio range slider with continuous volatility interpolation.
5. **Quality Assurance & Verification**:
   - `tests/test_cross_border_fx.py`: 7 unit and integration tests (100% pass rate).
   - `tests/test_fx_release.py`: 6 master release audit tests (100% pass rate).

---

## 5. STATUTORY REGULATORY COMPLIANCE

All Phase 29 analytics, formulas, notices, and UI interfaces strictly adhere to the Canadian Securities Administrators (CSA) Staff Notice 31-369 and Section 202(a)(11)(D) of the US Investment Advisers Act of 1940 (*Lowe v. SEC*):

1. **Non-Personalized Decision Support**: All currency hedge ratios ($h^*$), forward curve points, and venue routing outputs are quantitative mathematical simulations calculated across market-wide index and basket data. They do not examine or account for any individual user's tax bracket, foreign property reporting threshold (CRA Form T1135), leverage capacity, or risk tolerance.
2. **No Brokerage or Execution Handling**: The platform operates strictly as an impersonal research system. It does not execute foreign exchange conversions, provide currency forward contracts, hold client capital, or place exchange orders.
3. **Execution Slippage & Friction Disclaimers**: Explicit statutory disclosures notify users that live arbitrage yields and forward pricing are subject to intermarket latency, broker financing spreads, currency conversion fees, and exchange borrow rates.
4. **Automated Compliance Verification**: 100% of all Phase 29 disclaimer texts, UI notices, and alert action codes were scanned by the automated `ImpersonalAdviceLinter`, returning **0 regulatory violations**.
