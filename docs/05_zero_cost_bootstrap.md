# 05 — The Zero-Cost Architecture & Bootstrapping Strategy

**Scope:** How to build, backtest, paper-trade, and operate a US + Canada market intelligence engine at **$0.00/month cash outlay** using free infrastructure, public official APIs, and strategic architectural isolation.

---

## 1. The Brutal Truth: What Can and Cannot Be $0

When someone asks *"can we do this at no cost?"*, the answer depends entirely on **who sees the data**:

| Use Case | Monthly Cash Cost | Is It Legal? | Why / Binding Constraint |
|---|---|---|---|
| **Personal Quant Workstation** (You run it on your machine or private cloud for your own investing/research) | **$0.00** | **100% Legal** | You consume public data, SEC filings, official central bank feeds, and personal-use market APIs. Zero licensing restrictions apply. |
| **Public Track Record / Paper Trading / Newsletter** (You publish derived scores, regimes, and signals, NOT raw market feeds) | **$0.00** | **100% Legal** | Derived works (scores, indicator outputs, written commentary) are not raw exchange data. You use free cloud tiers (Cloudflare Pages, Groq free tier, Oracle Cloud). |
| **Open Source / BYOK (Bring Your Own Key)** (You distribute the software; users provide their own free API keys) | **$0.00** | **100% Legal** | You distribute code, not data. Users make their own API calls under personal free tiers. |
| **Commercial SaaS Redistributing Raw TSX/Nasdaq Feeds** (Paying users log into your web app and see live/delayed price charts) | **CANNOT be $0** | **Illegal without exchange agreements** | TMX Datalinx and Nasdaq require commercial redistribution agreements, distributor fees (US$1,500–2,000/mo), and per-subscriber fees. Scraped feeds will trigger cease-and-desist or IP bans. |

### The Golden Rule of Zero-Cost Market Intelligence
> **You can compute on free data; you cannot resell or redistribute raw exchange data for free.**
> If your product outputs **derived intelligence** (Scores 0–100, Regime classifications, AI synthesis, Entry/Exit signal zones) rather than raw tick/depth feeds, you avoid 95% of commercial exchange licensing burdens.

---

## 2. The Complete $0 Infrastructure Stack

Every component in this table has been verified for 2026 availability, commercial terms, and generous free allowances.

| Component | Selected Free Provider | Free Tier Allowance | What We Run On It | Limitations / Gotchas |
|---|---|---|---|---|
| **Core Compute & Backend** | **Oracle Cloud Always Free Tier** (Ampere A1 Flex) | • 2 to 4 ARM OCPUs<br>• 12 to 24 GB RAM<br>• 200 GB NVMe storage<br>• 10 TB/month outbound egress | • PostgreSQL 16 + TimescaleDB<br>• FastAPI backend engine<br>• Celery / Redis task queue | Upgrade to "Pay As You Go" account with $0 spend to prevent idle instance reclamation and bypass capacity queue. |
| **Scheduled Ingestion Cron** | **GitHub Actions** | • 2,000 Linux minutes/mo (private repo)<br>• Unlimited (public repo) | • Daily 16:30 ET EOD pipeline<br>• Daily SEC filing scanner<br>• Backtest runners | 22 trading days × 5 mins/day = 110 mins/mo (only 5.5% of quota). |
| **Frontend & Web Hosting** | **Cloudflare Pages** | • Unlimited bandwidth<br>• 500 builds/month<br>• Custom domains & free SSL<br>• Global edge CDN | • Next.js static / SSR frontend<br>• Dashboard UI & charts<br>• Documentation | Free tier explicitly allows commercial use. Zero egress fees. |
| **Edge Storage & Static DB** | **Cloudflare R2 + D1** | • R2: 10 GB storage, 10M reads/mo<br>• D1: 5 GB SQLite, 5M reads/day | • Daily static JSON scores<br>• Backtest report PDFs<br>• Lightweight user profile sync | D1 is SQLite (no Timescale/pgvector), but perfect for serving pre-calculated daily scores to the web. |
| **AI / LLM Intelligence** | **Groq Free Tier** + **Google Gemini 2.5/3 Flash** | • Groq: 30 RPM, 1,000 RPD (Llama 3.3 70B, Qwen 2.5)<br>• Gemini: 15 RPM, 1,500 RPD (Flash/Flash-Lite) | • SEC 8-K & 10-K extraction<br>• News deduplication & sentiment<br>• FACT/CALCULATION/INFERENCE explanation layer | Gemini free tier data may be used for model training (never send private portfolio data; public filings/news only). Groq requires no CC. |
| **Local LLM Fallback** | **Ollama (Self-Hosted)** | • Completely unlimited<br>• Runs on your Mac / PC / ARM VM | • Local embeddings (bge-small-en)<br>• Offline news classification (Llama 3.1 8B, Qwen 2.5 7B) | Needs 8–16 GB RAM. 100% private, zero API dependencies. |

