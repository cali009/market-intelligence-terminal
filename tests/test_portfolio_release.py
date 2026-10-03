"""
Phase 26 Release Verification & Comprehensive Audit Suite
Validates end-to-end data integrity, mathematical invariants, crisis stress preservation,
orchestrator allocation conservation, terminal workstation UI synchronization,
and statutory regulatory compliance (CSA Staff Notice 31-369 / SEC Publisher Exclusion).
"""

import itertools
import json
from pathlib import Path
import pytest

from src.compliance.linter import ImpersonalAdviceLinter


class TestPortfolioReleaseAudit:
    """Master audit verifying Phase 26 production artifacts and invariants."""

    @classmethod
    def setup_class(cls):
        cls.data_dir = Path("data/feeds")
        cls.public_api_dir = Path("public/api")
        cls.web_dir = Path("web")
        cls.public_dir = Path("public")

        # Load feeds
        with open(cls.data_dir / "portfolio_hrp.json", "r", encoding="utf-8") as f:
            cls.hrp_feed = json.load(f)
        with open(cls.data_dir / "portfolio_stress.json", "r", encoding="utf-8") as f:
            cls.stress_feed = json.load(f)
        with open(cls.data_dir / "portfolio_orchestration.json", "r", encoding="utf-8") as f:
            cls.orch_feed = json.load(f)

    def test_all_three_feeds_exist_and_serialize_cleanly(self):
        """Audit that all three Phase 26 JSON feeds exist in both feeds/ and public/api/."""
        feed_names = ["portfolio_hrp.json", "portfolio_stress.json", "portfolio_orchestration.json"]
        for fname in feed_names:
            feed_path = self.data_dir / fname
            pub_path = self.public_api_dir / fname
            clean_pub_path = self.public_api_dir / fname.replace(".json", "")

            assert feed_path.exists(), f"Feed missing: {feed_path}"
            assert pub_path.exists(), f"Public feed missing: {pub_path}"
            assert clean_pub_path.exists(), f"Clean URL feed missing: {clean_pub_path}"

            # Verify valid non-empty JSON
            assert feed_path.stat().st_size > 500, f"Feed {fname} is suspiciously small"
            assert pub_path.stat().st_size > 500, f"Public feed {fname} is suspiciously small"

    def test_hrp_and_orchestration_mathematical_consistency(self):
        """Verify mathematical integrity, weight conservation, and conviction scaling."""
        hrp_allocs = self.hrp_feed["allocations"]
        orch_positions = self.orch_feed["positions"]

        assert len(hrp_allocs) == 19, "HRP universe must cover all 19 dual-market securities"
        assert len(orch_positions) >= 15, "Orchestrator must track active universe"

        # Check capital conservation: deployed + cash == total capital
        total_cap = self.orch_feed["total_capital"]
        deployed = self.orch_feed["deployed_capital"]
        cash = self.orch_feed["cash_reserve"]
        assert total_cap == 100000.0, f"Standard base capital must be $100,000, got {total_cap}"
        assert abs((deployed + cash) - total_cap) < 1e-4, f"Capital mismatch: deployed {deployed} + cash {cash} != {total_cap}"

        # Check dual market exposures
        us_pct = self.orch_feed["us_weight_pct"]
        ca_pct = self.orch_feed["ca_weight_pct"]
        deployed_pct = self.orch_feed["deployed_pct"]
        assert abs((us_pct + ca_pct) - deployed_pct) < 1e-2, "US + CA exposure must equal total deployed pct"
        assert 20.0 <= us_pct <= 40.0, f"US exposure {us_pct}% outside expected range"
        assert 20.0 <= ca_pct <= 40.0, f"CA exposure {ca_pct}% outside expected range"

        # Verify conviction scalar mapping across positions
        for sym, pos in orch_positions.items():
            assert sym in hrp_allocs, f"Position {sym} not present in HRP universe"
            grade = pos["conviction_grade"]
            scalar = pos["conviction_scalar"]
            if grade == "A":
                assert abs(scalar - 1.25) < 1e-4
            elif grade == "B":
                assert abs(scalar - 1.00) < 1e-4
            elif grade == "C":
                assert abs(scalar - 0.50) < 1e-4
            elif grade == "SKIP":
                assert abs(scalar - 0.00) < 1e-4

            # Target shares must be non-negative integer
            assert isinstance(pos["target_shares"], int)
            assert pos["target_shares"] >= 0

    def test_crisis_replay_stress_consistency_and_capital_preservation(self):
        """Verify scenario replays, stress PnL, and outperformance preservation alpha."""
        scenarios = self.stress_feed["scenarios"]
        expected_ids = ["2008_GFC", "2015_OIL_CRASH", "2020_COVID_FLASH", "2022_RATE_SHOCK", "2026_STAGFLATION_TARIFF"]

        for scen_id in expected_ids:
            assert scen_id in scenarios, f"Scenario {scen_id} missing from stress feed"
            scen = scenarios[scen_id]
            assert "name" in scen
            assert "historical_period" in scen
            assert "portfolio_return_pct" in scen
            assert "benchmark_return_pct" in scen
            assert "capital_preservation_delta" in scen
            assert "worst_asset" in scen
            assert "best_asset" in scen

        # In 2008 GFC, 2022 Rate Shock, and 2026 Tariff shock, HRP must preserve capital over Equal-Weight
        assert scenarios["2008_GFC"]["capital_preservation_delta"] > 2000.0, "2008 GFC preservation delta must be > $2,000"
        assert scenarios["2022_RATE_SHOCK"]["capital_preservation_delta"] > 4000.0, "2022 Rate Shock preservation delta must be > $4,000"
        assert scenarios["2026_STAGFLATION_TARIFF"]["capital_preservation_delta"] > 1000.0, "2026 Tariff Shock preservation delta must be > $1,000"

    def test_parametric_var_cvar_mathematical_properties(self):
        """Verify parametric tail risk monotonicity and Basel III sqrt(10) horizon expansion."""
        var_95_1d = abs(self.stress_feed["var_95_1d_pct"])
        cvar_99_1d = abs(self.stress_feed["cvar_99_1d_pct"])
        var_95_10d = abs(self.stress_feed["var_95_10d_pct"])
        cvar_99_10d = abs(self.stress_feed["cvar_99_10d_pct"])

        # Monotonicity: CVaR (99%) > VaR (95%)
        assert cvar_99_1d > var_95_1d, f"1d CVaR 99% ({cvar_99_1d}%) must exceed 1d VaR 95% ({var_95_1d}%)"
        assert cvar_99_10d > var_95_10d, f"10d CVaR 99% ({cvar_99_10d}%) must exceed 10d VaR 95% ({var_95_10d}%)"

        # Horizon scaling: 10d == sqrt(10) * 1d
        sqrt_10 = 10.0 ** 0.5
        expected_var_10d = var_95_1d * sqrt_10
        expected_cvar_10d = cvar_99_1d * sqrt_10

        assert abs(var_95_10d - round(expected_var_10d, 2)) <= 0.05, f"10d VaR {var_95_10d} deviates from sqrt(10) scaling {expected_var_10d}"
        assert abs(cvar_99_10d - round(expected_cvar_10d, 2)) <= 0.05, f"10d CVaR {cvar_99_10d} deviates from sqrt(10) scaling {expected_cvar_10d}"

    # Sentinel predicates fall into two disjoint families.
    #
    # ALLOCATION-CONSTRUCTION predicates are a function of the weights the orchestrator
    # chose, so under a nominal allocation they are a true invariant: if one fires, the
    # allocation math regressed.
    #
    # MARKET-CONDITION predicates are a function of live market state (FX basis, dealer
    # positioning, sovereign yields, venue flow, options IV vs realized vol) and fire
    # whenever the market warrants it, irrespective of the nominal allocation. Requiring
    # these to be silent would make the suite depend on a specific market regime.
    ALLOCATION_PREDICATES = {
        "PORTFOLIO_CONCENTRATION_BREACH",
        "CORRELATION_SPIKE_WARNING",
        "CVAR_TAIL_RISK_BREACH",
        "FACTOR_CROWDING_BREACH",
        "FX_COMMODITY_OVEREXPOSURE",
        "ALPHA_EROSION_WARNING",
        "EXCESSIVE_TURNOVER_BREACH",
        "ESTIMATION_ERROR_SPIKE",
        "UNHEDGED_CURRENCY_DRAG",
    }
    # Execution/operational telemetry. Deliberately a THIRD family rather than being folded
    # into one of the two above:
    #   - not ALLOCATION, because that family carries the invariant "a nominal allocation
    #     must never breach a construction limit", and Predicate 28 fires on governor
    #     rejection history, which a nominal allocation says nothing about;
    #   - not MARKET, because it is not a market condition at all -- it describes the
    #     alignment between generated signals and the declared risk envelope.
    EXECUTION_PREDICATES = {
        "PRE_TRADE_RISK_BREACH",
    }
    MARKET_PREDICATES = {
        "CROSS_BORDER_PARITY_DISLOCATION",
        "GAMMA_FLIP_REGIME_TRANSITION",
        "VOLATILITY_SKEW_TAIL_INVERSION",
        "SOVEREIGN_YIELD_CURVE_INVERSION",
        "TERM_PREMIUM_SHOCK",
        "DARK_POOL_STEALTH_DISTRIBUTION",
        "SHORT_VOLUME_SQUEEZE_SPIKE",
        "VARIANCE_RISK_PREMIUM_COLLAPSE",
        "VOLATILITY_CLUSTERING_HAZARD",
    }

    def test_portfolio_risk_sentinels_integrity(self):
        """
        Verify that the nominal allocation breaches no construction limit, and that any
        sentinel which does fire is a legitimate market-condition warning.
        """
        alerts = self.orch_feed.get("portfolio_alerts", [])

        # 1. The invariant: a nominal allocation must never breach a construction limit.
        breaches = [a for a in alerts if a["predicate_type"] in self.ALLOCATION_PREDICATES]
        assert breaches == [], (
            "Nominal allocation breached a construction limit: "
            + ", ".join(sorted({a["predicate_type"] for a in breaches}))
        )

        # 2. Every alert must be a known predicate -- no orphaned or malformed type.
        all_families = (self.ALLOCATION_PREDICATES | self.MARKET_PREDICATES
                        | self.EXECUTION_PREDICATES)
        unknown = [a["predicate_type"] for a in alerts
                   if a["predicate_type"] not in all_families]
        assert unknown == [], f"Unknown predicate types emitted: {unknown}"

        # 3. Any alert that does fire must be well-formed and risk-framed, not advisory.
        #    Market and execution predicates may both fire; allocation predicates may not
        #    (asserted above), so the well-formedness check spans the families that can fire.
        for a in alerts:
            assert a["predicate_type"] in (self.MARKET_PREDICATES | self.EXECUTION_PREDICATES)
            assert a["severity"] in ("CRITICAL", "WARNING", "NOTICE")
            assert a["symbol"] and a["headline"] and a["body"] and a["action_required"]
            assert isinstance(a["trigger_level"], (int, float))
            assert isinstance(a["current_price"], (int, float))

        # Validate posture classification
        posture = self.stress_feed["tail_risk_posture"]
        assert posture in ["LOW_TAIL_RISK", "MODERATE_TAIL_RISK", "ELEVATED_TAIL_RISK", "CRITICAL_TAIL_RISK"]
        assert posture == "LOW_TAIL_RISK", f"Expected LOW_TAIL_RISK, got {posture}"

    def test_sentinel_predicate_families_cover_every_emitted_type(self):
        """
        Guard against the two families drifting out of sync with the engine: every
        predicate the sentinel can emit must be classified above.
        """
        import re
        from pathlib import Path

        src = (Path("src") / "engine" / "quant_intel_sentinel.py").read_text(encoding="utf-8")
        start = src.find("def evaluate_portfolio_level_sentinels")
        end = src.find("def evaluate_universe_sentinels")
        assert start > 0 and end > start

        emitted = set(re.findall(r'predicate_type="([A-Z_0-9]+)"', src[start:end]))
        assert emitted, "no predicates found -- parsing assumption broke"

        families = {
            "ALLOCATION": self.ALLOCATION_PREDICATES,
            "MARKET": self.MARKET_PREDICATES,
            "EXECUTION": self.EXECUTION_PREDICATES,
        }
        for a, b in itertools.combinations(families, 2):
            overlap = families[a] & families[b]
            assert not overlap, f"Predicate in both {a} and {b}: {overlap}"

        unclassified = emitted - set().union(*families.values())
        assert not unclassified, (
            f"New predicate(s) not classified into a family: {sorted(unclassified)}"
        )

    def test_ui_bundle_synchronization_and_tab_integration(self):
        """Audit web/index.html and public/index.html for Phase 26 UI workstations."""
        for path in [self.web_dir / "index.html", self.public_dir / "index.html"]:
            assert path.exists(), f"UI file {path} missing"
            html = path.read_text(encoding="utf-8")

            # Check navigation tab button
            assert 'data-tab="portfoliohrp"' in html, f"Missing portfoliohrp tab button in {path}"
            assert 'switchTab(\'portfoliohrp\'' in html, f"Missing switchTab handler in {path}"

            # Check main workstation container
            assert 'id="tab-portfoliohrp"' in html, f"Missing tab-portfoliohrp container in {path}"
            assert 'id="portfolioHrpContainer"' in html, f"Missing portfolioHrpContainer in {path}"

            # Check JavaScript integration functions
            assert 'loadPortfolioHrpAndRender' in html, f"Missing loadPortfolioHrpAndRender in {path}"
            assert 'renderPortfolioHrpView' in html, f"Missing renderPortfolioHrpView in {path}"
            assert 'setPortfolioCapital' in html, f"Missing setPortfolioCapital in {path}"
            assert 'selectStressScenario' in html, f"Missing selectStressScenario in {path}"
            assert 'setHrpSection' in html, f"Missing setHrpSection in {path}"
            assert 'quickLaunchTcaFromHrp' in html, f"Missing quickLaunchTcaFromHrp in {path}"

    def test_master_release_compliance_linter_zero_violations(self):
        """Statutory compliance audit across all Phase 26 production artifacts."""
        linter = ImpersonalAdviceLinter()
        files_to_scan = [
            Path("src/engine/portfolio_hrp.py"),
            Path("src/engine/portfolio_stress.py"),
            Path("src/engine/portfolio_orchestrator.py"),
            Path("data/feeds/portfolio_hrp.json"),
            Path("data/feeds/portfolio_stress.json"),
            Path("data/feeds/portfolio_orchestration.json"),
        ]

        total_violations = 0
        violation_details = []

        for fpath in files_to_scan:
            text = fpath.read_text(encoding="utf-8")
            violations = linter.lint_text(text)
            if violations:
                total_violations += len(violations)
                violation_details.append(f"{fpath}: {violations}")

        assert total_violations == 0, f"Found {total_violations} compliance violations: {violation_details}"
