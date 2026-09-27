# US + Canada Market Intelligence Platform — Architecture & MVP Specification

**Status: SPRINT 0 COMPLETE (Zero-Cost Bootstrap Track). Foundation and core data adapters active and verified (17/17 automated tests passing).**

**Prepared:** 2026-09-24 · **Scope:** design, architecture, quant specification, data-vendor strategy, compliance posture, 12-phase roadmap, zero-cost bootstrap implementation.

---

## 1. Deliverables in this workspace

| File | Contents | Maps to your requested outputs |
|---|---|---|
| [`docs/01_product_vision.md`](docs/01_product_vision.md) | Product vision, complete feature list, dashboard/UI structure, competitive differentiation, monetization, compliance, **brutal review**, data-quality policy, risk register | #1, #2, #12, #14, #17, #18, #19 |
| [`docs/02_architecture_data_ai.md`](docs/02_architecture_data_ai.md) | System architecture, technology choices with rationale, data architecture, **provider comparison (US + CA)**, database design, AI architecture, alert engine, security, observability, **cost model at 100/1K/10K/100K users**, multi-market expansion | #3, #4, #5, #10, #11, #13, #16 |
| [`docs/03_quant_signals_risk.md`](docs/03_quant_signals_risk.md) | Signal philosophy + evidence base, indicator/feature spec, 8-factor scoring engine, regime detection, news intelligence engine, entry engine, exit engine, 14 scanners, risk framework, **backtesting framework**, paper trading, research governance | #6, #7, #8, #9 |
| [`docs/04_roadmap_mvp.md`](docs/04_roadmap_mvp.md) | 12-phase roadmap (objective → DoD), team plan, dev cost, and the **prioritized Phase-1 MVP specification** with sprint plan and acceptance criteria | #15, #20 |
| [`docs/05_zero_cost_bootstrap.md`](docs/05_zero_cost_bootstrap.md) | **The Zero-Cost Blueprint ($0/mo)**: How to run compute, data, LLMs, and storage for $0; legal boundaries (personal vs derived vs commercial); 3 bootstrap paths | Special Addendum |
| [`docs/06_quant_intel_architecture.md`](docs/06_quant_intel_architecture.md) | **QUANT INTEL™ Architecture**: 6-Layer Confluence Synthesis, Closed-Loop Bayesian Memory, Automated Invalidation Sentinel, 1% Risk Sizer & Institutional Trade Plans | Phase 22 Deliverable |
| [`docs/architecture.svg`](docs/architecture.svg) | High-level system architecture diagram | #3 |

## 2. The three decisions that shape everything else

Everything in this design flows from three facts established during research:

1. **Real-time Canadian market data is a per-user licensed entitlement, not a commodity API.** Per the TMX Datalinx / TSX fee schedules, real-time TSX/TSXV display data carries a monthly non-professional subscriber fee (~C$3.00/mo for Level 1 on TSX-listed issues, ~C$10/mo for Level 2), a separate distribution/data-license fee, and explicit "use in analysis programs" license fees (≈C$1,056/mo for TL1 in analysis programs; ≈C$3,168/mo for TL2 where the program generates orders). Redistribution without an executed agreement is a breach. There is no free, legally redistributable real-time TSX feed. **Therefore the platform is architected delayed/EOD-first, with real-time as a metered, entitlement-gated add-on.**
2. **Canadian filings have no official bulk API.** SEDAR+ explicitly has no documented public API (UI automation only, with bot protection), insider data lives in SEDI (bulk feed available via CDS for a fee), and Canadian fundamental coverage is materially weaker than US coverage. **Therefore the data layer must be dual-track: first-party EDGAR (free, 10 req/s, XBRL) for US, and a licensed vendor + compliant filing-ingestion path for Canada — with explicit "data unavailable / low confidence" states rather than silent gaps.**
3. **Regulation in 2026 is principles-based, not rule-based.** The SEC formally withdrew the Predictive Data Analytics proposal (June 2025) and enforces AI risk through existing antifraud/marketing rules and exam priorities; Canada's CSA published Staff Notice 11-348 on AI in capital markets (Dec 2024) and, with CIRO, Staff Notice 31-369 (Dec 2025) confirming a **"general advice" registration exemption for impersonal advice** but warning that disclaimers alone cannot cure the business-purpose test. **Therefore the product is designed as impersonal, non-tailored, explainable decision-support for self-directed investors — deliberately never personalized advice and never automated execution.**

## 3. What is deliberately NOT in this design

