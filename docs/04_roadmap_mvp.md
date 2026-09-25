# 04 — Development Roadmap (12 Phases) & Phase-1 MVP Specification

> Part of the US + Canada Market Intelligence Platform design set. See [`../README.md`](../README.md).
> **Nothing here is built yet. Phase 1 starts only on your explicit approval.**

---

## 1. ROADMAP OVERVIEW

| Phase | Name | Duration | Incremental cost (US$) | Exit outcome |
|---|---|---|---|---|
| **P1** | MVP — dual-market research workstation | 14 weeks | 180K–320K | Users can research US+CA names with transparent data, EOD/delayed analytics, and honest disclaimers |
| **P2** | Market scanner | 6 weeks | 60K–95K | 14 scanners + filters, daily/intraday (delayed) |
| **P3** | News intelligence | 10 weeks | 110K–180K | Deduped, entity-linked, classified events with price linkage |
| **P4** | Technical engine | 8 weeks | 85K–140K | Full multi-timeframe indicator/feature library + S/R + volume profile |
| **P5** | Fundamental engine | 9 weeks | 95K–160K | PIT fundamentals, ratios, quality/valuation scores, peer comparisons |
| **P6** | AI signal engine | 10 weeks | 120K–200K | 8-factor scoring, entry engine, explanations w/ FACT-CALC-INFERENCE-UNCERTAINTY |
| **P7** | Backtesting | 12 weeks | 140K–230K | Event-driven PIT backtester + anti-overfit suite + published reports |
| **P8** | Paper trading | 6 weeks | 60K–100K | Virtual book, live scoreboard, degradation monitoring |
| **P9** | Portfolio intelligence | 8 weeks | 90K–150K | Holdings, risk contribution, correlation clusters, per-holding verdicts |
| **P10** | Alerts | 7 weeks | 75K–125K | Multi-channel alerts with hygiene, budgets, quiet hours, consent ledger |
| **P11** | Advanced AI | 12 weeks | 150K–260K | Filing deep-read with citations, diffs vs prior period, research agent (quota-bound) |
| **P12** | Production scaling | 10 weeks | 110K–190K | Multi-region, entitlement service, SOC 2 readiness, API/white-label |
| | **Total** | **~26–30 months** | **US$1.28M–2.15M** | (Excludes data-licence fees and cloud run-rate) |

Sequencing note: P3 (news) and P7 (backtesting) are deliberately *after* the MVP so the product can be used and validated internally before spending on expensive feed licensing — but **P7 is not optional and must land before any signal is shown to a paying customer as a "signal"**. Until P7 completes, all outputs are labelled "research prototype — no backtest published".

**Cross-phase gates (apply to every phase):** data-quality suite green · AI eval harness green (if AI touched) · no unlicensed data fields served · disclosure blocks present · perf budget met · security scan clean · rollback tested.

---

## 2. PHASE-BY-PHASE SPECIFICATION

