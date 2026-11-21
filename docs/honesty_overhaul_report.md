# Honesty Overhaul — §8 Deliverables

Branch: `honesty-overhaul` on `cali009/market-intelligence-terminal`
Commits (one per phase, plus one follow-up fix):

| Phase | Commit | Subject |
|---|---|---|
| 1 | `e0b1372` | replace the fabricated paper-trading panel with a computed validation gate engine |
| 2 | `0287666` | delete the fabricated operations, licensing and market-microstructure layers |
| 3a | `dc4e383` | real VIX and a corrected P/E |
| 3b | `e461a65` | entry zones, target ladders and a real R:R |
| 3c | `4476c69` | header staleness badge and C$500 default capital |
| 4 | `38e662b` | publish the research ledger verbatim and add a Research Ledger tab |
| 5 | `7f85c88` | Core Portfolio tab (localStorage only) and §7 check 10 |
| 2-fix | `a486c90` | stop publishing the simulated microstructure and TCA data |

Verified pushed: `git ls-remote origin honesty-overhaul` → `a486c907f11ba4f5755e3fe76f5058dda702b0ee`, equal to local `HEAD`.

**Deploy: NOT DONE.** See §4.

---

## 1. Check table

All ten §7 checks are implemented in `scripts/honesty_checks.py` and run automatically. No check is a stub.

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | No hard-coded statistics | **PASS** | No literal win rate / expectancy / Sharpe assigned to an output field anywhere in `src/`; every such field traces to a function of trade data. |
| 2 | Scorecard recomputation equals stored | **PASS** | Every strategy in `strategy_validation.json` is recomputed from `backtests.json` inside `src/engine/strategy_validation.py` at export time and asserted equal. |
| 3 | No two identical full scorecards | **PASS** | 8 distinct scorecards; the 4 formerly-identical ones (`n=19, 78.9%, +1.35R, Sharpe 3.78`) are gone with `paper_trading.json`. |
| 4 | Forbidden strings removed | **PASS** | Extended this round to scan published **payloads**, not just prose. Verified the check *fails* against the pre-fix `quant_intel.json` and passes against the current feeds. |
| 5 | One VIX everywhere = latest `^VIX` close | **PASS** | One measured close, `22.91` on `2026-11-16`, published once in `daily_summary.json → volatility_index` with source, age and a Canadian-proxy note. |
| 6 | P/E in 0–100 or flagged | **PASS** | 0 unflagged out-of-range values. `CNR = −60.20` carries `pe_flag: NEGATIVE_EARNINGS`. |
| 7 | T1 < T2 < T3 and zone low < high | **PASS** | 8 plans; `risk_reward_ratio` takes 8 distinct values. |
| 8 | One PBO everywhere; no "low overfitting risk" when all lose OOS | **PASS** | One value `0.133`, labelled `not informative — no strategy profitable out-of-sample`. |
| 9 | Quantlab panel renders; banner matches backtest JSON | **PASS** | 7 experiments, each with a verdict; non-empty `programme_status`; tab wired to the feed; banner strategy count agrees with the backtest bundle. |
| 10 | No console errors; no panel stuck on "Loading…" | **PASS** | 20 panes / 20 registered tab ids, 7 explicit "not available" empty states, jsdom page-load harness `RESULT: PASS`. |

Phase-scoped runs: `--phase 1` 4/4 · `--phase 2` 1/1 · `--phase 3` 3/3 · `--phase 4` 1/1 · `--phase 5` 1/1.
`scripts/page_load_selfcheck.mjs`: **PASS** — 40 `/api` files available, 80 verdict rows and 6 status cards rendered, 0 console errors, 0 uncaught exceptions.

**pytest: 884 passed / 6 failed / 2 skipped (284 s).** The 6 failures are pre-existing and unrelated to this work — each is a test pinned to a constant that the market-data refresh invalidated (a 25,000 position cap vs 25,647.3 observed; an Adaptive Sharpe floor of 0.35 vs 0.34; three Black-Litterman / meta-label / HRP pinned values). None of the six is in the honesty-overhaul code path. They are **not** fixed and **not** hidden.

