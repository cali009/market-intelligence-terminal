# 02 — System Architecture, Data Architecture, AI Architecture, Providers, Security & Cost Model

> Part of the US + Canada Market Intelligence Platform design set. See [`../README.md`](../README.md).
> **All prices/limits are as published in September 2026 sources and must be re-verified at contracting time.**

---

## 1. SYSTEM ARCHITECTURE

### 1.1 Architectural principles
1. **Separate the data plane from the decision plane.** Market data ingestion is a high-throughput, low-intelligence pipeline. Signal generation is a low-throughput, high-intelligence pipeline. Different scaling profiles, different failure modes.
2. **Batch-first, streaming-optional.** Nightly full-universe computation + intraday incremental refresh. This is cheap, reproducible, and sufficient for multi-day horizons. Streaming is added only where a licence and a business case exist.
3. **Compute once, serve many.** Market-wide analysis (regime, sector rotation, breadth, universe screening) is computed once per cycle and shared across all tenants. Only portfolio-specific and user-specific work is per-tenant. This is what makes a free tier economically possible.
4. **Every derived artefact is versioned and reproducible.** `(strategy_version, feature_version, dataset_version, universe_version)` → deterministic output. Required for audit and for honest backtest-vs-live comparison.
5. **Licence-aware by design.** Every data field carries an entitlement class; the API layer enforces it. Compliance is a middleware concern, not a UI afterthought.
6. **Provider-agnostic core.** Canonical schemas; providers are adapters. Replacing a vendor must not require touching the quant engine (IEX Cloud lesson).

### 1.2 Logical layers

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ L8  CLIENTS         Web app (Next.js) · Push/Email/SMS alert delivery · Public API      │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L7  EDGE / BFF      CDN · WAF · AuthN/AuthZ · Rate limit · Entitlement gate · Caching   │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L6  APPLICATION     API (FastAPI) · Portfolio svc · Alert svc · Scanner svc · Billing   │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L5  DECISION        Scoring engine · Entry/Exit engine · Regime · News intelligence ·   │
│                     Risk engine · Explanation service (LLM w/ guardrails)               │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L4  RESEARCH/QUANT  Feature store · Indicator lib · Backtest engine · Paper trading ·   │
│                     Strategy registry · Model/eval registry                              │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L3  STORAGE         Postgres (OLTP) · TimescaleDB (bars) · pgvector (embeddings) ·      │
│                     OpenSearch (documents) · Object store + Iceberg (raw/curated) ·     │
│                     Redis (cache/queue)                                                 │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L2  PROCESSING      Orchestrator (Dagster) · dbt transforms · DQ gates · Corporate      │
│                     actions · Symbol master · Calendars · Event bus (NATS/Redpanda)     │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L1  INGESTION       Provider adapters (price/fundamentals/news/filings/macro) ·         │
│                     Filing connectors (EDGAR, SEDAR+ licensed) · Retry/backoff/ratelimit│
├───────────────────────────────────────────────────────────────────────────────────────┤
│ L0  SOURCES         Exchange & vendor feeds · SEC EDGAR · SEDAR+/SEDI · Newswires ·     │
│                     Central banks & statistical agencies · Vendor fundamentals          │
└───────────────────────────────────────────────────────────────────────────────────────┘
       Cross-cutting: Security · Observability · Lineage/Governance · Compliance · Cost control