### PHASE 1 — MVP
| Aspect | Specification |
|---|---|
| **Objective** | Prove the core loop: ingest dual-market data reliably → compute transparent analytics → explain them with provenance → let a user research and journal decisions. No signals sold as actionable. |
| **Features** | Auth; watchlists; symbol pages (chart, indicators, fundamentals summary, news headlines, data-lineage badges); daily market overview (US + CA); regime banner (rules-based v0, clearly labelled); position journal with manual P&L; disclaimer & consent framework; admin coverage/data-quality console |
| **Architecture** | L0–L3 + minimal L6–L7 per `02…`: provider adapters → raw zone → DQ gate → Postgres/Timescale → Next.js app + FastAPI |
| **Database** | `security`, `ticker_history`, `bar_1d`, `bar_1h`, `news_event` (headlines only), `corporate_action`, `market_calendar`, `watchlist`, `journal_position`, `data_quality_event`, `consent_record`, `audit_log` |
| **APIs** | US: Massive-class delayed/EOD. CA: licensed reseller EOD/delayed. Fundamentals: EDGAR (`companyfacts`, `submissions`) + vendor for CA. Macro: FRED, BoC Valet, StatCan WDS (all free). Headlines: vendor with commercial terms or filings-only |
| **AI models** | None in the signal path. Optional: small model for headline *labelling only*, off by default |
| **Dev tasks** | (1) Repo, CI/CD, IaC, envs; (2) provider adapter framework + 4 adapters; (3) canonical schema + migrations; (4) DQ gates + lineage; (5) corporate-action engine v1; (6) symbol master + calendars (US/CA); (7) nightly pipeline (Dagster); (8) indicator lib v1 (SMA/EMA/RSI/MACD/ATR/BB/ADX/VWAP); (9) API + caching; (10) frontend IA, symbol page, dashboard; (11) watchlists/journal; (12) provenance UI + disclaimers/consent; (13) admin coverage console |
| **Testing** | Unit (schema, calendars, adjustments), integration (adapter fixtures + recorded responses), DQ suite (reconciliation, missing days, split sanity), E2E (signup→watchlist→symbol→journal), load (1K concurrent reads), accessibility (WCAG AA) |
| **Security** | IdP + MFA; RLS; secrets manager; WAF; dependency scanning; pen-test scope defined (executed pre-public launch) |
| **Cost** | Build 180K–320K; run ~$2.2K/mo at 100 users (see `02…` §8) |
| **Definition of Done** | 500 US + 250 CA names live; ≥ 99% pipeline success over 10 consecutive sessions; 100% of displayed numbers carry provenance; DQ incidents detected before user report; zero unlicensed fields served; legal review of disclaimers complete; kill-criteria metrics instrumented |

### PHASE 2 — Market scanner
| Aspect | Specification |
|---|---|
| **Objective** | Turn the data layer into a daily question-answering machine ("what is setting up today?") with honest filters. |
| **Features** | 14 scanners (`03…` §8) with shared filter language; saved filters; scanner history & outcome tracking; per-scanner published expectancy (once P7 lands); scanner-level regime gating; "why matched" explanations |
| **Architecture** | Adds a scanner service + materialized ranking tables; nightly full-universe compute, intraday incremental (delayed data only) |
| **Database** | `scanner_definition`, `scanner_run`, `scanner_result`, `feature_daily`, `universe_membership` (as-of), `liquidity_profile` |
| **APIs** | Same as P1 + intraday delayed bars for US and CA |
| **AI models** | None (rules only) |
| **Dev tasks** | Universe builder with cap/liquidity bands per market; scanner DSL (YAML → SQL/vectorized Python); ranking materialization; UI table with column presets; filter persistence; "why matched" factor render |
| **Testing** | Golden-set tests per scanner (hand-verified matches), boundary tests (liquidity floors), determinism tests (same input→same output), latency budget (< 400 ms p95 query) |
| **Security** | Rate limits on scanner API; per-tier scan quotas |
| **Cost** | 60K–95K build; +$300–700/mo data |
| **DoD** | All 14 scanners deterministic + explained; no scanner returns > 25 names/page; each scanner's rule documented in-app; DQ floor enforced |

