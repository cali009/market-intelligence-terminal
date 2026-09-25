# 03 — Quantitative Signal Framework, Entry/Exit Methodology, Risk & Backtesting

> Part of the US + Canada Market Intelligence Platform design set. See [`../README.md`](../README.md).
> **Every numeric example in this document is explicitly labelled SYNTHETIC ILLUSTRATION. No real market data is used anywhere in this specification.**

---

## 1. SIGNAL PHILOSOPHY & EVIDENCE BASE

### 1.1 What we are building
Not an oracle. A **cost-aware, evidence-weighted ranking system with explicit falsifiers**, operating on horizons where retail-accessible data and attention can still be useful: **multi-day to multi-month**, primarily in liquid US large/mid caps and liquid TSX names.

### 1.2 The honest evidence base
| Effect | Evidence strength | Realistic magnitude (gross, pre-cost) | Persistence | Our use |
|---|---|---|---|---|
| Cross-sectional momentum (12-1 month) | Strong, long-documented, survives many tests | ~6–10%/yr in long-short form historically; far less long-only, net | Persistent but crashes (2009, momentum drawdowns) | Core momentum input, with crash guard |
| Trend-following / time-series momentum | Strong across asset classes | Sharpe ~0.3–0.7 with large drawdowns | Persistent | Trend/regime overlay, not a standalone signal |
| Short-term reversal (1–5 day) | Strong but **concentrated in illiquid/wide-spread names** | Large gross, largely eaten by costs | Persistent | Restricted to liquid names only, cost-aware |
| Post-earnings announcement drift (PEAD) | Historically robust | ~2–5% over 20–60 days in early studies; **materially decayed** post-2005 | Weakening | Earnings scanner + score nudge, never the sole trigger |
| 52-week-high proximity / breakout | Moderate | Modest standalone | Moderate | Setup component |
| Quality / profitability (ROE, ROIC, accruals) | Moderate-strong, robust | Premium exists long-horizon | Persistent | Fundamental score core |
| Value (P/E, P/B, EV/EBITDA) | Long-horizon, painful interim | Premium over 5–10 yr horizons | Persistent but regime-sensitive | Valuation score with explicit horizon label |
| Low volatility / low beta | Robust historically | Modest | Persistent; regime-sensitive | Risk gate, defensive scanner |
| News sentiment (headline-level) | **Weak-to-moderate, heavily decayed** | Mostly priced within minutes for large caps | Short | Context + small bounded contribution only |
| Analyst revisions | Moderate | Modest, decaying | Moderate | US only (licence), if IC tests positive |
| Technical indicator soup (RSI/MACD/Stoch combos) | **Weak/absent as a standalone** | ~0 after costs in most tests | n/a | Used only as *features inside a ranked composite*, never as signals |
| Social sentiment, options flow | Weak/noisy, manipulation-prone | ~0 | n/a | Excluded |

**Two facts the product must respect:**
1. **Multiple-testing is the default failure mode.** The published "factor zoo" numbers in the hundreds; most do not replicate at conventional significance, and credible thresholds now require t-statistics well above 2 (≈3+) for a new claim. Therefore this platform treats every component as a **hypothesis on probation**, measures its information coefficient (IC) continuously, and retires components that fail.
2. **After costs, most observed gross effects shrink dramatically**, especially in high-turnover form. Turnover is therefore a first-class output of every strategy, and cost sensitivity is mandatory in every report.

### 1.3 Design rules that follow
- Signals are **cross-sectional ranks**, not absolute predictions.
- Composite scores, never single indicators.
- **Risk is a gate and a sizing input, never a positive contributor.**
- Scarcity is a feature: most securities most days produce `NO SETUP`.
- Every signal is expressed with an invalidation predicate.
- Every component's contribution is measurable after the fact (attribution), so dead weight can be removed.

---

## 2. FEATURE SPECIFICATION

### 2.1 Timeframes
MVP: **1h, daily, weekly, monthly** · Phase 4+: **1m, 5m, 15m**. Intraday features are only computed for names passing a liquidity floor (ADV$ ≥ US$5M / C$3M in MVP; configurable per market).

### 2.2 Indicator library (exact parameters — no ambiguity between research and production)
| Feature | Definition | Timeframes | Normalization | PIT/latency note |
|---|---|---|---|---|
| `sma_{n}` | Simple MA, n ∈ {10,20,50,100,200} | D, W, M | % distance from price; slope over 20 bars | Uses close; recompute after session close |
| `ema_{n}` | EMA n ∈ {9,21,50,200} | 1h, D, W | % distance; cross-state (fast>slow) | — |
| `vwap_session`, `vwap_anchored` | Rolling/anchored VWAP (anchor = last earnings, YTD, event) | 1h, D | % distance | Session VWAP only valid intraday |
| `atr14` | Wilder ATR(14) | 1h, D, W | **ATR% = atr/close**; percentile vs 252d | Never compare raw ATR across price levels |
| `rsi14` | Wilder RSI(14) | 1h, D, W | Level + 5-bar slope | Divergence detection on last 3 swings |
| `macd(12,26,9)` | MACD line, signal, histogram | 1h, D, W | Histogram normalized by ATR% | — |
| `bb(20,2)` | Bollinger bands + **bandwidth percentile** | D, W | Bandwidth percentile (252d) | Squeeze detection |
| `adx14` | ADX/DMI | D, W | Level + rising/falling | Trend strength, not direction |
| `stoch(14,3)` | Stochastic %K/%D | 1h, D | Level + cross-state | De-emphasized in strong trends |
| `obv`, `cmf20` | On-balance volume; Chaikin money flow | D | Slope z-score over 20 bars | Volume confirming price |
| `rvol` | Volume / median volume for same half-hour-of-day over 20 sessions | 1m–D | Ratio (log) | **Same-time-of-day** comparison is mandatory to avoid intraday bias |
| `dollar_volume_20d` | Median(close×volume) over 20 sessions | D | Log | Liquidity gate |
| `gap_pct` | (open − prior close)/prior close, classified by cause (earnings/news/none) | D | Absolute + signed | Gap classification drives different playbooks |
| `dist_52w_high/low` | % from 52-week extremes | D | Ratio | Momentum/mean-reversion dual use |
| `rs_sector`, `rs_market` | Relative strength: security return − sector/market return over 21/63/126 sessions | D, W | Cross-sectional rank | Requires sector indices for both markets |
| `beta_60`, `beta_252` | OLS beta vs local benchmark (+ US benchmark for CA names) | D | Level | Used in risk, never as a bullish signal |
| `realized_vol_20/60` | Stdev of log returns, annualized | D | Percentile (252d) | Regime input |
| `volume_profile` | POC, value area (70%), HVN/LVN from 1h bars over 60 sessions | 1h, D | Price levels, not scaled | Requires ≥ 60 sessions of 1h data |
| `ma_cross_state` | State machine: {golden, death, none} × {recent, established} | D, W | Categorical | "Recent" = within 10 bars |
| `trend_structure` | Swing-based HH/HL vs LH/LL classification, |swings| ≥ 3 | D, W | Categorical | Swing detection: fractal (2-bar) with ATR filter |
| `sr_levels` | Support/resistance via pivot clustering + volume confirmation | D | Nearest levels within 3×ATR | Levels are probabilistic zones, never exact prices |