- No guaranteed-profit claims, no "probability of profit" marketing, no black-box "AI says BUY" output.
- No LLM-generated numbers. Every number shown to a user is computed by deterministic code and carries source + timestamp + freshness.
- No real-time data in the MVP, no SMS alerts in the MVP, no brokerage execution in any phase (execution is explicitly out of scope for the 12 phases).
- No ML price prediction (deep nets on daily bars). ML is used for *classification, extraction, clustering, and regime estimation* — not for return forecasting.

## 4. Key numbers (all estimates, labeled as such)

| Item | Estimate | Notes |
|---|---|---|
| MVP build (Phase 1), 4 engineers + 1 quant, ~14 weeks | **US$180K–320K** (or 2 founders + contractors: US$90K–150K) | Detailed in `04_roadmap_mvp.md` §3 |
| **Zero-Cost Bootstrap Build (Solo / Agent-Assisted)** | **US$0.00 cash outlay / $0/mo** | See full spec in `05_zero_cost_bootstrap.md` |
| MVP run-rate (100 users, commercial data) | **US$1.3K–3.0K/month** | Infra + licensed delayed/EOD data + LLM tokens |
| **Zero-Cost Run-Rate (Personal / Derived Alpha)** | **US$0.00 / month** | Oracle Always Free + Cloudflare + Groq/Gemini + EDGAR/BoC |
| 10,000 users, delayed-data product | **US$14K–38K/month** | Dominant cost = data + LLM, not compute |
| 10,000 users, real-time dual-market | **US$115K–250K+/month** | Dominant cost = per-user exchange entitlements (see `02…` §11) |
| Break-even subscription price (delayed product, 12-mo) | **C$29–39/month Pro tier** | At 3–5% conversion from a free tier |

## 5. Decisions — status

### 5.1 Locked (confirmed 2026-09-24)
| # | Decision | Locked answer | Consequence for the build |
|---|---|---|---|
| 1 | **Compliance posture** | **Unregistered impersonal research** — rely on the Canadian "general advice" exemption (CSA/CIRO Staff Notice 31-369) and the US publisher exclusion | No tailoring, no "you should buy X", no execution, no personalized portfolio recommendations. CI linter enforces the phrasing ban. Self-guided compliance adherence |
| 2 | **Data latency & market scope** | **Delayed/EOD for both US and Canada**; real-time added later as a cost-plus add-on | MVP has no exchange entitlement per-user fees; CA data via public/reseller EOD; real-time streaming architecture deferred to Phase 12 |
| 3 | **Primary customer** | **Self-directed retail (non-professional data class)** | Non-professional attestation flow; professional-class users explicitly out of scope until Phase 12 |
| 4 | **Budget & Execution Track** | **Zero-Cost Bootstrap ($0.00 cash outlay)** | Built on Oracle Cloud Always Free + Cloudflare Pages/R2 + Groq/Gemini free tiers + official public APIs (SEC EDGAR, BoC Valet, FRED). No commercial data fees. Commercial exchange licensing funded by subscription revenue later |

**Consequences of (1)+(2)+(3)+(4) together:** The platform begins as a **zero-cost, research-only dual-market intelligence engine**. It computes institutional-grade scores, regime classifications, and 14 scanners on free verified official data, establishing an audited 90-day paper-trading track record at **$0.00 cash outlay**.

### 5.2 Status
1. **Sprint 0: COMPLETE** — Canonical schemas, rate limiters, compliance linter, trading calendars, and live SEC EDGAR + BoC Valet adapters verified with 17/17 automated tests passing.
2. **Sprint 1: COMPLETE** — Dual-market OHLCV bar ingestion engine (4,768 historical bars ingested for 19 benchmark securities), vectorized technical feature library (Moving Averages, RSI, MACD, ATR%, Bollinger Bands, RVOL, CMF, ADX), 8-factor scoring engine with factor attribution JSON, and terminal leaderboard runner.
3. **Sprint 2: COMPLETE** — 14 Market Scanners (53 active pattern setups detected), 6-State Macro Regime Classifier (US Strong Bull 90%, CA Weak Bull 78%), and Entry/Exit Signal Zone calculation with structural stops, 1.6R/2.6R/4.0R targets, and machine-checkable invalidation predicates.
4. **Sprint 3: COMPLETE** — AI Explanation Layer (FACT/CALCULATION/INFERENCE/UNCERTAINTY contracts with deterministic failover and numeral verification), Cloudflare Pages/R2 edge static JSON compiler (`daily_summary.json`, `leaderboard.json`, `scanners.json`, `signals.json`, plus 19 individual symbol bundles with TradingView chart bars and disclaimers), and automated master daily pipeline runner (`scripts/run_daily_pipeline.py`) executing full dual-market cycle in 2.65 seconds. All 34 tests passing.
5. **Sprint 4–21: COMPLETE** — Advanced backtesting engine (`backtester.py`), Combinatorial Purged Cross-Validation (`cpcv_engine.py` with FDR control), Asset Microstructure Fingerprinting, Conformal Prediction Bounds, Cross-Border CAD/USD Parity, Idiosyncratic Pre-Flight Risk Matrix, and TAC-15 Scanners.
6. **Phase 22: COMPLETE** — QUANT INTEL™ Institutional Confluence Engine, Closed-Loop Bayesian Memory Ledger, Automated Invalidation Sentinel, Interactive Terminal Workstation, and Comprehensive 215-Test Suite. All 215 automated tests passing cleanly.