### PHASE 3 — News intelligence
| Aspect | Specification |
|---|---|
| **Objective** | Measure whether news actually predicts anything, and deliver it honestly. |
| **Features** | Dedupe/cluster, entity linking, sentiment/materiality/horizon classification, price-linkage (reaction measurement), priced-in heuristic, event calendar (earnings/dividends/splits), negative-event hard gates |
| **Architecture** | News ingestion workers; embedding + pgvector; OpenSearch for document search; LLM gateway with schema-constrained outputs; eval harness |
| **Database** | `news_event`, `news_security_link`, `news_cluster`, `event_calendar`, `llm_invocation` (prompt/model/version/cost), `eval_result` |
| **APIs** | Licensed newswire (commercial terms) + EDGAR FTS + SEDAR+ via licensed vendor + company IR feeds |
| **AI models** | Small classifier (event type + sentiment + materiality) with calibrated confidence; embeddings (local); no generative text in this phase |
| **Dev tasks** | Ingest connectors; normalization; MinHash + embedding dedupe; entity resolver with dual-listed disambiguation; classification + eval set (≥1,500 labels); price-linkage job; materiality blend; score integration hooks; news UI |
| **Testing** | Dedupe precision/recall on a labelled duplicate set; entity-linking accuracy incl. CA/US collision cases; calibration curves for sentiment; adversarial ambiguity set; **hallucinated-number rate = 0** (numeric verifier) |
| **Security** | Content is data, never instructions; no tools with side effects; prompt/output logging |
| **Cost** | 110K–180K build; +$500–1,500/mo feeds; LLM ~$60–220/mo batched |
| **DoD** | ≥ 95% dedupe precision; ≥ 90% entity-link precision on gold set; published measured expectancy of news-driven signals (even if ≈ 0 — that is a valid, publishable result); negative-event gate live |

### PHASE 4 — Technical engine
Objective: complete the feature/label backbone with multi-timeframe alignment, S/R, volume profile, and a versioned feature store. Adds `sr_levels`, `volume_profile`, `trend_structure`, MTF matrix, feature versioning + backfill tooling. Testing: feature parity tests (research vs production code paths produce identical values), PIT tests, numeric stability on corporate actions. DoD: all features reproducible from raw data; feature store versioned; MTF matrix rendered; no feature reads future data (CI-enforced).

### PHASE 5 — Fundamental engine
Objective: PIT-correct fundamentals for both markets with quality/valuation scoring and honest handling of Canadian data gaps. Adds concept mapping (US-GAAP/IFRS), filing-date gating, peer groups (sector × size), quality metrics (ROIC, accruals, margin trend, leverage trend), valuation percentiles, dividend safety, `N/A` handling with weight renormalization. Testing: restatement tests (a 2019 decision must not see 2024 restated data), cross-source reconciliation, coverage report per market. DoD: ≥ 95% coverage on US core universe, ≥ 80% on CA core, published coverage matrix, zero silent defaults.

### PHASE 6 — AI signal engine
Objective: the 8-factor scoring, entry engine, and the explanation contract. Adds scoring service, confidence tiering with empirical calibration, regime-conditional gating, entry/exit engines, EXPLANATION SERVICE (FACT/CALC/INFERENCE/UNCERTAINTY with numeric verification), signal UI. Testing: score determinism, attribution completeness, numeral-verification gate, advice-language linter, explanation A/B review by humans. DoD: zero unverifiable numerals; 100% of signals have invalidation predicates; confidence tiers empirically ordered in the validation window; disclosure block on every signal.

### PHASE 7 — Backtesting
Objective: earn the right to call anything a "signal". Event-driven engine, PIT + delisted-inclusive universes, cost model, walk-forward + purged K-fold, DSR/PBO, capacity analysis, published report template (`03…` §10.5), strategy registry with pre-registration. **Gate: no user-facing "signal" language until this phase is complete.** DoD: ≥ 3 strategies fully validated; at least one *retired* on evidence; test-set-touch policy enforced in tooling; disclosures auto-attached.

### PHASE 8 — Paper trading
Virtual portfolios, next-bar fill model with spreads/slippage, resolution tracking (MFE/MAE/realized R), public per-strategy scoreboard, degradation monitor, randomized-entry control group. DoD: 90 days of live paper data; degradation analysis published; ≥ 1 strategy promoted to `live` (or honest evidence that none qualify).

