"""
Tests for Static JSON & Edge Data Exporter
Verifies edge artifact compilation, schema completeness, and disclaimers.
"""

import json
from pathlib import Path
from src.data.edge_exporter import edge_exporter, DIST_DIR, SYMBOLS_DIR


def test_edge_export_compilation():
    stats = edge_exporter.export_all()
    assert stats["symbol_files"] > 0
    assert stats["dist_files"] == 4

    # Verify daily_summary.json exists and is valid JSON
    summary_path = DIST_DIR / "daily_summary.json"
    assert summary_path.exists()
    with open(summary_path, "r") as f:
        summary_data = json.load(f)

    assert "regimes" in summary_data
    assert "US" in summary_data["regimes"]
    assert "CA" in summary_data["regimes"]
    assert "market_leaders" in summary_data
    assert len(summary_data["market_leaders"]) > 0

    # Verify leaderboard.json
    leaderboard_path = DIST_DIR / "leaderboard.json"
    assert leaderboard_path.exists()
    with open(leaderboard_path, "r") as f:
        leaderboard_data = json.load(f)
    assert len(leaderboard_data["securities"]) > 0

    # Verify at least one symbol detail file exists (e.g. AAPL.json)
    aapl_path = SYMBOLS_DIR / "AAPL.json"
    assert aapl_path.exists()
    with open(aapl_path, "r") as f:
        aapl_data = json.load(f)

    assert aapl_data["symbol"] == "AAPL"
    assert "ai_explanation" in aapl_data
    assert "FACT" in aapl_data["ai_explanation"]
    assert "chart_bars" in aapl_data
    assert len(aapl_data["chart_bars"]) > 0
    assert "disclaimers" in aapl_data
