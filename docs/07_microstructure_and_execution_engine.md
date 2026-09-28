# QUANT INTEL™ Phase 25: Real-Time Microstructure, WebSocket Streaming & Algorithmic Execution Simulator (TCA) Architecture

**Document Version:** 1.0.0  
**Classification:** Institutional Decision-Support Architecture & Quantitative Specification  
**Jurisdictions Covered:** United States (NYSE / NASDAQ / BATS) & Canada (TSX / TSXV / CSE)  
**Compliance Framework:** CSA Staff Notice 31-369 (Impersonal Research Exemption) & SEC Publisher Exclusion (*Lowe v. SEC*, 472 U.S. 181)  

---

## 1. EXECUTIVE SUMMARY & OBJECTIVES

The Phase 25 subsystem introduces an institutional-grade high-frequency order flow and market microstructure layer to the dual-market intelligence platform. In modern electronic markets, high-conviction swing and intraday trading setups cannot rely solely on daily OHLCV bars or retrospective technical indicators; they require precise visibility into top-of-book queue dynamics, aggressor volume imbalances, market impact costs, and execution slippage.

### 1.1 Core Subsystem Milestones
1. **Phase 25.1 — Tick Microstructure Engine & Depth of Market (DOM)**:
   - High-precision implementation of the **Lee-Ready (1991)** trade signing algorithm.
   - 5-level Tiered Depth of Market (DOM) L2 order book simulation.
   - Real-time **Order Book Imbalance (OBI)**, **Microprice**, and **Cumulative Volume Delta (CVD)** across rolling 1-minute and 5-minute delta windows.
   - Empirical **Kyle's Lambda ($\lambda_{\text{Kyle}}$)** price impact coefficient computation.
   - Institutional microstructural passive absorption detection and flash liquidity void sentinels.
2. **Phase 25.2 — Real-Time Streaming Protocols (SSE & Asynchronous WebSocket)**:
   - High-throughput asynchronous bidirectional WebSocket server (`src/engine/streaming_server.py`) operating on `ws://0.0.0.0:8001/ws/microstructure`.
   - Multi-channel JSON-RPC client multiplexing (`subscribe`, `unsubscribe`, `ping`/`PONG` heartbeats) for `ticks`, `book`, and `metrics`.
   - Native Server-Sent Events (SSE) broadcasting endpoint (`/api/stream/ticks`) at 2 Hz.
   - Multi-tier dynamic client streamer in the terminal workstation with automated cascading failover: Native WebSocket $\rightarrow$ Server-Sent Events $\rightarrow$ Buffered Edge Polling.
3. **Phase 25.3 — Algorithmic Execution Simulator & Transaction Cost Analysis (TCA)**:
   - Institutional implementation of **Almgren-Chriss (2000)** and **Kyle (1985)** market impact models.
   - 5 supported algorithmic order execution strategies: `DIRECT_MARKET`, `LIMIT_PASSIVE`, `TWAP`, `VWAP`, and `POV_10` (Dynamic Iceberg).
   - Dual-market exchange fee schedules: US Reg NMS maker-taker fees vs. Canadian UMIR/TSX continuous trading fees.
   - Seamless integration with the QUANT INTEL™ institutional trade plan card and Invalidation Sentinel (`EXECUTION_SLIPPAGE_WARNING`).
   - Interactive terminal workstation simulator providing dynamic child order slicing timelines and multi-strategy cost comparisons.
4. **Phase 25.4 — Release Audit, Health Telemetry & Architectural Documentation**:
   - Full telemetry integration into `/api/production_health.json`.
   - Symbol drawer modal deep-dive integration.
   - Rigorous automated test coverage and compliance audit.

---

## 2. MATHEMATICAL & ALGORITHMIC SPECIFICATIONS

### 2.1 Aggressor Side Determination: Lee-Ready (1991) Algorithm
To classify incoming trade prints as buyer-initiated or seller-initiated without internal broker tags, the engine applies the **Lee-Ready (1991)** trade signing algorithm:

$$\text{Midpoint}_t = \frac{P^{\text{bid}}_t + P^{\text{ask}}_t}{2}$$

