"""
Phase 33 Master Release Verification & Comprehensive Audit Suite

Validates end-to-end data integrity, realized-volatility estimator consistency,
GARCH-family MLE validity, HAR-RV forecast integrity, Variance Risk Premium
arithmetic, Predicates 26 & 27 wiring, the Cboe index licensing firewall,
terminal UI workstation synchronization, and statutory regulatory compliance
(CSA Staff Notice 31-369 / SEC Publisher Exclusion section 202(a)(11)(D)).
"""

from datetime import date
import json
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter
from src.models.schemas import VolatilityVrpFeed

# Cboe index names and tickers that must never appear in a shipped artifact.
BANNED_INDEX_TOKENS = ["VIX", "VIX9D", "VIX3M", "VIX6M", "VVIX", "Cboe Volatility Index"]


class TestVolatilityVrpReleaseAudit:
    """Master audit verifying Phase 33 production artifacts, invariants, and UI synchronization."""

    @classmethod
    def setup_class(cls):
        cls.data_dir = Path("data/feeds")
        cls.public_api_dir = Path("public/api")
        cls.web_dir = Path("web")
        cls.public_dir = Path("public")

        with open(cls.data_dir / "volatility_vrp.json", "r", encoding="utf-8") as f:
            cls.feed = json.load(f)

    # ------------------------------------------------------------------
    # 1. Production artifact existence and schema validation
    # ------------------------------------------------------------------
    def test_volatility_feed_exists_and_serializes_cleanly(self):
        """The feed must exist in feeds/ and public/api/ (.json plus clean route)."""
        feed_path = self.data_dir / "volatility_vrp.json"
        pub_path = self.public_api_dir / "volatility_vrp.json"
        clean_pub_path = self.public_api_dir / "volatility_vrp"

        assert feed_path.exists(), f"Missing feed: {feed_path}"
        assert pub_path.exists(), f"Missing public staged feed: {pub_path}"
        assert clean_pub_path.exists(), f"Missing public clean route: {clean_pub_path}"

        parsed = VolatilityVrpFeed.model_validate(json.loads(pub_path.read_text(encoding="utf-8")))
        assert parsed.universe_count >= 15
        assert parsed.median_realized_vol_pct > 0.0

    def test_public_clean_route_matches_json_route_byte_for_byte(self):
        """The extensionless clean route must be an exact copy of the .json route."""
        assert (self.public_api_dir / "volatility_vrp.json").read_bytes() == \
               (self.public_api_dir / "volatility_vrp").read_bytes()

    def test_staged_feed_matches_source_feed_content(self):
        """
        Staged and source feeds must agree semantically, ignoring BOTH wall-clock stamps.

        `generated_at` changes on every export, and `as_of_date` rolls at UTC midnight,
        while other suites (test_edge_exporter -> export_all()) legitimately regenerate
        the source feed mid-run. Comparing them raw makes this test order- and
        time-dependent. VRP and GARCH content is the property that actually matters.
        """
        source = json.loads((self.data_dir / "volatility_vrp.json").read_text(encoding="utf-8"))
        staged = json.loads((self.public_api_dir / "volatility_vrp.json").read_text(encoding="utf-8"))

        src_as_of = source.get("as_of_date")
        stg_as_of = staged.get("as_of_date")
        for payload in (source, staged):
            payload.pop("generated_at", None)
            payload.pop("as_of_date", None)

        assert source == staged, "Staged feed content drifted from source feed"

        # Tolerate a UTC-midnight roll; reject a genuinely stale deploy.
        gap = (date.fromisoformat(src_as_of) - date.fromisoformat(stg_as_of)).days
        assert 0 <= gap <= 1, (
            f"Staged volatility feed is stale: source as_of {src_as_of} vs staged {stg_as_of}"
        )

    # ------------------------------------------------------------------
    # 2. Cboe index licensing firewall
    # ------------------------------------------------------------------
    def test_no_cboe_index_token_in_any_shipped_artifact(self):
        """No Cboe index value, ticker, or name may appear in any shipped artifact."""
        for path in (
            self.data_dir / "volatility_vrp.json",
            self.public_api_dir / "volatility_vrp.json",
            self.public_api_dir / "volatility_vrp",
        ):
            blob = path.read_text(encoding="utf-8")
            for token in BANNED_INDEX_TOKENS:
                assert token not in blob, f"{path}: banned index token '{token}'"

    def test_feed_declares_index_reference_free(self):
        """The machine-readable licensing posture must be published."""
        staged = json.loads((self.public_api_dir / "volatility_vrp.json").read_text(encoding="utf-8"))
        assert staged["cboe_index_reference_free"] is True

    # ------------------------------------------------------------------
    # 3. Mathematical invariants
    # ------------------------------------------------------------------
    def test_vrp_variance_identity_holds_in_feed(self):
        """VRP variance points == IV^2 - RV^2 for every shipped dossier with VRP."""
        for sym, d in self.feed["dossiers"].items():
            v = d["vrp"]
            if not v["available"]:
                continue
            expected = v["implied_vol_pct"] ** 2 - v["realized_vol_pct"] ** 2
            assert abs(v["vrp_variance_pts"] - expected) < 0.05, sym

    def test_vrp_volatility_identity_holds_in_feed(self):
        """VRP volatility points == IV - RV for every shipped dossier with VRP."""
        for sym, d in self.feed["dossiers"].items():
            v = d["vrp"]
            if not v["available"]:
                continue
            expected = v["implied_vol_pct"] - v["realized_vol_pct"]
            assert abs(v["vrp_vol_pts"] - expected) < 0.02, sym

    def test_no_fabricated_implied_volatility_in_feed(self):
        """Symbols without an options surface must carry null IV and null VRP."""
        for sym, d in self.feed["dossiers"].items():
            v = d["vrp"]
            if not v["available"]:
                assert v["implied_vol_pct"] is None, f"{sym}: fabricated implied volatility"
                assert v["vrp_variance_pts"] is None
                assert v["vrp_vol_pts"] is None
                assert v["seller_edge"] is None
                assert v["unavailable_reason"]

    def test_vrp_coverage_matches_options_universe(self):
        """VRP availability must exactly match the Phase 30 options surface coverage."""
        opts_path = self.data_dir / "options_intelligence.json"
        if not opts_path.exists():
            pytest.skip("options feed absent")
        opts = json.loads(opts_path.read_text(encoding="utf-8"))
        optionable = {s for s, d in opts["dossiers"].items() if d.get("thirty_day_atm_iv_pct")}
        available = {s for s, d in self.feed["dossiers"].items() if d["vrp"]["available"]}
        assert available == optionable & set(self.feed["dossiers"].keys())
        assert self.feed["vrp_coverage_count"] == len(available)

    def test_all_three_garch_variants_present_with_valid_persistence(self):
        """Every dossier must carry three variant fits with stationary persistence."""
        for sym, d in self.feed["dossiers"].items():
            variants = {f["variant"] for f in d["model_selection"]["fits"]}
            assert variants == {"GARCH_1_1", "EGARCH_1_1", "GJR_GARCH_1_1"}, sym
            for f in d["model_selection"]["fits"]:
                assert 0.0 < f["persistence"] < 1.0, f"{sym}/{f['variant']}: {f['persistence']}"
                assert abs(f["log_likelihood"]) < 1e9, f"{sym}/{f['variant']} hit divergence penalty"
                assert f["converged"] is True, f"{sym}/{f['variant']} did not converge"

    def test_aic_selection_is_consistent_in_feed(self):
        """The selected variant must be the minimum-AIC fit in every dossier."""
        for sym, d in self.feed["dossiers"].items():
            aics = {f["variant"]: f["aic"] for f in d["model_selection"]["fits"]}
            best = min(aics, key=aics.get)
            assert d["model_selection"]["best_variant"] == best, sym
            assert abs(d["model_selection"]["best_aic"] - aics[best]) < 0.01

    def test_realized_estimators_are_all_present_and_finite(self):
        """All four estimators must be reported for every symbol."""
        for sym, d in self.feed["dossiers"].items():
            r = d["realized"]
            for key in ("close_to_close_pct", "parkinson_pct", "garman_klass_pct", "yang_zhang_pct"):
                assert isinstance(r[key], (int, float)) and r[key] >= 0.0, f"{sym}/{key}"
            assert r["bars_used"] >= 60

    def test_har_forecasts_are_positive_with_valid_r_squared(self):
        """HAR-RV forecasts must be positive and R^2 within [0, 1]."""
        for sym, d in self.feed["dossiers"].items():
            h = d["har_rv"]
            assert 0.0 <= h["r_squared"] <= 1.0, sym
            assert h["forecast_1d_pct"] > 0.0
            assert h["forecast_5d_pct"] > 0.0
            assert h["forecast_22d_pct"] > 0.0

    def test_regime_bands_are_consistent_in_feed(self):
        """Regime labels must follow the documented baseline-ratio bands."""
        for sym, d in self.feed["dossiers"].items():
            ratio = d["regime"]["baseline_ratio"]
            regime = d["regime"]["regime"]
            if ratio >= 2.0:
                assert regime == "CRISIS", sym
            elif ratio >= 1.35:
                assert regime == "ELEVATED", sym
            elif ratio <= 0.75:
                assert regime == "LOW", sym
            else:
                assert regime == "NORMAL", sym

    # ------------------------------------------------------------------
    # 4. Sentinel, orchestrator, exporter wiring
    # ------------------------------------------------------------------
    def test_predicates_26_and_27_are_wired_into_sentinel_engine(self):
        """The sentinel must expose volatility_vrp_metrics and both Phase 33 predicates."""
        import inspect
        from src.engine.quant_intel_sentinel import quant_intel_sentinel

        sig = inspect.signature(quant_intel_sentinel.evaluate_portfolio_level_sentinels)
        assert "volatility_vrp_metrics" in sig.parameters

        source = inspect.getsource(quant_intel_sentinel.evaluate_portfolio_level_sentinels)
        for token in (
            "VARIANCE_RISK_PREMIUM_COLLAPSE",
            "VOLATILITY_CLUSTERING_HAZARD",
            "AVOID_SHORT_VOLATILITY",
            "SCALE_DOWN_EQUITY_SIZING",
        ):
            assert token in source, f"{token} not wired"

    def test_predicate_27_is_not_gated_behind_vrp_availability(self):
        """
        Regression guard: the VRP guard must not `continue` the loop iteration, or
        Predicate 27 would silently never evaluate symbols lacking implied volatility.
        """
        import inspect
        from src.engine.quant_intel_sentinel import quant_intel_sentinel

        source = inspect.getsource(quant_intel_sentinel.evaluate_portfolio_level_sentinels)
        start = source.find("# 26. PREDICATE: VARIANCE_RISK_PREMIUM_COLLAPSE")
        end = source.find("# 27. PREDICATE: VOLATILITY_CLUSTERING_HAZARD")
        assert start > 0 and end > start, "predicate blocks not found"

        # Strip comments before scanning, so the explanatory comment describing this
        # very pitfall is not mistaken for a live `continue` statement.
        code_lines = []
        for line in source[start:end].splitlines():
            code = line.split("#", 1)[0].strip()
            if code:
                code_lines.append(code)

        assert not any(l == "continue" or l.startswith("continue") for l in code_lines), (
            "Predicate 26 block must not `continue`, or Predicate 27 is skipped for "
            "symbols without a VRP"
        )

    def test_predicate_27_fires_for_symbol_without_implied_volatility(self):
        """
        Behavioural proof of the guard above: a symbol with an elevated baseline ratio
        but NO implied volatility must still trigger the clustering hazard predicate.
        """
        from src.engine.quant_intel_sentinel import quant_intel_sentinel

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={},
            volatility_vrp_metrics={
                "per_symbol": {
                    # Elevated clustering but NO implied volatility / VRP at all.
                    "NOSURFACE": {
                        "vrp_available": False,
                        "implied_vol_pct": None,
                        "vrp_variance_pts": None,
                        "baseline_ratio": 2.5,
                        "conditional_vol_pct": 40.0,
                        "baseline_20d_pct": 16.0,
                    }
                }
            },
        )
        fired = [
            a for a in alerts
            if a.predicate_type == "VOLATILITY_CLUSTERING_HAZARD" and a.symbol == "NOSURFACE"
        ]
        assert fired, "Predicate 27 must fire independently of implied volatility coverage"
        assert fired[0].current_price == pytest.approx(2.5, abs=0.005)
        assert fired[0].action_required == "SCALE_DOWN_EQUITY_SIZING"

        # And Predicate 26 must stay silent for the same symbol (no VRP to assess).
        assert not [a for a in alerts if a.predicate_type == "VARIANCE_RISK_PREMIUM_COLLAPSE"]

    def test_orchestrator_consumes_volatility_engine(self):
        """The orchestrator must import, evaluate, and pass volatility metrics onward."""
        src = (Path("src") / "engine" / "portfolio_orchestrator.py").read_text(encoding="utf-8")
        assert "from src.engine.volatility_forecast_vrp import volatility_forecast_vrp_engine" in src
        assert "volatility_forecast_vrp_engine.evaluate_volatility_forecasts()" in src
        assert "volatility_vrp_metrics=volatility_forecast_vrp_engine.compute_sentinel_metrics" in src

    def test_edge_exporter_and_build_script_stage_the_feed(self):
        """Exporter and Firebase build script must both handle the Phase 33 feed."""
        exporter = (Path("src") / "data" / "edge_exporter.py").read_text(encoding="utf-8")
        assert "volatility_forecast_vrp_engine.export_feed" in exporter

        build = (Path("scripts") / "build_firebase_public.py").read_text(encoding="utf-8")
        assert '("volatility_vrp.json", "volatility_vrp.json")' in build
        assert '("volatility_vrp.json", "volatility_vrp")' in build

    # ------------------------------------------------------------------
    # 5. Terminal UI workstation integration and synchronization
    # ------------------------------------------------------------------
    def test_terminal_ui_workstation_integration_and_synchronization(self):
        """web/index.html and public/index.html must both contain the Phase 33 workstation."""
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        pub_html = (self.public_dir / "index.html").read_text(encoding="utf-8")

        assert web_html == pub_html, "public/index.html drifted from web/index.html"

        for html_content in (web_html, pub_html):
            # Tab button and panel
            assert 'data-tab="volatilityvrp"' in html_content
            assert 'id="tab-volatilityvrp"' in html_content
            assert "'volatilityvrp'" in html_content  # registered in switchTab tabIds

            # Workstation taxonomy tags
            assert "4 RV ESTIMATORS" in html_content
            assert "GARCH / EGARCH / GJR MLE" in html_content
            assert "HAR-RV CASCADE" in html_content
            assert "VARIANCE RISK PREMIUM" in html_content

            # Market filter controls
            assert "setVrpMarketFilter('ALL')" in html_content
            assert "setVrpMarketFilter('US')" in html_content
            assert "setVrpMarketFilter('CA')" in html_content

            # Loader and renderer functions
            assert "loadVolVrpAndRender" in html_content
            assert "renderVolVrpView" in html_content
            assert "buildVrpChart" in html_content
            assert "refreshVolVrpData" in html_content

            # Feed fetching (both .json and extensionless fallback)
            assert "/api/volatility_vrp.json" in html_content
            assert "/api/volatility_vrp'" in html_content

            # Sentinel surfacing
            assert "P26/P27 WATCH" in html_content
            assert "VOL NOMINAL" in html_content

    def test_ui_discloses_model_provenance_and_licensing(self):
        """The UI must disclose internal derivation and the absence of index references."""
        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "MODEL PROVENANCE &amp; LICENSING NOTICE" in web_html
        assert "internally derived inputs only" in web_html
        assert "No third-party index value is referenced" in web_html
        assert "do not predict direction" in web_html

    # ------------------------------------------------------------------
    # 6. Statutory compliance
    # ------------------------------------------------------------------
    def test_master_release_compliance_linter_zero_violations(self):
        """All Phase 33 disclaimers and UI copy must pass the impersonal advice linter."""
        linter = ImpersonalAdviceLinter()

        for disc in self.feed.get("disclaimers", []):
            assert linter.lint_text(disc) == [], f"Disclaimer violation: {disc[:70]}"

        web_html = (self.web_dir / "index.html").read_text(encoding="utf-8")
        assert "STATUTORY RESEARCH NOTICE &amp; MODEL RISK" in web_html

        for phrase in ("you should buy", "we recommend you", "buy this stock", "recommended allocation"):
            assert phrase not in web_html.lower()
