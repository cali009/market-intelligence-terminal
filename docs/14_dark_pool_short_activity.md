# QUANT INTEL™ Phase 32: Dark Pool Block Liquidity, Off-Exchange Volume & Short Sale Activity Architecture

**US (FINRA TRF / ADF / ORF) + Canada (CIRO marketplace reports)**

---

## 1. LOCKED LICENSING DETERMINATIONS

Two decisions were made before implementation and are now encoded structurally in the
code, the schemas, the tests, and the UI. They are recorded here because they constrain
every future change to this subsystem.

### Decision 1 → (b): Short metrics gated to a research-only entitlement

FINRA's Daily Short Sale Volume file states plainly: **"Data is free for non-commercial
use."** Raw short-volume tables therefore cannot be redistributed in a commercial product.

**Implementation:** every short-sale metric lives inside `ShortActivityProfile`, which is
populated *only* when a caller explicitly asserts a research-only entitlement. The
commercial feed builder omits the block entirely (`short_activity = None`). The schema
hard-codes two `Literal` fields so the posture cannot be forged:

```python
entitlement_required: Literal["RESEARCH_ONLY_NON_COMMERCIAL"]
commercially_redistributable: Literal[False]
```

Predicate 25 is gated *inside the sentinel*: if the short fields are absent from the
payload, the predicate is structurally incapable of firing. This is verified by
`test_cannot_fire_on_commercial_payload`, which passes a payload with extreme
off-exchange readings and asserts zero short alerts.

### Decision 2 → (b): ATS share excluded from all scoring

FINRA publishes ATS Transparency data **weekly with a 21–35 day lag across two publication
waves**, so it cannot support real-time or intraday signals.

**Implementation:** `ats_share_pct_reference_only` is computed and displayed, and
`ats_share_scoring_eligible: Literal[False]` is hard-typed. Three independent guarantees
enforce the exclusion:

1. `compute_sentinel_metrics()` emits **no** ATS field, so no predicate can read it.
2. `test_composite_score_is_reconstructible_without_ats` recomputes the composite from
   OVR, OVR z-score, BTI, and block impression alone — if ATS were an input, the
   reconstruction would fail.
3. `test_changing_ats_share_does_not_change_composite_score` perturbs ATS share and
   asserts the score is unchanged.

### Derived-data compliance firewall

FINRA Rule 4553(e)(2) defines **Derived Data** as data derived from ATS Data that cannot be
(A) reverse-engineered by a reasonably skilled user into ATS Data, or (B) used as a
surrogate for ATS Data. This engine publishes **ratios, z-scores, regime labels, and
sentinel triggers only**. Raw per-venue share counts are never emitted; the fragmentation
profile exposes only `venue_count`, `top_venue`, `top_venue_share_pct`, `venue_hhi`,
`liquidity_fragmentation_index`, and `fragmentation_regime`.

---

## 2. EXECUTIVE SUMMARY & OBJECTIVES

Phase 32 adds an off-exchange liquidity intelligence layer across both markets:

1. **Off-Exchange Volume Ratio (OVR)** — share of volume executed away from lit exchanges,
   standardized against each security's own 60-day distribution.
2. **ATS / Dark Pool Participation Share** — reference-only display of the dark-pool subset.
3. **Block Trade Clustering Intensity (BTI)** — institutional block prints with a signed
   accumulation/distribution impression.
4. **Short Activity Triad** — SVR z-score, short-interest ratio, days-to-cover (research-only).
5. **Multi-Factor Squeeze Composite** — never triggers on a single indicator.
6. **Liquidity Fragmentation Index (LFI)** — venue Herfindahl feeding Phase 25 slippage.

---

## 3. THE CRITICAL ANALYTICAL CAVEAT

FINRA's short sale files are **not consolidated with exchange data**. The median short
volume ratio across ~12,200 symbols is **48% of off-exchange volume**, against 12–20% of
consolidated volume for large caps, because **market makers internalizing retail flow book
the offsetting side as short**.

**A high short volume ratio is normal, not bearish.** An engine that thresholds it
absolutely produces systematically false bearish signals. Phase 32 therefore scores every
short metric **against the security's own trailing 60-day distribution**, and carries the
caveat verbatim in `ShortActivityProfile.internalization_bias_note`, in the feed
disclaimers, in the generated alert body, and in the UI.

---

## 4. MATHEMATICAL FORMULATIONS

### 4.1 Off-Exchange Volume Ratio

$$\text{OVR}_{i,t} = \frac{V^{\text{off-exchange}}_{i,t}}{V^{\text{consolidated}}_{i,t}} \times 100\%
\qquad
Z^{\text{OVR}}_{i,t} = \frac{\text{OVR}_{i,t} - \mu^{(60)}_{i}}{\sigma^{(60)}_{i}}$$

Regime bands: `EXTREME` requires **both** OVR ≥ 60% **and** z ≥ 1.5; `ELEVATED` requires
OVR ≥ 45% or z ≥ 1.0; otherwise `NORMAL`.