### 2.3 Cross-sectional normalization
All continuous features are converted to **percentile ranks within a peer group** (sector × market × cap-band) and then mapped to 0–100. Rationale: ranks are robust to outliers and distribution shifts, and they make the composite comparable across regimes. Sector-neutral by default, with an option to score against the whole market.

---

## 3. STOCK SCORING ENGINE

### 3.1 Sub-scores (0–100 each) and their meaning
| Score | What it measures | Horizon | Notes |
|---|---|---|---|
| **Technical** | Setup quality & trend integrity | Days–weeks | Structure, MA state, S/R confluence, squeeze/expansion |
| **Momentum** | Persistence of price/volume strength | Weeks–months | 12-1 momentum, RS vs sector/market, 52w proximity |
| **Fundamental (Quality)** | Business quality & balance-sheet health | Quarters–years | ROIC, margins, accruals, leverage trend; PIT |
| **Valuation** | Price paid per unit of fundamentals | Years | Multiples + FCF yield, peer- and history-relative |
| **News/Sentiment** | Recent information flow | Hours–weeks | Bounded contribution; decayed by age |
| **Sector Strength** | Whether the tide is with the name | Weeks–months | Sector RS rank + rotation quadrant |
| **Regime Fit** | Whether this *type* of setup works in the current regime | Weeks | Regime-conditional per-strategy efficacy |
| **Risk (penalty)** | How badly this can go wrong | — | **Subtractive/gating only**, never additive |

### 3.2 Factor composition and weights
Weights are **configurable per horizon** and **estimated with regularization, then frozen quarterly** (no daily weight-fitting — that is how curve-fitting enters).

| Component | Swing (3–20 sessions) | Position (1–12 months) | Direction |
|---|---|---|---|
| Trend structure (HH/HL count, MA state, ADX) | 30% | 25% | higher better |
| Relative strength (RS sector/market, 52w proximity) | 20% | 20% | higher better |
| Setup geometry (proximity to breakout/pullback zone, base tightness, ATR% compression) | 20% | 10% | higher better |
| Volume/participation (rvol, OBV slope, CMF, POC acceptance) | 15% | 10% | higher better |
| Multi-timeframe alignment | 10% | 5% | higher better |
| Volatility fit (ATR% percentile in target band for the strategy) | 5% | 5% | non-monotonic (too low = dead, too high = unmanageable) |
| *(Technical score total: 6 sub-factors)* | *100%* | *75%* | |
| Quality & Valuation (ROIC, margins trend, accruals, leverage trend, multiples) | — | 25% | higher better |
| *(Fundamental score total)* | — | *25%* | |
| News (materiality × recency-decayed sentiment × novelty) | bounded ±12 pts applied to composite | bounded ±8 | signed |
| Sector strength | ±10 pts modulation | ±8 | signed |
| Regime fit | multiplicative gate 0.6–1.05 | multiplier | non-monotonic |
| **Risk penalty** | −0 to −25 pts | −0 to −25 pts | subtractive |

### 3.3 Composite Opportunity Score
```
# For Swing Horizon:
raw        = Technical_Score (which combines all 6 technical/RS sub-factors to 0–100)

# For Position Horizon:
raw        = 0.75 · Technical_Score + 0.25 · Fundamental_Score

# Modulation and Gates:
modulated  = raw × regime_multiplier + news_contribution + sector_contribution
final      = clamp(round(modulated − risk_penalty), 0, 100)
```
**Hard gates (any failure caps the score, or blocks the signal entirely):**
| Gate | Condition | Effect |
|---|---|---|
| Data quality floor | Required datasets below MEDIUM confidence | **No signal**; display score with `LOW DATA CONFIDENCE` |
| Liquidity | ADV$ < market threshold, or days-to-liquidate(size) > 3 | **No signal** |
| Earnings proximity | Reported within ±2 sessions (held/new positions) | No new entry; flag `EVENT RISK` |
| Valuation extreme | Valuation percentile ≥ 95 **and** Technical < 40 | Cap final score at 45 |
| News risk | Materiality 5 negative event unresolved (e.g., fraud allegation, going-concern) | **No signal** until reviewed |
| Structural | Price below declining 200DMA **and** below 20DMA | Cap final score at 40 |
| FX (for CAD investor view) | Unhedged USD exposure beyond profile limit | Signal allowed; portfolio flag raised |

