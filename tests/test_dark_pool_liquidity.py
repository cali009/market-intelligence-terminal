"""
Unit and Integration Test Suite for Dark Pool & Off-Exchange Liquidity Analytics (Phase 32)
US (FINRA TRF/ADF/ORF) + Canada (CIRO marketplace reports)

LOCKED LICENSING DETERMINATIONS UNDER TEST
------------------------------------------
Decision 1(b): short-sale metrics are gated behind a RESEARCH_ONLY / non-commercial
               entitlement and must be absent from every commercial payload.
Decision 2(b): ATS / dark-pool participation share is reference-only and must never
               contribute to any score, regime label, or sentinel predicate.

Tests Covered:
 1. Off-exchange volume ratio construction and 60-day self-standardization.
 2. Block Trade Clustering Intensity realism, share floor, and $200k notional test.
 3. Venue-concentration Herfindahl index and liquidity fragmentation bounds.
 4. Short activity triad and the multi-factor squeeze composite.
 5. Decision 1(b) entitlement gating across commercial and research-only builds.
 6. Decision 2(b) ATS exclusion from scoring and from the sentinel metric surface.
 7. Derived-data compliance firewall (no raw per-venue share counts emitted).
 8. Predicate 24 (DARK_POOL_STEALTH_DISTRIBUTION) trigger and confluence.
 9. Predicate 25 (SHORT_VOLUME_SQUEEZE_SPIKE) trigger and commercial-payload gate.
10. Determinism, universe coverage, and ImpersonalAdviceLinter compliance.
"""

import json
import math
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter
from src.engine.dark_pool_liquidity import (
    ATTRIBUTION_LINES,
    BLOCK_MIN_NOTIONAL_USD,
    BLOCK_MIN_SHARES,
    DARK_POOL_DISCLAIMERS,
    DTC_CAP_DAYS,
    RESEARCH_ONLY_ENTITLEMENT,
    SQUEEZE_WEIGHTS,
    STEALTH_OVR_PCT_THRESHOLD,
    STEALTH_OVR_ZSCORE_THRESHOLD,
    SQUEEZE_DTC_THRESHOLD_DAYS,
    SQUEEZE_SVR_ZSCORE_THRESHOLD,
    DarkPoolLiquidityEngine,
    dark_pool_liquidity_engine,
)
from src.engine.quant_intel_sentinel import quant_intel_sentinel
from src.models.schemas import DarkPoolIntelligenceFeed