### PHASE 9 — Portfolio intelligence
Import (CSV/manual; broker-read later), cost basis, unrealized P/L, weights, correlation clusters, risk contribution, beta (local + cross-market), FX exposure, stress replays, drawdown circuit breaker recommendations, per-holding HOLD/WATCH/REDUCE/EXIT driven by the exit engine. Privacy: portfolio data encrypted, RLS, never used for model training without explicit opt-in. DoD: risk numbers reconcile to independent calculation; stress scenarios documented; no personalized-advice phrasing (CI linter).

### PHASE 10 — Alerts
Alert engine with taxonomy/severity, policy engine (rules, quiet hours, budgets, hysteresis, cooldowns, dedupe), channels (in-app SSE, email, web push, mobile push, SMS opt-in transactional), consent ledger (CASL/TCPA-compliant records), delivery telemetry, opt-out handling within 10 business days, quiet-hours enforcement. DoD: delivery p95 < 60 s (email) / < 5 s (in-app); bounce < 2%; opt-out < 0.5%/mo; consent records complete and auditable; SMS gated by jurisdiction and double opt-in.

### PHASE 11 — Advanced AI
Filing deep-read with citation spans and section diffs (10-K/10-Q/AIF/MD&A/risk factors), guidance extraction, management-change detection, earnings-call transcript analysis (where licensed), on-demand research agent (read-only tools, quota-bound), model cards, prompt regression suite, cost guardrails. DoD: citation-resolution ≥ 99.5%; hallucination rate 0 on the audit sample; per-user cost within quota; model cards published.

### PHASE 12 — Production scaling
Multi-region read replicas, ClickHouse for intraday analytics, entitlement service (per-user data class, audit reporting for exchanges), load testing to 100K users, cost dashboards per tier, SOC 2 Type I readiness, public API + white-label packaging, DR drills, on-call rotation, status page. DoD: 99.9% availability target achieved over 60 days; entitlement audit report generated from production data; DR restore drill < RTO; unit economics per tier within target.

---

## 3. PHASE 1 (MVP) — PRIORITIZED SPECIFICATION

### 3.1 Scope decisions (locked for MVP)
| Decision | Choice | Rationale |
|---|---|---|
| Data latency | **Delayed/EOD only** | Licence cost and legal clarity; multi-day horizons are where the edge can exist |
| Markets | **US + Canada, EOD parity; US-only intraday** | Delivers the dual-market promise without the CA real-time entitlement |
| Universe | 500 US (cap > US$2B, ADV$ > US$20M) + 250 CA (cap > C$500M, ADV$ > C$3M) | Liquidity and data-quality floor |
| Timeframes | Daily + weekly (+1h for US liquid names) | Sufficient for the MVP's research-only positioning |
| Signals | **NONE that claim actionability.** Rule-based "conditions observed" observations, explicitly labelled `research prototype — no backtest published` | Ethical and regulatory hygiene; avoids shipping signals before P7 |
| AI | No generative text in v1; if used, headline labelling only, behind a flag | Hallucination surface must be zero in the MVP |
| Alerts | Email digest + in-app only | Avoid CASL/TCPA complexity until P10 |
| Portfolio | Manual journal (no advice, no verdicts) | Keeps the MVP out of personalized-advice territory |
| Mobile | Responsive web only | No app-store overhead |
| Pricing | Free public beta (waitlist) with a documented path to paid at P6+ | Build the live track record first |

### 3.2 Explicit non-goals for MVP
Backtesting, paper trading, signals with entry/stop/target, news sentiment, pattern recognition, mobile apps, SMS, broker integration, options, crypto, social sentiment, personalized advice, real-time data.