```

See [`architecture.svg`](architecture.svg) for the rendered diagram.

### 1.3 Runtime topology (MVP → scale)
| Stage | Topology | Why |
|---|---|---|
| **MVP (0–1K users)** | Single AWS region `ca-central-1`; ECS Fargate services (web, api, worker, scheduler); RDS Postgres Multi-AZ; S3; ElastiCache Redis; SQS; Cloudflare in front | Lowest ops burden; Canadian data residency by default; no Kubernetes tax on a 4-person team |
| **Growth (1K–10K)** | Add read replicas, Timescale compression/continuous aggregates, split worker pools (ingest vs compute), Redpanda event bus, dedicated research environment, second region (`us-east-1`) for US-latency-sensitive reads | Read/write split is the first real bottleneck; research isolation prevents noisy-neighbour backtests |
| **Scale (10K–100K)** | Multi-region active/read, ClickHouse for high-volume intraday analytics, sharded ingestion by venue, autoscaled compute pools, per-tenant caching tiers, entitlement service as a first-class service | Data volume and entitlement enforcement dominate; Postgres alone stops being comfortable for tick-level retention |

**Degraded mode (must be designed, not hoped for):** if a provider fails → serve last-known-good EOD data with a prominent staleness banner; disable intraday scanners; keep portfolio diagnostics and historical analytics running. Never fail open with stale data presented as current.

### 1.4 Technology recommendations and why

| Layer | Recommendation | Why (and what I'd avoid) |
|---|---|---|
| Frontend | **Next.js (App Router) + TypeScript**, Tailwind, shadcn/ui, TanStack Query/Table, Zustand | SSR/RSC for fast first paint on dense dashboards; best-in-class charting ecosystem; huge hiring pool. *Avoid* heavy SPA frameworks for a data-dense, SEO-relevant research product |
| Charts | **TradingView Lightweight Charts** (Apache-2.0) for price/candles; **ECharts** for heatmaps/quadrants | Lightweight Charts is free and performs well; avoids building a chart engine. *Avoid* drawing proprietary exchange data into a chart library that requires its own data licence assumptions — you display *your* licensed data |
| API | **FastAPI (Python) + Pydantic** for the domain/compute API; Next.js route handlers as BFF for user-facing aggregation | Python keeps the quant/ML stack in one language; Pydantic gives typed contracts and OpenAPI generation. *Avoid* a Python monolith serving HTML |
| Streaming to client | **SSE** for alert/status streams; WebSocket only for licensed real-time quotes (paid tier) | SSE is cheap, proxy-friendly, and sufficient for alerts. WebSocket fanout is where costs explode |
| Orchestration | **Dagster** | Software-defined assets map perfectly to "dataset → asset → downstream score", with lineage and backfills as first-class concepts. *Avoid* cron + shell scripts at any scale; Airflow is heavier than needed |
| Transforms | **dbt** for SQL models; Python for feature computation | Version-controlled, testable transformations; DQ tests live next to the models |
| Time-series DB | **TimescaleDB** (Postgres extension) | One operational database; hypertables, compression (10–20×), continuous aggregates for 1h/1d rollups, full SQL joins with relational data. *Avoid* InfluxDB (weak joins, weaker SQL) for a system that constantly joins bars to fundamentals |
| High-volume analytics (scale) | **ClickHouse** | Columnar, extremely fast for cross-sectional scans and intraday analytics at billions of rows |
| Research/backtest store | **DuckDB + Parquet on S3 (Iceberg table format)** | Zero-server columnar analytics over the full PIT history; Iceberg gives snapshot/version semantics so a backtest can pin a dataset version |
| OLTP | **PostgreSQL 16+** with Row-Level Security | Multi-tenant isolation without a separate tenant DB per customer; RLS is auditable |
| Vectors | **pgvector** (H7/HNSW) | Dedupe + similarity search for news without another datastore; migrate to a dedicated vector DB only if needed |
| Document search | **OpenSearch** (or Postgres FTS early) | Filing/news full-text with highlighting for citation spans |
| Cache/queue | **Redis** (cache, rate limits, locks) + **SQS/NATS** early → **Redpanda** at scale | Right-sized for each stage; Redpanda gives Kafka API without Zookeeper ops |
| ML/AI serving | Python + **LiteLLM gateway** (multi-provider routing), **ONNX Runtime** for small local models | Avoids vendor lock-in; routes cheap tasks to cheap models; pins model versions for auditability |
| Embeddings | Local open-weight embedding model served on CPU/GPU (batch) | Cost control: embeddings for millions of documents must not be per-call API priced |
| Auth | **Managed IdP (Clerk/Auth0) + passkeys/TOTP**, SAML/OIDC for enterprise later (WorkOS) | Do not hand-roll auth; passkey support is now an expectation; enterprise SSO is a checkbox for the Professional tier |
| Billing | **Stripe** (+ Stripe Tax for GST/HST and US sales tax) | Handles the tax nexus complexity you do not want to build |
| IaC / CI-CD | **Terraform + GitHub Actions**; container images to ECR; migrations via Alembic | Reproducible envs; migrations must be reviewable and reversible |
| Observability | **OpenTelemetry → Grafana Cloud** (metrics/logs/traces), **Sentry** for app errors, **PagerDuty** for pages | One vendor for metrics+logs+trace reduces cost and context-switching |
| Secrets | **AWS Secrets Manager + KMS**; short-lived credentials; no secrets in env files in prod | Rotation and audit are table stakes |
| Email/Push/SMS | **SES or Postmark** (email), **FCM/APNs** via web/mobile push, **Twilio** (SMS, transactional only) | Deliverability and compliance controls matter more than price here |

---

## 2. DATA ARCHITECTURE

### 2.1 Medallion zones
| Zone | Contents | Storage | Mutability |
|---|---|---|---|
| **Raw (Bronze)** | Byte-exact provider responses, filing documents, news payloads, with fetch metadata | S3, partitioned by `source/dt`, Parquet/JSONL | **Immutable** (append-only; corrections are new objects) |
| **Normalized (Silver)** | Canonical schemas, adjusted prices, PIT fundamentals, entity-resolved news | TimescaleDB + Postgres | Upsert with `knowledge_at` versioning |
| **Curated (Gold)** | Features, indicators, scores, signals, regime, sector aggregates, backtest results | Postgres (+ Iceberg snapshots) | Recomputed per cycle, versioned |
| **Serving** | Materialized views, precomputed rankings, cached payloads | Redis + Postgres MVs | TTL-managed |

### 2.2 Canonical schema highlights (design-level DDL)

```sql
-- Securities master: never key analytics on ticker
CREATE TABLE security (
  security_id      BIGSERIAL PRIMARY KEY,
  figi             TEXT UNIQUE,               -- or vendor-stable composite id
  isin             TEXT,
  name             TEXT NOT NULL,
  country          CHAR(2) NOT NULL,          -- US | CA | ...
  primary_venue    TEXT NOT NULL,             -- XNYS | XNAS | ARCX | XTSE | XTSV | CNSX
  asset_class      TEXT NOT NULL,             -- EQUITY | ETF | REIT | INDEX
  currency         CHAR(3) NOT NULL,
  status           TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | DELISTED | SUSPENDED
  delist_date      DATE,
  delist_reason    TEXT,
  sector_gics      TEXT, industry_gics TEXT,
  first_trade_date DATE, is_dual_listed BOOLEAN DEFAULT FALSE,
  paired_security_id BIGINT REFERENCES security(security_id)  -- TSX<->US listing link
);

CREATE TABLE ticker_history (
  security_id BIGINT REFERENCES security(security_id),
  ticker TEXT NOT NULL, venue TEXT NOT NULL,
  valid_from DATE NOT NULL, valid_to DATE,          -- NULL = current
  PRIMARY KEY (security_id, ticker, venue, valid_from)
);

-- Bitemporal bars: what we knew, when
CREATE TABLE bar_1d (
  security_id BIGINT NOT NULL,
  trade_date  DATE NOT NULL,
  open NUMERIC(18,6), high NUMERIC(18,6), low NUMERIC(18,6), close NUMERIC(18,6),
  volume BIGINT, vwap NUMERIC(18,6), trades INT,
  is_adjusted BOOLEAN NOT NULL DEFAULT FALSE,
  knowledge_at TIMESTAMPTZ NOT NULL,    -- when this version became known
  retrieved_at TIMESTAMPTZ NOT NULL,
  source_provider TEXT NOT NULL,
  quality_status TEXT NOT NULL,         -- OK | SUSPECT | QUARANTINED
  PRIMARY KEY (security_id, trade_date, is_adjusted, knowledge_at)
);  -- Timescale hypertable, partitioned on trade_date, compressed after 30d

CREATE TABLE fundamental_pit (
  security_id BIGINT NOT NULL,
  concept     TEXT NOT NULL,            -- canonical: revenue_ttm, eps_diluted_q, ...
  period_end  DATE NOT NULL,            -- fiscal period end
  period_type TEXT NOT NULL,            -- Q | Y | TTM
  value       NUMERIC(24,6),
  unit        TEXT, currency CHAR(3),
  filing_date DATE NOT NULL,            -- knowledge gate (NEVER use period_end for joins)
  accession   TEXT, source_provider TEXT NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (security_id, concept, period_end, period_type, filing_date)
);

CREATE TABLE news_event (
  news_id      BIGSERIAL PRIMARY KEY,
  cluster_id   BIGINT,                  -- dedupe group
  published_at TIMESTAMPTZ NOT NULL,
  ingested_at  TIMESTAMPTZ NOT NULL,
  source_name  TEXT NOT NULL, source_url TEXT, is_primary_source BOOLEAN,
  headline     TEXT NOT NULL, body_ref TEXT,          -- S3 pointer, never blob in OLTP
  category     TEXT,                    -- EARNINGS | M&A | GUIDANCE | LEGAL | ...
  sentiment    TEXT,                    -- POSITIVE | NEGATIVE | NEUTRAL | UNCERTAIN
  materiality  SMALLINT,                -- 1..5
  horizon      TEXT,                    -- IMMEDIATE | SHORT | MEDIUM | LONG
  novelty      SMALLINT,                -- 1..5 (dedupe/clustering output)
  extraction_confidence NUMERIC(4,3),
  model_version TEXT, prompt_version TEXT,
  CONSTRAINT body_ref_required CHECK (body_ref IS NOT NULL)
);