---

## 3. The Zero-Cost Data Pipeline (US + Canada)

How to get institutional-grade inputs without paying a vendor a dime:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        FREE OFFICIAL & PUBLIC SOURCES                  │
├───────────────────┬───────────────────┬────────────────────────────────┤
│   US SEC EDGAR    │  BANK OF CANADA   │     FRED (St. Louis Fed)       │
│  • 10-K / 10-Q    │  • Daily CAD/USD  │     • US 2Y/10Y Yields         │
│  • XBRL Fin. Facts│  • Policy Rates   │     • CPI & Inflation          │
│  • Form 4 Insiders│  • CORRA & Yields │     • Fed Funds Rate           │
│  • 10 req/s FREE  │  • 100% Free API  │     • 100% Free API            │
└─────────┬─────────┴─────────┬─────────┴────────────────┬───────────────┘
          │                   │                          │
          ▼                   ▼                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        PRICE & MARKET BARS                             │
├───────────────────────────────────┬────────────────────────────────────┤
│         Personal / Prototype      │          Public / Derived          │
│  • yfinance (US + TSX/TSXV)       │  • Stooq EOD CSVs (US + Macro)     │
│  • Finnhub (60 calls/min free US) │  • Alpha Vantage (25/day free)     │
│  • CBOE Delayed Quotes (US)       │  • User BYOK (Bring Your Own Key)  │
└───────────────────────────────────┴────────────────────────────────────┘
```

### 3.1 US Fundamentals: SEC EDGAR (Official & 100% Free)
- **Endpoint:** `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json`
- **What it gives:** Point-in-time balance sheets, income statements, cash flows, revenue, net income, EPS, shares outstanding for all US public companies back to 2009.
- **Cost:** **$0.00**. Completely free, official, public domain, and unrestricted for commercial applications.
- **Rule:** SEC requires a declared `User-Agent: Sample Company Name AdminContact@domain.com` and throttles to 10 requests per second.

### 3.2 Canadian & US Macro: Official Central Bank APIs
- **Bank of Canada Valet API:** `https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json`
  - Free, no API key required, permanent URL scheme.
  - Gives daily USD/CAD FX rates, BoC target interest rate, Government of Canada bond yields (2Y, 5Y, 10Y, 30Y), CORRA.
- **Statistics Canada Web Data Service (WDS):**
  - Free API for Canadian CPI, GDP, employment, retail sales.
- **Federal Reserve Economic Data (FRED):**
  - Free API key. Covers US CPI, 10Y-2Y Treasury spread, Fed Funds effective rate, VIX daily closes, high-yield credit spreads.
- **Energy Information Administration (EIA):**
  - Free API key. Spot prices for WTI Crude, Brent Crude, Henry Hub natural gas (critical for TSX energy/materials weighting).

### 3.3 News & Material Events: Free RSS Feeds
Instead of paying US$2,000/month for a Bloomberg or MT Newswires feed, tap the source feeds directly at $0:
- **SEC EDGAR RSS Feed:** Real-time stream of every 8-K (material event), 10-Q, and Form 4 filed.
- **GlobeNewswire & PR Newswire RSS Feeds:** Filtered by TSX and US tickers.
- **Company Investor Relations RSS:** Direct corporate releases.
- Process: An ingestion worker checks RSS feeds every 5 minutes (tiny bandwidth). When an 8-K or press release appears, it sends the plain text to **Groq Free (Llama 3.3 70B)** to extract:
  1. Materiality flag (Binary: Yes/No)
  2. Event type (Earnings, M&A, Offering, Guidance, Lawsuit, Clinical Trial)
  3. Directional sentiment (−1.0 to +1.0)
  4. Time horizon (Immediate, 1-month, 1-quarter)

### 3.4 Price Bars: How to Handle Without Exchange Licensing Fees
Price data is where commercial costs live. Here is how to navigate it:

1. **Phase 1 (Development, Backtesting, Personal Use):**
   - Use `yfinance` / `stooq`. It covers NYSE, NASDAQ, AMEX, TSX (`.TO`), and TSXV (`.V`) with decades of daily bars and intraday history.
   - Run your complete backtesting framework, regime detection, factor scoring, and paper trading.
   - Cost: **$0.00**.

2. **Phase 2 (Public Web App / Derived Intelligence):**
   - **Do NOT publish raw interactive Candlestick charts with scraped live data.**
   - Instead, compute all technical indicators, momentum, RVOL, S-R levels, and 0–100 scores in the backend.
   - Publish **derived outputs** on the web app:
     - "TSX Composite Momentum Score: 74/100"
     - "Regime: Low-Volatility Bull (Confidence 82%)"
     - "Entry Zone: $42.50 – $43.10 | Stop: $40.80 | Target 1: $46.00"
     - "Signal Quality: Grade A (R:R 2.8:1)"
   - For chart display, embed TradingView's free lightweight widgets or use a **BYOK (Bring Your Own Key)** option where users input their personal free Finnhub/Alpha Vantage/Polygon key.

---

## 4. The Daily Zero-Cost Workflow Architecture

The entire platform can run automatically on a serverless/free schedule without a human touching it:

```
ET Time    Action                                          Runner / Cost
────────────────────────────────────────────────────────────────────────────
16:00      US & Canadian Markets Close                     —
16:05      GitHub Action cron triggers                     GitHub Actions ($0)
16:06      Ingest daily EOD bars (750 symbols)             Python script ($0)
16:10      Ingest BoC Valet, FRED, and SEC 8-K feeds       Direct API ($0)
16:15      Run Quant Pipeline:                             Oracle Cloud ($0)
           • Technical indicators & volume profiles        or GitHub runner
           • 8-Factor scoring engine (0–100)
           • HSMM Regime Detection
           • Scanner filters (14 scanners)