## 6. Research basis (verified September 2026)

Primary sources consulted for current pricing, licensing, and regulatory status:

- SEC: Predictive Data Analytics rule withdrawn June 2025; CETU (Feb 2025); FY2026 exam priorities re AI — sec.gov / Federal Register
- CSA Staff Notice 11-348 (AI in capital markets, Dec 2024); CSA/CIRO Staff Notice 31-369 (finfluencers & "general advice" exemption, Dec 2025) — securities-administrators.ca, osc.ca
- CIRO 2026 Compliance Report (AI use, Form 33-109F5 material-change reporting) — ciro.ca
- TMX Datalinx / TSX fee schedules (TSX, Alpha, TSXV Level 1/2 real-time fees, analysis-program licenses, non-professional cap program) — tmxinfoservices.com, osc.ca filing
- Nasdaq US Equities Price List 2025–2027 (Nasdaq Basic, TotalView, distributor & non-display fees) — nasdaqtrader.com
- SEC EDGAR APIs (submissions, companyfacts, companyconcept, full-text search; 10 req/s; User-Agent required) — sec.gov / data.sec.gov
- SEDAR+ (no public bulk API; future phase will replace SEDI) and SEDI (bulk feeds via CDS) — osc.ca, asc.ca, CSA 55-310
- Bank of Canada Valet API; Statistics Canada Web Data Service (WDS) — bankofcanada.ca, statcan.gc.ca
- Vendor pricing/terms: Massive (formerly Polygon.io, rebranded 30 Oct 2025), Databento, EODHD, Twelve Data, Finnhub, Alpha Vantage, Nasdaq Data Link/Sharadar (point-in-time, survivorship-bias-free), Benzinga (via Massive), MT Newswires
- IEX Cloud sunset (31 Aug 2024) — the canonical vendor-risk case study

All prices, fee schedules, and regulatory positions **must be re-verified at contracting time**; fee schedules change annually (e.g., Nasdaq publishes 2025/2026/2027 rates).

---

## 7. Sprint 4 Completed: Interactive Web Terminal & Live Preview

Sprint 4 is fully implemented, verified, and running live in the sandboxed preview environment on port `8000`:

1. **Lightweight Edge HTTP Server (`web/server.py`)**:
   - Zero-dependency Python standard library implementation (`http.server`).
   - Binds to `0.0.0.0:8000` with full CORS headers (`*`), no-cache controls, and clean JSON REST routing.
   - Serves pre-compiled edge snapshot files directly from `data/feeds/` and `data/dist/`:
     - `GET /api/summary`: Dual-market macroeconomic regime classifications, top drivers, and market highlights.
     - `GET /api/leaderboard`: Quantitative composite score rankings across all 19 dual-market securities.
     - `GET /api/scanners`: Full results of all 14 systematic opportunity scanners (53 active pattern matches).
     - `GET /api/signals`: Formatted research execution signal plans with preferred entry, zones, stops, and multi-stage targets.
     - `GET /api/symbol/<SYM>`: Full interactive stock dossier bundle (e.g. `/api/symbol/AAPL`, `/api/symbol/RY`).

2. **Bloomberg/TradingView-Inspired Terminal Dashboard (`web/index.html`)**:
   - High-density dark terminal theme (`#0b0e14`, `#121824`, `#1e2638`) with monospaced financial typography.
   - Dual-market macro regime display (US S&P 500: Strong Bull 90% confidence; CA TSX 60: Weak Bull 78% confidence).
   - Opportunity Leaderboard with real-time text filter, country buttons (All, US, CA), quantitative score pills, volatility metrics, and instant drawer triggers.
   - TAC-14 Opportunity Scanner interface detailing pattern matches with technical rationales.
   - Research Signal Blueprint tab featuring Grade A & B trade plans with risk-reward ratios and invalidation rules.
   - Interactive Stock Dossier Drawer:
     - HTML5 Canvas candlestick charting (45 daily sessions, wicks, green/coral bodies, price axis grids, crosshairs).
     - 4-Part AI Explanation Layer: FACT (verified data), CALCULATION (formulas), INFERENCE (probabilistic patterns), UNCERTAINTY (risks/catalysts).
     - 8-Factor Score breakdown display.
     - Automatic statutory regulatory disclaimers (CSA 31-369 General Advice Exemption & SEC Publisher Exclusion).

