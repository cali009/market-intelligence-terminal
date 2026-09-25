# 01 — Product Vision, Features, UX, Monetization, Compliance, Brutal Review

> Part of the US + Canada Market Intelligence Platform design set. See [`../README.md`](../README.md) for the index and approval gate.

---

## 1. PRODUCT VISION

### 1.1 One-line positioning
**A transparent, dual-market (US + Canada) market-intelligence and decision-support workstation that shows self-directed investors *what changed, why it matters, what the evidence is, what would invalidate the thesis, and what the risk is* — with the data lineage exposed at every step.**

### 1.2 What it is
- An **evidence workstation**: unstructured news + filings + structured price/fundamental/macro data → normalized, deduplicated, scored, explained.
- A **risk-first decision-support tool**: every signal ships with an entry zone, invalidation, stop, R:R, position size, and a falsifiable thesis.
- A **research platform with an honest scoreboard**: every strategy that generates a signal has a walk-forward backtest, a live paper-trading record, and published hit-rate/expectancy with sample sizes.
- A **dual-market specialist**: genuine coverage of TSX, TSXV, and (where reliable) CSE, alongside NYSE/NASDAQ/AMEX — including cross-border realities that US-only tools ignore (CAD/USD exposure, interlisted names, sector-weight divergence, different filing regimes).

### 1.3 What it explicitly is NOT
| Not this | Why not |
|---|---|
| A profit-guarantee or "AI predicts the next move" product | Unfalsifiable, non-compliant marketing risk, and empirically unsupportable |
| Personalised financial advice | Would require registration (NI 31-103 in Canada; Advisers Act/RIA or BD in the US) and suitability/KYC obligations |
| An automated execution system | Turns the product into trading/dealer activity; 12-phase scope explicitly excludes execution |
| A high-frequency or intraday scalping tool | Retail latency + data entitlements make this a losing proposition; the realistic edge is minutes-to-days |
| A data terminal clone | Competing with Bloomberg on data breadth is not winnable; competing on *Canada depth + explanation quality + honest track record* is |

### 1.4 Target users (in priority order)
1. **Self-directed retail investors (Canada + US)** trading their own accounts, C$5K–C$500K, holding days-to-months, who already use a broker app + TradingView but lack systematic process. *Non-professional data entitlement class — the cheapest and largest segment.*
2. **Semi-professional swing traders** with disciplined rules who want scanners + alerts + risk tooling, and who will pay for workflow speed.
3. **Small RIAs / portfolio managers / family offices** (Year 2+): need audit-ready rationale, portfolio risk, and both markets. *Note: this segment flips data entitlements to Professional (~10–27× per-user exchange fees) and pulls the product toward personalized advice — a different compliance and cost profile, deliberately deferred.*
4. **Content/education creators and newsletter publishers** (licensing partner tier) — a distribution channel, not the core.

### 1.5 Product principles (non-negotiable, enforced in code review)
1. **No number without provenance.** Every datum carries `source`, `as_of`, `retrieved_at`, `freshness_status`, `confidence`. UI renders a badge when provenance is weak.
2. **Separate FACT / CALCULATION / INFERENCE / UNCERTAINTY** in every output (mandated by the Explanation Contract, see `03…` §6.6).
3. **LLMs never produce numbers.** Deterministic code computes; LLMs classify, extract, and summarise with citations to retrieved spans.
4. **Confidence is about evidence, not about outcome.** The score measures setup quality; confidence measures how much we trust the analysis. They are separate fields and are never multiplied into a pseudo-probability of profit.
5. **Every thesis has a falsifier.** A signal with no machine-checkable invalidation condition cannot be published.
6. **Backtest ≠ performance.** All hypothetical/backtested figures carry the required disclosures and are never presented as results achieved.
7. **Missing data is stated, never imputed silently.** If Canadian fundamentals for a TSXV name are absent, the Fundamental Score shows `N/A — insufficient data` and downstream weights renormalize with a visible warning — it does not default to a neutral 50.

---

## 2. COMPLETE FEATURE LIST

Legend: **M** = MVP (Phase 1) · **S** = Phase 2–6 (scanner/technical/fundamental/signal engines) · **L** = Phase 7–12 (backtest, paper, portfolio, alerts, advanced AI, scale)

### 2.1 Markets & coverage
| # | Feature | Phase |
|---|---|---|
| F-01 | US: NYSE, NASDAQ, AMEX equities; ETFs; REITs | M |
| F-02 | Canada: TSX, TSXV; ETFs; REITs | M |
| F-03 | CSE coverage where data is verifiable (flagged low-confidence otherwise) | S |
| F-04 | Indices: S&P 500, Nasdaq Composite/100, Dow, Russell 2000, VIX; S&P/TSX Composite, S&P/TSX 60, S&P/TSX SmallCap, TSXV Composite | M |
| F-05 | Pluggable market registry (add LSE/ASX/others later without schema change) | M (design) / L (activation) |
| F-06 | Dual-listed / interlisted analytics (TSX ↔ NYSE/NASDAQ basis, FX-adjusted performance) | S |

### 2.2 Market data (unified pipeline)
| # | Feature | Phase |
|---|---|---|
| F-10 | Daily OHLCV, adjusted & unadjusted, split/dividend-correct corporate actions | M |
| F-11 | Intraday bars: 1m, 5m, 15m, 1h (delayed in MVP; real-time only in paid entitlement tier) | M (EOD+1h) / S (1m,5m) |
| F-12 | Weekly & monthly aggregation | M |
| F-13 | Pre-market / after-hours where the licence permits | S |
| F-14 | Volume, relative volume (RVOL vs 20-day average same-time-of-day), dollar volume | M |
| F-15 | Market cap, float, shares outstanding (PIT-correct) | S |
| F-16 | Bid/ask, spread, NBBO-quality flags (US); TMX best-bid/offer where licensed (CA) | L |
| F-17 | Volatility (realized, ATR%, Bollinger width percentile), beta vs local + US benchmark | M |
| F-18 | 52-week high/low, distance-to-high, gap detection & classification | M |
| F-19 | Short interest / days-to-cover (US semi-monthly; CA where available) | S |
| F-20 | Options-implied vol & skew (US only, informational; never used as a signal in MVP) | L |

### 2.3 Technical analysis
| # | Feature | Phase |
|---|---|---|
| F-30 | Indicators: SMA, EMA, VWAP (session & anchored), RSI, MACD, Bollinger, ATR, ADX/DMI, Stochastic, OBV, CMF, Keltner | S |
| F-31 | Support/resistance detection (pivot clustering + volume-confirmed levels), breakouts, breakdowns | S |
| F-32 | Volume profile (POC, value area, HVN/LVN) for intraday + daily | S |
| F-33 | Trend structure classification (HH/HL vs LH/LL, swing-count) and MA-crossover state machine | S |
| F-34 | Relative strength vs sector index, market index, and cross-market (US vs CA) | S |
| F-35 | Multi-timeframe alignment matrix (1m→monthly) rendered as one panel | S |
| F-36 | **Composite scoring, never single-indicator signals** (see `03…` §3) | S |