16:30      Run Entry/Exit Signal Engine                    Deterministic code ($0)
16:35      Generate AI Explanation summaries for Top 20    Groq Free LPU ($0)
           (Llama 3.3 70B: ~20 calls @ 500 tokens = 10k tok)
16:40      Compile daily JSON intelligence snapshots       FastAPI / Python ($0)
16:45      Push static data to Cloudflare R2 / D1          Cloudflare CLI ($0)
16:46      Cloudflare Pages updates instantly via CDN      Cloudflare Pages ($0)
17:00      Send daily digest email via Resend Free Tier    Resend (3,000/mo $0)
────────────────────────────────────────────────────────────────────────────
Total Daily Execution Time: ~40 minutes
Total Monthly Compute & Data Cost: $0.00
```

---

## 5. The Three Strategic Bootstrapping Paths

### Path A: "The Solo Quant / Private Alpha Engine" (100% $0 Forever)
- **Who it is for:** You, using the system to trade and allocate your own capital.
- **Architecture:** Local desktop (Mac/PC) or single Oracle Always Free ARM VM + TimescaleDB + `yfinance` + SEC EDGAR + BoC Valet + Ollama / Groq.
- **Why this works:** You have zero commercial compliance exposure, zero exchange distribution licensing, zero hosting costs, and zero user-support overhead.
- **Cost:** **$0.00 / month**.
- **Time to build:** 4–6 weeks for an MVP.

### Path B: "The Derived-Intelligence Publisher" (100% $0 Cloud Setup)
- **Who it is for:** Building an audience and a live, audited track record before asking for money.
- **Architecture:**
  - Backend runs daily cron on GitHub Actions or Oracle Cloud.
  - Scores, scanner results, and AI summaries exported as static JSON to Cloudflare R2 / Pages.
  - Public website displays daily rankings, scanner results, regime status, and the **Live Falsifiable Track Record (Paper Trading)**.
  - Users sign up for a free daily email digest (via Resend free tier: 3,000 emails/mo).
- **Exchange licensing avoidance:** You never redistribute tick quotes. You publish proprietary analytical research.
- **Cost:** **$0.00 / month**.
- **Monetization trigger:** When you have 1,000+ newsletter subscribers or active users asking for real-time intraday alerts, introduce a paid Pro tier. The subscription revenue *funds* the commercial data licenses from Day 1 of monetization.

### Path C: "Open Core / BYOK Platform" (100% $0 Infrastructure for You)
- **Who it is for:** Community-driven software with zero infrastructure liability.
- **Architecture:** The core application is distributed as a Docker container or desktop app (Electron/Tauri).
- **Data model:** Users bring their own free API keys (SEC EDGAR is free, FRED is free, BoC is free; users plug in their own free Finnhub or Massive key for quotes).
- **Cost to you:** **$0.00**. Users run the compute on their own machines.

---

## 6. How the Original MVP Plan Changes Under the Zero-Cost Model

| Area | Original MVP Spec (Commercial SaaS) | Zero-Cost Bootstrapping Spec |
|---|---|---|
| **Capital Outlay** | US$180K–320K (Dev team) + US$2.2K/mo (Data) | **$0.00 cash outlay** (Self-built / solo / agent-assisted) |
| **Compute & Cloud** | AWS ECS Fargate + RDS Multi-AZ + Timescale | Oracle Cloud Always Free (Ampere A1) + Cloudflare Pages/R2/D1 |
| **Price Data** | Massive.com (US$199/mo) + QuoteMedia CA (US$1,500/mo) | `yfinance` / Finnhub Free for research; Derived scores published |
| **Fundamentals** | Sharadar PIT (US$250/mo) | SEC EDGAR official XBRL API (US) + Free web scraping of SEDAR notices |
| **Macro Data** | FRED + BoC Valet (same) | FRED + BoC Valet (same, already $0) |
| **AI / LLM** | Anthropic Claude 3.5 Sonnet API (~US$300/mo) | Groq Free (Llama 3.3 70B) + Gemini Flash Free (1,500 RPD) + Ollama |
| **Exchange Agreements** | Mandatory pre-launch TMX/Nasdaq agreements | Bypassed: Personal use initially, or derived scores only |
| **Legal Opinion** | US$15K–30K outside Canadian/US counsel | Self-guided compliance adherence to CSA 31-369 impersonal exemption rules |
| **Primary Goal** | Commercial subscription launch on Day 1 | **Establish a 90-day audited out-of-sample paper track record** |

---

## 7. Recommended Action Plan to Start at $0

1. **Step 1: Set up the $0 Core Stack (Day 1)**
   - Provision an Oracle Cloud Always Free ARM instance (or run local Docker on your current machine).
   - Install PostgreSQL 16 with TimescaleDB extension.
   - Register free API keys: FRED, Bank of Canada Valet (no key needed), Groq, Google AI Studio.

2. **Step 2: Build the Free Data Ingestion Engine (Week 1–2)**
   - Implement the SEC EDGAR XBRL parser (10 req/s, free US company facts).
   - Implement the BoC Valet & FRED macro parsers (CAD/USD, rates, yield spreads).
   - Implement the daily bar ingestion adapter for 500 US + 250 Canadian stocks.

3. **Step 3: Run the Quant & Scoring Engine (Week 3–4)**
   - Calculate all 8 sub-scores and the composite 0–100 score.
   - Run the 6-state HSMM Regime Detection model.
   - Execute the 14 opportunity scanners at market close.

4. **Step 4: Launch the 90-Day Public / Private Paper Trading Scoreboard (Week 5+)**
   - Automatically log every generated entry and exit signal to an append-only audit table.
   - Track win rate, Sharpe ratio, max drawdown, and profit factor with 100% realistic slippage and fees.
   - **Cost to date: $0.00.**
   - Once the paper scoreboard proves out-of-sample edge over 90 days, you can choose to either keep using it as your personal edge for $0, or turn on paid subscriptions to fund commercial data licensing.

---

## 8. Verified API Contracts & Integration Specifications

Every endpoint, header, rate limit, and series ID below has been live-tested and verified.

### 8.1 Bank of Canada Valet API (Official, Free, No Key Required)
- **Base URL:** `https://www.bankofcanada.ca/valet/observations/{seriesNames}/json`
- **Query Parameters:** `?recent=1` (latest observation) or `?start_date=YYYY-MM-DD`
- **Verified Series Identifiers:**
  - `FXUSDCAD`: Daily CAD/USD bilateral exchange rate (16:30 ET release)
  - `BD.CDN.2YR.DQ.YLD`: Government of Canada benchmark bond yield, 2-year
  - `BD.CDN.10YR.DQ.YLD`: Government of Canada benchmark bond yield, 10-year
  - `BD.CDN.5YR.DQ.YLD`: Government of Canada benchmark bond yield, 5-year
  - `STATIC_LIQUIDITY_TARGET_CAN`: Bank of Canada Target for the Overnight Rate (policy rate)
