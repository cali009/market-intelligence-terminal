"""
Phase 32 Master Release Verification & Comprehensive Audit Suite

Validates end-to-end data integrity, off-exchange liquidity invariants, block trade
geometry, venue fragmentation bounds, Predicates 24 & 25 wiring, the two locked
licensing determinations, terminal UI workstation synchronization, and statutory
regulatory compliance (CSA Staff Notice 31-369 / SEC Publisher Exclusion
section 202(a)(11)(D)).

LOCKED LICENSING DETERMINATIONS UNDER AUDIT
-------------------------------------------
Decision 1(b): short-sale metrics gated to a RESEARCH_ONLY / non-commercial
               entitlement; must be absent from every commercially staged artifact.
Decision 2(b): ATS / dark-pool participation share is reference-only and must be
               excluded from all scoring and sentinel logic.
"""

import json
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter
from src.models.schemas import DarkPoolIntelligenceFeed


class TestDarkPoolReleaseAudit:
    """Master audit verifying Phase 32 production artifacts, invariants, and UI synchronization."""

    @classmethod
    def setup_class(cls):
        cls.data_dir = Path("data/feeds")
        cls.public_api_dir = Path("public/api")
        cls.web_dir = Path("web")
        cls.public_dir = Path("public")

        with open(cls.data_dir / "dark_pool_liquidity.json", "r", encoding="utf-8") as f:
            cls.feed = json.load(f)

    # ------------------------------------------------------------------
    # 1. Production artifact existence and schema validation
    # ------------------------------------------------------------------
    def test_dark_pool_feed_exists_and_serializes_cleanly(self):
        """The feed must exist in feeds/ and public/api/ (.json plus clean route)."""
        feed_path = self.data_dir / "dark_pool_liquidity.json"
        pub_path = self.public_api_dir / "dark_pool_liquidity.json"
        clean_pub_path = self.public_api_dir / "dark_pool_liquidity"

        assert feed_path.exists(), f"Missing feed: {feed_path}"
        assert pub_path.exists(), f"Missing public staged feed: {pub_path}"
        assert clean_pub_path.exists(), f"Missing public clean route: {clean_pub_path}"

        parsed = DarkPoolIntelligenceFeed.model_validate(json.loads(pub_path.read_text(encoding="utf-8")))
        assert parsed.universe_count >= 15
        assert parsed.aggregate_us_off_exchange_share_pct > 0.0
        assert parsed.aggregate_ca_off_exchange_share_pct > 0.0

    def test_public_clean_route_matches_json_route_byte_for_byte(self):
        """The extensionless clean route must be an exact copy of the .json route."""
        assert (self.public_api_dir / "dark_pool_liquidity.json").read_bytes() == \
               (self.public_api_dir / "dark_pool_liquidity").read_bytes()

    def test_staged_feed_matches_source_feed_content(self):
        """
        Staged and source feeds must agree semantically, ignoring BOTH wall-clock stamps.

        `generated_at` changes on every export and `as_of_date` rolls at UTC midnight,
        while other suites (test_edge_exporter -> export_all()) legitimately regenerate
        the source feed mid-run. Comparing them raw makes this test time-dependent.
        Dark-pool content is the property that actually matters.
        """
        source = json.loads((self.data_dir / "dark_pool_liquidity.json").read_text(encoding="utf-8"))
        staged = json.loads((self.public_api_dir / "dark_pool_liquidity.json").read_text(encoding="utf-8"))

        src_as_of = source.get("as_of_date")
        stg_as_of = staged.get("as_of_date")
        for payload in (source, staged):
            payload.pop("generated_at", None)
            payload.pop("as_of_date", None)

        assert source == staged, "Staged feed content drifted from source feed"

        # Tolerate a UTC-midnight roll; reject a genuinely stale deploy.
        from datetime import date as _date
        gap = (_date.fromisoformat(src_as_of) - _date.fromisoformat(stg_as_of)).days
        assert 0 <= gap <= 1, (
            f"Staged dark-pool feed is stale: source as_of {src_as_of} vs staged {stg_as_of}"
        )

    # ------------------------------------------------------------------
    # 2. DECISION 1(b): short metrics gated out of commercial artifacts
    # ------------------------------------------------------------------
    def test_no_short_metrics_in_any_commercial_artifact(self):
        """Neither the source feed nor the staged public feed may carry short metrics."""
        for path in (
            self.data_dir / "dark_pool_liquidity.json",
            self.public_api_dir / "dark_pool_liquidity.json",
            self.public_api_dir / "dark_pool_liquidity",
        ):
            data = json.loads(path.read_text(encoding="utf-8"))
            leaked = [s for s, d in data["dossiers"].items() if d.get("short_activity") is not None]
            assert leaked == [], f"{path}: short metrics leaked: {leaked}"

    def test_entitlement_gate_is_published_on_staged_feed(self):
        """The staged feed must carry the machine-readable licensing gate."""
        staged = json.loads((self.public_api_dir / "dark_pool_liquidity.json").read_text(encoding="utf-8"))
        gate = staged["entitlement_gate"]
        assert gate["short_metrics_license_class"] == "RESEARCH_ONLY_NON_COMMERCIAL"
        assert gate["short_metrics_commercial_redistribution_permitted"] is False
        assert gate["ats_share_excluded_from_scoring"] is True

    # ------------------------------------------------------------------
    # 3. DECISION 2(b): ATS excluded from scoring
    # ------------------------------------------------------------------
    def test_ats_share_marked_scoring_ineligible_in_staged_feed(self):
        """Every dossier in the staged feed must declare ATS share scoring-ineligible."""
        staged = json.loads((self.public_api_dir / "dark_pool_liquidity.json").read_text(encoding="utf-8"))
        for sym, d in staged["dossiers"].items():
            assert d["off_exchange"]["ats_share_scoring_eligible"] is False, sym

    def test_sentinel_metric_surface_carries_no_ats_field(self):
        """The engine's sentinel surface must not expose any ATS field."""
        from src.engine.dark_pool_liquidity import dark_pool_liquidity_engine

        feed = dark_pool_liquidity_engine.evaluate_dark_pool_liquidity(
            include_research_only_short_metrics=True, force_refresh=True
        )
        surface = dark_pool_liquidity_engine.compute_sentinel_metrics(feed)
        for sym, m in surface["per_symbol"].items():
            assert [k for k in m if "ats" in k.lower()] == [], f"{sym}: ATS leaked into sentinel surface"

    # ------------------------------------------------------------------
    # 4. Derived-data compliance firewall
    # ------------------------------------------------------------------
    def test_no_raw_per_venue_counts_in_staged_feed(self):
        """Fragmentation must publish aggregates only; no per-venue volume may leak."""
        staged = json.loads((self.public_api_dir / "dark_pool_liquidity.json").read_text(encoding="utf-8"))
        allowed = {
            "venue_count", "top_venue", "top_venue_share_pct",
            "venue_hhi", "liquidity_fragmentation_index", "fragmentation_regime",
        }
        for sym, d in staged["dossiers"].items():
            assert set(d["fragmentation"].keys()) == allowed, f"{sym}: {set(d['fragmentation'].keys())}"

    def test_no_absolute_ats_volume_in_staged_feed(self):
        """ATS participation must be a percentage, never an absolute share count."""
        staged = json.loads((self.public_api_dir / "dark_pool_liquidity.json").read_text(encoding="utf-8"))
        for sym, d in staged["dossiers"].items():
            fields = d["off_exchange"]
            assert "ats_volume" not in fields
            assert "ats_share_count" not in fields
            assert set(k for k in fields if "ats" in k.lower()) == {
                "ats_share_pct_reference_only", "ats_share_scoring_eligible"
            }

    def test_attribution_present_in_staged_feed(self):
        """FINRA and CIRO attribution must ship inside the staged feed."""
        staged = json.loads((self.public_api_dir / "dark_pool_liquidity.json").read_text(encoding="utf-8"))
        blob = " ".join(staged["entitlement_gate"]["attribution"])
        assert "FINRA" in blob
        assert "CIRO" in blob

    # ------------------------------------------------------------------
    # 5. Mathematical invariants
    # ------------------------------------------------------------------
    def test_off_exchange_volume_identity_holds_in_feed(self):
        """off_exchange_volume == consolidated_volume * OVR% for every shipped dossier."""
        for sym, d in self.feed["dossiers"].items():
            o = d["off_exchange"]
            expected = o["consolidated_volume"] * o["ovr_pct"] / 100.0
            assert abs(o["off_exchange_volume"] - expected) <= 1.0, sym

    def test_ats_share_never_exceeds_off_exchange_share_in_feed(self):
        """ATS volume is a subset of off-exchange volume."""
        for sym, d in self.feed["dossiers"].items():
            assert d["off_exchange"]["ats_share_pct_reference_only"] <= d["off_exchange"]["ovr_pct"] + 1e-9

    def test_hhi_lfi_identity_holds_in_feed(self):
        """LFI == 1 - HHI and HHI lies within [1/n, 1] for every shipped dossier."""
        for sym, d in self.feed["dossiers"].items():
            f = d["fragmentation"]
            assert abs(f["liquidity_fragmentation_index"] - (1.0 - f["venue_hhi"])) < 1e-3, sym
            assert (1.0 / f["venue_count"]) - 1e-6 <= f["venue_hhi"] <= 1.0 + 1e-9, sym

    def test_block_geometry_is_internally_consistent_in_feed(self):
        """Block count x average size must bound reported block volume."""
        for sym, d in self.feed["dossiers"].items():
            c = d["block_cluster"]
            assert c["block_count"] >= 1
            assert c["avg_block_shares"] >= 10_000
            assert c["block_volume"] <= c["block_count"] * c["avg_block_shares"]
            assert c["block_volume"] <= d["off_exchange"]["consolidated_volume"]

    # ------------------------------------------------------------------
    # 6. Sentinel, orchestrator, exporter wiring
    # ------------------------------------------------------------------
    def test_predicates_24_and_25_are_wired_into_sentinel_engine(self):
        """The sentinel must expose dark_pool_metrics and both Phase 32 predicates."""
        import inspect
        from src.engine.quant_intel_sentinel import quant_intel_sentinel

        sig = inspect.signature(quant_intel_sentinel.evaluate_portfolio_level_sentinels)
        assert "dark_pool_metrics" in sig.parameters

        source = inspect.getsource(quant_intel_sentinel.evaluate_portfolio_level_sentinels)
        for token in (
            "DARK_POOL_STEALTH_DISTRIBUTION",
            "SHORT_VOLUME_SQUEEZE_SPIKE",
            "SCRUTINIZE_BUY_SIGNALS",
            "FLAG_SHORT_COVERING_CASCADE",
        ):
            assert token in source, f"{token} not wired"

    def test_orchestrator_uses_commercial_gated_build(self):
        """The orchestrator must consume the commercial build (no research-only flag)."""
        src = (Path("src") / "engine" / "portfolio_orchestrator.py").read_text(encoding="utf-8")
        assert "from src.engine.dark_pool_liquidity import dark_pool_liquidity_engine" in src
        assert "dark_pool_liquidity_engine.evaluate_dark_pool_liquidity()" in src
        assert "dark_pool_metrics=dark_pool_liquidity_engine.compute_sentinel_metrics" in src
        # Must NOT request research-only short metrics in the commercial orchestration path.
        assert "include_research_only_short_metrics=True" not in src

    def test_edge_exporter_and_build_script_stage_the_feed(self):
        """Exporter and Firebase build script must both handle the Phase 32 feed."""
        exporter = (Path("src") / "data" / "edge_exporter.py").read_text(encoding="utf-8")
        assert "dark_pool_liquidity_engine.export_feed" in exporter
        assert "include_research_only_short_metrics=True" not in exporter

        build = (Path("scripts") / "build_firebase_public.py").read_text(encoding="utf-8")
        assert '("dark_pool_liquidity.json", "dark_pool_liquidity.json")' in build
        assert '("dark_pool_liquidity.json", "dark_pool_liquidity")' in build

    # ------------------------------------------------------------------
    # 7. Terminal UI workstation integration and synchronization
    # ------------------------------------------------------------------
    def test_terminal_ui_workstation_integration_and_synchronization(self):
        """web/index.html and public/index.html must both contain the Phase 32 workstation."""
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        pub_html = (self.public_dir / "index.html").read_text(encoding="utf-8")

        assert web_html == pub_html, "public/index.html drifted from web/index.html"

        for html_content in (web_html, pub_html):
            # Tab button and panel
            assert 'data-tab="darkpool"' in html_content
            assert 'id="tab-darkpool"' in html_content
            assert "'darkpool'" in html_content  # registered in switchTab tabIds

            # Workstation taxonomy tags
            assert "OFF-EXCHANGE VOLUME RATIO" in html_content
            assert "BLOCK TRADE CLUSTERING" in html_content
            assert "LIQUIDITY FRAGMENTATION" in html_content

            # Market filter controls
            assert "setDarkPoolMarketFilter('ALL')" in html_content
            assert "setDarkPoolMarketFilter('US')" in html_content
            assert "setDarkPoolMarketFilter('CA')" in html_content

            # Loader and renderer functions
            assert "loadDarkPoolAndRender" in html_content
            assert "renderDarkPoolView" in html_content
            assert "buildOvrBarChart" in html_content
            assert "refreshDarkPoolData" in html_content
            assert "flag24" in html_content

            # Feed fetching (both .json and extensionless fallback)
            assert "/api/dark_pool_liquidity.json" in html_content
            assert "/api/dark_pool_liquidity'" in html_content

            # Sentinel surfacing
            assert "P24 WATCH" in html_content
            assert "LIQUIDITY NOMINAL" in html_content

    def test_ui_discloses_both_licensing_determinations(self):
        """The UI must disclose the derived-data posture and the entitlement gate."""
        import html as html_mod

        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "DERIVED-DATA &amp; ENTITLEMENT NOTICE" in web_html
        assert "RESEARCH_ONLY_NON_COMMERCIAL" in web_html
        assert "Research-Only Entitlement Required" in web_html

        # The UI renders the ATS lag with an en dash entity; unescape before matching.
        rendered = html_mod.unescape(web_html)
        assert ("21–35 day lag" in rendered) or ("21-35 day lag" in rendered), (
            "UI must disclose the 21-35 day ATS publication lag"
        )

        # The machine-readable feed carries the same rationale in plain ASCII.
        gate = self.feed["entitlement_gate"]
        assert "21-35 day lag" in gate["ats_share_exclusion_reason"]

    def test_ui_does_not_fabricate_short_metric_values(self):
        """The UI must render a locked panel, not invented short-metric numbers."""
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        # The locked panel must state the fields are absent from the payload.
        assert "not present in this payload" in web_html
        # No client-side rendering of short values from the commercial feed.
        assert "short_activity.svr_pct" not in web_html
        assert "short_activity.days_to_cover" not in web_html
        assert "short_activity.squeeze_score" not in web_html

    def test_ui_marks_ats_share_as_reference_only(self):
        """The ATS column must be visibly labelled reference-only."""
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "ats_share_pct_reference_only" in web_html
        assert "excluded from all scoring" in web_html.lower() or "Reference only" in web_html

    # ------------------------------------------------------------------
    # 8. Statutory compliance
    # ------------------------------------------------------------------
    def test_master_release_compliance_linter_zero_violations(self):
        """All Phase 32 disclaimers, attribution lines, and UI copy must pass the linter."""
        linter = ImpersonalAdviceLinter()

        for disc in self.feed.get("disclaimers", []):
            assert linter.lint_text(disc) == [], f"Disclaimer violation: {disc[:70]}"

        for line in self.feed["entitlement_gate"]["attribution"]:
            assert linter.lint_text(line) == [], f"Attribution violation: {line[:70]}"

        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "STATUTORY RESEARCH NOTICE &amp; DATA ATTRIBUTION" in web_html

        for phrase in ("you should buy", "we recommend you", "buy this stock", "recommended allocation"):
            assert phrase not in web_html.lower()