1. **Quote Rule**:
   - If $P_t > \text{Midpoint}_t$, the trade executed above the prevailing midpoint and is classified as **BUY_AGGRESSOR** ($s_t = +1$).
   - If $P_t < \text{Midpoint}_t$, the trade executed below the prevailing midpoint and is classified as **SELL_AGGRESSOR** ($s_t = -1$).
2. **Tick Rule (Fallback for exact midpoint executions)**:
   - If $|P_t - \text{Midpoint}_t| < \epsilon$ (where $\epsilon = 10^{-5}$):
     - If $P_t > P_{t-1}$ (uptick), $s_t = +1$ (**BUY_AGGRESSOR**).
     - If $P_t < P_{t-1}$ (downtick), $s_t = -1$ (**SELL_AGGRESSOR**).
     - If $P_t = P_{t-1}$ (zero-tick), $s_t = s_{t-1}$ (maintain previous sign).

### 2.2 Order Book Imbalance (OBI)
Order Book Imbalance captures the immediate supply/demand asymmetry across the top-of-book depth:

$$\text{OBI}_t = \frac{\sum_{i=1}^{K} V^{\text{bid}}_{i,t} - \sum_{i=1}^{K} V^{\text{ask}}_{i,t}}{\sum_{i=1}^{K} V^{\text{bid}}_{i,t} + \sum_{i=1}^{K} V^{\text{ask}}_{i,t}} \in [-1.0, +1.0]$$

- $\text{OBI} > +0.20$: Buyer replenishment / bid-side queue support.
- $\text{OBI} < -0.20$: Ask-side supply wall / seller replenishment.
- $-0.20 \le \text{OBI} \le +0.20$: Order book in neutral equilibrium.

### 2.3 Volume-Weighted Microprice
The classical mid-price assigns equal weight to bid and ask regardless of size. The **Microprice** ($P_{\text{micro}}$) adjusts for depth skew:

$$P_{\text{micro}} = \frac{V^{\text{bid}}_{1} \cdot P^{\text{ask}}_{1} + V^{\text{ask}}_{1} \cdot P^{\text{bid}}_{1}}{V^{\text{bid}}_{1} + V^{\text{ask}}_{1}} = P^{\text{mid}} + \frac{1}{2} \cdot \text{Spread} \cdot \text{OBI}_{L1}$$

When the bid queue is heavily loaded, $P_{\text{micro}}$ shifts upward toward the ask, signaling an impending upward quote revision.

### 2.4 Cumulative Volume Delta (CVD)
The Cumulative Volume Delta accumulates signed aggressor trade volume across the session:

$$\text{CVD}_T = \sum_{t=1}^{T} s_t \cdot V_t$$

Rolling momentum windows track institutional aggression shifts:
- **$\Delta \text{CVD}_{1\text{m}}$**: 1-minute rolling delta window.
- **$\Delta \text{CVD}_{5\text{m}}$**: 5-minute rolling delta window.

A divergence where price tests resistance while $\Delta \text{CVD}$ drops sharply identifies **Bearish Aggressor Exhaustion**. Conversely, price holding support while $\text{CVD}$ expands identifies **Bullish Passive Absorption**.

### 2.5 Kyle's Lambda ($\lambda_{\text{Kyle}}$) Price Impact Coefficient
Following Albert S. Kyle (1985), price impact reflects how much prices move per unit of signed order flow:

$$\Delta P_t = \lambda_{\text{Kyle}} \cdot Q_t + \epsilon_t \implies \lambda_{\text{Kyle}} = \frac{|\Delta P_t|}{\sqrt{V_t}}$$

- **Resilient Book ($\lambda < 0.030$)**: Thick book depth where institutional orders can execute with minimal adverse price excursion.
- **Fragile Book ($\lambda \ge 0.050$)**: Thin queues where market sweeps inflict substantial market impact.

### 2.6 Almgren-Chriss (2000) Optimal Order Slicing & Market Impact
For an institutional order of $N$ shares executed over horizon $T$ divided into $M$ child slices $n_1, n_2, \dots, n_M$ ($N = \sum_{k=1}^M n_k$):

1. **Temporary Market Impact ($I_{\text{temp}}$)**:
   $$I_{\text{temp}}(n_k) = \frac{n_k}{10,000} \cdot \lambda_{\text{Kyle}} \cdot \left(\frac{1}{M^{\alpha}}\right)$$
   where $\alpha \approx 0.55$ reflects the universal non-linear square-root law of market impact.