- **Response Structure:**
  ```json
  {
    "observations": [
      {
        "d": "2026-09-24",
        "FXUSDCAD": {"v": "1.4136"},
        "BD.CDN.10YR.DQ.YLD": {"v": "3.96"}
      }
    ]
  }
  ```
- **Error Handling & Resilience:** BoC publishes around 16:30 ET on Canadian banking days. If called on Canadian bank holidays, return last available observation; tag `freshness = stale_holiday`.

### 8.2 SEC EDGAR Official XBRL API (Official, Free, No Key Required)
- **Base URL:** `https://data.sec.gov`
- **Strict Headers Requirement:**
  ```http
  User-Agent: MarketIntelPlatform/1.0 (dev-ops@marketintel.local)
  Accept-Encoding: gzip, deflate
  ```
  *(Missing or generic `User-Agent` returns HTTP 403 immediately).*
- **Rate Limit Policy:** Hard limit of 10 requests/second per IP across `data.sec.gov`.
  - **Implementation Guardrail:** Implement a token-bucket rate limiter set strictly to **8 requests/second** to guarantee safety margin.
- **Key Endpoints:**
  - Ticker-to-CIK Mapping: `https://www.sec.gov/files/company_tickers.json` (cached locally for 24 hours).
  - Company Facts (all historic XBRL items): `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json` (e.g., Apple CIK 320193 becomes `CIK0000320193.json`).
  - Submissions & Filings History: `https://data.sec.gov/submissions/CIK{cik:010d}.json`.