---

## 2. Before / after

"Before" values are read from the live site snapshot pulled at the start of this work (`live_snapshot/`), so they are the figures that were actually being served.

### Paper scorecards

Before: `paper_trading.json` published four **identical** scorecards, each `status: "LIVE_QUALIFIED"`.

| Strategy | Before | After (computed from that strategy's own trades) |
|---|---|---|
| ENSEMBLE_8F | 19 trades · 78.9% · +1.35R · Sharpe 3.78 · PF 6.99 · MaxDD 4.2% · `LIVE_QUALIFIED` | **FAILED** · 96 OOS trades · 6 months · −1.4851% · −0.4565R · t = −3.410 · gates 2 pass / 6 fail |
| TAC01_PULLBACK | 19 · 78.9% · +1.35R · Sharpe 3.78 · PF 7.04 · MaxDD 4.2% · `LIVE_QUALIFIED` | **INSUFFICIENT SAMPLE** · 80 trades · 6 mo · −0.3140% · t = −0.624 |
| TAC02_SQUEEZE | 19 · 78.9% · +1.35R · Sharpe 3.78 · PF 7.04 · MaxDD 4.2% · `LIVE_QUALIFIED` | **INSUFFICIENT SAMPLE** · 65 · 6 mo · −0.5263% · t = −1.190 |
| TAC04_MOMENTUM | 19 · 78.9% · +1.35R · Sharpe 3.78 · PF 7.02 · MaxDD 4.2% · `LIVE_QUALIFIED` | **FAILED** · 72 · 6 mo · −1.4856% · t = −3.652 |
| TAC03_BREAKOUT_CHASE | 14 · 35.7% · 0.0R · `RETIRED` | **FAILED** · 7 · 4 mo · −3.2357% · t = −2.736 |
| PO3_LIQUIDITY_SWEEP | not published | **INSUFFICIENT SAMPLE** · 10 · 6 mo · +1.5070% · t = +0.965 |
| PO3_META_GATED | not published | **INSUFFICIENT SAMPLE** · 8 · 4 mo · +1.8563% · t = +1.142 |
| ADAPTIVE_DUAL_REGIME | not published | **INSUFFICIENT SAMPLE** · 18 · 6 mo · +0.8600% · t = +0.841 |

Summary now published: **8 evaluated · 0 passed all gates · 3 failed · 5 insufficient sample · 0 promoted to live.** Forward record: `exists: false, entries: 0` — no strategy has any timestamped forward paper record, so gate 8 cannot pass for any of them.

### Scanner statistics — removed, not computed

15 scanner definitions previously carried typed-in `historical_win_rate_pct`, `expectancy_r`, `forward_5d_return_pct` and `sample_size` (e.g. `59.2% / +0.52R / +1.85% / n=142`). No scanner rule ID appears anywhere in `backtester.py`, so none of those numbers was computable. All four fields are deleted. Each definition now carries one field:

> `stats_status`: "No per-rule performance statistics are published. The scanner rules are not wired into the walk-forward backtest engine, so win rate, forward return, expectancy and sample size cannot be computed for them. Publishing an estimate would be fabrication."

### PBO

| | Before | After |
|---|---|---|
| Value | `0.0` | `0.133` |
| Label | `LOW_OVERFITTING_RISK` | `not informative — no strategy profitable out-of-sample` |

The old feed claimed low overfitting risk while simultaneously reporting `fdr_rejected_count: 8`. The computed value is now derived from 15 CPCV paths / 6 blocks / 5 embargo days, and the assessment states plainly that it must not be read as reassurance because no strategy is credibly profitable.

### VIX

| | Before | After |
|---|---|---|
| Source | Two contradictory literals: `default_vix = 16.0` (`macro_regime.py:91`, applied to every date) and `21.4` hard-coded in the alert body | One measured `^VIX` close, fetched and stored in `volatility_index_daily` (505 rows, 2024-11-13 → 2026-11-16) |
| Value | 16.0 everywhere in regimes, 21.4 in alerts | **22.91** on 2026-11-16, published once |
| Canadian index | not addressed | `canadian_index: null` — `^VIXC` returns HTTP 404 from this source (verified); Canadian regimes are labelled as using the US index as a proxy |

Regime history changed materially as a result: SPY now resolves **492 regimes** across 6 states (STRONG_BULL 276, WEAK_BULL 135, HIGH_VOLATILITY 33, BEARISH 27, CONSOLIDATION 17, CRISIS 4), with **34 dates skipped** for want of a VIX close rather than silently defaulted. Previously every date used a flat 16.0, which suppressed the volatility states entirely.

### P/E

| Symbol | Before | After | Flag |
|---|---|---|---|
| AAPL | 175.98 | **46.34** | — |
| RY | 69.72 | **19.62** | — |
| TD | 73.32 | **14.25** | — |
| CNR | 72.35 | **−60.20** | `NEGATIVE_EARNINGS` |
| XOM | 169.04 | **23.98** | — |
| MSFT | 29.19 | **28.86** | — |

Root cause proven by direct DB read: the engine summed a single fiscal period instead of trailing twelve months (RY Q1 5.785 B vs FY 20.369 B = 3.5×; TD 4.043 vs 20.538 = 5.1×), and fell back to `shares = 1,000,000,000` when share count was missing. Both removed. Currency is now reconciled per fact with the Bank of Canada rate, and `pe_flag` travels with `pe_ratio` into `daily_summary`, `leaderboard` and `quant_intel`. `shap_engine` records a per-row `pe_imputed` boolean instead of silently substituting `25.0`.

### Targets and entry zones

Before: 13 plans, `risk_reward_ratio = 2.5` on **all 13** (an algebraic tautology — `T2 = entry + 2.5 × risk`, so `(T2 − entry)/risk` is 2.5 by construction), and `realized_rr_t1` populated with invented fills.

| Symbol | Before zone | After zone | After T1 / T2 / T3 | R:R |
|---|---|---|---|---|
| RY | [286.00, 289.32] | **[283.76, 286.66]** | 291.91 / 312.29 / 328.25 | 0.58 |
| TD | [175.16, 177.01] | **no plan** — fails the PULLBACK precondition and the 1-ATR proximity rule | — | — |
| MSFT | **[529.39, 527.39]** (inverted — low > high) | **[510.83, 521.57]** | 521.83 / 616.41 / 675.47 | 0.10 |
| SPY | [749.26, 756.92] | **[734.55, 742.65]** | 741.38 / 849.33 / 914.95 | 0.03 |
| NVDA | **no plan before, no plan after** — NVDA is not in the signal feed on either side | — | — | — |

Plan count 13 → **8** after enforcing `t1 < t2 < t3`, `entry_low < entry_high`, the PULLBACK moving-average precondition and the 1-ATR proximity rule. `risk_reward_ratio` now takes **8 distinct values** (0.03–0.98) and equals `planned_rr_t1`. `realized_rr_t1` is `None` on every plan — there is no forward record to realise against.

**The R:R figures are now honest and therefore unattractive.** Seven of eight plans have a first-target reward below one unit of risk. That is the arithmetic, not a regression.

### Default capital

| | Before | After |
|---|---|---|
| Default | `$100,000` | **`C$500`** |
| Presets | $50K / $100K / $250K | C$500 / C$2.5K / C$10K / C$25K (still user-configurable) |
| Zero-share case | silently rounded up | explicit red notice: "No position: 1% of this capital is less than the per-share risk. Do not round up." |

Header now renders a data stamp with a staleness badge, e.g. `data as of … · latest bar 2026-11-16 · 143.6h old STALE`.

---

## 3. Removed panels and claims

### Panels removed

`tab-microstructure` (order book / DOM, CVD, OBI, "LIVE STREAMING ~42 ms") · `tab-darkpool` (ATS / dark-pool analytics) · `tab-optionssurface` (options GEX surface) · the Phase 12 "Production & Entitlements" tab (replaced by a small **System Status** box) · the CASL/TCPA consent ledger and delivery telemetry · the in-browser TCA simulator · capacity planning · "MANDATORY AI GOVERNANCE" · the Order Flow & DOM badge on the Quant Intel cards · the Execution TCA telemetry strip.

### Feeds retired (no longer generated or published)

`paper_trading.json` · `production_health.json` · `microstructure_snapshot.json` · `dark_pool_liquidity.json` · `options_intelligence.json` · `summary.json`

### Engines deleted

`src/engine/paper_trading.py` · `production_scale.py` · `microstructure.py` · `dark_pool_liquidity.py` · `options_surface_gex.py` · `streaming_server.py`

Deleted rather than left on disk unreferenced, so nothing can be quietly re-wired to them.

### Claims removed

`PROMOTED TO LIVE` (4) · `LIVE_QUALIFIED` (7) · `edge confirmed` (4) · `LIVE STREAMING` (2) · `survivorship-bias-free` (1) · `Official EOD Exchange Consolidation` (1) · `white-label` (1) · the fabricated paper-trading scoreboard · the 6 hard-coded alert definitions (rate 4.25 / −25 bps / FX 1.4136, VIX 21.4, score 86 / RVOL 2.45, stop C$81.20 / −1.0R, drawdown −4.3% vs −6.0%, RVOL 2.8 / score 79) · 15 scanner definitions' typed-in win rates and expectancies · 39 fallback/constant sites removed from `web/index.html` in the Phase 1 diff alone (count of removed lines matching a `|| <number>` or `? <number>` fallback pattern; the true total is higher because not every constant matched that pattern).

Replaced with statements of what is true:
- **"No broker connection. No orders are ever sent."**
- **"There are no subscribers, tiers, or entitlements. This is a personal research dashboard, not a product."**
- **"There is no SOC 2 report, no audit, and no disaster-recovery programme. None has ever been performed."**
- **"No TMX Datalinx subscription; this platform is not a certified reseller of TMX data."**
- Feed names now state their real source: "Yahoo Finance via the unofficial chart API (query1.finance.yahoo.com), end-of-day", "SEC EDGAR XBRL", "Bank of Canada Valet" — with `survivorship_biased: true` on each equity feed, because the 19-ticker universe is drawn from currently-listed large caps.
- AI panel: was `hallucination_rate 0.00 / citation_resolution 100.0` from 6 samples → now `samples_checked: 6, not_a_hallucination_measurement: true`; model cards carry `citation: null, hallucination: null`.

**Found and fixed after the phase was reported complete** (commit `a486c90`): the Phase 2 panel deletion left `microstructure.py` wired into `quant_intel.py`. The published `quant_intel.json` still carried, for all 19 symbols, a `microstructure_metrics` block (CVD, order-book imbalance, Kyle's lambda, buyer/seller volume split) and every trade plan carried an execution strategy, child-slice count, estimated fill price and slippage estimate derived from a **generated** order book — rendered on-screen as `DIRECT_MARKET (1 child slice) | Est. Fill: $352.79 | +0.0 bps`. All of it is now gone, the four TCA fields are `Optional` and null, and `OrchestratedPosition.expected_slippage_bps` / `execution_strategy` are `Optional` so a required float can no longer force an invented value back into the feed. Check 4 now fails on any published feed carrying a synthesised field.

---

## 4. Files changed and deploy

`git diff --stat origin/main..HEAD`: **234 files changed, 102,790 insertions(+), 181,441 deletions(−)** — 17 added, 30 deleted, 187 modified.

New code: `src/engine/strategy_validation.py` · `src/engine/system_status.py` · `src/data/vix_ingestion.py` · `scripts/honesty_checks.py` · `scripts/page_load_selfcheck.mjs` · `tests/test_strategy_validation.py` · `tests/test_phase32_invariants.py` · `data/feeds/quantlab_status.json` · `migrations/002_honesty_drop_scanner_stats.sql`.

### Deploy command

**I did not deploy.** This sandbox has no Firebase CLI and no credentials, and you chose the `handoff` route. The repo deploys through `.github/workflows/firebase_deploy.yml`, which runs on `main` only. To deploy:

```
git checkout main
git merge honesty-overhaul
git push origin main
```

The push to `main` triggers the GitHub Actions hosting deploy automatically. If you prefer a local deploy instead:

```
npx firebase-tools deploy --only hosting
```

**Live URL: `https://market-intelligence-terminal.web.app`** — this is the URL the "before" snapshot was pulled from. **It currently serves the OLD code.** It will only change after the merge above.

---

## 5. Uncertainties

Stated plainly. Nothing below is marked done.

1. **Deploy not performed.** The live site still serves the fabricated version. The merge command is above.
2. **The feeds are 6 days stale.** `bar_1d` ends 2026-11-16 and the VIX close is 143.6 h old, so the header STALE badge fires on load. I re-exported the feeds from the existing database rather than running the daily pipeline, because this sandbox's access to the Yahoo chart API is unverified and I would not guess at fresh prices. **Re-run `scripts/run_daily_pipeline.py` before deploying** if you want current data. Every number in this report describes the 2026-11-16 close.
3. **`backtests.json` was not regenerated.** `scripts/run_backtests.py` was last run in Phase 1. The scorecards above are computed from that ledger. Re-running it against current bars will change the trade counts and means.
4. **6 pytest failures remain, unfixed.** `test_meta_label_classifier::test_train_meta_classifier`, `test_meta_label_dataset::test_sqlite_persistence_and_querying`, `test_po3_backtest` ×2, `test_portfolio_bayesian::test_black_litterman_posterior_updating`, `test_portfolio_release::test_hrp_and_orchestration_mathematical_consistency`. All are pinned to constants the market-data refresh invalidated. Fixing them means deciding whether the new observed values are right, which is your call, not mine.
5. **Running the test suite still mutates `data/market_intel.db`.** I traced it: no row counts change and no schema objects are added or removed — the file grows by one 4 KB page. It is a page-allocation artifact, not fabricated data, but the working tree needs `git checkout -- data/market_intel.db` after a suite run. I did not chase it further.
6. **No forward paper record exists.** `data/forward_paper_log.jsonl` does not exist at all; the feed reports `exists: false, entries: 0`. Gate 8 (≥3 months of timestamped forward paper trading) therefore cannot pass for any strategy, and no strategy can be called validated, promoted or live. The panel says so.
7. **`quantlab_status.json` is hand-maintained, not generated.** It is a verbatim copy of the JSON in §5 of your brief, describing experiments run in your separate research system. Nothing in this repo computes, verifies or recomputes those figures. The Research Ledger tab displays them as published and says so on the panel.
8. **The FX feed's key names are backwards.** `spot_cad_usd` holds C$ per US$1 (1.4298) and `spot_usd_cad` holds the reciprocal (0.6994). I did not rename them — too many consumers. The Core Portfolio tab instead takes whichever value is dimensionally C$ per US$1, prints the rate it used, and refuses to convert when neither is plausible. The underlying naming bug is still there.
9. **The Core Portfolio tab has never been used by a human.** I verified its arithmetic in jsdom (XIU 100 @ 54.00 CAD + SPY 10 @ 739.95 USD → C$15,979.81; weights 33.79 / 66.21; drift ∓26.21 pp; suggested 77 BUY / 3 SELL whole units), but nobody has entered real holdings into it.
10. **Canadian regime classification uses the US VIX as a proxy.** There is no Canadian volatility index available from this source (`^VIXC` → HTTP 404, `^VXN` is NASDAQ-only). The panel labels it. It is still a proxy.
11. **`portfolio_stress.json` still contains `VIX_SPIKE_PCT: 250.0` and `VIX_MAX_LEVEL: 82.7`.** These are historical scenario parameters (82.69 was the real March 2020 intraday high), not a current volatility claim, so I left them. Check 5 exempts that file by name. If you would rather they were gone, say so.
12. **The 19-ticker universe is survivorship-biased and remains so.** Every equity feed now states `survivorship_biased: true`, but the bias itself is not fixed — that needs a point-in-time universe, which is a data-sourcing project, not a code change.