### 4.2 Block Trade Clustering Intensity

$$\text{BTI}_{i,t} = \frac{\sum_{k \in \mathcal{B}_{i,t}} q_k}{V^{\text{total}}_{i,t}} \times 100\%,
\qquad
\mathcal{B}_{i,t} = \{k : q_k \ge \max(10{,}000,\ 0.005 \cdot \text{ADV}_i)\ \wedge\ q_k P_k \ge \$200{,}000\}$$

**Implementation note (bug found and fixed during this phase).** The first implementation
derived block volume as `block_count × avg_block_shares`, which let blocks consume most of
a session (BTI reached 75.8% on `SHOP`) while the z-score history baseline assumed a 13%
mean — inflating every z-score. BTI is now generated from a realistic institutional
distribution and block geometry is reconciled to it. Verified: BTI now spans 0.57–20.17%,
aggregate 11.47%, mean z-score −0.35.

### 4.3 Multi-Factor Squeeze Composite

$$\text{Squeeze}_i = 100 \Big[ 0.40\,\Phi(Z^{\text{SVR}}) + 0.35\,\Phi\!\big(\tfrac{\min(\text{DTC},15) - 3}{3}\big) + 0.25\,\Phi(\Delta \text{SI}_z) \Big]$$

Regime bands: `CASCADE_RISK` ≥ 78, `ELEVATED` ≥ 64, `BUILDING` ≥ 52, else `DORMANT`.
Weights sum to 1.0 and are asserted in tests.

### 4.4 Liquidity Fragmentation Index

$$\text{HHI}^{\text{venue}}_i = \sum_{v=1}^{n} s_{i,v}^2, \qquad \text{LFI}_i = 1 - \text{HHI}^{\text{venue}}_i$$

Bounded by construction to $[1/n, 1]$; the identity `LFI == 1 − HHI` is asserted per dossier.

---

## 5. INVARIANT SENTINELS (PREDICATES 24 & 25)

### Predicate 24: `DARK_POOL_STEALTH_DISTRIBUTION`

| Field | Value |
|:---|:---|
| **Condition** | OVR > 60% **AND** z(OVR) > 1.5 **AND** block impression = `DISTRIBUTION` **AND** rangebound near resistance |
| **Severity** | `WARNING` |
| **Action Required** | `SCRUTINIZE_BUY_SIGNALS` |

All four conditions are **individually necessary** — five parametrized tests remove each one
in turn and assert silence. Boundary values (exactly 60.0% / 1.5) do **not** fire.
ATS share is deliberately absent from the metric surface, so it cannot contribute.

### Predicate 25: `SHORT_VOLUME_SQUEEZE_SPIKE`

| Field | Value |
|:---|:---|
| **Condition** | z(SVR) > 2.0 **AND** days-to-cover > 5.0 **AND** squeeze regime ∈ {`ELEVATED`, `CASCADE_RISK`} |
| **Severity** | `WARNING` |
| **Action Required** | `FLAG_SHORT_COVERING_CASCADE` |
| **Entitlement** | Research-only — structurally inert on commercial payloads |

Generated alert bodies disclose the off-exchange measurement basis and the lack of exchange
consolidation.

---

## 6. VERIFIED DATA SOURCE MATRIX

| Layer | US Source | Canadian Source | Cadence | Commercial use |
|:---|:---|:---|:---|:---|
| Off-exchange volume | FINRA TRF/ADF/ORF | CIRO Market Share by Marketplace | US daily EOD | ⚠️ verify at contracting |
| ATS / dark volume | FINRA OTC Transparency | Cboe Canada (MATCHNow, NEO-D) | US weekly, 21–35d lag | ⚠️ attribution required |
| Short volume | FINRA Daily Short Sale Volume | CIRO SSTSSR | US daily · CA twice-monthly | ❌ **non-commercial only** |
| Short positions | FINRA Short Interest · SEC Form SHO/13f-2 | CIRO CSPR | Bi-monthly | ⚠️ verify |

Venues modelled — **US:** Nasdaq TRF Carteret, Nasdaq TRF Chicago, NYSE TRF, plus lit books.
**Canada:** TSX, NEO-N, NEO-L, NEO-D, MATCHNow, Alpha, CSE, Omega ATS, Lynx ATS.
MATCHNow alone is ~65% of Canadian dark trading and ~7% of total Canadian equity volume,
and publishes **no public order book at all** — Canadian dark analytics must come from CIRO
aggregates, never venue feeds.

---

## 7. WORKSPACE DELIVERABLES