CREATE TABLE news_security_link (
  news_id BIGINT REFERENCES news_event(news_id),
  security_id BIGINT REFERENCES security(security_id),
  link_confidence NUMERIC(4,3), is_primary BOOLEAN, span_start INT, span_end INT,
  PRIMARY KEY (news_id, security_id)
);

CREATE TABLE score_snapshot (
  security_id BIGINT NOT NULL, as_of TIMESTAMPTZ NOT NULL,
  technical SMALLINT, fundamental SMALLINT, momentum SMALLINT, news SMALLINT,
  regime SMALLINT, sector_strength SMALLINT, valuation SMALLINT, risk SMALLINT,   -- risk = penalty input
  opportunity SMALLINT, confidence_tier CHAR(1),
  gates_failed TEXT[], factor_attribution JSONB NOT NULL,
  feature_version TEXT NOT NULL, model_version TEXT, data_quality_floor TEXT,
  PRIMARY KEY (security_id, as_of)
);

CREATE TABLE signal (
  signal_id BIGSERIAL PRIMARY KEY,
  security_id BIGINT, strategy_id TEXT, strategy_version TEXT,
  generated_at TIMESTAMPTZ NOT NULL, direction TEXT,           -- LONG | EXIT | NONE
  entry_zone_low NUMERIC, entry_zone_high NUMERIC, preferred_entry NUMERIC,
  alt_entry NUMERIC, stop NUMERIC, t1 NUMERIC, t2 NUMERIC, t3 NUMERIC,
  rr_ratio NUMERIC(6,2), holding_period_bucket TEXT,
  strength SMALLINT, confidence_tier CHAR(1),
  rationale JSONB NOT NULL,                    -- FACT/CALC/INFERENCE/UNCERTAINTY blocks
  invalidation JSONB NOT NULL,                 -- machine-checkable predicates (required)
  risk_notes JSONB, data_lineage JSONB,
  disclaimer_version TEXT NOT NULL,
  CONSTRAINT invalidation_required CHECK (jsonb_array_length(invalidation) > 0)
);

CREATE TABLE signal_outcome (         -- the live scoreboard
  signal_id BIGINT PRIMARY KEY REFERENCES signal(signal_id),
  resolved_at TIMESTAMPTZ, outcome TEXT,   -- T1 | T2 | T3 | STOP | EXPIRED | INVALIDATED
  mfe NUMERIC, mae NUMERIC, realized_r NUMERIC, days_held INT,
  max_drawdown_within NUMERIC, notes TEXT
);

-- Compliance: consent + entitlements
CREATE TABLE consent_record (
  user_id UUID, channel TEXT,                       -- EMAIL | SMS | PUSH
  message_class TEXT,                               -- TRANSACTIONAL | MARKETING
  consent_type TEXT,                                -- EXPRESS | IMPLIED
  source_url TEXT, ip INET, locale TEXT,
  granted_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ,
  evidence JSONB, PRIMARY KEY (user_id, channel, message_class, granted_at)
);

CREATE TABLE data_entitlement (
  user_id UUID, dataset TEXT,                       -- TSX_L1_REALTIME | NASDAQ_BASIC | ...
  subscriber_class TEXT NOT NULL,                   -- NON_PROFESSIONAL | PROFESSIONAL
  valid_from DATE, valid_to DATE,
  verified_at TIMESTAMPTZ, verification_method TEXT,  -- declaration | docs | vendor call
  PRIMARY KEY (user_id, dataset, valid_from)
);
```

**Key design decisions**
- **Bitemporal everything that can be restated** (bars, fundamentals, corporate actions, scores) via `knowledge_at`. This is what makes look-ahead bugs detectable rather than catastrophic.
- **`filing_date` is the knowledge gate** for fundamentals; CI test asserts no join uses `period_end` alone.
- **Bodies/documents never live in OLTP** — S3 pointers only.
- **Signals are immutable**; a revision is a new row. Outcomes attach to the original.
- **Risk is stored separately from the opportunity score** and is used as a gate/penalty, not a component.

### 2.3 Ingestion & processing pipeline
```
Provider adapters (one per source, typed, with rate-limit + retry + circuit breaker)
   └─▶ Raw landing (S3, immutable, checksummed)
        └─▶ Validation & DQ gates
             ├─ schema/DQ pass ─▶ Normalization (canonical concepts, currency, units)
             │                        ├─▶ Corporate-action engine (splits, dividends, spinoffs)
             │                        ├─▶ Symbol master & entity resolution
             │                        └─▶ PIT store (bitemporal upsert)
             │                                 └─▶ Feature computation (versioned) ─▶ Feature store
             │                                          └─▶ Scoring / Regime / Signals
             │                                                   └─▶ Alerts + API cache
             └─ DQ fail ─▶ Quarantine + ops alert + (where permitted) secondary-source fill