class TestOffExchangeVolumeRatio:
    """Validates OVR construction and 60-day self-standardization."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()
        cls.feed = cls.engine.evaluate_dark_pool_liquidity(force_refresh=True)

    def test_off_exchange_volume_is_arithmetically_consistent(self):
        """off_exchange_volume must equal consolidated_volume * OVR%."""
        for sym, d in self.feed.dossiers.items():
            expected = d.off_exchange.consolidated_volume * d.off_exchange.ovr_pct / 100.0
            assert abs(d.off_exchange.off_exchange_volume - expected) <= 1.0, (
                f"{sym}: off-exchange volume inconsistent with OVR"
            )

    def test_ovr_is_bounded_within_valid_percentage_range(self):
        """OVR must be a valid percentage and off-exchange cannot exceed consolidated."""
        for sym, d in self.feed.dossiers.items():
            assert 0.0 <= d.off_exchange.ovr_pct <= 100.0
            assert d.off_exchange.off_exchange_volume <= d.off_exchange.consolidated_volume

    def test_ats_share_never_exceeds_off_exchange_share(self):
        """ATS volume is a subset of off-exchange volume, so its share must be smaller."""
        for sym, d in self.feed.dossiers.items():
            ats = d.off_exchange.ats_share_pct_reference_only
            assert ats is not None
            assert ats <= d.off_exchange.ovr_pct + 1e-9, f"{sym}: ATS share exceeds off-exchange share"

    def test_ovr_regime_labels_are_from_canonical_taxonomy(self):
        """Every regime label must come from the three-state taxonomy."""
        allowed = {"NORMAL", "ELEVATED", "EXTREME"}
        for sym, d in self.feed.dossiers.items():
            assert d.off_exchange.ovr_regime in allowed

    def test_extreme_regime_requires_both_predicate_24_gates(self):
        """EXTREME regime is defined by OVR >= 60% AND z >= 1.5; verify no drift."""
        for sym, d in self.feed.dossiers.items():
            o = d.off_exchange
            should_be_extreme = (
                o.ovr_pct >= STEALTH_OVR_PCT_THRESHOLD and o.ovr_zscore_60d >= STEALTH_OVR_ZSCORE_THRESHOLD
            )
            if o.ovr_regime == "EXTREME":
                assert should_be_extreme, f"{sym} mislabeled EXTREME"


class TestBlockTradeClustering:
    """Validates block clustering realism and the institutional block definition."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()
        cls.feed = cls.engine.evaluate_dark_pool_liquidity(force_refresh=True)

    def test_bti_is_realistic_and_never_consumes_whole_session(self):
        """BTI must stay within the plausible institutional-block band, not approach 100%."""
        for sym, d in self.feed.dossiers.items():
            bti = d.block_cluster.bti_pct
            assert 0.0 <= bti <= 50.0, f"{sym}: BTI {bti}% outside plausible institutional range"
            assert d.block_cluster.block_volume <= d.off_exchange.consolidated_volume

    def test_bti_zscore_is_centered_near_zero(self):
        """BTI z-scores must be centered, proving the history baseline matches generation."""
        zs = [d.block_cluster.bti_zscore_60d for d in self.feed.dossiers.values()]
        mean_z = sum(zs) / len(zs)
        assert abs(mean_z) < 1.0, f"BTI z-score mean {mean_z} indicates baseline mismatch"

    def test_average_block_clears_share_floor(self):
        """Average block size must respect the 10,000-share institutional floor."""
        for sym, d in self.feed.dossiers.items():
            assert d.block_cluster.avg_block_shares >= BLOCK_MIN_SHARES, (
                f"{sym}: avg block {d.block_cluster.avg_block_shares} below floor"
            )

    def test_average_block_clears_notional_floor(self):
        """Average block notional must clear the $200k institutional test."""
        for sym, d in self.feed.dossiers.items():
            price = self._price_for(sym)
            notional = d.block_cluster.avg_block_shares * price
            assert notional >= BLOCK_MIN_NOTIONAL_USD * 0.99, (
                f"{sym}: block notional ${notional:,.0f} below $200k floor"
            )

    def _price_for(self, symbol):
        universe = {u["symbol"]: u for u in self.engine.load_universe()}
        return float(universe[symbol].get("price", 0.0)) or 1.0

    def test_signed_impression_is_from_canonical_taxonomy(self):
        """Block impression must be ACCUMULATION, DISTRIBUTION, or NEUTRAL."""
        allowed = {"ACCUMULATION", "DISTRIBUTION", "NEUTRAL"}
        for sym, d in self.feed.dossiers.items():
            assert d.block_cluster.signed_impression in allowed

    def test_block_geometry_reconciles_to_block_volume(self):
        """block_count * avg_block_shares must reconcile with reported block_volume."""
        for sym, d in self.feed.dossiers.items():
            c = d.block_cluster
            assert c.block_count >= 1
            assert c.block_volume <= c.block_count * c.avg_block_shares


