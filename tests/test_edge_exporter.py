"""
Tests for Static JSON & Edge Data Exporter
Verifies edge artifact compilation, schema completeness, and disclaimers.

ISOLATION: export_all() is a full production run — it regenerates every feed in
data/feeds/ and data/dist/. Those feeds are tracked artifacts, and the
release-audit suites (test_yield_release, test_volatility_release) compare a
staged public/ copy against data/feeds/ and assert the staged copy is not more
than a day old. Running the exporter in place therefore rewrites tracked files
mid-suite and fails those audits for a reason that has nothing to do with the
code under test. Every path the exporter writes through is redirected to a
temporary directory here.
"""

import json
import shutil

import pytest

from src.data import edge_exporter as exporter_mod
from src.data.edge_exporter import edge_exporter


@pytest.fixture(scope="module")
def export_run(tmp_path_factory):
    """Run the exporter once, into a throwaway tree, and report what it wrote.

    Returns the temporary directories, the returned stats, and a fingerprint of
    the real tracked feed tree taken immediately before the run so a test can
    prove nothing outside the temporary tree was touched.
    """
    from config.settings import DATA_DIR

    tmp = tmp_path_factory.mktemp("edge_export")
    feeds_dir = tmp / "feeds"
    dist_dir = tmp / "dist"
    symbols_dir = feeds_dir / "symbols"
    dist_symbols_dir = dist_dir / "symbols"

    # The exporter only creates these directories in __init__, which has already
    # run against the real paths by the time a test monkeypatches them.
    for d in (feeds_dir, dist_dir, symbols_dir, dist_symbols_dir):
        d.mkdir(parents=True, exist_ok=True)

    # export_all() reads some artifacts back out of the directory it is about to
    # write (strategy_validation reads backtests.json from each target root).
    # Seed both temporary roots with the shipped inputs so this is still a full
    # run. data/dist/ is not tracked and can be pruned between sessions, so the
    # feeds copy is the fallback source for anything it is missing.
    for src in sorted((DATA_DIR / "feeds").glob("*.json")):
        shutil.copy2(src, feeds_dir / src.name)
        shutil.copy2(src, dist_dir / src.name)

    def fingerprint():
        return {
            p: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted((DATA_DIR / "feeds").glob("*.json"))
            + sorted((DATA_DIR / "dist").glob("*.json"))
            + sorted((DATA_DIR / "attestations").glob("*.json"))
        }

    real_before = fingerprint()

    orig = {k: getattr(exporter_mod, k) for k in
            ("FEEDS_DIR", "DIST_DIR", "SYMBOLS_DIR", "DIST_SYMBOLS_DIR")}
    exporter_mod.FEEDS_DIR = feeds_dir
    exporter_mod.DIST_DIR = dist_dir
    exporter_mod.SYMBOLS_DIR = symbols_dir
    exporter_mod.DIST_SYMBOLS_DIR = dist_symbols_dir

    # export_all() also refreshes the annual AOS attestation, which re-stamps
    # data/attestations/aos_<year>.json with the current time and git revision.
    # That file is tracked, so a test run must not rewrite it either.
    from src.execution import aos_attestation as aos_mod

    attest_dir = tmp / "attestations"
    attest_dir.mkdir(parents=True, exist_ok=True)
    orig_attest = aos_mod.ATTESTATION_DIR
    aos_mod.ATTESTATION_DIR = attest_dir
    try:
        stats = edge_exporter.export_all()
    finally:
        for k, v in orig.items():
            setattr(exporter_mod, k, v)
        aos_mod.ATTESTATION_DIR = orig_attest

    return {
        "stats": stats,
        "feeds": feeds_dir,
        "dist": dist_dir,
        "symbols": symbols_dir,
        "real_before": real_before,
        "fingerprint": fingerprint,
    }


def test_edge_export_compilation(export_run):
    dist_dir = export_run["dist"]
    symbols_dir = export_run["symbols"]
    stats = export_run["stats"]

    assert stats["symbol_files"] > 0
    assert stats["dist_files"] >= 4
    assert stats["total_catalysts"] > 0

    # Verify daily_summary.json exists and is valid JSON
    summary_path = dist_dir / "daily_summary.json"
    assert summary_path.exists()
    with open(summary_path, "r") as f:
        summary_data = json.load(f)

    assert "regimes" in summary_data
    assert "US" in summary_data["regimes"]
    assert "CA" in summary_data["regimes"]
    assert "market_leaders" in summary_data
    assert len(summary_data["market_leaders"]) > 0
    assert "news_intelligence_summary" in summary_data

    # HONESTY OVERHAUL (Phase 3): the bundle must carry exactly one volatility
    # figure with its source and staleness attached, rather than a per-engine
    # number of the writer's choosing.
    vol = summary_data.get("volatility_index")
    assert vol is not None, "daily_summary must publish the single measured ^VIX block"
    assert vol["index"] == "^VIX"
    assert isinstance(vol["close"], float)
    assert vol["close"] > 0
    assert vol["as_of_date"]
    assert "unofficial" in vol["source"]
    assert vol["canadian_index"] is None, "no Canadian volatility index is available"
    assert vol["canadian_note"], "the Canadian proxy must be labelled, not hidden"

    # Verify news_filings.json exists and has valid structure
    news_path = dist_dir / "news_filings.json"
    assert news_path.exists()
    with open(news_path, "r") as f:
        news_data = json.load(f)
    assert "catalysts" in news_data
    assert len(news_data["catalysts"]) > 0
    assert "summary" in news_data

    # Verify leaderboard.json
    leaderboard_path = dist_dir / "leaderboard.json"
    assert leaderboard_path.exists()
    with open(leaderboard_path, "r") as f:
        leaderboard_data = json.load(f)
    assert len(leaderboard_data["securities"]) > 0

    # Verify at least one symbol detail file exists (e.g. AAPL.json)
    aapl_path = symbols_dir / "AAPL.json"
    assert aapl_path.exists()
    with open(aapl_path, "r") as f:
        aapl_data = json.load(f)

    assert aapl_data["symbol"] == "AAPL"
    assert "ai_explanation" in aapl_data
    assert "FACT" in aapl_data["ai_explanation"]
    assert "chart_bars" in aapl_data
    assert len(aapl_data["chart_bars"]) > 0
    assert "disclaimers" in aapl_data


def test_edge_export_does_not_touch_the_tracked_feed_tree(export_run):
    """The redirected run must leave the shipped feeds alone.

    Regression guard for the isolation fixture: if a future change reintroduces
    a hard-coded production path inside export_all(), the exporter will write
    outside the temporary tree again and this test will fail.
    """
    after = export_run["fingerprint"]()
    before = export_run["real_before"]

    assert before, "expected tracked feeds to exist for the guard to be meaningful"
    assert set(before) == set(after), "export_all() created or removed a tracked feed file"

    touched = [str(p) for p in before if before[p] != after.get(p)]
    assert not touched, "export_all() rewrote tracked feed artifacts: %s" % touched[:10]

    # And the temporary tree did receive the output.
    assert (export_run["dist"] / "daily_summary.json").exists()
    assert (export_run["dist"] / "strategy_validation.json").exists()