### 3.3 User stories (MoSCoW)
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| S-01 | As a visitor I can create an account and sign in with MFA | Must | Passkey/TOTP supported; email verified; session rotation |
| S-02 | As a user I can search any US/CA listed name and open its symbol page | Must | ≤ 500 ms p95; returns unresolved-ticker message for delisted/unknown; shows venue and currency |
| S-03 | As a user I can see a price chart with indicators | Must | Chart loads < 1.5 s; indicators match the canonical library within 1e-9; adjusted/unadjusted toggle |
| S-04 | As a user I can see every number's source and freshness in one click | Must | Provenance popover on 100% of numeric fields; staleness badge thresholds enforced |
| S-05 | As a user I can see a fundamentals summary that says `N/A — data unavailable` rather than a blank or zero | Must | CA coverage gaps render explicitly; no silent defaults anywhere |
| S-06 | As a user I can build watchlists and see them sorted by day change and RVOL | Must | Persisted; ≤ 2 lists on free tier; sort stable |
| S-07 | As a user I can read today's headlines for a name (deduped, source-linked) | Should | Dedupe applied; links to primary source; publication timestamps shown |
| S-08 | As a user I can journal a position manually and see P/L | Should | Currency-aware; corporate actions applied; no advice language |
| S-09 | As a user I can see market overview tiles for US and CA | Must | Index, breadth, VIX, yields, FX, oil; each with timestamps |
| S-10 | As a user I can see a regime banner with its contributing inputs | Should | v0 rules-based, labelled "v0 prototype"; shows 3 drivers |
| S-11 | As an admin I can see feed health, coverage, and DQ incidents | Must | Freshness per feed; missing-day report; incident log |
| S-12 | As an operator I can replay a pipeline date to fix an error | Must | Idempotent re-run; versioned output; no overwrite of raw |
| S-13 | As a user I can export my watchlist and journal | Should | CSV; includes data timestamps |
| S-14 | As a user I see clear, versioned disclaimers and consent controls | Must | Disclaimer version stored per user; consent ledger entries on signup |

### 3.4 Sprint plan (14 weeks, 7 × 2-week sprints)
| Sprint | Weeks | Deliverable | Demo |
|---|---|---|---|
| **S0** | 0 (2 weeks pre-work) | Vendor contracts/trials, entitlement terms confirmed, legal review of disclaimers, IaC skeleton, repo/CI | Signed data agreements or fallback plan |
| **S1** | 1–2 | Canonical schema, migrations, symbol master, calendars, one US adapter end-to-end, raw zone + lineage | One symbol's EOD data traceable from provider payload to API |
| **S2** | 3–4 | CA adapter, DQ gates, corporate-action engine v1, nightly orchestration, indicator lib v1 | Split-adjusted CA symbol renders correctly; DQ failure quarantines and alerts |
| **S3** | 5–6 | Auth, watchlists, symbol page v1, provenance UI, chart | Full user journey: signup → search → chart → provenance popover |
| **S4** | 7–8 | Fundamentals (EDGAR + CA vendor), `N/A` handling, market overview tiles, coverage console | US + CA fundamentals side by side with explicit gap rendering |
| **S5** | 9–10 | Headlines (dedup), journal v1, disclaimers/consent, admin DQ console, perf tuning | Deduped news rail; journal P/L; consent ledger visible |
| **S6** | 11–12 | Hardening: load test, accessibility, security scan, DR drill, observability, kill-criteria instrumentation | Load test at 5× expected concurrency; restore drill |
| **S7** | 13–14 | Private beta (25–50 users), bug triage, DQ burn-in, documentation, beta metrics | 10 consecutive clean sessions; beta feedback report |

### 3.5 Team (MVP)
| Role | FTE | Focus |
|---|---|---|
| Full-stack engineer (lead) | 1.0 | Next.js app, API, auth, DX |
| Data/platform engineer | 1.0 | Adapters, pipelines, DQ, orchestration, infra |
| Backend engineer | 1.0 | Domain services, schema, performance |
| Quant/data-science (part-time → full by P4) | 0.5 | Indicator library correctness, universe construction, regime v0 |
| Product designer (contract) | 0.3 | IA, dense-data UX, accessibility |
| Founder/PM + compliance owner | 1.0 | Scope, vendor negotiation, legal review, user research |
| **Total** | **~4.8 FTE** | Part-time QA/security consultants as needed |