- **Primary US-GAAP Concepts Extracted:**
  - Revenue: `RevenueFromContractWithCustomerExcludingAssessedTax` or `Revenues`
  - Net Income: `NetIncomeLoss`
  - Operating Cash Flow: `NetCashProvidedByUsedInOperatingActivities`
  - Total Assets: `Assets`
  - Total Liabilities: `Liabilities`
  - Stockholders Equity: `StockholdersEquity`
  - Diluted Shares: `CommonStockSharesOutstanding` or `WeightedAverageNumberOfDilutedSharesOutstanding`

### 8.3 Federal Reserve Economic Data (FRED API)
- **Base URL:** `https://api.stlouisfed.org/fred/series/observations`
- **Authentication:** `api_key={FREE_API_KEY}&file_type=json`
- **Rate Limit:** 120 requests/minute (free key obtained in 30 seconds at stlouisfed.org).
- **Verified Series Identifiers:**
  - `DGS10`: 10-Year Treasury Constant Maturity Rate (daily)
  - `DGS2`: 2-Year Treasury Constant Maturity Rate (daily)
  - `T10Y2Y`: 10Y minus 2Y Yield Spread (canonical yield curve inversion indicator)
  - `FEDFUNDS`: Federal Funds Effective Rate
  - `VIXCLS`: CBOE Volatility Index (daily close)
  - `BAMLH0A0HYM2`: ICE BofA US High Yield Index Option-Adjusted Spread (credit stress indicator)
  - `CPIAUCSL`: US Consumer Price Index for All Urban Consumers

### 8.4 Canadian Issuer Regulatory News Pipeline
- **Sources (Free RSS):**
  - **Newsfile Corp RSS:** Primary dissemination service for Canadian resource, tech, and micro/small-cap reporting issuers (`https://www.newsfilecorp.com/rss/all`).
  - **Cision / CNW Canada Newswire RSS:** Primary dissemination for TSX large/mid-caps (`https://www.newswire.ca/rss/`).
  - **GlobeNewswire Canada Feeds:** (`https://www.globenewswire.com/NewsRoom/Rss/Canada`).