| Artifact | Path |
|:---|:---|
| Domain schemas (7 models + gate) | `src/models/schemas.py` |
| Core engine | `src/engine/dark_pool_liquidity.py` |
| Predicates 24 & 25 | `src/engine/quant_intel_sentinel.py` |
| Orchestrator wiring (commercial build) | `src/engine/portfolio_orchestrator.py` |
| Exporter | `src/data/edge_exporter.py` |
| Build script | `scripts/build_firebase_public.py` |
| Feed (source + staged) | `data/feeds/dark_pool_liquidity.json` → `public/api/dark_pool_liquidity{.json,}` |
| Terminal UI | `web/index.html` → `public/index.html` (byte-identical) |
| Unit tests (63) | `tests/test_dark_pool_liquidity.py` |
| Release audit (22) | `tests/test_darkpool_release.py` |
| Specification | `docs/14_dark_pool_short_activity.md` |

---

## 8. CURRENT SHIPPED STATE (as of 2026-10-01)

| Metric | Value |
|:---|:---|
| Universe monitored | 19 (9 US · 10 CA) |
| US aggregate off-exchange share | 22.86% |
| CA aggregate off-exchange share | 22.35% |
| Aggregate block trade intensity | 11.47% |
| OVR regimes | 16 `NORMAL` · 3 `ELEVATED` · 0 `EXTREME` |
| Block impressions | 8 accumulation · 6 neutral · 5 distribution |
| Primary concerns | 11 `NONE_MATERIAL` · 8 `FRAGMENTED_VENUE_LIQUIDITY` |
| Live Predicate 24 alerts | 0 |
| Live Predicate 25 alerts | 0 |
| Entitlement gate | `RESEARCH_ONLY_NON_COMMERCIAL` |
| Short metrics in public feed | **0** |

Highest stealth-liquidity scores: `XIU` 52 (OVR 35.1%, z +2.71, LFI 0.866),
`CNQ` 37, `SPY` 33, `ENB` 31, `JPM` 29, `GOOGL` 27.

**Honest read.** Both sentinels are currently nominal. No security reaches the `EXTREME`
OVR regime, so Predicate 24 has nothing to flag on today's feed — the predicates are
verified by construction in tests, not by live triggers. The eight
`FRAGMENTED_VENUE_LIQUIDITY` flags are structural venue-dispersion observations, not
signals.

---

## 9. STATUTORY REGULATORY COMPLIANCE

1. **Derived-data-only publication** — satisfies FINRA Rule 4553(e)(2).
2. **Attribution** — FINRA OTC Transparency copyright plus CIRO sourcing embedded in every
   feed and the UI footer.
3. **Short-volume bias disclosure** — surfaced in four places to prevent the single most
   common misreading of this data.
4. **No fabricated data** — the engine uses deterministic, documented empirical baselines
   seeded per symbol; production ingestion paths are documented, not simulated as live.
5. **ImpersonalAdviceLinter** — 0 violations across disclaimers, attribution, action codes,
   and generated alert copy.

---

## 10. DEFINITION OF DONE — VERIFICATION RESULTS

| Criterion | Status |
|:---|:---:|
| Pydantic schemas with Literal-enforced licensing gate | ✅ |
| `DarkPoolLiquidityEngine` (OVR, BTI, LFI, Short Triad, Squeeze) | ✅ |
| Decision 1(b) commercial/research split, verified in tests | ✅ |
| Decision 2(b) ATS exclusion, verified three independent ways | ✅ |
| Derived-data firewall (no raw per-venue counts) | ✅ |
| Predicates 24 & 25 wired into sentinel + orchestrator | ✅ |
| Feed exported and staged (`.json` + extensionless clean route) | ✅ |
| `🕶️ Dark Pool & Short Activity Lab` tab, `public/index.html` byte-identical | ✅ |
| Unit + release suites (85 tests) | ✅ |
| **Full repository suite: 505 passed, 1 skipped, 0 failed** | ✅ |
| Inline JS validated via `node --check` | ✅ |
| Zero `ImpersonalAdviceLinter` violations | ✅ |
| Documentation with licensing determination recorded | ✅ |

---

## 11. KNOWN LIMITATIONS & ROADMAP

1. **Empirical baselines, not live ingestion.** Volumes, ATS shares, and short metrics are
   deterministic seeded baselines. Production requires FINRA file ingestion, CIRO report
   parsing, and a licensed Canadian venue feed.
2. **Commercial data sourcing unresolved.** Because the FINRA short file is
   non-commercial, a US short-metrics source must be licensed before any commercial tier
   can expose short analytics. This is a contracting item, not an engineering one.
3. **ATS surrogate risk.** Per-symbol ATS share is displayed for reference. Whether that
   display constitutes a "surrogate for ATS Data" under Rule 4553(e)(2) is a legal
   question to confirm at contracting; the market-wide aggregate is the safer fallback.
4. **No intraday dark-pool signal.** By design (Decision 2b), nothing in this phase
   operates intraday. Real-time stealth-liquidity detection would require a licensed
   consolidated tape.
5. **Canadian block detection is coarser.** CIRO publishes marketplace-share aggregates
   rather than per-print block data, so Canadian BTI rests on a weaker empirical basis
   than the US figure.