2. **Spread Crossing Cost**:
   $$\text{Cost}_{\text{spread}} = \frac{1}{2} \cdot \text{Spread} \cdot N \quad (\text{for liquidity takers})$$
3. **Implementation Shortfall (IS)**:
   $$\text{IS} = (P_{\text{eff}} - P_{\text{arrival}}) \cdot N + \text{Exchange Fees}$$
   $$\text{IS}_{\text{bps}} = \frac{\text{IS}}{P_{\text{arrival}} \cdot N} \times 10,000$$

### 2.7 Algorithmic Strategy Specifications
| Strategy | Execution Mechanism | Liquidity Role | Market Impact Model | Fill Probability |
|:---|:---|:---:|:---|:---:|
| **DIRECT_MARKET** | Aggressive sweep across L1–L5 DOM depth tiers. | TAKER | Depth-weighted DOM walk + Kyle's $\lambda$ residual. | $99.9\%$ (Immediate) |
| **LIMIT_PASSIVE** | Resting limit order posted at best bid (buy) or best ask (sell). | MAKER | Captures half-spread; maker rebate earned; adverse selection modeled. | $\mathbb{P}(\text{Fill}) \in [25\%, 92\%]$ via logistic OBI |
| **TWAP** | Uniform child order slicing across $M$ equal time intervals. | MIXED | $I \propto M^{-0.55}$ square-root reduction. Alternating maker/taker. | $95.0\%$ |
| **VWAP** | Slicing weighted by intraday U-curve volume $[0.28, 0.16, 0.12, 0.16, 0.28]$. | MIXED | Impact weighted inversely to volume bin depth ($1/\sqrt{w_k}$). | $96.5\%$ |
| **POV_10** | Dynamic Iceberg capping participation rate at $10\%$ of visible volume. | MAKER | Rested passive child slices. Suppresses signaling footprint. | $91.0\%$ |

---

## 3. DUAL-MARKET MICROSTRUCTURE & EXCHANGE FEE SCHEDULES

### 3.1 United States Reg NMS Framework
- **Regulatory Rules**: SEC Rule 610 (Access Fees capped at 30 mils / $0.0030 per share) & Rule 611 (Order Protection Rule prohibiting trade-throughs).
- **Exchange Fees**:
  - Taker Fee: $+\$0.0030$ / share.
  - Maker Rebate: $-\$0.0020$ / share.
  - Regulatory Clearing: FINRA Trading Activity Fee (TAF) $+\$0.000166$ / share.
  - Net Effective Taker Rate: $+\$0.003166$ / share.
  - Net Effective Maker Rate: $-\$0.001834$ / share.

### 3.2 Canadian UMIR & CIRO Framework
- **Regulatory Rules**: Canadian Investment Regulatory Organization (CIRO) Universal Market Integrity Rules (UMIR) & Order Protection Rule (OPR). Standard TSX continuous board lots (100 shares for stocks $\ge \$1.00$).
- **Exchange Fees**:
  - TSX Continuous Taker Fee: $+\text{C}\$0.0016$ / share.
  - TSX Continuous Maker Rebate: $-\text{C}\$0.0009$ / share.
  - CIRO Regulation Fee: $+\text{C}\$0.000150$ / share.
  - Net Effective Taker Rate: $+\text{C}\$0.001750$ / share.
  - Net Effective Maker Rate: $-\text{C}\$0.000750$ / share.

---

## 4. SUBSYSTEM ARCHITECTURE & DATA FLOW