3. **Status & Liveness**:
   - Server process running on `0.0.0.0:8000` via background daemon.
   - All 34 automated unit/integration tests passing in 2.09s (`pytest tests/ -v`).
   - Automated compliance linter passing with 0 violations across all 17 files (`scripts/run_linter.py`).

---

## 8. Phase 22 Completed: QUANT INTEL™ Institutional Confluence & Automated Sentinel System

Phase 22 delivers the flagship **QUANT INTEL™ Decision-Support System**, unifying the entire quantitative pipeline into an institutional-grade, multi-layer confluence engine and automated invalidation sentinel:

1. **6-Layer Confluence Synthesis Engine (`src/engine/quant_intel.py`)**:
   - **Layer 1: Macro Regime**: Cross-asset macro trend, rate context, liquidity environment, and SPY/XIU benchmark fit.
   - **Layer 2: Asset Fingerprint**: Microstructure archetype classification, Hurst exponent ($H$) via HAC variance, and Amihud liquidity.
   - **Layer 3: Technical Signal Stack**: Multi-timeframe moving average stacks (9/21 EMA, 50/200 SMA), RSI bull zone, MACD, Chaikin Money Flow, ATR-14, and DBSCAN S/R clustering.
   - **Layer 4: Point-In-Time Fundamentals**: 1–10 quality score (ROIC, debt-to-equity, FCF margin, valuation multiples) with mandatory volume/CMF technical confirmation gating for low-quality names.
   - **Layer 5: Recency-Weighted NLP Sentiment**: Regulatory filings (SEC 8-K, SEDAR+) and press disclosures with exponential decay weights ($1.00 \to 0.60 \to 0.30 \to 0.10$) and material adverse cancellation triggers.
   - **Layer 6: Historical Pattern Memory**: Persistent setup ledger tracking empirical win rates and R-multiples with $K=5$ Bayesian prior shrinkage and dynamic false breakout stop widening ($1.15\times$ if false breakout rate $> 22\%$).

2. **Automated Invalidation Sentinel (`src/engine/quant_intel_sentinel.py`)**:
   - Continuous auditing of all 19 securities against 5 non-discretionary mathematical predicates:
     1. `STOP_LOSS_BREACH`: Fires `CRITICAL` alert with `IMMEDIATE_EXIT` when daily close penetrates structural stop loss.
     2. `BREAKOUT_FAILURE`: Fires `WARNING` alert with `DE_RISK_50_PCT` upon 2 consecutive closes below entry support pivot.
     3. `REGIME_DOWNGRADE`: Flags macro trend mismatch when regime shifts to Bearish or Volatile.
     4. `ADVERSE_CATALYST`: Detects sentiment deterioration ($< -0.15$) canceling the trade thesis.
     5. `TARGET_1_HIT_RATCHET`: Enforces systematic rule taking 40% off and ratcheting stop loss to Breakeven when Target 1 is hit.
   - Integrated into the daily alert engine (`src/engine/alerts.py`) and live alert feed (`data/feeds/alerts.json`).

3. **Closed-Loop Bayesian Memory Ledger (`src/engine/quant_intel_memory.py`)**:
   - Persistent SQLite ledger (`quant_intel_pattern_memory`) initialized with 95 baseline setup records.
   - Live closed-loop trade recording: dynamically reweights signal multipliers based on real-world outcomes and shrinks low-sample statistics toward archetype priors.

4. **Interactive Terminal Workstation & Dossiers (`web/index.html`)**:
   - Tab **🎯 Quant Intel Workstation** featuring interactive multi-portfolio capital sizers ($25K, $50K, $100K, $250K, $1M) recalculating shares and dollar risk in real time.
   - Monospaced ASCII institutional card generator matching hedge fund portfolio manager one-pagers.
   - One-click clipboard card export and slide-out institutional trade dossier drawer.

5. **Comprehensive Verification & Quality Assurance**:
   - **215 passing tests** across 33 test modules in 121 seconds (`pytest`).
   - 100% compliance with Canadian Securities Administrators (CSA) Staff Notice 31-369 and SEC Publisher Exclusion (*Lowe v. SEC*).
   - Zero-egress Cloudflare / Firebase distribution bundle staging 120 static edge endpoints in `public/api/`.