```

**Corporate-action engine (critical correctness component)**

| Concern | Handling |
|---|---|
| Splits | Adjust history multiplicatively; store both raw and adjusted; never overwrite raw |
| Dividends | Adjusted-close series for indicators; unadjusted for income/yield computation |
| Spinoffs | Treat as a return-of-capital event; requires vendor action data; flag names where mapping is uncertain |
| Symbol/ticker changes | Resolve via `security_id` + `ticker_history`; re-point watchlists |
| Delistings | Never delete; mark `DELISTED` with date + reason; assign delisting return assumption *by reason* for backtests (documented: M&A ≈ deal price; bankruptcy ≈ −80…−100%; index deletion ≈ market return) |
| FX for cross-listed | Daily BoC reference rates; store both local and CAD-normalized series for Canadian-investor reporting |

**Market calendar service & Dual-Market Holiday Asymmetry (Critical Real-World Edge Case)**:
US and Canadian markets do NOT share the same holiday calendar. There are 7–8 sessions per year where one market is open and the other is closed:
- **US Closed, TSX Open:** Martin Luther King Jr. Day (Jan), Washington's Birthday / Presidents' Day (Feb), Juneteenth (June 19), US Thanksgiving Thursday (Nov).
- **TSX Closed, US Open:** Victoria Day (Canada, late May; usually different from US Memorial Day), Canada Day (July 1; when July 4 is US), Civic Holiday (first Monday in August), National Day for Truth and Reconciliation (banks/fixed income), Canadian Thanksgiving (second Monday in October), Boxing Day (Dec 26).
- **Operational Rules:**
  1. Pipeline execution is partitioned by `calendar_id` (`XNYS` vs `XTSE`). The ingestion worker for an exchange only runs if that exchange's calendar is in `OPEN` status.
  2. Cross-market correlation features (e.g., TSX stock correlation to S&P 500, or CAD/USD beta) **forward-fill the closed venue's prior close** and flag `cross_market_sync: partial`. They NEVER trigger a missing-data DQ failure.
  3. Half-day early closes (e.g., Black Friday in US closing at 13:00 ET; Christmas Eve) adjust intraday volume profile expectations (e.g., RVOL benchmark is scaled by historical half-day volume curves).

**Canonical XBRL Concept Fallback Chains (SEC EDGAR Engine)**:
Because US companies file under varying US-GAAP taxonomies across industries, the ingestion parser uses deterministic fallback priority chains rather than a single hardcoded tag:
- **Revenue:**
  1. `RevenueFromContractWithCustomerExcludingAssessedTax` (standard tech, industrial, retail)
  2. `Revenues` (broad general tag)
  3. `SalesRevenueNet` (manufacturing, consumer products)
  4. `InterestAndDividendIncomeOperating` (commercial banks, JPM/BAC)
  5. `OperatingRevenueUnrealizedGainLossOnDerivativeInstruments` (energy utilities)
- **Net Income:**
  1. `NetIncomeLoss` (standard)
  2. `ProfitLoss` (broad IFRS/foreign private issuers)
  3. `NetIncomeLossAvailableToCommonStockholdersBasic`
- **Operating Cash Flow:**
  1. `NetCashProvidedByUsedInOperatingActivities`
  2. `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations`
- **Diluted Shares Outstanding:**
  1. `WeightedAverageNumberOfDilutedSharesOutstanding`
  2. `CommonStockSharesOutstanding`
- **Total Debt:**
  1. `LongTermDebtNoncurrent` + `ShortTermBorrowings`
  2. `DebtCurrent` + `LongTermDebtNoncurrent`

**Cross-Listed Security Architecture (US + Canada)**:
For dual-listed names (e.g., Shopify: `SHOP` on NYSE in USD, `SHOP.TO` on TSX in CAD):
1. `security` table links them via `paired_security_id`.
2. Both maintain independent raw OHLCV bars in their local trading currency (`USD` vs `CAD`).
3. Technical indicators (RSI, Moving Averages, Bollinger Bands) are computed on the local series (dimensionless).
4. Relative Volume (RVOL) is venue-specific (Canadian bank holiday does not suppress US RVOL).
5. Fundamentals are canonicalized to the issuer's reporting currency (Shopify reports in USD), and converted dynamically for Canadian portfolio displays using Bank of Canada daily `FXUSDCAD` rates.

### 2.4 Multi-market expansion design (add LSE/ASX later without rework)
1. `country`, `primary_venue`, `currency`, `calendar_id` are first-class on `security`.
2. Canonical fundamental concepts map onto a per-market taxonomy adapter (US-GAAP/IFRS, Canadian IFRS with different line items).
3. Sector taxonomy = GICS globally; per-market sector indices are config, not code.
4. Regime model is per-market with shared features; adding a market = adding a feature set + calibration window.
5. Entitlement classes are data-driven (`dataset → entitlement_class → field`), so a new market's licence terms are configuration.
6. Providers are adapters with capability flags: `supports_intraday`, `supports_canada`, `supports_delisted`, `supports_pit`, `redistribution_allowed`.

---

## 3. DATA PROVIDERS — EVALUATION & RECOMMENDATION

> **Do not assume a free API is usable commercially.** Provider "personal use" licences (e.g., Finnhub's plans are labeled *Personal Use*; EODHD separates personal from commercial; Alpha Vantage's free tier is not a commercial grant) do not permit redistribution inside a paid product. Exchange-sourced real-time display data requires an entitlement agreement **plus** per-subscriber and distributor fees, and typically audit rights.

### 3.1 Price & market data — US
| Provider | Entry cost | Real-time | History depth | US coverage | CA coverage | Licensing / commercial | Verdict |
|---|---|---|---|---|---|---|---|
| **Massive (ex-Polygon.io)** | Free (5 calls/min, delayed); ~US$29 / $79 / $99 / $199 (Advanced = real-time stocks) / ~$449 all-asset | Yes on Advanced (individual, non-pro) | 20+ yrs on top tiers; flat files | Excellent (trades/quotes/bars/ref) | Minimal | Individual tiers for non-pro; business/redistribution tier reported ~US$2,000+/mo — **contract explicitly for redistribution** | **Primary US price provider** for MVP→Growth |
| **Databento** | Usage-based (pay per GB/query) | Yes | Deep tick + historical | Excellent | Limited | Institutionally oriented; clear licensing paths | Secondary/validation + historical tick if needed |
| **Intrinio** | Enterprise-ish | Yes | Deep | Excellent | Some | Rights depend on package | Upgrade path for fundamentals-vendor consolidation |
| **Alpaca** | Free/low-cost with brokerage | Yes | Moderate | Good | No | Tied to brokerage; not a long-term data licence | Prototyping only |
| **Nasdaq / NYSE direct feeds** | Enterprise + non-display fees | Yes | Deep | Native | Via TMX IP for Canada | Full distributor obligations; non-display fees are severe (Nasdaq: $$ tens of thousands/mo at scale) | Only if you become a serious distributor |
| **EODHD** | ~US$20–100/mo personal; commercial separately | Partial (intraday on higher tiers) | 30 yrs | Good | **Yes (TSX/TSXV via QuoteMedia)** | Explicit personal vs commercial split; redistribution needs agreement | Best *value* for CA+US EOD/intraday **historical research**; not a redistribution grant |
| **Twelve Data** | ~US$29/$99/$329; enterprise from ~US$1,099 | Yes on higher tiers | Good | Good | Partial | More conditional; public display/attribution caveats | Fallback |
| **Finnhub** | ~US$50/$130/$200 | Yes on higher tiers | 10–40 yrs | Good | TSX tick coverage on top tier | **Listed as Personal Use** — needs commercial negotiation | Prototype reference only |
| **Alpha Vantage** | Free 25/day; ~US$50 premium | No real-time display rights | 20 yrs | Good | Partial | Not a commercial redistribution grant | Prototype/reference only |
| **Yahoo Finance (unofficial)** | Free | No | Good | Good | Good | **No licence, no SLA, ToS prohibits redistribution** | **Never in production.** Acceptable only as a personal sanity check, never as a system input |

### 3.2 Price & market data — Canada (the hard part)
| Provider | Cost shape | Real-time | Coverage | Licensing reality | Verdict |
|---|---|---|---|---|---|
| **TMX Datalinx (direct)** | Per-subscriber + distribution + data-license fees | Yes | TSX, TSXV, Alpha (Toronto) | Authoritative. Published schedule includes non-pro L1 ~C$3.00/mo per interrogation device (TSX-listed), L2 ~C$10/mo, professional ~C$8.30 (L1 TSX) / ~C$19.80 (L2), distribution fees, and "use in analysis programs" license fees (≈C$1,056/mo for real-time L1 analysis use; ≈C$3,168/mo where programs generate orders), plus a Non-Professional Data Fee Cap Program at a flat monthly cap | **Required** for TSX/TSXV real-time or near-real-time redistribution |
| **Nasdaq Canada (CX2/CXD) feed** | Per-user + non-display + distributor fees | Yes | Canadian venues it operates | Own schedule, e.g. pro L1 ~US$5.60, per-quote fees, non-display L1 US$400/US$200 by usage class | Secondary Canadian venue entitlement |
| **QuoteMedia** | Enterprise seat/feed pricing | Yes | **TSX, TSXV, NEO** incl. SEDAR filings | Reseller path — often the pragmatic route to licensed Canadian data + filings without negotiating TMX directly | **Recommended pragmatic route** for MVP: license CA via a reseller |
| **ICE Data Services / LSEG / FactSet / S&P Global** | Enterprise contracts (five-to-six figures/yr) | Yes | Global incl. Canada | Full institutional licensing and audit support | Post-PMF for the Professional tier |
| **EODHD (via QuoteMedia)** | Low | No (EOD/intraday history) | TSX/TSXV | Cheap for **history/research**; verify redistribution scope | Recommended for research history; confirm display rights separately |
| **CSE** | Varies; often sparse | Limited | CSE | Data availability genuinely limited; some coverage via vendors | Ship as "best-effort / low confidence" until verified |

**Recommendation (Canada):** MVP ships **delayed/EOD Canadian data via a licensed reseller (e.g., QuoteMedia-class) + licenced historical EOD for research**, with a documented plan to move to a **direct TMX Datalinx agreement** when real-time CA display is monetized. Do not scrape TMX, SEDAR+, or vendor portals in production.

### 3.3 Fundamentals
| Provider | Cost | Depth | US | CA | Licensing | Verdict |
|---|---|---|---|---|---|---|
| **SEC EDGAR APIs** (free) | $0 | Full XBRL, as-filed, filings text | Excellent (US filers incl. many Canadian interlisted) | Only Canadian issuers that file with SEC | US government public data; respect 10 req/s + declared User-Agent | **Primary US fundamentals source** |
| **Sharadar (Nasdaq Data Link)** | Tiered (non-pro/pro) | **Point-in-time, ~18K tickers incl. ~12K delisted, 99% survivorship-free, from 1998, 150+ indicators** | Excellent | Canadian interlisted/ADRs included partially | Tiered licence; pro tier for commercial use | **Recommended PIT + delisted universe source for backtesting** — this is what makes honest backtests possible |
| **Financial Modeling Prep / Intrinio / EODHD** | US$20–500+/mo | 5–20 yrs | Good | Partial (weaker CA depth) | Verify commercial/redistribution | Practical enrichment |
| **S&P Capital IQ / FactSet / Refinitiv** | Enterprise | Deep, standardized, PIT | Excellent | Excellent (incl. Canadian issuers) | Full contract | Post-PMF for Professional tier / fundamentals parity |
| **Company filings directly (SEDAR+)** | $0 to read | As-filed documents | — | Core Canadian source | **No public bulk API**; respect terms; use licensed redistribution for commercial product | Use licensed vendor for scale; direct access for manual/verification flows |

### 3.4 News, filings & events
| Provider | Cost | Latency | Licensing | Verdict |
|---|---|---|---|---|
| **Benzinga (via Massive)** | ~US$99/mo **individual use**; enterprise tiers for redistribution | ~25 ms websocket class | Individual vs commercial split; archive/backtest licensing is separately negotiated | Best cost/quality for trader-grade US news *if* commercial terms negotiated |
| **MT Newswires** | Quote | Near real-time, ticker-tagged | Enterprise; strong redistribution story in wealth-management apps | Primary candidate for **Canadian + US** newswire coverage in production |
| **Dow Jones / Bloomberg newswire** | Six figures/yr | Real-time | Strict redistribution; expensive per-end-user | Only at Professional/enterprise scale |
| **Marketaux / APITube / NewsData.io** | US$0–200/mo | Minutes | Clearer for real-time use; softer on archive redistribution | Budget/R&D option with careful terms review |
| **SEC EDGAR full-text search + submissions API** | $0 | Near real-time (minutes) | Public | **Primary US filings/events source** (8-K, Form 4, 13D/G, S-1) |
| **SEDAR+ (via licensed vendor)** | Vendor-dependent | Vendor-dependent | No public bulk API; commercial redistribution needs licence | **Required** for real Canadian filing intelligence |
| **SEDI insider data (via CDS)** | Licence fee | Daily+ | Bulk/real-time resale available under licence | Insider signal for Canada |
| **Company IR RSS / press releases** | $0 | Immediate | Public; per-site terms apply | Supplementary, high-signal (primary source) |
| **GDELT** | $0 | 15 min | Open, attribution | Macro/geopolitical *context only*, never a trade signal |

### 3.5 Macro, FX, commodities
| Provider | Cost | Contents | Verdict |
|---|---|---|---|
| **FRED (St. Louis Fed)** | $0 (API key) | 800K+ US/global series: rates, CPI, employment, yields, spreads | Primary US macro |
| **Bank of Canada Valet API** | $0, no key | Policy rate, CORRA, GoC bond yields, FX reference rates (incl. USD/CAD) | Primary Canadian rates/FX |
| **Statistics Canada WDS API** | $0 | CPI, LFS employment, GDP, retail sales (vector-based; ~15 methods) | Primary Canadian macro |
| **US BLS / BEA** | $0 | CPI, employment, GDP | Primary US prints |
| **EIA** | $0 | Oil, natural gas inventories/production | Energy inputs for TSX transmission |
| **CBOE (VIX, term structure)** | Index values via licensed vendors; VIX term structure data via CBOE licence | Volatility regime inputs | Licence carefully; VIX index level only where permitted |
| **ICE/CBOT commodity futures settlements** | Licensed | WTI, gold, silver, copper, nat gas | Route via your market-data vendor if futures entitlements aren't purchased separately |
| **OECD / World Bank** | $0 | Long-horizon macro, cross-country | Research context |

### 3.6 Recommended provider stack by stage
| Stage | US price | CA price | Fundamentals | News | Filings | Macro |
|---|---|---|---|---|---|---|
| **MVP (Phase 1)** | Massive Starter/Delayed (US EOD + delayed) | Licensed reseller: CA EOD/delayed; EODHD for historical research | SEC EDGAR (US) + vendor EOD fundamentals; licensed CA fundamentals | Vendor headline feed with explicit commercial terms (or filings-only to start) | EDGAR + SEDAR+ via reseller | FRED + BoC Valet + StatCan WDS (all free) |
| **Phase 2–6** | Massive Advanced or Databento | QuoteMedia/TMX reseller with display rights | Add PIT/delisted dataset (Sharadar-class) for backtests | MT Newswires-class or Benzinga commercial | EDGAR FTS + SEDAR+ licensed | + EIA, BLS/BEA, CBOE licence |
| **Professional / scale** | Exchange direct + non-display where justified | Direct TMX Datalinx agreement (+ cap program) | S&P CIQ / FactSet-class | Dow Jones/Bloomberg if the tier funds it | Both, with archive rights | Full macro suite |

**Contract checklist before signing any data agreement:** redistribution rights (display + derived + non-display), user classification obligations (pro/non-pro), per-user and distributor fees, delayed-vs-real-time boundaries (and rules for *derived* data), audit rights and how to evidence usage, history/archive rights (can you keep and use history for backtests?), termination/transition notice, and whether derived scoring outputs may be shown to end users.

---

## 4. AI ARCHITECTURE

### 4.1 Principle: the LLM is not the analyst
| Job | Done by | Why |
|---|---|---|
| Numbers, indicators, scores, sizing, R:R | **Deterministic code** | Reproducible, testable, auditable |
| Regime state estimation | **Statistical model (HMM/HSMM) + rules** | Interpretable, calibratable |
| Sentiment/materiality/horizon classification | **Small LLM/classifier with strict schema** | Cheap, fast, measurable against labelled sets |
| Summarising filings/news, extracting structured facts | **LLM with retrieval + citation spans** | Its genuine strength |
| Writing the user-facing explanation | **Template engine + LLM paraphrase with numeric verification** | Must never invent |
| Anomaly/novelty detection in news | **Embeddings + clustering** | Dedupe and novelty are geometric, not semantic-guessing problems |

### 4.2 Task-level model routing
| Task | Model class | Rationale | Guardrails |
|---|---|---|---|
| Headline classification (18-way event taxonomy + sentiment) | Small/cheap model, temp ≈ 0 | Millions of items; cost-sensitive; classification is calibrated | JSON schema validation; confidence threshold; low-confidence → UNCERTAIN |
| Materiality & horizon scoring | Small model + rule features (market cap, RVOL, $ impact, guidance presence) | Needs a numeric prior to anchor | Blended: `materiality = f(model_score, numeric_prior)` |
| Filing section extraction (risk factors, MD&A, guidance) | Mid-tier model, long context | Needs comprehension | Output must quote spans with offsets; rejected if spans don't exist in source |
| News clustering / dedupe | Embeddings + MinHash + time windowing | Geometric | Cluster labelled as one event; publishers within cluster recorded |
| Narrative synthesis (why this score) | Mid/top-tier model, template-bound | Reads better, still bounded | Post-processor verifies every numeral + citation; strips unverifiable causal claims |
| Macro commentary | Template-driven, minimal LLM | High regulatory/hallucination risk | Only restates computed series and published calendar events |
| Deep research agent ("explain this filing") | Top-tier model, retrieval-tooled, on-demand (paid tiers) | High value, low volume | Read-only tools; per-user quota; full prompt/output logging |

### 4.3 The explanation contract (enforced in code)
Every AI-facing text block is composed of four typed sections; the API rejects blocks missing any of them:

```json
{
  "facts":        [{"statement":"Price closed 2.1% above the 50-day SMA on 2026-09-23.",
                    "value":214.37,"source":"TMX_DATALINX","as_of":"2026-09-23T16:00:00-04:00","doc_ref":null}],
  "calculations": [{"statement":"ATR(14) = 5.83 (1.9% of price); relative volume = 2.4× 20-day average.",
                    "formula":"atr14/close","inputs":{"atr14":5.83,"close":306.2},"feature_version":"feat_v1.4.0"}],
  "inferences":   [{"statement":"The surge in relative volume alongside an MA reclaim suggests institutional accumulation is likely.",
                    "confidence":0.62,"basis":"Historical analogue set n=184, 58% positive drift over 20 sessions"}],
  "uncertainties":[{"statement":"Canadian fundamentals coverage for this issuer is incomplete (2 of 9 concepts missing), so the Fundamental Score is capped at MEDIUM confidence.",
                    "missing_fields":["roic","interest_coverage"],"effect":"Fundamental score suppressed from composite"}]
}
```

Regression tests (CI): (a) every numeral in `inferences` must appear in `facts`/`calculations` inputs; (b) every `doc_ref` must resolve; (c) causal verbs (`because`, `due to`, `caused`) are disallowed unless attached to an explicit attribution citation.

### 4.4 Retrieval architecture
- Documents chunked with structural awareness (10-K/10-Q/AIF sections, not blind token windows), embedded with a finance-tuned embedding model, stored in pgvector with metadata (issuer, doc type, date, accession/URL).
- Hybrid retrieval: BM25/keyword + vector + recency + issuer filter; re-rank by cross-encoder for the top-k only.
- **Citation-first generation**: the model returns `{sentence, doc_ref, span}` tuples; sentences without resolvable spans are dropped before display.
- **Point-in-time retrieval for backtests**: when reconstructing a historical signal explanation, retrieval is restricted to documents with `published_at ≤ as_of`. This is a hard filter, not a prompt instruction.

### 4.5 Evaluation harness (build before features)
| Layer | What is measured | Method |
|---|---|---|
| Classification | Accuracy/F1 per category; **calibration curves** for sentiment and materiality | Human-labelled gold set (≥ 1,500 items: US + Canadian, easy + deliberately ambiguous) |
| Extraction | Numeric extraction accuracy vs source document; hallucinated-number rate (target: **0**) | Automated span verification + sampled human audit |
| Summarisation | Faithfulness, coverage, causal-claim rate | LLM-as-judge **plus** human spot checks; numeric verifier as hard gate |
| Entity linking | Precision/recall of ticker mapping (incl. dual-listed ambiguity) | Labelled mapping set |
| Safety/regulatory | % of outputs containing advice-like or personalized phrasing (target: 0) | Regex + classifier linter in CI |
| Cost/latency | Tokens and $ per 1K items; p95 latency | Dashboarded per task |

**Release gate:** no prompt or model change ships without passing the harness; versions are pinned (`model_version`, `prompt_version`) and recorded on every generated artefact so any past output can be reproduced.

### 4.6 Cost control
Batch everything (nightly + intraday cycles), cache by content hash, route by task, cap per-user deep-analysis calls, and use local embedding models. Design target: **≤ US$0.02/user/day** for free-tier users, ≤ US$0.25/user/day for Premium with quotas.

---

## 5. ALERT ENGINE

### 5.1 Architecture
```
Trigger sources: signal svc, price/risk monitor, news svc, fundamental-change detector,
                 macro/regime svc, portfolio risk monitor
      └─▶ Alert candidate (typed payload + evidence + severity + dedupe key)
           └─▶ Policy engine: user rules · risk profile · quiet hours · alert budget ·
               hysteresis/cooldown · entitlement check · dedupe/merge
                └─▶ Channel fanout: in-app (SSE) · email · push · SMS (transactional, opt-in)
                     └─▶ Delivery log + open/action telemetry → precision feedback loop