```
                          ┌────────────────────────────┐
                          │ Real-Time Market Ticks /   │
                          │ Simulated Order Flow (DOM) │
                          └─────────────┬──────────────┘
                                        │
                                        ▼
                          ┌────────────────────────────┐
                          │   MicrostructureEngine     │
                          │  - Lee-Ready Trade Signing │
                          │  - 5-Tier DOM Synthesizer  │
                          │  - OBI & Microprice        │
                          │  - CVD (Session, 1m, 5m)   │
                          │  - Kyle's Lambda (λ)       │
                          └───────┬────────────┬───────┘
                                  │            │
            ┌─────────────────────┘            └────────────────────┐
            ▼                                                       ▼
┌───────────────────────────┐                             ┌────────────────────────────┐
│ MicrostructureStreaming   │                             │   ExecutionAlgoEngine      │
│ Server (WebSocket 8001)   │                             │  - Almgren-Chriss Slicing  │
│  - Bidirectional JSON-RPC │                             │  - Direct Market Sweep     │
│  - SSE (/api/stream/ticks)│                             │  - Passive Limit Model     │
│  - Anti-Fatigue Throttling│                             │  - TWAP / VWAP / POV-10%   │
└───────────┬───────────────┘                             │  - Dual-Market TCA Fees    │
            │                                             └─────────────┬──────────────┘
            │                                                           │
            │                  ┌────────────────────────────────────────┘
            ▼                  ▼
┌────────────────────────────────────────────────────────┐
│ QUANT INTEL™ Synthesizer & Sentinel                   │
│  - Enriched Trade Plan (Strategy, Fill, Slippage bps)  │
│  - Sentinel Predicate 7: LIQUIDITY_VOID_SPIKE          │
│  - Sentinel Predicate 8: MICROSTRUCTURE_SELL_SWEEP     │
│  - Sentinel Predicate 9: EXECUTION_SLIPPAGE_WARNING    │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ Terminal Workstation UI (`web/index.html`)             │
│  - Level 2 Depth of Market (DOM) Ladder                │
│  - Live Time & Sales Tape (Lee-Ready Colors)           │
│  - CVD & Kyle's Lambda Telemetry Gauges                │
│  - Interactive Algorithmic Slicing Timeline Table      │
│  - Drawer Dossier Microstructure Deep-Dive Panel       │
└────────────────────────────────────────────────────────┘
```

---

## 5. STATUTORY IMPERSONAL RESEARCH COMPLIANCE

### 5.1 CSA Staff Notice 31-369 Compliance (Canada)
The microstructure and execution simulation pipeline strictly operates under the **general advice exemption**:
- **Impersonal Quantitative Analytics**: All slippage metrics, order book depths, and algorithmic slicing recommendations are derived systematically from market data.
- **No Fiduciary Order Routing**: The system does not connect to broker order-routing APIs, does not manage funds, and does not execute real-money orders.
- **No Personal Suitability Assessment**: The platform never inquires about a user's net worth, personal risk tolerance, or tax status.

### 5.2 SEC Publisher Exclusion (*Lowe v. SEC*, 472 U.S. 181)
- Quantitative algorithms, transaction cost models, and child order timelines are published regularly as bona fide impersonal decision-support research.
- All outputs are audited automatically by `src/compliance/linter.py` prior to export, prohibiting promissory language or first-person advice verbs.

---

## 6. VERIFICATION, AUDIT & TEST SUITE RESULTS

The Phase 25 implementation was validated with a dedicated multi-tier test suite:

1. **`tests/test_microstructure.py`** (10 tests):
   - Lee-Ready trade signing accuracy across quote and tick rules.
   - OBI mathematical boundary invariants $[-1.0, +1.0]$.
   - Kyle's Lambda price impact coefficient positivity.
   - Bullish absorption divergence and liquidity void sentinels.
   - Dual-market snapshot feed serialization and compliance linter pass.
2. **`tests/test_streaming_server.py`** (6 tests):
   - Client connection registration and `CONNECTION_ACK`.
   - Multi-channel `subscribe` and `unsubscribe` command dispatching.
   - `ping` / `PONG` connection keep-alive heartbeats.
   - Multi-client burst broadcast filtering.
   - Graceful client disconnect handling and resource cleanup.
3. **`tests/test_execution_algo.py`** (10 tests):
   - Direct Market DOM sweep and volume-weighted execution.
   - Limit Passive spread capture and maker fee rebates.
   - TWAP uniform slicing share conservation and monotonic offsets.
   - VWAP U-curve intraday weighting distribution.
   - POV 10% dynamic iceberg participation capping.
   - US Reg NMS vs. Canadian UMIR maker-taker fee divergence.
   - QUANT INTEL trade plan TCA telemetry integration.
   - Sentinel Predicate 9 (`EXECUTION_SLIPPAGE_WARNING`) alert triggering.
   - Compliance linter zero violations across all outputs.

**Total Verified Tests**: **61 passed in 20.06s** across the entire active test suite.
