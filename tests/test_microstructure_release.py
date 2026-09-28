"""
Phase 25.4 Final Release Verification & Master System Integration Audit
US + Canada Dual-Market Intelligence Platform
"""

import json
from pathlib import Path
import pytest

from src.compliance.linter import linter
from src.data.db import db
from src.engine.microstructure import microstructure_engine
from src.engine.execution_algo import execution_algo_engine
from src.engine.production_scale import production_scale_engine
from src.engine.quant_intel import quant_intel_engine


class TestMicrostructureRelease:
    """Master release audit test suite verifying Phase 25 completeness, edge feeds, and compliance."""

    def test_all_19_symbols_microstructure_and_execution_integrity(self):
        """Asserts all 19 securities have valid order books, metrics, and execution profiles."""
        sec_rows = db.execute_query("SELECT symbol, country, exchange FROM security ORDER BY symbol;")
        assert len(sec_rows) == 19

        for sec in sec_rows:
            sym = sec["symbol"]
            country = sec["country"]
            dossier = microstructure_engine.build_symbol_dossier(sym)

            # Check book validity
            assert dossier.order_book.mid_price > 0.0
            assert dossier.order_book.spread_dollars > 0.0
            assert len(dossier.order_book.bids) == 5
            assert len(dossier.order_book.asks) == 5

            # Check metrics bounds
            assert -1.0 <= dossier.metrics.order_book_imbalance <= 1.0
            assert dossier.metrics.kyle_lambda > 0.0
            assert dossier.metrics.liquidity_state in ("NORMAL", "THIN_BOOK", "LIQUIDITY_VOID", "SPREAD_EXPANSION")
            assert dossier.metrics.absorption_signal in ("NONE", "BULLISH_ABSORPTION", "BEARISH_EXHAUSTION")

            # Check execution profiles
            assert dossier.execution_profiles is not None
            assert "size_100" in dossier.execution_profiles
            assert "size_500" in dossier.execution_profiles
            assert dossier.execution_profiles["size_500"]["recommended_strategy"] in (
                "DIRECT_MARKET", "LIMIT_PASSIVE", "TWAP", "VWAP", "POV_10"
            )

    def test_production_health_feed_telemetry_schema(self):
        """Asserts that production health telemetry contains Phase 25 Microstructure & TCA components."""
        feed = production_scale_engine.generate_production_health_feed()
        assert "microstructure_telemetry" in feed
        telem = feed["microstructure_telemetry"]
        assert telem["websocket_port"] == 8001
        assert telem["sse_endpoint"] == "/api/stream/ticks"
        assert telem["tca_endpoint"] == "/api/execution/simulate"
        assert telem["active_monitored_symbols"] == 19
        assert len(telem["supported_algorithms"]) == 5
        assert telem["compliance_posture"] == "IMPERSONAL_RESEARCH_ONLY"

        component_names = [c["name"] for c in feed["production_status"]["components"]]
        assert "Microstructure & Real-Time Tick Streaming (Phase 25)" in component_names
        assert "Algorithmic Execution Simulator & TCA (Phase 25.3)" in component_names

    def test_architectural_doc_completeness_and_compliance(self):
        """Asserts docs/07_microstructure_and_execution_engine.md exists and passes compliance."""
        doc_path = Path("docs/07_microstructure_and_execution_engine.md")
        assert doc_path.exists(), "Phase 25 architectural document is missing!"

        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "Lee-Ready (1991)" in content
        assert "Order Book Imbalance (OBI)" in content
        assert "Kyle's Lambda" in content
        assert "Almgren-Chriss (2000)" in content
        assert "CSA Staff Notice 31-369" in content
        assert "Lowe v. SEC" in content
        assert "US Reg NMS" in content
        assert "Canadian UMIR" in content

        # Statutory compliance linting
        violations = linter.lint_text(content)
        assert len(violations) == 0, f"Compliance violations found: {violations}"

    def test_end_to_end_tca_simulation_logic(self):
        """Asserts that evaluate_all_strategies returns non-negative costs and valid child slices."""
        dossier = microstructure_engine.build_symbol_dossier("NVDA")
        tca_res = execution_algo_engine.evaluate_all_strategies(
            symbol="NVDA",
            side="BUY",
            shares=350,
            book=dossier.order_book,
            metrics=dossier.metrics,
            country="US",
        )
        assert "strategies" in tca_res
        for name, data in tca_res["strategies"].items():
            assert data["expected_effective_price"] > 0.0
            assert len(data["child_slices"]) >= 1
            assert sum(s["shares"] for s in data["child_slices"]) == 350
            assert data["fill_probability_pct"] >= 20.0

    def test_quant_intel_dossier_trade_plan_has_tca_execution(self):
        """Asserts that evaluated QUANT INTEL dossiers contain TCA execution parameters."""
        dossier = quant_intel_engine.evaluate_ticker("SHOP")
        plan = dossier.trade_plan
        assert plan.tca_execution_strategy in ("DIRECT_MARKET", "LIMIT_PASSIVE", "TWAP", "VWAP", "POV_10")
        assert plan.tca_effective_fill_price > 0.0
        assert plan.tca_optimal_slices >= 1
        assert "Execution TCA:" in dossier.formatted_card
        assert len(linter.lint_text(dossier.formatted_card)) == 0