### 3.6 Tech decisions locked for MVP
Next.js + TypeScript + Tailwind + shadcn/ui · TradingView Lightweight Charts · FastAPI + Pydantic · PostgreSQL 16 + TimescaleDB + pgvector · Redis · S3 + Parquet (Iceberg) · Dagster + dbt · AWS `ca-central-1` (ECS Fargate, RDS Multi-AZ, S3, ElastiCache) · Cloudflare edge · Terraform + GitHub Actions · Grafana Cloud + Sentry · Stripe (later phases) · Clerk/Auth0.

### 3.7 Cost (MVP)
| Line | Amount (US$) |
|---|---|
| Engineering (4 FTE × 14 weeks, blended) | 145K–250K |
| Quant (0.5 FTE × 14 weeks) | 25K–45K |
| Design (contract) | 12K–25K |
| Infra + data + LLM (14 weeks) | 8K–18K |
| Legal (data agreements review, disclaimers, privacy policy, counsel opinion letter on Canadian "general advice" posture) | 15K–40K |
| Contingency (15%) | 30K–55K |
| **Total MVP** | **US$235K–433K** (contractor-heavy variant: 120K–200K) |

### 3.8 MVP risks specific to the build
| Risk | Mitigation |
|---|---|
| Canadian data licence negotiations stall | Start S0 immediately; fallback = EOD-only CA via reseller + filings-only coverage, with explicit coverage disclosure |
| Corporate-action edge cases corrupt history | Two-source reconciliation + hard sanity gates + manual review queue |
| Scope creep toward signals | Non-goals document is contractual; signal work requires P7 |
| LLM temptation creeps into MVP | No generative text in MVP code paths; flag-gated and eval-harness-gated |
| Beta users interpret research output as advice | Labelling discipline, onboarding copy, in-product scope statement, support scripts |

### 3.9 Definition of Done for Phase 1 (acceptance)
1. 750 symbols live (500 US + 250 CA) with ≥ 99% pipeline success over 10 consecutive sessions.
2. 100% of displayed numeric fields carry provenance; staleness badges functioning.
3. `N/A` semantics correct on every gap; no silent defaults (verified by test suite).
4. p95 symbol page ≤ 500 ms; p95 dashboard ≤ 800 ms at 5× expected concurrency.
5. Zero unlicensed data fields reachable (verified by entitlement middleware tests).
6. Disclaimers + consent ledger versioned and stored; advice-language CI linter passing.
7. DQ incidents detected before user reports in ≥ 95% of injected-fault tests.
8. Restore drill completed within RTO; rollback verified.
9. Documentation: architecture, runbooks, data-coverage matrix, provider-contract register, kill-criteria dashboard.
10. Private beta with 25–50 users completed, with a written findings report and a go/no-go recommendation for Phase 2.

---

## 4. POST-MVP DECISION POINTS

| After | Decision | Inputs |
|---|---|---|
| P3 | Is news measurably additive after costs? | Measured expectancy, IC of news component |
| P6 | Do confidence tiers hold empirically? | Calibration report |
| P7 | Does *any* strategy survive honest testing? | DSR/PBO, live-paper plan |
| P8 | Is paper expectancy within 60% of backtest? | Scoreboard + degradation monitor |
| P9 | Do users value risk tooling enough to pay? | Conversion + interview data |
| P10 | Which alert channels earn their compliance cost? | Engagement vs opt-out rates |
| P12 | Does the real-time tier clear a 45% gross margin? | Entitlement cost per cohort vs ARPU |

---

## 5. IMMEDIATE NEXT STEPS ON APPROVAL (Sprint 0 checklist — Enterprise Path)