- **Processing Rule:**
  1. Poll RSS every 10 minutes during market hours.
  2. Normalize issuer name and map to TSX symbol via local company master table.
  3. Hash `MD5(normalized_headline + publication_date)` to guarantee zero duplicate processing.
  4. Pass headline and first 300 words to Groq Free LLM for classification.

### 8.5 Groq & Gemini Free Inference Routing Contract
- **Groq Free LPU:**
  - Base URL: `https://api.groq.com/openai/v1` (uses standard `openai` Python SDK).
  - Model: `llama-3.3-70b-versatile` (or `qwen/qwen-2.5-32b`).
  - Rate Limits: 30 requests/minute, 6,000 tokens/minute, 1,000 requests/day.
  - Temperature: `0.0` (strict deterministic JSON extraction).
- **Google Gemini API Free Tier (Fallback & Synthesis):**
  - Model: `gemini-2.5-flash` or `gemini-3.1-flash-lite`.
  - Rate Limits: 10–15 requests/minute, 1,000–1,500 requests/day.
  - Context Window: 1M tokens (allows feeding full 10-Q Item 2 MD&A sections in one prompt).
- **Enforced JSON Output Schema for AI News & Explanation:**
  ```json
  {
    "symbol": "SU.TO",
    "is_material": true,
    "event_category": "EARNINGS_RELEASE",
    "sentiment_score": 0.65,
    "horizon": "MEDIUM_TERM",
    "key_factual_claims": [
      "Q3 Free Cash Flow increased 18% YoY to C$2.1B",
      "Net debt reduced below C$8.0B trigger target"
    ],
    "identified_risks": [
      "Downstream refinery maintenance turnaround scheduled for Q4"
    ]
  }
  ```

### 8.6 Oracle Cloud Always Free Provisioning Runbook (ARM Ampere A1)
To ensure permanent uptime without capacity cancellation:
1. **Account Setup:** Register for Oracle Cloud Free Tier.
2. **PAYG Upgrade (Crucial Trick):** Convert the tenancy to "Pay As You Go" (requires credit card pre-auth of ~$100 which is immediately refunded).
   - *Why:* Pure Always-Free tenancies face automated idle-VM reclamation if CPU falls below 20%. PAYG accounts get the **exact same free tier resources permanently free** (3,000 OCPU-hours and 18,000 GB-hours/month = 4 OCPUs / 24 GB RAM 24/7/365) and are **exempt from idle reclamation**.
   - Set a hard budget alert in the OCI billing console of **$0.01/month**.
3. **Compute Shape Allocation:**
   - VM Shape: `VM.Standard.A1.Flex`
   - OCPUs: 2 or 4 (Ampere ARM64)
   - Memory: 12 GB or 24 GB
   - Boot Volume: 150 GB (under the 200 GB free limit)
   - OS: Ubuntu 24.04 LTS (aarch64)
4. **Software Stack:**
   - Docker + Docker Compose
   - PostgreSQL 16 with TimescaleDB (`timescale/timescaledb:latest-pg16` has official ARM64 support)
   - Redis 7 (alpine, ARM64)
   - Python 3.12/3.13 venv with FastAPI & Celery

### 8.7 Cloudflare Pages + R2 Deployment Runbook
1. **Frontend Repository:** Hosted on GitHub (Private repository).
2. **Cloudflare Pages:** Connect directly to GitHub repo.
   - Build command: `npm run build`
   - Framework preset: `Next.js (Static Export / Edge)`
   - Cost: **$0.00** (unlimited requests, custom domain, SSL).
3. **Daily Data Push via Wrangler CLI:**
   ```bash
   # Daily at 16:45 ET, backend runner dumps static JSON snapshots
   wrangler r2 object put marketintel-data/daily/latest_scores.json --file=./scores.json
   wrangler r2 object put marketintel-data/daily/regime_status.json --file=./regime.json
   ```
   Cloudflare Pages frontend fetches `/api/latest_scores.json` directly from R2 via Cloudflare's internal high-speed fabric with **zero egress fees**.