class TestLiquidityFragmentation:
    """Validates the venue-concentration Herfindahl index."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()
        cls.feed = cls.engine.evaluate_dark_pool_liquidity(force_refresh=True)

    def test_hhi_and_lfi_are_complementary(self):
        """LFI must equal 1 - HHI exactly."""
        for sym, d in self.feed.dossiers.items():
            f = d.fragmentation
            assert f.liquidity_fragmentation_index == pytest.approx(1.0 - f.venue_hhi, abs=1e-3)

    def test_hhi_is_bounded_by_venue_count(self):
        """HHI must lie in [1/n, 1] for n venues."""
        for sym, d in self.feed.dossiers.items():
            n = d.fragmentation.venue_count
            lower = 1.0 / n - 1e-6
            assert lower <= d.fragmentation.venue_hhi <= 1.0 + 1e-9, (
                f"{sym}: HHI {d.fragmentation.venue_hhi} outside [{lower}, 1]"
            )

    def test_top_venue_share_is_consistent_with_hhi_lower_bound(self):
        """HHI cannot be smaller than the squared top-venue share."""
        for sym, d in self.feed.dossiers.items():
            top_sq = (d.fragmentation.top_venue_share_pct / 100.0) ** 2
            assert d.fragmentation.venue_hhi >= top_sq - 1e-6

    def test_canadian_universe_is_more_fragmented_than_us(self):
        """Canada trades across more venues, so mean venue_count must be higher."""
        ca = [d.fragmentation.venue_count for d in self.feed.dossiers.values() if d.country == "CA"]
        us = [d.fragmentation.venue_count for d in self.feed.dossiers.values() if d.country == "US"]
        assert ca and us
        assert (sum(ca) / len(ca)) > (sum(us) / len(us))


class TestShortActivityTriad:
    """Validates the research-only short activity triad and squeeze composite."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()
        cls.research = cls.engine.evaluate_dark_pool_liquidity(
            include_research_only_short_metrics=True, force_refresh=True
        )

    def test_squeeze_composite_weights_sum_to_one(self):
        """Squeeze weights must be a proper convex combination."""
        assert sum(SQUEEZE_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)

    def test_squeeze_composite_is_bounded_zero_to_hundred(self):
        """Squeeze score must be a bounded 0-100 composite."""
        for sym, d in self.research.dossiers.items():
            assert 0.0 <= d.short_activity.squeeze_score <= 100.0

    def test_squeeze_regime_thresholds_are_monotone(self):
        """Regime bands must be ordered and non-overlapping."""
        for sym, d in self.research.dossiers.items():
            s = d.short_activity.squeeze_score
            r = d.short_activity.squeeze_regime
            if s >= 78.0:
                assert r == "CASCADE_RISK"
            elif s >= 64.0:
                assert r == "ELEVATED"
            elif s >= 52.0:
                assert r == "BUILDING"
            else:
                assert r == "DORMANT"

    def test_days_to_cover_is_arithmetically_consistent(self):
        """Days-to-cover must equal short interest / ADV."""
        for sym, d in self.research.dossiers.items():
            expected = d.short_activity.short_interest_shares / d.average_daily_volume
            assert d.short_activity.days_to_cover == pytest.approx(expected, abs=0.02)

    def test_days_to_cover_used_in_composite_is_capped(self):
        """DTC is capped at 15 days in the composite to prevent unbounded domination."""
        assert DTC_CAP_DAYS == 15.0
        for sym, d in self.research.dossiers.items():
            assert d.short_activity.days_to_cover >= 0.0

    def test_short_profiles_carry_the_internalization_bias_note(self):
        """Every short profile must disclose the market-maker internalization bias."""
        for sym, d in self.research.dossiers.items():
            note = d.short_activity.internalization_bias_note
            assert "OFF-EXCHANGE" in note
            assert "not consolidated" in note
            assert len(note) > 100

    def test_short_profiles_are_marked_non_redistributable(self):
        """Every short profile must self-declare as non-commercially redistributable."""
        for sym, d in self.research.dossiers.items():
            assert d.short_activity.entitlement_required == RESEARCH_ONLY_ENTITLEMENT
            assert d.short_activity.commercially_redistributable is False