### 2.4 Fundamentals
| # | Feature | Phase |
|---|---|---|
| F-40 | Revenue, revenue growth (YoY, QoQ, 3Y CAGR, TTM) | S |
| F-41 | EPS, EPS growth, surprise history vs consensus (US: analyst data licence required) | S |
| F-42 | FCF, FCF margin, cash conversion, accruals ratio (earnings-quality proxy) | S |
| F-43 | Gross/operating/net margins, trend and peer-relative | S |
| F-44 | ROE, ROIC (with WACC spread), ROCE | S |
| F-45 | Debt: total, net, D/E, interest coverage, maturity wall | S |
| F-46 | Valuation: P/E, fwd P/E, PEG, EV/EBITDA, P/S, P/B, FCF yield, dividend yield | S |
| F-47 | Dividend: yield, growth streak, payout ratio, coverage, safety score | S |
| F-48 | Analyst estimates & revisions (US licensed; Canada sparse → flagged) | L |
| F-49 | Insider transactions (EDGAR Form 4 = high quality; SEDI = licensed/gated) | S |
| F-50 | Point-in-time correctness: every fundamental joined on *filing date*, never fiscal period end | M (design) / S (enforcement) |

### 2.5 News, filings & events
| # | Feature | Phase |
|---|---|---|
| F-60 | Ingestion: newswires, press releases, EDGAR (8-K, 10-Q/K, S-1, 13D/G, Form 4), SEDAR+ (via licensed vendor), transcripts where licensed | L (full) |
| F-61 | Deduplication (simhash + embedding + entity/time clustering) so one story from 14 outlets counts once | L |
| F-62 | Entity linking: ticker/CIK/issuer → company → sector → index | L |
| F-63 | Classification: sentiment (pos/neg/neutral/uncertain) + category + materiality + expected horizon (immediate / short / medium / long) | L |
| F-64 | Novelty & priced-in estimation: how much of the move already happened, RVOL at publication, peer-spillover check | L |
| F-65 | Event calendar: earnings dates, dividend ex-dates, splits, lockups, index rebalances, macro prints | S |
| F-66 | Event taxonomies: M&A, capital raises, guidance, management change, litigation, regulatory, distress | L |
| F-67 | News → price linkage backtest (does this category actually predict drift? which categories have alpha and which don't?) | L |

### 2.6 Macro & regime
| # | Feature | Phase |
|---|---|---|
| F-70 | Rates: Fed funds & path, BoC policy rate, 2/5/10/30Y UST + GoC, real yields | S |
| F-71 | Yield curve: 2s10s, 3m10y, inversion state, Canada–US spread (drives CAD) | S |
| F-72 | Inflation: US CPI/PCE, Canada CPI (BoC measures), shelter vs core split | S |
| F-73 | Labour: US NFP, unemployment, claims; Canada LFS, unemployment | S |
| F-74 | Growth: GDP nowcasts (NY Fed/Atlanta), ISM, Canadian GDP, retail sales | S |
| F-75 | FX: CAD/USD spot + drivers (oil, rate spread, risk appetite) | S |
| F-76 | Commodities: WTI, gold, silver, copper, natural gas (critical for TSX materials/energy) | S |
| F-77 | Risk: VIX (spot + term structure), MOVE, credit spreads (HY/IG OAS), TED/CP stress | S |
| F-78 | Sector performance & rotation map (both markets, GICS) | S |
| F-79 | Geopolitics: structured event tagging only; **no LLM speculation about consequences** | L |
| F-80 | Transmission model: macro factor → sector sensitivity matrix (empirically estimated betas, not opinions) | S |

### 2.7 Regime detection
| # | Feature | Phase |
|---|---|---|
| F-85 | 6 regimes: Strong Bull, Weak Bull, Sideways/Consolidation, High-Volatility, Bearish, Crisis/Stress | S |
| F-86 | Multi-input (trend, breadth, vol, credit, rates, commodity, FX, dispersion) — no single-indicator labels | S |
| F-87 | Real-time **filtered** (causal) probabilities — never smoothed retro-fitted labels | S |
| F-88 | Plain-language explanation of the classification + which inputs disagreed | S |
| F-89 | Regime-conditional strategy gating (a strategy that fails in High-Vol is disabled, not silently degraded) | S |
| F-90 | Regime-by-market: US and Canada classified separately (they diverge; e.g., energy-led TSX vs tech-led S&P) | S |

### 2.8 Scoring, signals, exits
| # | Feature | Phase |
|---|---|---|
| F-95 | 8 sub-scores (Technical, Fundamental, Momentum, News/Sentiment, Regime, Sector Strength, Valuation, Risk) 0–100 | S |
| F-96 | Composite Opportunity Score + **confidence tier (A/B/C/D)** reported separately | S |
| F-97 | Full attribution UI: every score decomposes to contributing factors with weights and raw inputs | S |
| F-98 | Entry engine: entry zone, preferred + alternative entry, stop, 3 targets, R:R, holding-period estimate, strength, confidence, invalidation predicates | S |
| F-99 | Exit engine: HOLD / WATCH / REDUCE / EXIT / EMERGENCY RISK with explicit triggering evidence | L |
| F-100 | Thesis tracker: why you (or the system) entered, what changed since, is the thesis still intact | L |

### 2.9 Scanners
14 scanners (Breakout, Pullback, Momentum, Oversold reversal, Undervalued, Earnings, Dividend, Unusual volume, Small/mid-cap, Defensive, Sector rotation, Canadian, US, Best-setups-today) with a shared filter language: market, exchange, sector, cap band, price, risk, horizon, liquidity (ADV$), dividend, growth, valuation, technical setup, data-quality floor. Definitions in `03…` §8.

### 2.10 Backtesting, paper trading, portfolio
| # | Feature | Phase |
|---|---|---|
| F-110 | Event-driven backtester with PIT data, survivorship-free universes, transaction costs, slippage, borrow costs, FX costs | L |
| F-111 | Metrics: CAGR, Sharpe, Sortino, max DD, Calmar, win rate, avg win/loss, profit factor, expectancy, trades, avg hold, longest losing streak, turnover, capacity | L |
| F-112 | Anti-overfitting suite: walk-forward, purged K-fold + embargo, deflated Sharpe, PBO, parameter-sensitivity heatmaps, cost-sensitivity | L |
| F-113 | Data partitioning: TRAIN / VALIDATION / TEST / LIVE, with a "test set touched once" policy enforced by tooling | L |
| F-114 | Paper-trading environment: virtual portfolio, real signal stream, auto-fill model, live scoreboard vs backtest expectation | L |
| F-115 | Portfolio intelligence: import/holdings, cost basis, unrealized P/L, weight, correlation cluster, risk contribution, per-holding HOLD/WATCH/REDUCE/EXIT | L |
| F-116 | Portfolio-level: beta (local + US), sector concentration, factor exposure, FX exposure, stress scenarios, drawdown circuit breaker | L |

### 2.11 Alerts, platform, ops
| # | Feature | Phase |
|---|---|---|
| F-120 | Alert types: entry, exit, stop, target, breakout, breakdown, major news, earnings, unusual volume, large move, fundamental deterioration, macro risk, portfolio risk | L |
| F-121 | Channels: in-app (MVP-ready), email (S), web push (L), mobile push (L), SMS (only as double-opt-in transactional, Phase 10+, jurisdiction-gated) | L |
| F-122 | Alert hygiene: severity, hysteresis, cooldowns, dedupe, per-user budget, quiet hours | L |
| F-123 | Data-quality console: freshness per feed, missing-data ratio, provider incidents, DQ alerts | S |
| F-124 | Data lineage drill-down: click any number → source, timestamp, transformations, licences | M |
| F-125 | Audit log (append-only, hash-chained) of every signal generated + every alert delivered | L |
| F-126 | Admin: entitlement vetting, professional/non-professional classification, exchange audit reporting | S |

---

## 3. DASHBOARD / UI STRUCTURE

Design reference: Bloomberg density + TradingView familiarity, without the "casino" aesthetics. Dark-first, high-contrast, tabular numerals, keyboard-driven (`/` search, `⌘K` command palette, `g d` = dashboard, `g s` = scanner).

### 3.1 Information architecture

```
/app
├── Dashboard (Market Overview)        ← single-screen cockpit
├── Markets
│   ├── US Overview   ├── Canadian Overview
│   ├── Sectors & Rotation            └── Indices, FX, Commodities, Rates, VIX
├── Scanner            ← 14 saved scanners + custom filter builder
├── Signals            ← today's LONG/EXIT candidates, with confidence tiers
├── Symbol/[TICKER]    ← the stock page (§3.3)
├── News               ← deduped, entity-linked, materiality-sorted stream
├── Portfolio          ← holdings, risk, per-holding verdicts
├── Paper Trading      ← virtual book, scoreboard vs backtest
├── Backtests          ← strategy registry, results, disclosures
├── Alerts             ← rules, history, delivery health
└── Settings           ← risk profile, entitlements, data sources, consent
```

### 3.2 Main dashboard panels
| Panel | Contents | Data latency budget |
|---|---|---|
| **Regime banner** | US + CA regime with filtered probability bars, 3 bullet reasons, disagreement flags | 5 min |
| **Market overview strip** | Index, change, breadth (adv/dec), VIX + term slope, 10Y UST & GoC, CAD/USD, WTI, gold | 15 min |
| **US market / Canadian market tiles** | Sector heatmap, top movers by dollar volume, RVOL leaders, 52w hi/lo counts | 15 min |
| **Top opportunities** | Ranked cards: ticker, setup tag, score, confidence tier, entry zone, R:R, key reason, top risk | 5 min |
| **Top risks** | Deteriorating names in *your* portfolio + high-news-risk holdings + crowded/exhausted setups | 5 min |
| **Breakouts / breakdowns** | Failed vs confirmed breakouts with RVOL confirmation | EOD→15 min |
| **News rail** | Deduped events, sentiment + materiality + horizon chips, "already priced?" indicator | 1–5 min (licence-dependent) |
| **Sector rotation map** | Relative-strength quadrant (leading/improving/lagging/weakening) for US & CA | Daily |
| **Portfolio & alerts** | Exposure, P/L, risk contribution, active alerts with severity | 1 min (positions) |

### 3.3 Stock page (the core artefact)
Order matters — decision-relevant content above the fold:

1. **Header**: price, % change, data badge (`LIVE 15m delayed` / `EOD` / `STALE 42m`), currency, exchange, cap band, liquidity grade.
2. **Verdict bar**: current stance — `NO SETUP / WATCH / POTENTIAL LONG / HOLD / REDUCE / EXIT` — plus score ring, confidence tier, and the *one-sentence* thesis.
3. **AI Explanation block** (§`03…` §6.6), structured as: **Facts → Calculations → Inferences → Uncertainties**, then Bull case / Bear case / Key catalysts / Key risks / Invalidation.
4. **Trade plan**: entry zone (preferred + alternative), stop, T1/T2/T3, R:R, expected holding period, position-size calculator bound to the user's risk profile.
5. **Chart**: candles + overlays, multi-timeframe switcher, VWAP, volume profile, support/resistance bands, signal markers with historical signal dots.
6. **Multi-timeframe technical matrix**: rows = timeframes, cols = trend/momentum/vol/volume verdicts.
7. **Score decomposition**: 8 sub-scores with expandable factor lists (this is where transparency lives).
8. **Fundamentals**: PIT-aware tables with "as reported on <filing date>" tooltips; trend sparklines; peer-relative percentile bars; **explicit `N/A — no reliable data` cells**.
9. **News & events**: deduped timeline with event-type chips, horizon, and "market reaction so far" (return since publication, RVOL at publication).
10. **Risk panel**: ATR%, gap history, liquidity (ADV$), days-to-liquidate at your position size, earnings blackout warning, borrow difficulty (shorts).
11. **Historical signals & backtest**: this setup's past occurrences on this ticker with outcomes, plus the strategy's walk-forward stats *with sample size and disclosure block*.
12. **Data lineage**: per-dataset source, timestamp, method, licence note.

### 3.4 UX patterns that reinforce trust
| Pattern | Implementation |
|---|---|
| **Provenance badge on every number** | Hover/click → popover: source, as_of, retrieved_at, freshness, confidence, licence |
| **Confidence ≠ score** | Distinct visual language: score = bar/ring; confidence = A/B/C/D chip with tooltip explaining evidence basis |
| **N/A over fake neutrality** | Missing data renders as an explicit dashed `N/A` block, never a neutral 50 |
| **Disagreement surfacing** | When sub-scores conflict (e.g., 88 technical / 32 fundamental), the UI *foregrounds* the conflict rather than averaging it away |
| **"Why now" timestamp** | Every signal shows generation time + next scheduled recompute + what specific input would change it |
| **Backtest disclosure block** | Non-dismissible on first view; includes period, universe, costs, gross *and* net, and "hypothetical — not achieved in an actual account" |
| **Alert budget meter** | Users see how many alerts they'll get today; prevents notification fatigue and churn |
| **Plain-language mode** | Toggle that swaps jargon ("ADX 31") for explanation ("trend is strong and getting stronger") with the metric still available |

### 3.5 UI anti-patterns (explicitly banned)
Red/green flashing on price change; "AI predicts +14%"; win-rate without sample size; leaderboards of "top picks" that hide losses; auto-generated "conviction %" derived from arbitrary weights; infinite-scroll news without dedupe; any number rendered without a data badge; and (critically) any backtest equity curve displayed without its fees/slippage and survivorship notes.

---

## 4. COMPETITIVE DIFFERENTIATION

Be honest about the starting position: "AI stock picks" is a saturated, low-trust category with a graveyard of failed products (IEX Cloud's shutdown shows even infrastructure can vanish; hundreds of "AI trading" apps churn users on hype). Differentiation therefore cannot be "AI", and cannot be "we have data".

Where a real moat can be built, in priority order:

| # | Differentiator | Why defensible |
|---|---|---|
| D-1 | **Canada done seriously** | US-only tools ignore TSX/TSXV, CAD/USD effects, SEDAR+/SEDI filings, oil-and-financials sector structure, and Canadian tax/FX reality. Vendoring Canadian data correctly is operationally painful — the pain *is* the moat |
| D-2 | **Data-confidence transparency** | Displaying freshness/confidence/lineage per datum is rare in retail tools; it is the correct answer to "never fabricate", and it builds the trust that converts to paid |
| D-3 | **Falsifiability + live scoreboard** | Published pre-registered invalidation conditions and a public live-vs-backtest track record. Almost nobody does this because it invites accountability — which is exactly why it differentiates |
| D-4 | **Explanation contract (FACT/CALC/INFERENCE/UNCERTAINTY)** | Forces the product to be auditable by design and is a barrier to copy-cat quick builds |
| D-5 | **Risk-first workflow** | Position sizing, portfolio risk contribution, correlation clustering, FX exposure for Canadian investors holding US names — features institutions expect that retail tools skip |
| D-6 | **Cross-border analytics** | Interlisted basis, USD-vs-CAD-hedged performance attribution, dual-listed arbitrage-ish monitoring, US-vs-CA sector rotation divergence. Structurally unavailable from single-market tools |
| D-7 | **Filings-native intelligence** | Most retail "news AI" reads headlines. Reading full 8-K/10-Q/MD&A/Risk Factors/AIF (Canada) with citation spans and diffing against the prior period is where non-obvious, slower-alpha information lives |
| D-8 | **Honest cost/entitlement architecture** | Cheap, reliable non-real-time product for the many; real-time as a paid entitlement pass-through for the few. Competitors who promise "free real-time" are either unlicensed (legal risk) or unprofitable |
| D-9 | **Alert discipline** | Few, high-precision, well-explained alerts vs. notification spam |

**What I would *not* claim as differentiation:** LLM-based sentiment (commoditized), technical indicators (commoditized), "AI-powered" anything (meaningless), backtests (everyone has them; almost everyone's are biased).

---

## 5. MONETIZATION MODEL

### 5.1 Tiers
Prices are indicative CAD/USD and must be re-based against realized data-entitlement costs per cohort (see `02…` §11).

| | **Free** | **Pro** | **Premium** | **Professional** |
|---|---|---|---|---|
| Price (CAD/mo, annual) | C$0 | C$29 | C$79 | C$249+ |
| Price (USD/mo, annual) | US$0 | US$22 | US$59 | US$189+ |
| Data latency | EOD + daily digest | EOD + 15-min delayed (US) | + 15-min delayed (CA), intraday scans | Real-time where licensed, per-seat entitlement |
| Watchlists | 2 (25 names) | 10 | Unlimited | Unlimited + team sharing |
| Scanners | 3 preset, daily | All 14, daily+intraday | All + custom filters | All + API export |
| Signals | 3/day, delayed 24h | Full stream, scored | + entry/exit plans, risk sizing | + portfolio-level, exportable rationale pack |
| News engine | Headlines only | + sentiment/materiality chips | + full event intelligence & price-reaction | + filings deep-read (10-K/AIF diffs) |
| Backtests | View only, 1 strategy | View all | Run custom (quota) | Run + export raw results |
| Paper trading | 1 portfolio | 5 | Unlimited | Unlimited + API |
| Alerts | Daily email digest | Email + in-app real-time | + push, unlimited rules | + SMS (opt-in), webhooks, Slack |
| Portfolio intelligence | 1 portfolio, basic | + risk contribution, correlation | + stress tests, factor exposure | + client-ready reports, multi-account |
| API access | — | — | Read-only, 1K calls/day | 50K calls/day + bulk |
| Support | Community | Email (48h) | Priority (12h) | Named contact, SLA |

### 5.2 Gating philosophy
Gate on **data entitlements, compute intensity, and workflow depth** — never on "unlocking the AI". The AI explanation layer is always visible (it is the trust builder and the acquisition engine); scarcity lives in real-time data, alert volume, custom backtests, and API access.

### 5.3 Add-ons & secondary revenue
| Stream | Notes |
|---|---|
| Real-time data pass-through (US ~US$15–20/mo; CA ~C$10–15/mo) | Priced above the entitlement cost with explicit pass-through labeling; only viable with verified non-professional status |
| Professional seat licence | Priced to cover professional entitlement fees (often US$80+ per user/month for depth data) |
| Data API (Canada coverage) | Genuinely scarce: a TSX/TSXV-aware market-data + fundamentals API. Requires redistribution rights in the vendor agreements — do not assume, negotiate explicitly |
| White-label scanner/news widget for brokers, media, newsletters | High-margin, low-support; compliance review per partner |
| Education/community (research reports, methodology courses) | Reinforces "research & educational" positioning |
| Affiliate/referral to discount brokers | **Disclose prominently.** Recommending securities while receiving compensation for referrals is a conflict-of-interest and registration-adjacent activity — keep affiliate links out of any security-specific page, or disclose in-line per CSA/CIRO interest-disclosure expectations |

### 5.4 Unit economics guardrails
- Free tier must cost < C$0.35/user/month all-in (LLM + compute + email). Achieved by caching, batch scoring (shared market-wide analysis computed once for everyone), and EOD data only.
- Per-user LLM cost must be metered and capped; deep multi-document analysis is reserved for paid tiers with quotas.
- Gross margin target: ≥ 75% for Free→Premium, ≥ 45% for the real-time tier (entitlement drag), ≥ 70% for Professional (higher price absorbs pro fees).

---

## 6. COMPLIANCE CONSIDERATIONS (US + CANADA)

> **This is engineering guidance, not legal advice.** Retain Canadian and US securities counsel before launch; the classification decision in §6.2 is the single highest-leverage legal decision in the project.

### 6.1 The five activity classes — keep them distinct in product, marketing, and code
| Class | What it is | Regulatory consequence | Design rule |
|---|---|---|---|
| **Market information** | Raw prices, volumes, filings, calendars | Exchange/vendor licence obligations; delayed vs real-time entitlements; audit rights | Never redistribute without a license; log entitlements; per-user fee reporting |
| **Research / general information** | Impersonal, non-tailored analysis, screening, scoring, education | Not necessarily "advice" if impersonal and no business purpose of advising | The default posture for this product |
| **Educational information** | Teaching method, "how RSI works" | Generally lower risk | Keep as separate content type, labelled |
| **Personalized financial advice** | Recommendations suited to a person's circumstances | **Registration required** (CSA/CIRO in Canada; SEC/state RIA or BD in the US); suitability/KYC/KYP; Reg BI for BDs | **Product must never do this.** No "you should buy", no portfolio-specific allocation built from user's personal data |
| **Automated trading / order generation** | Placing or generating orders | Dealer/broker-dealer registration; exchange member rules (CIRO UMIR in Canada); order-generating analysis programs trigger *higher* market-data licence fees | **Explicitly out of scope for all 12 phases** |

### 6.2 The Canadian "general advice" exemption (critical finding)
CSA/CIRO **Staff Notice 31-369** (Dec 2025) confirms the practical position: if advice is **not tailored to the needs of the individual receiving it**, the provider may rely on the "general advice" exemption, **but must disclose any financial or other interest in a security mentioned**. Two warnings from the notice that directly bind this product:
- **Registration cannot be avoided by a disclaimer.** The test is whether you are in the business of advising (factors: holding out, intermediating, compensation, regularity). A "not advice" footer does not cure a personalized recommendation.
- **If you use AI to provide advice, you may be held responsible for what the AI does as if you had done it directly.**

Design consequences (mandatory):
1. All output framed as **"analytical output / potential setup / research finding"**, never "you should buy X".
2. **No tailoring.** No user-specific position advice, no "given your income, buy X". Portfolio features show *diagnostics and risk facts* (weights, correlation, concentration, risk contribution) plus generic stance labels — the user remains the decision-maker.
3. **Interest disclosure** on every symbol page (do we hold it? affiliate relationship? sponsor?).
4. **Compensation model check**: subscription for research ≠ advising, but *solicitation* and *personalized* services tip into registration. Get an opinion letter on file before monetizing.

### 6.3 United States
- **Advisers Act publisher exclusion**: bona fide publishers of impersonal, general commentary have historically been excluded from the definition of investment adviser (cf. *Lowe v. SEC*, 472 U.S. 181 (1985)) — **but** the exclusion erodes if the service becomes personalized, if it involves "entanglement" with clients' affairs, or if it directs trading. Do not rely on the exclusion as a compliance strategy; rely on *staying impersonal*.
- **SEC AI posture (2026)**: the Predictive Data Analytics proposal was **withdrawn in June 2025** (no AI-specific rule in force); oversight now runs through existing antifraud/disclosure rules, the **CETU** enforcement unit (created Feb 2025 for AI/cyber misconduct), and **FY2026 exam priorities** demanding that firms claiming AI show it genuinely drives decisions — i.e., **no "AI-washing"**.
- **If you ever become an RIA**: SEC **Marketing Rule 206(4)-1** governs advertising — backtested/model performance is *hypothetical performance* requiring policies, relevance to audience, disclosure of criteria/assumptions/risks/limitations, net-of-fee presentation with gross, and records retention. Design the backtest UI to satisfy this standard *now* so the capability is there if registration ever happens.
- **If you ever become a BD**: FINRA rules, Reg BI, FINRA's 2026 GenAI report expectations (human-in-the-loop, prompt/output logging as books-and-records, vendor diligence, agent permissioning).
- **Other US obligations**: EDGAR fair-access (declared User-Agent, ≤10 req/s); SOC 2 (commercial expectation, not law); state privacy laws (CCPA/CPRA + others) for personal data; CAN-SPAM; **TCPA/CTIA** for SMS (prior express written consent for marketing; transactional-only alert SMS with strict content isolation; 10DLC registration; quiet hours; state mini-TCPA traps e.g. Florida/Florida-style limits, Virginia 10-year opt-out retention).
- **Data**: US real-time display data requires exchange entitlements; Nasdaq sets non-professional (US$1/mo Nasdaq Basic; US$15/mo TotalView) vs professional (US$27.30–89.50/mo) rates with distributor fees (US$1,560–2,170/mo) and severe non-display fees for programmatic use at scale.

### 6.4 Canada (beyond registration)
- **Exchanges/data**: TMX Datalinx is the licensor of TSX/TSXV/Alpha data; real-time redistribution requires an executed agreement, per-subscriber fees, and audit rights. Nasdaq Canada (CX2) has its own schedule. **CSE** data availability is limited — flag as "best-effort, low confidence" rather than pretending coverage.
- **CASL**: express or implied consent required for *any* commercial electronic message (email and SMS), sender identification (name + mailing address + contact), working unsubscribe honored within 10 business days; implied consent expires (2 years post-purchase, 6 months post-inquiry); **purchased lists are prohibited**; penalties up to C$10M for businesses. Build consent records (type, timestamp, source, IP) from day one.
- **Privacy**: PIPEDA (federal) + **Quebec Law 25** (privacy officer, consent, breach notification, data-portability), Alberta/BC PIPA. Store Canadian PII in Canada (`ca-central-1`) to simplify; maintain a privacy policy and DSAR workflow.
- **Language**: Quebec's Charter of the French Language (Bill 96 amendments) imposes French-language requirements for commercial communications/contracts directed at Quebec consumers — **verify with counsel if you market to Quebec** and budget localization.
- **Consumer protection / misleading advertising**: Canada's Competition Act (and recent amendments) plus provincial consumer protection create liability for misleading performance claims even outside securities law. This is why "no guaranteed profits" is also a *product* rule, not just an ethics rule.
- **SEDAR+/SEDI terms**: SEDAR+ offers no public bulk API; scraping is subject to terms and bot protection; SEDI bulk data is available for resale through CDS under licence. Use licensed redistribution, not scraping, for anything commercial.

### 6.5 Built-in compliance controls (engineering)
| Control | Implementation |
|---|---|
| Impersonal-output linter | CI test that fails any user-facing template containing personalization patterns ("you should", "for your portfolio, buy", "recommended for you") |
| Disclosure injection | Every signal payload includes `interest_disclosure` and `disclaimer_version`; API rejects payloads missing either |
| Hypothetical-performance block | Component that must wrap any backtest figure; unit test asserts presence of period/universe/cost/gross-net/disclaimer fields |
| Consent ledger | Immutable table: consent_type, timestamp, source_url, IP, locale, message_type (transactional/marketing) |
| Entitlement gate | Middleware that checks user's market-data class (non-pro/pro) before serving real-time fields; denial logs the reason |
| AI disclosure | Every AI-generated text block carries a model ID + prompt version + "AI-generated summary, verify source" chip with link to the primary source span |
| Marketing claim scanner | Pre-publication lint on marketing copy for banned claims (“guaranteed”, “risk-free”, “predicts”, “win rate X%” without context) |

---

## 7. BRUTAL REVIEW

You asked me to be critical rather than agreeable. Here is the honest assessment.

### 7.1 Where this idea can fail outright
1. **The data-licensing trap is the most likely killer.** Canadian real-time data has per-user fees, distribution fees, analysis-program fees, and audit rights. If you build a product that *appears* to give real-time TSX data without agreements, you are exposed to takedown, back-fees, and vendor termination. If you buy the rights properly, per-user economics at scale ($3/non-pro/month for TSX L1, ~$8.30/pro, plus distribution and "analysis program" fees) can exceed a C$29 subscription's margin. **Mitigation:** delayed/EOD-first architecture; real-time sold as a cost-plus add-on; the TMX non-professional cap program is the scaling relief valve (a flat monthly cap per the published schedule, worth negotiating early).
2. **"AI picks stocks" is a saturated, low-trust category.** Distribution, not algorithms, will kill you — CAC in retail fintech is brutal and churn correlates with drawdowns, meaning you lose users exactly when you most need revenue.
3. **Crowded trade in the bull case: the backtests are the easy part.** Anyone can produce a 1.8 Sharpe backtest of an RSI+MACD combo. If your differentiator is the backtest, you have no differentiator.
4. **Support burden is underestimated.** Decision-support products generate "should I sell?" tickets. Those tickets are, functionally, personalized advice requests. **Mitigation:** canned, non-personalized responses; clear scope; no 1:1 advice channel; escalation policy.
5. **B2B pull (RIAs/advisers) will arrive and it changes everything** (professional data fees, registration, suitability, audit). Decide deliberately whether to accept that pull; do not drift into it.

### 7.2 Where AI can hallucinate (and the specific guards)
| Hallucination venue | Guard |
|---|---|
| Inventing numbers, prices, or ratios | LLM never emits numbers; a post-processor verifies every numeral in generated text against the computed payload; mismatch → text rejected |
| Fabricating a filing or article that doesn't exist | Retrieval-only generation with mandatory citations to retrieved `document_id` + character span; unsupported sentences stripped by a citation validator |
| Inventing a CEO quote / paraphrase drift | Quotes only allowed verbatim from retrieved spans with span offsets; paraphrase must be labelled INFERENCE |
| Overconfident causal claims ("fell because of X") | Causal language banned; templates force "moved alongside / consensus attributions to X" phrasing with the source noted |
| Sentiment overreach on ambiguous items | Four-way output including **Uncertain**; the model is *scored on calibration*, and the eval set includes deliberately ambiguous headlines |
| Summarizing stale news as new | Freshness gate: any source older than the dedupe window is flagged as background context, not catalyst |
| Silent failure on Canadian filings (different taxonomy/format) | Canadian document parsers are separately evaluated; when extraction confidence is low → `UNCERTAINTY` block, not a guess |
| Prompt injection from ingested content | Untrusted text is never concatenated into instruction space; content is passed as data with strict delimiters; the model has no tools with side effects |

### 7.3 Where bad data produces false signals
| Failure | Example | Guard |
|---|---|---|
| Split/dividend adjustment errors | A 5:1 split unadjusted → "–80% crash" → spurious oversold signal | Corporate-action engine with reconciliation vs two sources; hard sanity gates (|daily move| > 60% triggers mandatory review before publication) |
| Stale quote carried forward | Illiquid TSXV name quoted on last trade from hours ago | Per-issue freshness check using trade recency + spread; liquidity grade gate prevents signals on stale/illiquid names |
| Symbol reuse / ticker change | Delisted ticker reused by a new issuer → history spliced | Permanent internal `security_id` (FIGI-style) with ticker history table; never key analytics on ticker alone |
| Restated fundamentals used as if original | Backtest gets today's restated numbers for a 2019 decision | Point-in-time fundamentals with `knowledge_date`; PIT enforcement tested in CI |
| Survivorship bias | Backtest universe = today's index members | Universe reconstructed as-of-date including delistings (requires survivorship-free source such as a PIT/ delisted-inclusive dataset) |
| Currency confusion | US price compared to CAD fundamental, or CAD earnings with USD EPS | Explicit currency tagging on every monetary field; unit tests on dual-listed names |
| Provider corrections without versioning | Vendor silently revises history | Immutable raw zone + bitemporal storage; revisions create a new version, never overwrite |
| Timezone/holiday errors | US holiday treated as low-volume signal day; TSX hours assumed = NYSE | Market-calendar service per venue (including early closes and TSX/TSXV differences) |

### 7.4 Where latency makes signals useless
- **Retail news latency is minutes, not microseconds.** For mega-caps, meaningful news is priced within ~seconds-to-minutes; a 15-minute-delayed feed means the "news catalyst" signal is a *context label*, not an entry trigger. Be honest in the UI: label news with "market has likely already moved — reaction measured".
- **Where latency does *not* matter much:** multi-day swing/position setups driven by trend structure, fundamentals, and regime. **That is where the product should live**, and it is also where the cost of data is lowest.
- **Where latency kills you:** intraday breakouts, earnings-day reaction trades, halt-resumption plays. Do not sell these as core features on delayed data. If real-time is added later, capacity/timing must be re-architected (streaming ingestion, per-user entitlement gating, sub-second alert fanout).
- **Alert fanout latency**: a 5-minute alert delay on a swing setup is fine; the same delay on a stop-loss alert is unacceptable *in principle* — so the product must state clearly that stop alerts are advisory notifications, not broker-side protection. **Always tell the user to place actual protective stops at their broker.**

### 7.5 Where backtesting misleads
- **Overfitting through search.** Run 5,000 parameter combos, keep the best, and you have a curve-fit, not a strategy. Guards: pre-registration, parameter *sensitivity* heatmaps (fragile = reject), deflated Sharpe ratio, PBO via combinatorially-symmetric cross-validation, and a holdout touched once.
- **Ignoring frictions.** Costs dominate high-turnover strategies. Model commission + half-spread + slippage + impact, plus borrow for shorts and FX for cross-currency trades — then stress costs ×2 and ×3 and report the strategy only if it survives.
- **Look-ahead via fundamentals & index membership** — the two most common fatal errors. Enforced via PIT joins and as-of universe reconstruction.
- **Regime dependency.** A momentum strategy backtested 2010–2021 looks great; 2022 and 2025 regimes break it. Always report per-regime performance and gate strategies by regime.
- **Survivorship in small caps.** TSXV/CSE delisting and cease-trade rates are high; a "small-cap momentum" backtest on surviving tickers can be wildly optimistic. Requires delisting-inclusive data and a delisting-return assumption (e.g., −60% to −100% with a documented rationale) for any Canadian small-cap strategy.
- **Multiple-testing/snooping via "we tried many universes"**: keep a research log of *every* experiment, not just the winners. Publish the log in-app (research transparency page).

### 7.6 What creates false confidence
Score precision (a "87/100") without an error bar; confidence tiers assigned by eye instead of by out-of-sample evidence; a single equity curve; "we beat the market in backtest" comparisons against the *wrong* benchmark (compare to a risk-matched, sector-adjusted, FX-adjusted benchmark); and smooth-looking composite scores that hide the fact that components disagree violently. **Counter-designs:** decile presentation where appropriate, confidence tiers defined by measurable evidence thresholds, benchmark hygiene (local currency and CAD-adjusted views for Canadian investors), and mandatory disagreement surfacing.

### 7.7 Regulatory risks
Already covered in §6. The three highest: (1) drifting into personalized advice; (2) unlicensed data redistribution; (3) misleading performance claims. Add a fourth, emerging: **AI disclosure expectations** — supply a model card, document prompt versions, log outputs, and be able to explain any signal months later (auditable by design). Both CIRO (2026 compliance report) and SEC (CETU, exam priorities) are converging on "if you use AI, you must be able to prove how it works and that you supervise it".

### 7.8 Features that are unnecessary (cut or defer)
| Cut | Why |
|---|---|
| Options flow / dark-pool prints | Expensive data, crowd-chasing signal, poor retail decision value |
| Social sentiment (Twitter/Reddit scraping) | Noisy, manipulation-prone, licensing murkiness; adds noise not edge |
| Crypto | Different market, different compliance, dilutes dual-market focus |
| Real-time tick streaming to web clients at scale | Enormous fansout + entitlement cost for negligible decision-support value |
| Automated execution / broker integration | Regulatory category change; not worth it |
| "AI chatbot that answers anything about stocks" | Hallucination surface with no decision framework; the structured panels are better |
| Deep-learning price forecasting | Empirically weak on daily bars, expensive to maintain, impossible to explain creditably |
| Portfolio auto-rebalancing recommendations | Steps into personalized advice |

### 7.9 What an experienced quant would reject in a naive version of this plan
1. Weighted-sum indicator scoring with hand-picked weights (arbitrary, unstable) → replace with **cross-sectional ranking + regime-conditional weights estimated from data, with stability constraints**.
2. Equal-weighting 8 scores into an "Opportunity Score" including a *Risk* score, which double-counts and inverts polarity (a high risk score isn't bullish) → **Risk must be a gate/penalty and a sizing input, never a positive contributor to opportunity**.
3. Regime labelling by fixed thresholds of VIX/200DMA → replace with a **state-space model (HMM/HSMM) on standardized multi-asset features with filtered probabilities and an explicit no-look-ahead test**.
4. Signals that fire every day on every name → **require a minimum evidence threshold; most days most names should produce "no setup"**. Scarcity is a feature.
5. Backtest Sharpe without turnover, capacity, and cost sensitivity → **reject any strategy whose edge disappears at 2× costs or whose capacity is below the target AUM**.
6. Any claimed "edge" from news sentiment on mega-caps → **measure it, publish it, and if it's zero, remove it from the score** (the platform should be willing to conclude its own components don't work).
7. Fundamental "scores" mixing valuation and quality into one number → **keep Valuation and Quality separate**, because cheap-and-bad and expensive-and-good are structurally different trades.

### 7.10 What an institutional investor would expect (if/when you sell to them)
Point-in-time data lineage; documented data-vendor contracts with redistribution rights; a research environment with versioned code and reproducible results; factor attribution and benchmark-relative reporting (IR, tracking error, turnover, capacity); explicit risk limits per strategy; kill-switch criteria; an auditable signal log; model cards and validation reports; segregation of research vs production code paths; backtest-vs-live drift monitoring; and a written change-control policy. Most retail competitors cannot produce any of these — this is a credible long-term moat, but it is *cost*, not differentiation, in Year 1.

### 7.11 What would make this product genuinely differentiated (summary)
Canadian depth + cross-border analytics + radical data transparency + falsifiable signals with a public live scoreboard + risk-first workflow for the self-directed investor. **Not** "AI picks".

### 7.12 Kill criteria (decide these now, not later)
- If, after Phase 3 (news intelligence), measured news-driven drift has no statistically meaningful expectancy after costs across a 2-year out-of-sample window → **remove the news signal from the composite** (keep it as context).
- If Phase 6 signals show < 45% hit rate with R:R ≥ 1.5 in paper trading over 3 months, or live-vs-backtest degradation > 40% of expectancy → **stop signal publication and re-derive** rather than shipping weaker signals.
- If per-user Canadian data entitlement cost (blended) exceeds 25% of blended ARPU by the time you reach 1,000 paying users → **re-negotiate, restructure to EOD-only for the base tier, or consider the cap program**; do not scale a negative-margin real-time tier.

---

## 8. DATA QUALITY & CONFIDENCE POLICY

### 8.1 Every data point carries a provenance envelope
```json
{
  "value": 214.37,
  "field": "close",
  "currency": "CAD",
  "effective_at": "2026-09-23T16:00:00-04:00",
  "knowledge_at": "2026-09-23T16:00:12-04:00",
  "retrieved_at": "2026-09-24T09:14:02-04:00",
  "source": { "provider": "TMX_DATALINX", "dataset": "TSX_EOD", "licence_tier": "delayed_15m" },
  "quality": {
    "status": "OK",
    "freshness_seconds": 45,
    "cross_check": { "vendor": "secondary_feed", "abs_diff_bps": 0.7, "verdict": "MATCH" },
    "method": "official_close"
  },
  "confidence": "HIGH"
}
```

### 8.2 DQ dimensions & rules
| Dimension | Rule | On failure |
|---|---|---|
| **Completeness** | Required fields/days present per market calendar | Fill from secondary source if permitted; else mark `MISSING` and degrade confidence |
| **Freshness** | Tiered SLOs: real-time < 5s (licensed), delayed < 5m after vendor delay, EOD by 20:00 ET, fundamentals by T+1 | UI badge turns amber/red; stale data excluded from intraday signals |
| **Accuracy** | Cross-vendor reconciliation (price tolerance 10 bps EOD, 50 bps intraday); OHLC validity (low ≤ open,close ≤ high); split/div consistency | Quarantine the bar; re-request; alert the data-ops channel |
| **Consistency** | Currency, units, share counts, enterprise-value definitions aligned across vendors | Normalize via mapping table; if unresolved → field suppressed |
| **Timeliness of knowledge** | Fundamentals joined on filing date; news on publication timestamp | PIT enforcement test in CI |
| **Lineage** | Every derived value traceable to inputs + transformation version | Derived value is not publishable without lineage record |
| **Licence compliance** | Field-level entitlement class (display/non-display, delayed/real-time, pro/non-pro) | Middleware blocks the field and logs the denial |

### 8.3 Confidence grades (evidence-based, not vibes)
| Grade | Data state | Reporting rule |
|---|---|---|
| **HIGH** | Primary source, fresh, cross-checked, PIT-correct, deep history | Full signal eligible |
| **MEDIUM** | Single source but fresh and internally consistent; or history < 3 years | Signal eligible; confidence tier capped at B |
| **LOW** | Missing fields, weak Canada coverage, no cross-check, thin history | Display only; **no entry signal**; scanners require a DQ floor |
| **UNKNOWN** | Source failed / unverifiable | Show `N/A — data unavailable`; never substitute a value |

### 8.4 Non-negotiable display rules
1. Every number: source + timestamp + freshness + confidence on demand (≤1 click).
2. Missing = `N/A` with reason, never zero, never neutral-default.
3. Contradictory sources: show both and the divergence; never silently pick one.
4. Delayed data is labelled as delayed everywhere it appears, not just on a settings page.
5. Vendor incidents appear in a public status page with affected datasets and user impact.

---

## 9. RISK REGISTER — BIGGEST TECHNICAL & BUSINESS RISKS

Scored 1–5 (Impact × Likelihood); owner assigned at project start.

| ID | Risk | I | L | Score | Primary mitigation |
|---|---|---|---|---|---|
| R-01 | **Data-vendor licence breach / unexpected per-user fees** | 5 | 4 | 20 | Executed agreements before any display; entitlement middleware; cost model per user class; TMX cap program negotiation; audit-ready usage reporting |
| R-02 | Vendor discontinuation (IEX Cloud precedent, Aug 2024) | 5 | 3 | 15 | Two-provider abstraction for each critical dataset; raw-zone retention so a migration doesn't lose history; contractual termination notice requirements |
| R-03 | Regulatory reclassification into personalized advice | 5 | 3 | 15 | Impersonal-by-design patterns; CI linter; counsel opinion letter; no tailoring features; no execution |
| R-04 | Backtest–live divergence destroying credibility | 4 | 4 | 16 | PIT/survivorship controls; cost stress; paper-trading gate; published live-vs-backtest scoreboard; kill criteria |
| R-05 | Signal quality is genuinely zero after costs | 5 | 3 | 15 | Component-level measurement; willingness to remove components; focus on multi-day horizons; regime gating |
| R-06 | LLM hallucination reaching production UI | 4 | 3 | 12 | Numeric verification pass; citation validator; eval harness with adversarial set; no side-effect tools |
| R-07 | LLM cost runaway | 3 | 4 | 12 | Batch/shared computation, caching, model routing (small models for classification), per-tier quotas, budget alarms |
| R-08 | Canadian data gaps masquerading as neutral scores | 4 | 4 | 16 | `N/A`-over-default rule; DQ floor for scanners; explicit coverage matrix published |
| R-09 | Alert fatigue → churn | 3 | 4 | 12 | Alert budgets, hysteresis, cooldowns, precision-over-recall tuning, user-controlled severity thresholds |
| R-10 | Churn spikes in drawdowns (the "wrong-time" revenue problem) | 4 | 4 | 16 | Value in drawdowns (risk tools, portfolio diagnostics), education, honest positioning; annual plans; B2B/API revenue diversification |
| R-11 | Support burden becomes de-facto advice channel | 3 | 4 | 12 | Response templates, no personalized answers, scope policy, staffing model |
| R-12 | Security breach / account takeover of portfolio data | 5 | 2 | 10 | MFA/passkeys, RLS multi-tenancy, KMS encryption, WAF, secrets management, pen test before launch, SOC 2 roadmap |
| R-13 | Privacy non-compliance (PIPEDA/Law 25) | 4 | 2 | 8 | Data residency `ca-central-1`, DSAR workflow, privacy officer, retention limits, DPIA before launch |
| R-14 | Single-region outage during market hours | 4 | 3 | 12 | Multi-AZ, warm standby, read-only degraded mode, status page, DR drills twice yearly |
| R-15 | Small-cap liquidity makes published plans unexecutable | 4 | 4 | 16 | ADV% position caps, days-to-liquidate metric shown, scanner liquidity floor, no TSXV signals below liquidity threshold |
| R-16 | Competitor/incumbent (brokers, TradingView, Seeking Alpha) shipping similar in CA | 3 | 3 | 9 | Depth in Canada + cross-border analytics + track record; move early on vendor agreements (scarcity) |
| R-17 | FX and tax misunderstandings producing wrong "returns" for Canadians | 4 | 3 | 12 | Dual-currency reporting, CAD-adjusted and local-currency views, explicit FX attribution, no tax advice |
| R-18 | Overfitting via research drift (many quiet experiments) | 4 | 4 | 16 | Research log, pre-registration, holdout policy enforced by tooling, peer review gate before any strategy goes live |

---

## 10. WHAT SUCCESS LOOKS LIKE (definition of product success)

Not "users made money" (unfalsifiable). Instead:
1. **Process metrics:** ≥ 70% of published signals carry complete plans; 100% of numbers have provenance; 100% of strategies have walk-forward + live paper records; DQ incidents detected before users report them.
2. **Calibration:** confidence tiers are empirically ordered (A-tier hit rate > B-tier > C-tier) out-of-sample. If they aren't, the tiering is broken and must be re-derived.
3. **User behaviour:** users open the *explanation* panel more than the chart (evidence of a decision-support habit rather than a hype habit).
4. **Business:** gross margin ≥ 75% on delayed tiers; < 8%/mo churn on annual plans; NPS > 40; the API/white-label line covering data fixed costs.
5. **Integrity:** at least one publicly documented instance of the platform *removing* a signal component because it failed out-of-sample. That is what a credible research product does.