```

### 5.2 Alert taxonomy & severity
| Severity | Examples | Default channels | Timing |
|---|---|---|---|
| **Critical** | Portfolio stop breached, emergency-risk condition, data outage affecting positions | In-app + push + email | Immediate |
| **High** | Entry setup confirmed, exit/reduce trigger, target reached, material negative news on a holding | In-app + push (+email digest) | Immediate to 5 min |
| **Medium** | Breakout/breakdown, unusual volume, earnings approaching, regime shift, sector rotation | In-app + email | Within cycle (≤ 15 min) |
| **Low** | Watchlist mood changes, valuation drift, DQ warnings | Email digest | Daily |

### 5.3 Alert hygiene rules (churn protection)
- **Hysteresis:** entry alerts require confirmation across two cycles (or a closing-price confirmation) to prevent flip-flops.
- **Cooldowns:** per (user, security, alert_type) — no repeat within a configurable window (default 3 trading days).
- **Budget:** default ≤ 8 high-severity alerts/user/day; surplus is rolled into a digest, not dropped.
- **Dedupe/merge:** news alerts merge by cluster; price alerts merge by trigger family.
- **Suppression:** earnings blackout (no *new* entry alerts within 2 sessions of a held position's earnings unless the user opted in).
- **Every alert includes:** trigger name, value vs threshold, timestamp, one-line reason, link to the full explanation, and the invalidation condition that would reverse it.
- **Explicit non-promise:** stop-loss alerts are *notifications*, not protection — the UI states clearly that the user must place protective orders at their broker.

---

## 6. SECURITY ARCHITECTURE

| Domain | Control |
|---|---|
| Identity | OIDC IdP; **passkeys/TOTP**; email verification; session rotation; device list; optional enterprise SAML/OIDC |
| Authorization | RBAC (user, pro, admin, analyst, service) + ABAC on ownership; **Postgres RLS** with `tenant_id`/`user_id` on every tenant table; deny-by-default |
| API security | Short-lived JWTs, scoped API tokens with rotation, per-key rate limits, idempotency keys, strict input validation, output encoding, no PII in URLs |
| Data at rest | KMS-managed encryption (Postgres, S3, backups); envelope encryption for sensitive columns; secrets in Secrets Manager; no secrets in repos (pre-commit scanning) |
| Data in transit | TLS 1.3 everywhere, HSTS, mTLS for internal service-to-service where warranted |
| Network | VPC private subnets for data stores; no public database endpoints; WAF + bot management at the edge; egress allow-listing for provider calls |
| Application | SAST/DAST in CI, dependency + container scanning (SBOM), no `eval`, structured logging with PII redaction |
| Abuse & fraud | Signup abuse controls (email/phone verification, velocity limits), alert-spam protection, entitlement tampering detection, anti-scraping on our own API |
| Auditability | Append-only hash-chained audit log: auth events, entitlement changes, consent changes, signal generation, alert delivery, admin actions |
| Privacy | Data residency `ca-central-1` for Canadian PII; retention schedules (e.g., alert logs 24 mo, consent records 7 yr, security logs 13 mo); DSAR tooling; PIPEDA + Quebec Law 25 alignment; DPA with subprocessors |
| Resilience | Multi-AZ; automated backups + **restore drills** (a backup you've never restored is a hypothesis); RPO 15 min / RTO 4 h targets; documented runbooks |
| Assurance roadmap | Penetration test before public launch; SOC 2 Type I by month 12–18 for the Professional/B2B tier; annual pen test thereafter |
| Vendor risk | Provider SLA review, key-person and single-vendor exposure tracked (IEX Cloud precedent), exit playbook per critical vendor |

---

## 7. OBSERVABILITY, MONITORING & LOGGING

| Category | Signal | Alert threshold | Owner |
|---|---|---|---|
| **Data freshness** | per-dataset lag vs SLO (real-time 5s, delayed 5m, EOD 20:00 ET, fundamentals T+1) | Breach > 2× SLO | Data ops |
| **Data quality** | DQ failure rate per dataset; quarantine counts; cross-vendor divergence bps | Failure > 1% of rows or divergence > 50 bps intraday | Data ops |
| **Pipeline** | Job success rate, runtime vs baseline, backfill queue depth | Job failure, or runtime > 1.5× baseline | Platform |
| **API** | p50/p95/p99 latency, 5xx rate, saturation | p95 > 800 ms, 5xx > 0.5% | Backend |
| **Signal pipeline** | signals generated/cycle, gate-failure distribution, score drift vs 30-day baseline | Score distribution shift > 2σ or signal count ±50% day-over-day | Quant |
| **Model/AI** | hallucinated-number rate (must be 0), citation-resolution rate, classification F1 drift, $ per 1K items | Any numeric-hallucination > 0 → block publication; F1 drop > 5% | ML |
| **Alerts** | delivery latency, bounce/undeliverable rate, opt-out rate, alert→action rate | Bounce > 2%, opt-out > 0.5%/month | Product |
| **Business** | MRR, churn, conversion, CAC, LLM cost/user, data cost/user, gross margin by tier | Margin < target for 2 consecutive months | Founder/Finance |
| **Security** | auth failures, anomalous access patterns, token abuse, WAF blocks | Any spike or privilege anomaly | Security |
| **Licence compliance** | per-user entitlement state, denied-field counts, audit-report readiness | Any unentitled real-time field served → P1 | Compliance |

Logging standard: structured JSON, `trace_id`/`request_id`/`tenant_id` on every line, OTel traces spanning client → api → engine → storage, PII redaction at the logger boundary, immutable log retention per policy.

---

## 8. COST MODEL

### 8.1 Assumptions
- Region: `ca-central-1` primary (Canada residency) + `us-east-1` for US-market reads at scale.
- Universe: 2,500 US + 2,000 CA securities scored; 7 timeframes (MVP: 4).
- LLM: batch classification on all news items; explanation synthesis for signal-bearing names only; local embeddings.
- Real-time scenarios are shown separately because entitlements dominate.
- **All figures are engineering estimates (US$), ±40%, to be revalidated during Sprint 0.**

### 8.2 Infrastructure (excluding data entitlements)
| Component | 100 users | 1,000 users | 10,000 users | 100,000 users |
|---|---|---|---|---|
| Compute (web/api/workers, Fargate→EKS) | $120 | $450 | $2,200 | $9,500 |
| Database (RDS/Timescale, IOPS, storage) | $180 | $520 | $2,000 | $6,500 |
| ClickHouse / analytics (scale only) | — | — | $600 | $3,200 |
| Object storage + Iceberg + egress | $30 | $90 | $400 | $1,600 |
| Redis / queues / event bus | $40 | $120 | $500 | $1,900 |
| CDN / WAF / DNS | $25 | $120 | $600 | $2,800 |
| Observability (metrics/logs/traces) | $60 | $250 | $1,100 | $4,200 |
| Email / push delivery | $20 | $150 | $1,300 | $9,000 |
| Backup/DR | $30 | $110 | $450 | $1,500 |
| **Infra subtotal** | **~$505** | **~$1,810** | **~$9,150** | **~$40,200** |

### 8.3 AI/LLM
| Component | 100 users | 1,000 users | 10,000 users | 100,000 users |
|---|---|---|---|---|
| Shared news classification (market-wide, batched) | $60 | $90 | $140 | $220 |
| Explanation synthesis (per signal, cached/shared) | $150 | $300 | $650 | $1,400 |
| Per-user on-demand deep analysis (quota-bound, paid tiers) | $80 | $700 | $6,500 | $48,000 |
| Embeddings (local model, GPU/CPU batch) | $40 | $60 | $120 | $260 |
| **LLM subtotal** | **~$330** | **~$1,150** | **~$7,410** | **~$49,880** |
| *Mitigation lever* | tighter quotas, more caching, smaller models | | | |

### 8.4 Market-data licensing (delayed/EOD product — the MVP path)
| Component | 100 users | 1,000 users | 10,000 users | 100,000 users |
|---|---|---|---|---|
| US delayed/EOD provider plan (Massive-class) | $99 | $199 | $499–1,500 | $2,000–5,000 |
| US PIT/delisted fundamentals (Sharadar-class) | $150 | $150 | $600 | $1,500 |
| CA EOD/delayed via reseller (QuoteMedia-class) | $400 | $900 | $2,500 | $6,500 |
| CA fundamentals + filings feed | $250 | $400 | $1,200 | $3,000 |
| News feed (commercial terms, US+CA) | $500 | $500 | $1,500 | $4,500 |
| Macro (FRED/BoC/StatCan/EIA) | $0 | $0 | $0 | $0 |
| **Data subtotal** | **~$1,400** | **~$2,150** | **~$6,800** | **~$17,500** |

### 8.5 Real-time variant — and why entitlement math decides the product
If real-time TSX + US display data were sold to every user (assuming non-professional class):

| Users | TMX L1 non-pro (≈C$3/user/mo) | Nasdaq Basic-class US non-pro (≈US$1/user/mo) | Distributor + analysis-program licence fees | Total data/mo (approx.) |
|---|---|---|---|---|
| 100 | C$300 | US$100 | ≈C$1,056 (analysis use) + distributor ≈US$1,600 | **≈US$3,300** |
| 1,000 | C$3,000 | US$1,000 | same + usage fees | **≈US$6,000** |
| 10,000 | C$30,000 | US$10,000 | + scale fees | **≈US$45,000–70,000** |
| 100,000 | C$300,000 → **capped under TMX non-professional cap program (flat monthly fee)** | US$100,000 | + enterprise/distributor tiers | **C$60K–110K cap path ≈ US$45K–90K, vs ≈US$370K uncapped** |

**Conclusions that shaped the architecture:**
1. Real-time at scale only works with the **cap/enterprise program negotiated**, and only if priced as a pass-through add-on (typically US$15–20/user/mo for US, C$10–15 for CA).
2. Professional-class users cost roughly **10–27× more** per head for display data. Never let a professional-class user pay a retail price — verify classification at onboarding and re-verify annually (audit rights exist).
3. Delayed/EOD-first is not a compromise; it is the only architecture with a defensible margin at retail price points.

### 8.6 Total monthly run-rate summary (indicative)
| Scenario | 100 users | 1,000 users | 10,000 users | 100,000 users |
|---|---|---|---|---|
| **Delayed/EOD product (recommended)** | **~$2.2K** | **~$5.1K** | **~$23K** | **~$108K** |
| **Real-time dual-market (all users)** | ~$4.1K | ~$9.0K | ~$58K–83K | ~$165K–205K |
| Revenue needed to hold 75% GM | $8.8K/mo | $20K/mo | $92K/mo | $432K/mo |
| Implied ARPU (delayed) | C$88+ | C$20–25 | C$9–12 | C$5–7 |

*(At 100,000 users the delayed product requires ~C$5–7 blended ARPU — achievable only with meaningful free-tier conversion and a well-priced Professional segment; a mostly-free user base at scale requires the white-label/API line to carry the fixed data costs. This is the single most important economic constraint in the plan.)*

### 8.7 Development cost (Phase 1 MVP and beyond)
Detailed in [`04_roadmap_mvp.md` §3](04_roadmap_mvp.md). Headline: **MVP US$180K–320K** with a 4-engineer + 1-quant team over ~14 weeks; **full 12-phase build US$1.6M–3.2M** over 24–30 months (or roughly 2× that if hiring senior quant + ML in high-cost markets).

---

## 9. DEPLOYMENT, CI/CD & ENVIRONMENTS

| Environment | Purpose | Data | Notes |
|---|---|---|---|
| `local` | Development | Synthetic + small licensed sample | Never ship real licensed data into local fixtures |
| `dev` | Integration | Delayed sample + sandbox providers | Auto-deploy on merge |
| `staging` | Pre-prod, backtest validation | Full delayed data, separate DB | Runs the eval harness + DQ suite on every release |
| `research` | Quant experimentation | Full PIT history (read-only) | Isolated compute; heavy jobs never run in prod |
| `prod` | Customer-facing | Entitled data only | Change control, canary releases, feature flags |

CI/CD gates: unit + integration tests → DQ test suite → AI eval harness → migration dry-run → security scan → canary (5% traffic, 30 min) → full rollout. Rollback plan required per release (schema changes must be backward-compatible for one release cycle).