1. **Vendor outreach (week 1):** Massive (US delayed→real-time path), a QuoteMedia-class Canadian reseller, a PIT/delisted fundamentals provider, a licensed newswire with Canadian coverage. Request redistribution, pro/non-pro, archive, and derived-data terms in writing.
2. **Legal (weeks 1–3):** Canadian counsel opinion on the "general advice" exemption and business-purpose test for this specific product; US counsel on publisher exclusion and Marketing Rule implications for backtest display; privacy policy (PIPEDA + Law 25); disclaimers v1.
3. **Entitlement model (week 2):** implement `dataset → entitlement_class → field` matrix with non-professional attestation flow.
4. **Kill-criteria instrumentation (week 2):** define the metrics/dashboards now so they exist before claims do.
5. **Sprint 1 kickoff:** canonical schema + first adapter end-to-end.

---

## 6. THE ZERO-COST BOOTSTRAP EXECUTION PLAN ($0 Cash Outlay)

For bootstrapping without outside capital or expensive data licenses, the roadmap adjusts to an **Alpha-First / Zero-Cost Track** (detailed fully in [`05_zero_cost_bootstrap.md`](05_zero_cost_bootstrap.md)):

### Sprint 0: Zero-Dollar Foundations (Week 1–2)
1. **Infrastructure Provisioning ($0):**
   - Provision Oracle Cloud Always Free ARM Ampere A1 (4 OCPU, 24 GB RAM, 200 GB NVMe, Ubuntu 24.04).
   - Alternatively: configure local Docker Compose environment (PostgreSQL 16 + TimescaleDB + Redis 7 + FastAPI).
   - Set up Cloudflare Pages project linked to private GitHub repository.
2. **API Keys & Official Ingestion Registration ($0):**
   - Register FRED API key (free, instant).
   - Register Groq Free Tier (30 RPM, 1,000 RPD) + Google AI Studio Gemini API key.
   - Configure SEC EDGAR compliant User-Agent (`AppName email@domain.com`).
   - Verify live connectivity to Bank of Canada Valet API (no key required).
3. **Compliance by Design ($0):**
   - Implement the impersonal advice linter in pre-commit hooks (bans "buy this", "recommended allocation", "you should").
   - Configure non-professional user declaration and statutory research disclaimers under CSA Staff Notice 31-369 and SEC publisher exclusion.
4. **Canonical Data Architecture Setup:**
   - Execute DDL migrations for `security`, `bar_1d`, `macro_observation`, `fundamental_fact`, and `data_quality_event`.

### Sprints 1–3: The Core Dual-Market Engine (Weeks 3–8)
- **Data Ingestion:**
  - Build SEC EDGAR XBRL parser for 500 US large/mid caps.
  - Build Bank of Canada Valet & FRED macro daily sync.
  - Build Canadian corporate news RSS ingestion (Newsfile, CNW Cision, GlobeNewswire).
  - Ingest 750 EOD bars (US + TSX/TSXV) via local pipeline.
- **Quant Analytics & Regime Engine:**
  - Vectorized technical indicators (RSI, VWAP, ATR, RVOL, S-R levels).
  - 6-state HSMM Regime Classifier (BoC/Fed yield spreads, credit spreads, RVOL).
  - 8-Factor scoring engine (0–100 composite score).
  - 14 Opportunity Scanners.

### Sprints 4–5: AI Explanation & Paper Scoreboard (Weeks 9–12)
- Groq/Gemini schema-constrained reasoning pipeline (FACT / CALCULATION / INFERENCE / UNCERTAINTY).
- Append-only Paper Trading execution simulator with realistic commission and slippage models.
- Daily static intelligence exporter to Cloudflare R2 / D1.
- Cloudflare Pages responsive frontend with TradingView lightweight charts.

### Acceptance & Monetization Trigger
- The engine operates unattended for 90 days at **$0.00 cash outlay**.
- Live paper trading track record is proven out-of-sample.
- Commercial data vendor contracts (TMX, QuoteMedia, Massive) are triggered **only when paying users or external capital fund them**.