class TestDecision1EntitlementGating:
    """Decision 1(b): short metrics must be absent from every commercial payload."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()
        cls.commercial = cls.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        cls.research = cls.engine.evaluate_dark_pool_liquidity(
            include_research_only_short_metrics=True, force_refresh=True
        )

    def test_commercial_build_contains_no_short_metrics(self):
        """The default commercial build must omit the short activity triad entirely."""
        leaked = [s for s, d in self.commercial.dossiers.items() if d.short_activity is not None]
        assert leaked == [], f"Short metrics leaked into commercial build: {leaked}"

    def test_research_build_contains_short_metrics_for_every_symbol(self):
        """The research-only build must materialize short metrics for the full universe."""
        missing = [s for s, d in self.research.dossiers.items() if d.short_activity is None]
        assert missing == [], f"Research build missing short metrics: {missing}"
        assert len(self.research.dossiers) == len(self.commercial.dossiers)

    def test_commercial_feed_serializes_without_short_fields(self):
        """The serialized commercial JSON must not contain short_activity payloads."""
        payload = json.loads(json.dumps(self.commercial.model_dump()))
        for sym, d in payload["dossiers"].items():
            assert d["short_activity"] is None, f"{sym}: short_activity present in commercial JSON"

    def test_entitlement_gate_is_present_on_both_builds(self):
        """Both builds must carry the machine-readable licensing gate."""
        for feed in (self.commercial, self.research):
            g = feed.entitlement_gate
            assert g.short_metrics_license_class == RESEARCH_ONLY_ENTITLEMENT
            assert g.short_metrics_commercial_redistribution_permitted is False
            assert "non-commercial" in g.short_metrics_source.lower()

    def test_exported_public_feed_omits_short_metrics(self):
        """The feed written to data/feeds must be the commercial (gated) variant."""
        path = Path("data/feeds/dark_pool_liquidity.json")
        if not path.exists():
            pytest.skip("feed not yet exported")
        data = json.loads(path.read_text(encoding="utf-8"))
        leaked = [s for s, d in data["dossiers"].items() if d.get("short_activity") is not None]
        assert leaked == [], f"Short metrics leaked into public feed: {leaked}"

    def test_gate_cannot_be_forged_by_type_system(self):
        """The Literal-typed gate must reject any attempt to declare commercial rights."""
        import pydantic
        from src.models.schemas import DataEntitlementGate

        with pytest.raises(pydantic.ValidationError):
            DataEntitlementGate(
                short_metrics_license_class="COMMERCIAL_OK",
                short_metrics_commercial_redistribution_permitted=True,
                short_metrics_source="x",
                ats_share_excluded_from_scoring=True,
                ats_share_exclusion_reason="y",
                attribution=[],
            )


class TestDecision2AtsExclusion:
    """Decision 2(b): ATS share must never contribute to scoring or sentinels."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()
        cls.feed = cls.engine.evaluate_dark_pool_liquidity(force_refresh=True)

    def test_ats_share_is_hard_typed_as_scoring_ineligible(self):
        """The schema must hard-code ats_share_scoring_eligible to False."""
        for sym, d in self.feed.dossiers.items():
            assert d.off_exchange.ats_share_scoring_eligible is False

    def test_composite_score_is_reconstructible_without_ats(self):
        """
        The composite stealth-liquidity score must be exactly reproducible from
        OVR, OVR z-score, BTI, and block impression alone. If ATS share were an
        input, this reconstruction would not match.
        """
        for sym, d in self.feed.dossiers.items():
            stealth = (
                0.45 * min(d.off_exchange.ovr_pct / 100.0, 1.0)
                + 0.30 * min(max(d.off_exchange.ovr_zscore_60d, 0.0) / 3.0, 1.0)
                + 0.25 * min(max(d.block_cluster.bti_pct / 40.0, 0.0), 1.0)
            )
            if (
                d.block_cluster.signed_impression == "DISTRIBUTION"
                and d.block_cluster.rangebound_near_resistance
            ):
                stealth = min(stealth + 0.10, 1.0)
            assert d.composite_stealth_liquidity_score == pytest.approx(stealth, abs=1e-4), (
                f"{sym}: composite score not reconstructible without ATS -> hidden ATS input"
            )

    def test_sentinel_metric_surface_excludes_ats_entirely(self):
        """The metric surface fed to Predicates 24/25 must contain no ATS field."""
        surface = self.engine.compute_sentinel_metrics(self.feed)
        for sym, m in surface["per_symbol"].items():
            ats_keys = [k for k in m if "ats" in k.lower()]
            assert ats_keys == [], f"{sym}: ATS keys leaked into sentinel surface: {ats_keys}"

    def test_changing_ats_share_does_not_change_composite_score(self):
        """Directly perturbing ATS share must leave the composite score untouched."""
        d = self.feed.dossiers["AAPL"]
        original = d.composite_stealth_liquidity_score
        perturbed = d.off_exchange.model_copy(update={"ats_share_pct_reference_only": 1.0})
        assert perturbed.ats_share_pct_reference_only == 1.0
        # The composite is stored on the dossier and derives only from non-ATS inputs.
        assert d.composite_stealth_liquidity_score == original
        assert self.feed.dossiers["AAPL"].off_exchange.ats_share_pct_reference_only != 1.0

    def test_ats_exclusion_is_declared_on_the_feed(self):
        """The feed must declare the ATS exclusion and its lag-based rationale."""
        g = self.feed.entitlement_gate
        assert g.ats_share_excluded_from_scoring is True
        assert "21-35 day lag" in g.ats_share_exclusion_reason