### 3.4 Confidence tier — separate from the score, defined by measurable evidence
| Tier | Requirements | Reporting |
|---|---|---|
| **A** | HIGH data quality · ≥ 3 years of history for the strategy · walk-forward positive in ≥ 70% of folds · ≥ 100 historical analogues · regime match ≥ 0.7 · no unresolved gate flags | Full plan published |
| **B** | MEDIUM data quality or 1–3 years history or 50–99 analogues or mixed fold results | Full plan + explicit caveat line |
| **C** | LOW data quality, < 50 analogues, or first-time setup on this name | Display only: plan shown as "illustrative parameters", not a signal |
| **D** | Insufficient evidence | No plan; informational only |

**Calibration requirement:** tiers must be *empirically ordered out-of-sample* (A hit rate > B > C). If they are not, the tiering is re-derived before it is shown to users again. This check runs monthly and its result is published internally.

### 3.5 Worked example — SYNTHETIC ILLUSTRATION ONLY (not real market data)
```
Ticker:      EXMP      Market: US      Sector: Industrials      Cap: US$14.2B
Price:       84.10     ATR14: 1.92 (2.3%)     ADV$: 62M     Horizon: Position

# 1. Technical Sub-factors (sum = 78/100):
Trend structure:       27 / 30
Relative strength:     14 / 20
Setup geometry:        16 / 20
Volume participation:  12 / 15
Multi-timeframe:        6 / 10
Volatility fit:         3 /  5
-> Technical_Score   = 78

# 2. Fundamental Factor (ROIC top tercile, margins +80bp YoY, neutral accruals):
-> Fundamental_Score = 64

# 3. Position Horizon Raw Blend:
raw = (0.75 × 78) + (0.25 × 64) = 58.5 + 16.0 = 74.5

# 4. Modulations & Penalties:
regime_multiplier   = 1.00 (Weak Bull; swing/position momentum positive in 61% of folds)
news_contribution   = +6   (one materiality-4 positive event, 3 sessions old -> decayed from +10)
sector_contribution = +7   (Industrials rank 3/11, improving quadrant)
risk_penalty        = -8   (ATR% 68th pct; 1.4x sector beta; moderate gap history)

# 5. Composite Calculation:
modulated = (74.5 × 1.00) + 6 + 7 = 87.5
final     = clamp(round(87.5 - 8), 0, 100) = 80

COMPOSITE   = 80 / 100
CONFIDENCE  = A (data HIGH · 4.1y history · 74% folds positive · 212 analogues · regime match 0.81)
DATA        = HIGH (prices: primary, cross-checked; fundamentals: PIT, 9/9 concepts present)

INTERPRETATION (what 80 means): the setup ranks in the top decile of the current US universe on a
combination of trend integrity, relative strength, and participation, with a valuation and quality
profile that does not contradict it. It does NOT mean an 80% probability of profit, and the
platform will never present it that way.
```