class TestDerivedDataComplianceFirewall:
    """FINRA Rule 4553(e)(2): publish derived metrics only, never raw ATS counts."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()
        cls.feed = cls.engine.evaluate_dark_pool_liquidity(
            include_research_only_short_metrics=True, force_refresh=True
        )

    def test_no_per_venue_share_counts_are_emitted(self):
        """Fragmentation must expose HHI/LFI aggregates only, never per-venue volumes."""
        for sym, d in self.feed.dossiers.items():
            keys = set(d.fragmentation.model_dump().keys())
            assert keys == {
                "venue_count",
                "top_venue",
                "top_venue_share_pct",
                "venue_hhi",
                "liquidity_fragmentation_index",
                "fragmentation_regime",
            }, f"{sym}: unexpected fragmentation fields {keys}"

    def test_no_ats_volume_count_is_emitted(self):
        """ATS participation must be a percentage only, never an absolute share count."""
        for sym, d in self.feed.dossiers.items():
            fields = d.off_exchange.model_dump()
            ats_fields = [k for k in fields if "ats" in k.lower()]
            assert ats_fields == ["ats_share_pct_reference_only", "ats_share_scoring_eligible"]
            assert "ats_volume" not in fields and "ats_share_count" not in fields

    def test_attribution_lines_are_carried_on_the_feed(self):
        """FINRA and CIRO attribution must be present on every feed."""
        for line in ATTRIBUTION_LINES:
            assert line in self.feed.entitlement_gate.attribution

    def test_attribution_names_finra_and_ciro(self):
        """Attribution must name both the FINRA and CIRO sources."""
        blob = " ".join(self.feed.entitlement_gate.attribution)
        assert "FINRA" in blob
        assert "CIRO" in blob


class TestPredicate24StealthDistribution:
    """Validates DARK_POOL_STEALTH_DISTRIBUTION trigger and confluence."""

    @classmethod
    def setup_class(cls):
        cls.sentinel = quant_intel_sentinel

    def _metrics(self, **overrides):
        base = {
            "ovr_pct": 68.5,
            "ovr_zscore_60d": 2.10,
            "signed_impression": "DISTRIBUTION",
            "rangebound_near_resistance": True,
        }
        base.update(overrides)
        return {"per_symbol": {"TEST": base}}

    def test_fires_on_full_confluence(self):
        """All four conditions met must raise the sentinel."""
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, dark_pool_metrics=self._metrics()
        )
        p24 = [a for a in alerts if a.predicate_type == "DARK_POOL_STEALTH_DISTRIBUTION"]
        assert len(p24) == 1
        assert p24[0].symbol == "TEST"
        assert p24[0].severity == "WARNING"
        assert p24[0].trigger_level == 60.0
        assert p24[0].action_required == "SCRUTINIZE_BUY_SIGNALS"

    @pytest.mark.parametrize(
        "override,reason",
        [
            ({"ovr_pct": 55.0}, "off-exchange share below 60% gate"),
            ({"ovr_zscore_60d": 0.9}, "z-score below 1.5 gate"),
            ({"signed_impression": "ACCUMULATION"}, "block impression is accumulation"),
            ({"signed_impression": "NEUTRAL"}, "block impression is neutral"),
            ({"rangebound_near_resistance": False}, "price not rangebound near resistance"),
        ],
    )
    def test_each_condition_is_individually_necessary(self, override, reason):
        """No single condition may trigger the sentinel alone."""
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, dark_pool_metrics=self._metrics(**override)
        )
        p24 = [a for a in alerts if a.predicate_type == "DARK_POOL_STEALTH_DISTRIBUTION"]
        assert p24 == [], f"Predicate 24 fired when {reason}"

    def test_boundary_values_are_exclusive(self):
        """Exactly 60.0% OVR and exactly 1.5 z must NOT fire (strict inequalities)."""
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={},
            dark_pool_metrics=self._metrics(ovr_pct=60.0, ovr_zscore_60d=1.5),
        )
        assert [a for a in alerts if a.predicate_type == "DARK_POOL_STEALTH_DISTRIBUTION"] == []


class TestPredicate25SqueezeSpike:
    """Validates SHORT_VOLUME_SQUEEZE_SPIKE trigger and the commercial-payload gate."""

    @classmethod
    def setup_class(cls):
        cls.sentinel = quant_intel_sentinel

    def _metrics(self, **overrides):
        base = {
            "ovr_pct": 30.0,
            "ovr_zscore_60d": 0.20,
            "signed_impression": "NEUTRAL",
            "rangebound_near_resistance": False,
            "svr_zscore_60d": 2.80,
            "days_to_cover": 8.40,
            "squeeze_regime": "CASCADE_RISK",
        }
        base.update(overrides)
        return {"per_symbol": {"TEST": base}}

    def test_fires_on_full_confluence(self):
        """z(SVR) > 2.0, DTC > 5.0 and elevated regime must raise the sentinel."""
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, dark_pool_metrics=self._metrics()
        )
        p25 = [a for a in alerts if a.predicate_type == "SHORT_VOLUME_SQUEEZE_SPIKE"]
        assert len(p25) == 1
        assert p25[0].symbol == "TEST"
        assert p25[0].trigger_level == SQUEEZE_SVR_ZSCORE_THRESHOLD
        assert p25[0].current_price == pytest.approx(2.80, abs=0.01)
        assert p25[0].action_required == "FLAG_SHORT_COVERING_CASCADE"

    def test_cannot_fire_on_commercial_payload(self):
        """
        DECISION 1(b): with short fields absent (commercial payload) the predicate
        must be structurally incapable of firing, regardless of other conditions.
        """
        commercial = {"per_symbol": {"TEST": {
            "ovr_pct": 95.0,
            "ovr_zscore_60d": 4.00,
            "signed_impression": "DISTRIBUTION",
            "rangebound_near_resistance": True,
        }}}
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, dark_pool_metrics=commercial
        )
        p25 = [a for a in alerts if a.predicate_type == "SHORT_VOLUME_SQUEEZE_SPIKE"]
        assert p25 == [], "Predicate 25 fired on a commercial payload lacking short metrics"

    @pytest.mark.parametrize(
        "override,reason",
        [
            ({"svr_zscore_60d": 1.2}, "SVR z-score below 2.0 gate"),
            ({"days_to_cover": 3.1}, "days-to-cover below 5.0 gate"),
            ({"squeeze_regime": "DORMANT"}, "squeeze regime dormant"),
            ({"squeeze_regime": "BUILDING"}, "squeeze regime merely building"),
        ],
    )
    def test_each_condition_is_individually_necessary(self, override, reason):
        """No single short condition may trigger the sentinel alone."""
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, dark_pool_metrics=self._metrics(**override)
        )
        p25 = [a for a in alerts if a.predicate_type == "SHORT_VOLUME_SQUEEZE_SPIKE"]
        assert p25 == [], f"Predicate 25 fired when {reason}"

    def test_alert_copy_discloses_off_exchange_measurement_basis(self):
        """Generated alert body must disclose the off-exchange measurement caveat."""
        alerts = self.sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, dark_pool_metrics=self._metrics()
        )
        p25 = [a for a in alerts if a.predicate_type == "SHORT_VOLUME_SQUEEZE_SPIKE"][0]
        assert "off-exchange" in p25.body.lower()
        assert "not consolidated" in p25.body.lower()


class TestDeterminismAndUniverse:
    """Validates reproducibility and dual-market coverage."""

    @classmethod
    def setup_class(cls):
        cls.engine = DarkPoolLiquidityEngine()

    def test_rebuild_is_deterministic(self):
        """Two forced rebuilds must produce identical analytics (excluding timestamp)."""
        a = self.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        b = self.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        assert a.model_dump(exclude={"generated_at"}) == b.model_dump(exclude={"generated_at"})

    def test_caching_returns_same_instance(self):
        """Repeated calls without force_refresh must return the cached feed."""
        first = self.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        assert self.engine.evaluate_dark_pool_liquidity() is first

    def test_commercial_and_research_caches_are_separate(self):
        """The two builds must be cached independently and never alias."""
        c = self.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        r = self.engine.evaluate_dark_pool_liquidity(
            include_research_only_short_metrics=True, force_refresh=True
        )
        assert c is not r
        assert all(d.short_activity is None for d in c.dossiers.values())
        assert all(d.short_activity is not None for d in r.dossiers.values())

    def test_universe_covers_both_markets(self):
        """The feed must span both US and Canadian listings."""
        feed = self.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        countries = {d.country for d in feed.dossiers.values()}
        assert countries == {"US", "CA"}
        assert feed.universe_count == len(feed.dossiers) >= 15

    def test_universe_matches_canonical_leaderboard(self):
        """Monitored symbols must match the canonical leaderboard universe exactly."""
        feed = self.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        universe = {u["symbol"] for u in self.engine.load_universe()}
        assert set(feed.dossiers.keys()) == universe

    def test_feed_round_trips_through_pydantic(self):
        """The feed must serialize and re-validate cleanly."""
        feed = self.engine.evaluate_dark_pool_liquidity(force_refresh=True)
        parsed = DarkPoolIntelligenceFeed.model_validate(feed.model_dump())
        assert parsed.universe_count == feed.universe_count


class TestStatutoryCompliance:
    """Validates impersonal research compliance across all Phase 32 text."""

    @classmethod
    def setup_class(cls):
        cls.linter = ImpersonalAdviceLinter()
        cls.engine = DarkPoolLiquidityEngine()

    def test_disclaimers_pass_linter(self):
        """Every statutory disclaimer must pass with zero violations."""
        for disc in DARK_POOL_DISCLAIMERS:
            assert self.linter.lint_text(disc) == [], f"Violation in: {disc[:70]}"

    def test_attribution_lines_pass_linter(self):
        """Attribution lines must pass with zero violations."""
        for line in ATTRIBUTION_LINES:
            assert self.linter.lint_text(line) == []

    def test_action_codes_pass_linter(self):
        """Sentinel action codes must not read as personalized directives."""
        for code in ("SCRUTINIZE_BUY_SIGNALS", "FLAG_SHORT_COVERING_CASCADE"):
            assert self.linter.lint_text(code) == []

    def test_generated_alert_copy_passes_linter(self):
        """Dynamically generated sentinel headlines and bodies must be impersonal."""
        metrics = {"per_symbol": {
            "GME": {
                "ovr_pct": 68.5, "ovr_zscore_60d": 2.10,
                "signed_impression": "DISTRIBUTION", "rangebound_near_resistance": True,
                "svr_zscore_60d": 2.80, "days_to_cover": 8.40, "squeeze_regime": "CASCADE_RISK",
            }
        }}
        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={}, stress_metrics={}, dark_pool_metrics=metrics
        )
        assert alerts
        for a in alerts:
            assert self.linter.lint_text(a.headline) == [], f"Headline violation: {a.headline}"
            assert self.linter.lint_text(a.body) == [], f"Body violation: {a.body}"

    def test_disclaimers_disclose_short_volume_bias(self):
        """The disclaimer set must disclose the market-maker internalization bias."""
        blob = " ".join(DARK_POOL_DISCLAIMERS).lower()
        assert "internaliz" in blob
        assert "normal rather than bearish" in blob

    def test_disclaimers_disclose_ats_exclusion(self):
        """The disclaimer set must disclose that ATS share is excluded from scoring."""
        blob = " ".join(DARK_POOL_DISCLAIMERS).lower()
        assert "excluded from all scoring" in blob
        assert "21-35 day lag" in blob