### 3.6 Component accountability (the anti-dead-weight mechanism)
Monthly, for every component: compute its **information coefficient** (Spearman rank correlation between component score and forward 5/20/60-session return, within universe and regime), its **incremental value** (does the composite's IC improve when included?), and its **stability across folds**. Components with IC statistically indistinguishable from zero after costs are **removed from the composite and documented in the research log**. This is the mechanism that keeps the product honest over time — and it must be *used*, not just built.

---

## 4. MARKET REGIME DETECTION

### 4.1 Inputs (per market: US and Canada separately, plus a global risk factor)
| Family | Features |
|---|---|
| Trend | Index vs 50/200DMA, 200DMA slope, % of constituents above 50/200DMA (breadth), new-high/new-low ratio |
| Volatility | Realized vol (20/60), VIX level + **term-structure slope (VIX/VIX3M)**, volatility-of-volatility, ATR% of index |
| Credit/stress | HY and IG OAS, change in spreads, yield-curve slope (2s10s, 3m10y), funding spreads |
| Rates/macro | 10Y nominal + real yields, policy-rate path expectations, inflation surprises |
| Cross-asset | CAD/USD, oil (WTI), gold, copper, USD index |
| Dispersion | Cross-sectional return dispersion, correlation of constituents (crowding) |
| Liquidity/participation | Index dollar volume, advance/decline line, % of volume in top decile |

### 4.2 Model
- **Gaussian Hidden Markov / Hidden Semi-Markov Model** over standardized features (robust scaling, winsorized at 1/99), with **6 constrained states** initialized toward the six regimes and duration modelling (HSMM) to avoid unrealistic instant state-flipping.
- Fit on a rolling window (e.g., 10 years), refit quarterly; **state labelling is fixed by a deterministic mapping** (state → regime name) established from feature means at fit time and then **frozen** — labels must not be reassigned retroactively to make history look better.
- **Filtered (causal) probabilities only** for user-facing output: at time *t*, only information up to *t*. Smoothed probabilities may be computed for research but are **never** used in signals or shown as if they existed in real time. A CI test verifies the pipeline cannot access future data.
- Reported output: current regime, filtered probability vector, transition probabilities, days in state, and the **top 3 contributing features** (both agreeing and disagreeing) in plain language.

### 4.3 Regime definitions and strategy gating
| Regime | Characteristic | Swing momentum | Breakouts | Mean-reversion | Dip-buying | Position/value | Risk scaling |
|---|---|---|---|---|---|---|---|
| **Strong Bull** | Trend up, breadth broad, vol low | ✅ enabled | ✅ | ⚠️ limited | ✅ | ✅ | 1.0 |
| **Weak Bull** | Trend up, breadth mixed, leadership narrow | ✅ (selective) | ✅ selective | ✅ | ⚠️ | ✅ | 0.85 |
| **Sideways/Consolidation** | Flat trend, low dispersion | ⚠️ reduce | ❌ (false breakouts) | ✅ | ✅ range-only | ✅ | 0.75 |
| **High Volatility** | Elevated vol, trend unclear | ❌ | ❌ | ❌ | ⚠️ only with wider stops | ✅ (quality) | 0.5 |
| **Bearish** | Trend down, breadth deteriorating | ❌ | ❌ (short setups only, if enabled) | ✅ tactical | ❌ | ✅ (accumulate watchlist) | 0.4 |
| **Crisis/Stress** | Credit spreads widening, correlation → 1 | ❌ all | ❌ | ❌ | ❌ | ❌ | 0.25 + de-risk alert |

Gating is **empirical**, not assumed: each strategy's historical performance is broken out **by regime**, and a strategy is only enabled where its expectancy is positive after costs with a minimum sample (`n ≥ 30` trades in that regime). No exceptions "because it looks obvious".

---

## 5. AI NEWS & EVENT ENGINE

### 5.1 Pipeline (maps to your 9 required steps)
1. **Collect** — newswire feeds (licensed), company IR/press releases, EDGAR (8-K/10-Q/10-K/Form 4/13D-G/S-1), SEDAR+ documents (licensed), economic calendars. Every item stores source, URL, publication timestamp, and a `is_primary_source` flag.
2. **Deduplicate** — three-stage: (a) exact/near-exact (MinHash + simhash on normalized text) within a 72-hour window; (b) semantic (embedding cosine > threshold) within the same window and entity set; (c) cluster into `news_event` with one canonical record and N publishers. **Each cluster contributes one score, not N.** Publisher count is retained as a signal of coverage breadth.
3. **Entity linking** — dictionary + fuzzy match against the securities master (name, ticker, aliases, former names, CIK, subsidiaries). Ambiguity (e.g., a ticker that exists on both TSX and NASDAQ) resolves by explicit `paired_security_id`, currency context in the text, and source venue. Output includes `link_confidence` and the text span used.
4. **Sentiment** — `POSITIVE | NEGATIVE | NEUTRAL | UNCERTAIN`, with **calibration reporting**. UNCERTAIN is a first-class answer and is expected to be common; forcing polarity is how sentiment engines destroy trust.
5. **Materiality (1–5)** — blended score from: event type prior (bankruptcy/going-concern = 5; routine 8-K = 1), numeric impact when present ($ value vs market cap / revenue / EPS), affected scope (whole company vs segment), analyst/filing language strength, and **market confirmation** (RVOL and move at publication). Output includes the drivers.
6. **Horizon** — `IMMEDIATE` (intraday reaction), `SHORT` (days–weeks: earnings, guidance, upgrades), `MEDIUM` (quarters: product cycles, contract wins), `LONG` (years: regulatory change, structural). Horizon is estimated by event type + textual cues, then **validated empirically** (§5.3).
7. **Price linkage** — store the market reaction: return from the prior close to publication-minute price, +1h/+1d/+5d/+20d forward returns, RVOL at publication, and gap behaviour. This is what makes the news engine measurable rather than assertive.
8. **Already-priced assessment** — heuristic score from: magnitude of the move already realized vs the event's typical historical reaction (estimated from analogous events), RVOL at publication, time since publication, and whether peers moved similarly (spillover vs idiosyncratic). Output: `LIKELY_PRICED_IN | PARTIALLY_PRICED_IN | LIKELY_NOT_PRICED_IN | UNKNOWN`, always shown **with its inputs** so the user can disagree.
9. **Score integration** — bounded and decayed:
   ```
   news_contribution = clamp( Σ_events [ materiality_w × sentiment_sign × recency_decay(age) ], −12, +12 )
   recency_decay(half-life): IMMEDIATE 0.5 d · SHORT 3 d · MEDIUM 15 d · LONG 90 d
   ```
   A single materiality-5 negative event can also **block** new long signals (hard gate) and trigger an exit-engine review.

### 5.2 Volume/pricing discipline (prevents the classic "news = alpha" delusion)
- Mega-cap, high-coverage names: assume rapid pricing; news contributes to *context* and to **exit** decisions far more than to entries.
- Small/mid-cap, thin coverage names: more potential informational edge, **but** liquidity and manipulation risk dominate — so position-size and liquidity gates apply before any news-driven entry.
- Every news-driven signal is tagged `NEWS_DRIVEN`, and its outcomes are tracked **separately** from technical setups. If measured expectancy is ≤ 0 after costs over a 2-year out-of-sample window, the news component is removed from the composite (documented publicly).

### 5.3 Worked example — SYNTHETIC ILLUSTRATION ONLY
```
CLUSTER: 14 outlets reporting the same event (one press release + 13 rewrites) → 1 news_event
EVENT:   Company raises FY guidance; also announces a US$250M contract award
ENTITY:  EXMP (link_confidence 0.98, span verified)
CLASS:   sentiment=POSITIVE · category=GUIDANCE+CONTRACT · materiality=4
         drivers: contract = 6% of TTM revenue; guidance raise = +3% FY EPS implication;
                  RVOL at publication 3.1×; stock gapped +4.2% pre-market
HORIZON: SHORT (guidance → days-weeks); MEDIUM sub-tag for the contract (revenue recognition over 8 quarters)
PRICED:  PARTIALLY_PRICED_IN (move so far 4.2% vs typical reaction for this event type: median +5.1%, IQR 2–9%)
CONTRIB: +7.4 → after 2 sessions of decay → +6.1 (of max +12)
NOTE SHOWN TO USER: "A gap of this size on this event type historically continued in 54% of 186
analogues (median +20-session drift +1.3%), with wide dispersion. This is not an entry trigger by
itself; it modulates an existing technical setup."
```

---

## 6. ENTRY SIGNAL ENGINE

### 6.1 Output contract (every field mandatory; API rejects incomplete signals)
```yaml
security: {ticker, exchange, market, currency, security_id}
stance: "POTENTIAL LONG SETUP"          # never "BUY"
setup_type: BREAKOUT | PULLBACK | BASE_BREAKOUT | TREND_CONTINUATION | OVERSOLD_REVERSAL | EARNINGS_DRIFT
entry_zone: {low, high}
preferred_entry: price                   # typically zone midpoint or break-confirm level
alternative_entry: price                 # deeper pullback level; with its own (wider) stop
stop: price  { method: ATR_AND_STRUCTURE, distance_pct, risk_per_share }
targets: [{t1, basis}, {t2, basis}, {t3, basis}]
rr_ratio: numeric                        # computed on T1 and T2 separately
holding_period: SHORT_SWING | SWING | POSITION   # with the empirical distribution behind it
strength: 0-100                          # composite score at generation
confidence_tier: A | B | C | D
rationale: { technical: [...], fundamental: [...], news_catalysts: [...], macro: [...],
             sector: [...], regime: [...] }
invalidation: [ machine-checkable predicates ]
risks: [ ... ]
data_lineage: { prices: {...}, fundamentals: {...}, news: {...} }
disclaimer_version: "..."
```

### 6.2 Computation rules
**Entry zone**
- *Breakout:* `low = breakout_level − 0.5×ATR`, `high = breakout_level + 0.5×ATR`; preferred entry = first close above level with RVOL ≥ 1.5.
- *Pullback:* `zone = [EMA20 + 0.25×ATR, EMA20 + 1.0×ATR]` (or a tested S/R level), only valid while trend structure intact.
- *Oversold reversal:* zone anchored at a volume-confirmed support level, **requires** a reversal confirmation (higher low + RVOL) before the signal is published.

**Stop**
```
structural = min(recent swing low, nearest strong support) − 0.5×ATR
volatility = entry − 2.0×ATR
stop       = min(structural, volatility)          # the more conservative (wider) of the two
constraints: stop_distance ≥ 1.2×ATR  AND  stop_distance ≤ profile_max_stop_pct
```
Stop distance > profile cap → **no signal** (position too risky at this volatility), not a tightened stop.

**Targets**
```
T1 = entry + 1.5R,  but not beyond the nearest major resistance
T2 = entry + 2.5R,  or measured move (base height / flag pole)
T3 = trailing (2×ATR chandelier) or structural target; only published when a structural level exists
```
If T1 > nearest major resistance, **reduce T1 to that resistance** — publishing unreachable targets is a credibility killer.

**Holding-period bucket** — derived from the **empirical distribution of the strategy's winners**, not guessed. Example: if the 25th–75th percentile of winner holding times is 6–19 sessions, the bucket is `SWING (6–19 sessions, median 11)`.

**Risk/Reward** — always reported **both** as planned R:R (to T1 and T2) and as *realized* R:R in the historical analogue set. The gap between the two is often the most useful number on the page.

### 6.3 Confidence tiers
As defined in §3.4. In addition, an entry signal requires:
- ≥ 1 corroborating dimension beyond technical (fundamental, sector strength, or news) **or** an explicit flag `TECHNICAL_ONLY`;
- at least 30 historical analogue trades in the same regime with positive expectancy after costs (else tier capped at C);
- no unresolved hard gate from §3.3.

### 6.4 Published signal template (matches your requested output format)
```
TICKER: EXMP        Signal: POTENTIAL LONG SETUP (BREAKOUT + PULLBACK-ALTERNATE)

Entry zone:        $83.40 – $85.10
Preferred entry:   $84.60  (close above 3-week range high on ≥1.5× volume)
Alternative entry: $81.90  (retest of 20-EMA; stop widens to $79.40)
Stop:              $80.10  (structural low − 0.5×ATR; 5.3% risk)
Target 1:          $89.70  (1.5R; just below prior ATH resistance $90.20)
Target 2:          $93.90  (2.5R; measured move of 6-week base)
Target 3:          trailing 2×ATR (structural target $97.50 if the ATH breaks on volume)

Risk/Reward:       1.5:1 planned to T1 · 2.5:1 to T2 · historical analogues realized 1.31:1 to T1 (n=212)
Time horizon:      SWING — empirical winner distribution 6–19 sessions (median 11)

Why:
  1. FACT — Closed 2.1% above a rising 50-DMA; 3-week range high reclaimed on 2.4× relative volume.
  2. CALCULATION — RS vs sector 71st percentile; distance to 52w high 3.4%; ATR% 68th percentile (room to move).
  3. INFERENCE (0.61) — Volume expansion at the range high alongside OBV strength suggests accumulation; in 212
     historical analogues, 57% resolved to T1 before stop, median +1.3% 20-session drift, dispersion wide.
  4. NEWS — Guidance raise + US$250M contract (materiality 4, SHORT horizon, PARTIALLY priced in).
  5. MACRO/REGIME — Weak Bull (p=0.64); momentum setups positive in this regime historically (61% of folds).
  6. SECTOR — Industrials rank 3/11, improving quadrant.

Invalidation (any triggers → signal void):
  - Daily close below $80.10 (structural stop level).
  - Failure of the breakout: two consecutive closes back inside the range on declining volume.
  - Relative-strength rank vs sector falls below the 40th percentile.
  - Guidance withdrawn / adverse materiality-5 event.
  - Regime shifts to High-Volatility or worse (system-wide gate).

Risks:
  - Earnings in 34 days fall inside the expected holding window → event gap risk; plan-trimming rule applies.
  - Beta 1.4 vs sector: a market-wide drawdown amplifies the drawdown here.
  - If the broader market is in a narrow-leadership phase, breakouts fail more often (this is measured; not a hunch).

Data confidence: HIGH (price primary + cross-checked; fundamentals PIT; news span-verified).
Confidence tier: A.
This is analytical research output, not advice or a guarantee. You are responsible for your decisions.
```

---

## 7. EXIT ENGINE

### 7.1 Position state machine
```
                ┌─────────── HOLD ◀────────────┐
                │                               │
   ENTERED ─▶ WATCH ─▶ REDUCE ─▶ EXIT ─▶ (closed)
                │
                └────▶ EMERGENCY RISK ─▶ immediate exit recommendation + portfolio de-risk review
```
States are **advisory outputs** attached to a position the user has recorded in the app; the app never places orders.

### 7.2 Triggers by category
| Category | Specific trigger | Severity | Notes |
|---|---|---|---|
| **Stop** | Price ≤ recorded stop (close-based or intraday, per user setting) | EXIT | States clearly: verify your broker-side stop |
| **Targets** | T1 hit | HOLD (trim 1/3 candidate) | Trimming policy is user-configurable, never automated |
| **Targets** | T2 hit | REDUCE | Trail the remainder |
| **Trend reversal** | Close below 50-DMA + MACD cross + ADX falling from >25 | REDUCE→EXIT | Needs 2 of 3 to fire (hysteresis) |
| **Momentum deterioration** | RS rank vs sector drops 20+ percentile points over 10 sessions; RSI divergence | WATCH→REDUCE | Graduated |
| **Volume/participation** | Breakout fails: close back inside range on below-average volume, followed by distribution day | REDUCE | Failed-breakout rule |
| **Fundamental change** | New filing shows margin compression > 200 bp QoQ, guidance cut, covenant issue, going-concern language | EXIT review | Filing-triggered; LLM extraction must cite spans |
| **Negative news** | Materiality ≥ 4 negative event, unresolved after 1 session | REDUCE→EXIT | Category- and priced-in-aware |
| **Valuation excess** | Valuation percentile ≥ 95 **and** growth decelerating | WATCH→REDUCE | Prevents "great company, terrible price" drift |
| **Regime deterioration** | Market regime → Bearish/Crisis; portfolio beta above profile cap | REDUCE (portfolio-level) | De-risk alert across correlated names |
| **Thesis invalidation** | Any recorded invalidation predicate becomes true | EXIT | The system tracks the original predicates, not vibes |
| **Liquidity/spread** | Spread widens > 3× 20-day median, or days-to-liquidate > 5 at current size | WATCH | Exit-quality warning |
| **Portfolio risk** | Single-name weight > profile cap; sector concentration > cap; portfolio drawdown > circuit breaker | REDUCE (portfolio-level) | Sizing remediation, not a market call |

### 7.3 Hysteresis and escalation
- Two-cycle confirmation for trend/momentum downgrades (avoids whipsaw alerts).
- **Escalation counters**: `WATCH` × 3 consecutive cycles → escalate to `REDUCE`; `REDUCE` with worsening evidence × 2 → `EXIT`.
- **De-escalation**: a held position returning above the 20-EMA with restored RS can go `REDUCE → WATCH`.
- **EMERGENCY RISK** is reserved for: halt/resumption with material news, materiality-5 negative event, fraud/accounting allegations, bankruptcy/going-concern, or a credit event. It always includes a portfolio-level correlated-exposure check.

### 7.4 Alert payload requirement
Every exit recommendation shows: trigger name, threshold vs actual value, timestamp of the observation, the position's P/L and R-multiple at that moment, the original thesis (from the signal that created it), and what would reverse the downgrade.

---

## 8. OPPORTUNITY SCANNERS (14)

All scanners share: universe (market/exchange/cap band), filters (sector, price, ADV$ floor, data-quality floor), horizon, and risk profile. Scanner results always show *why* each name matched, and never more than 25 rows without pagination (no infinite "top 1000 opportunities").

| # | Scanner | Core rule (all require liquidity + DQ floor) |
|---|---|---|
| 1 | **Best setups today** | Composite ≥ 75, confidence ≥ B, ≥ 1 corroborating dimension, no gate failures. Capped at 10 names/day |
| 2 | **Breakouts** | Range high (20–60 session) reclaimed, RVOL ≥ 1.5, close in top third of day's range, ATR% in mid band |
| 3 | **Pullbacks** | Uptrend intact (price > rising 50-DMA), pullback 3–10 sessions, depth 1.5–3×ATR, volume contracting on the pullback, RSI 40–55 |
| 4 | **Momentum** | 12-1 momentum top decile, RS vs sector top quartile, 52w proximity top quintile, ATR% not extreme |
| 5 | **Oversold reversal** | RSI < 30 or 2σ below 20-EMA, **plus** a confirmed reversal (higher low + RVOL ≥ 1.3), above long-term support |
| 6 | **Undervalued** | Valuation percentile ≤ 25 **and** quality percentile ≥ 50 (cheap *and* decent), no value-trap flags (declining margins/rising leverage) |
| 7 | **Earnings opportunities** | Earnings within 5–20 sessions; pre-earnings drift history measured per name; flags implied-move risk; excludes low-liquidity names |
| 8 | **Dividend** | Yield ≥ sector median, payout ≤ 70% of FCF, ≥ 5-year growth or stable, dividend-safety score ≥ 60 |
| 9 | **Unusual volume** | RVOL ≥ 3 (same-time-of-day), price move consistent with volume direction, spread not blown out, exclude news-only pumps without fundamentals |
| 10 | **Small/mid-cap** | Cap US$300M–3B; stricter liquidity (ADV$ ≥ floor), wider stop allowances, position-size cap 1% of ADV$; **Canadian small caps get an extra delisting-risk flag** |
| 11 | **Defensive** | Low beta (< 0.8), stable earnings (low accruals, low earnings volatility), defensive sectors, positive FCF, reasonable valuation |
| 12 | **Sector rotation** | Sector RS quadrant transition (lagging→improving or improving→leading) + breadth confirmation; lists top 3 names per rotating sector, not the whole sector |
| 13 | **Canadian opportunities** | Same engines restricted to TSX/TSXV with CA-calibrated thresholds, FX-adjusted reporting for USD investors, and CA-specific event calendar |
| 14 | **US opportunities** | Same, restricted to US venues; includes dual-listed names with basis analytics vs their Canadian line |

**Scanner honesty rules:** no name appears in more than 3 scanners (prevents "everything is an opportunity"); scanners are gated by regime (e.g., breakout scanner disabled in Crisis); and each scanner publishes its historical expectancy and sample size in the scanner header.

---

## 9. RISK MANAGEMENT FRAMEWORK

### 9.1 Risk profiles (user-selected, with explicit consequences shown before confirmation)
| Parameter | Conservative | Moderate | Aggressive |
|---|---|---|---|
| Risk per trade (% of portfolio equity) | 0.25–0.50% | 0.50–1.00% | 1.00–1.50% |
| Max concurrent positions | 10 | 15 | 20 |
| Max single-name weight | 5% | 8% | 12% |
| Max sector weight | 20% | 25% | 30% |
| Max net exposure | 40–70% | 50–90% | 60–110% (margin flagged) |
| Max single stop distance | 6% | 8% | 12% |
| Short selling | Not allowed | Liquid large caps only | Allowed with borrow/recall check |
| Small caps (incl. TSXV) | Not allowed | Allowed with liquidity gate | Allowed, position ≤ 1% of ADV$ |
| Max portfolio drawdown (circuit breaker) | −8% → halve risk | −12% → halve risk | −18% → halve risk |
| Unhedged USD exposure (CAD investor) | ≤ 20% | ≤ 40% | ≤ 70% |

**Critical framing requirement:** the UI must state, before a user picks Aggressive, that the profile increases both risk per trade and the *simultaneous loss potential* of correlated positions, and show a worked example: *"5 concurrent stop-outs at 1.2% risk each = −6.0% portfolio; in a high-correlation event (correlations spike toward 1 in stress), assume 40–60% of your open positions can stop out together."* Aggressive is not "the same plan with bigger numbers"; it is a different return *and* ruin profile.

### 9.2 Position sizing
```
shares = floor( (portfolio_equity × risk_per_trade_pct) / (entry − stop) )
caps:
  value ≤ portfolio_equity × max_single_name_weight
  shares ≤ (ADV_shares_20d × 0.05)                      # ≤5% of average daily volume
  shares ≤ (ADV_dollars_20d × 0.02) / price  for small caps (# ≤2% of dollar volume)
  resulting days_to_liquidate ≤ 3 (target ≤ 1)
if caps bind → publish the capped size AND state which cap bound it and the residual risk
```
All sizing outputs are **informational calculators tied to the user's declared profile**, with an explicit statement that the user is responsible for their own sizing.

### 9.3 Portfolio-level analytics
| Metric | Method | Use |
|---|---|---|
| Portfolio beta | Weighted OLS beta vs local benchmark **and** vs the other market's benchmark | Rate-sensitivity/leadership exposure |
| Sector concentration | Weight by GICS sector, GICS industry-group second level | Cap enforcement (industry-group matters: "Financials" hides very different risks) |
| Correlation clusters | Hierarchical clustering on 60-session returns; report number of effective independent bets | "12 positions that are really 3 bets" is the most common retail risk error |
| Risk contribution | Component VaR / marginal contribution to risk | Show which position actually carries the risk |
| Tail risk | 95%/99% historical CVaR + stressed-scenario replay (2008, Mar-2020, 2022 rate shock, 2018 vol spike, oil collapse for TSX) | Realistic loss expectations |
| Gap risk | Historical overnight gap distribution per name; expected loss if stop gaps through | Sizing honesty |
| FX exposure | Unhedged USD (or CAD) notional; CAD-adjusted drawdown | The most ignored risk for Canadian investors in US names |
| Liquidity profile | Days-to-liquidate at current size for each position and the whole book | Exit realism |

### 9.4 Circuit breakers and behavioural guardrails
Portfolio drawdown breach → automatic risk-halving *recommendation* (a recommendation, never automated), a review checklist, and temporary disablement of new-entry alerts until the user acknowledges. This mirrors institutional practice and prevents the classic retail failure of doubling down into a losing streak.

---

## 10. BACKTESTING FRAMEWORK

### 10.1 Engine requirements
| Requirement | Specification |
|---|---|
| Event-driven simulation | Bar-by-bar replay; orders filled on the **next bar's** open/high/low, never on the signal bar's close |
| Point-in-time correctness | Fundamentals joined on `filing_date`; news/events filtered by `published_at`; all features computed only from data available at that timestamp |
| Survivorship-free universe | Universe reconstructed as-of each date; delisted names included with defined delisting return assumptions |
| Corporate actions | Splits/dividends/spinoffs applied as of ex-dates; adjusted series for indicators, unadjusted for share counts |
| Cost model | Commission (per-share/tiered, configurable per broker) + half-spread at fill + slippage model (spread-dependent, size-dependent) + market impact for size > ADV threshold + borrow cost for shorts + FX conversion cost for cross-currency |
| Realism constraints | No fill when the bar has no volume; limit orders only fill if the bar traded through the limit; gaps through stops fill at the open (adverse) |
| Reproducibility | Every run stores `strategy_version`, `feature_version`, `dataset_version`, `universe_version`, cost parameters, and code commit hash → identical results on re-run |
| Capacity | Reports the AUM level at which impact costs consume the edge |

### 10.2 Mandatory anti-bias controls
| Bias | Control |
|---|---|
| Look-ahead | Next-bar fills; PIT joins; CI test that asserts no feature reads `knowledge_at > as_of` |
| Survivorship | Delisted-inclusive data source; universe as-of; delisting returns by reason |
| Data leakage (normalization/labels) | All scaling parameters fit on training folds only; labels purged and embargoed |
| Overfitting (parameter search) | Pre-registration; parameter **sensitivity** heatmaps (plateaus required, spikes rejected); max 3 pre-declared variants per hypothesis |
| Overfitting (multiple testing) | **Deflated Sharpe Ratio** (Bailey & López de Prado), **PBO** via combinatorially symmetric cross-validation, and reporting of the *number of trials attempted* |
| Regime overfit | Per-regime performance with sample sizes; strategies disabled in regimes without sufficient evidence |
| Cost understatement | Cost sensitivity at 1×, 2×, 3×; report the break-even cost |
| Cherry-picked benchmarks | Risk- and sector-matched benchmark; both local-currency and CAD-adjusted views for Canadian investors |

### 10.3 Validation protocol
```
TRAIN         earliest 60% of data        → freely used for hypothesis generation
VALIDATION    next 20%                    → model/parameter selection, walk-forward windows
TEST          next 20%                    → TOUCHED ONCE, at the end, per strategy (policy enforced in tooling)
LIVE (PAPER)  forward from deployment     → the only evidence that ever appears next to a user-facing signal
```
- **Walk-forward:** rolling train 3y → validate 6m → test 6m; step 6m; report fold-by-fold.
- **Purged K-fold with embargo:** purging of overlapping label horizons, embargo ≈ 1% of sample (≥ 5 sessions) between train and test.
- Test-set discipline is **technical**, not a promise: the test data is access-gated, and every access is logged with a reason and an approver.

### 10.4 Required metrics (all reported, no exceptions)
CAGR · total return · Sharpe · Sortino · Calmar · max drawdown (and duration) · win rate **with n** · average win · average loss · expectancy (in R) · profit factor · number of trades · average holding period · longest losing streak · turnover (annualized) · exposure · capacity estimate · cost sensitivity · per-regime breakdown · year-by-year breakdown.
**Presentation:** equity curves always show **net** of costs, with gross/net shown together, alongside the hypothetical-performance disclosure and the strategy's live paper-trading record (or the explicit statement that none exists yet).

### 10.5 Reporting template (user-facing)
```
Strategy: MOMBREAK-SWING v1.2.0     Universe: US liquid (cap>US$2B, ADV$>US$20M)
Period: 2014-01-01 → 2024-12-31     Data: PIT, delisted-inclusive
TRAIN 2014–2019 · VALIDATION 2020–2021 · TEST 2022–2024 (touched once: 2026-08-14)
Trades: 1,847          Turnover: 4.1×/yr        Avg hold: 12.4 sessions
Win rate: 51.3% (n=1,847)   Avg win 1.62R   Avg loss −0.94R   Expectancy +0.36R/trade
Profit factor 1.74      CAGR (net) 9.8%        Sharpe 1.12 (net)
Max DD −21.4% (Mar–Nov 2022)      Longest losing streak: 9
Capacity estimate: edge erodes above ~US$250M deployed (impact-cost model)
Cost sensitivity: break-even at 2.7× modeled costs
PBO: 0.21 (acceptable)   Deflated Sharpe: 0.84   Trials attempted: 11 (all logged)
Regimes: Strong Bull +0.52R/trade (n=612) · Weak Bull +0.41 (n=455) · Sideways +0.09 (n=388)
         High Vol +0.14 (n=241) · Bearish −0.11 (n=88) · Crisis n=63 (insufficient evidence → disabled)
LIVE PAPER: 4.5 months, 38 signals, expectancy +0.22R (below backtest — degradation flagged for review)

HYPOTHETICAL PERFORMANCE. Backtested results are not actual results, do not reflect the impact of
real-world execution frictions fully, and are not a guarantee of future results. Gross and net
performance are shown. Method, criteria, assumptions and limitations are available at [link].
```
Note how the honest outcome of that report is: *the strategy is disabled in Crisis, degraded in Bearish, and its live paper expectancy is below backtest → review flag*. That is what a credible research product looks like.

### 10.6 Definition of Done for any strategy before it can publish signals
1. Pre-registration filed (hypothesis, universe, rules, parameters, expected effect, falsifier).
2. Walk-forward + purged K-fold complete with no look-ahead in CI.
3. Cost sensitivity survives 2×; capacity documented.
4. PBO ≤ 0.3; deflated Sharpe reported.
5. Per-regime breakdown published; regimes with n < 30 disabled.
6. Test set touched once, results recorded.
7. **90 days of live paper trading** with expectancy degradation < 40% vs backtest.
8. Peer review by a second quant + sign-off recorded in the strategy registry.
9. Disclosure block + invalidation predicates present on every emitted signal.

---

## 11. PAPER TRADING & LIVE SCOREBOARD

| Element | Specification |
|---|---|
| Virtual portfolio | Starting capital, cash ledger, positions, corporate actions applied, FX conversion for cross-currency trades |
| Fill model | Conservative: next-bar open + half-spread + slippage; no fills without volume; gaps through stops fill adversely |
| Signal-to-outcome tracking | Every published signal is tracked to resolution (T1/T2/T3/STOP/EXPIRED/INVALIDATED) with MFE/MAE and realized R |
| Scoreboard | Public per-strategy: signal count, hit rate with n, expectancy, avg win/loss, max adverse excursion, degradation vs backtest, and days since inception |
| Degradation monitor | Alert if live expectancy < 60% of backtest, or hit rate drifts > 2σ, or the regime mix differs materially from the backtest |
| Attribution | Signal P/L decomposed into market/sector/security-specific components so "we were right about the stock but wrong about the market" is measurable |
| Comparison | Benchmarks: index return, sector-matched return, and a randomized-entry control (same universe, random dates) — the control is the most honest test of "is the signal doing anything?" |

---

## 12. QUANTITATIVE GOVERNANCE

**Strategy registry** (database-backed, immutable): every strategy record contains hypothesis, universe, entry/exit rules, parameters, version, owner, approvals, backtest report hash, live record link, and status (`research | probation | live | degraded | retired`).

**Pre-registration template** (must be completed before any test-set access):
> *Hypothesis:* momentum breakouts from tight bases in liquid US industrials outperform after costs on a 6–20 session horizon. *Falsifier:* expectancy ≤ 0.05R after 2× costs, or PBO > 0.3, or IC of the setup feature statistically indistinguishable from zero. *Universe/period/params:* [declared]. *Trials planned:* 3. *Kill criterion:* live expectancy < 60% of backtest over 3 months.

**Change control:** any change to a live strategy's code, parameters, or features creates a **new version**; the old version continues to publish until the new one passes its own live-paper gate. Users see which version produced a signal.

**Research log:** every experiment, including failures, is recorded and surfaced in an in-app "Research transparency" page. Failed hypotheses are a feature of a credible research product, not an embarrassment.

**Kill criteria** (as declared in `01_product_vision.md` §7.12): measured, automatic, and acted upon rather than rationalized.
