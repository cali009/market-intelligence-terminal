"""
Firebase Hosting Build & Packaging Script
US + Canada Market Intelligence Platform
Prepares the complete zero-cost static edge package inside public/ for instant deployment via Firebase Hosting.
"""

import shutil
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
FEEDS_DIR = ROOT_DIR / "data" / "feeds"
WEB_DIR = ROOT_DIR / "web"
PUBLIC_DIR = ROOT_DIR / "public"
API_DIR = PUBLIC_DIR / "api"
SYMBOLS_DIR = API_DIR / "symbols"


def build_firebase_bundle():
    print(">>> Building Firebase Hosting distribution bundle in public/...")
    
    # 1. Clean and recreate contents inside public/
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    if API_DIR.exists():
        shutil.rmtree(API_DIR)
    API_DIR.mkdir(parents=True, exist_ok=True)
    SYMBOLS_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Copy index.html
    shutil.copy2(WEB_DIR / "index.html", PUBLIC_DIR / "index.html")
    print(f"    ✓ Copied terminal app: public/index.html ({((WEB_DIR / 'index.html').stat().st_size / 1024):.1f} KB)")

    # 3. Copy core JSON feeds and create extensionless copies
    core_feeds = [
        ("daily_summary.json", "summary.json"),
        ("daily_summary.json", "daily_summary.json"),
        ("daily_summary.json", "summary"),
        ("daily_summary.json", "daily_summary"),
        ("leaderboard.json", "leaderboard.json"),
        ("leaderboard.json", "leaderboard"),
        ("scanners.json", "scanners.json"),
        ("scanners.json", "scanners"),
        ("signals.json", "signals.json"),
        ("signals.json", "signals"),
        ("backtests.json", "backtests.json"),
        ("backtests.json", "backtests"),
        ("news_filings.json", "news_filings.json"),
        ("news_filings.json", "news_filings"),
        ("news_filings.json", "catalysts.json"),
        ("news_filings.json", "catalysts"),
        ("journal.json", "journal.json"),
        ("journal.json", "journal"),
        ("watchlists.json", "watchlists.json"),
        ("watchlists.json", "watchlists"),
        ("feed_health.json", "feed_health.json"),
        ("feed_health.json", "feed_health"),
        ("fundamentals_coverage.json", "fundamentals_coverage.json"),
        ("fundamentals_coverage.json", "fundamentals_coverage"),
        ("exit_signals.json", "exit_signals.json"),
        ("exit_signals.json", "exit_signals"),
        ("paper_trading.json", "paper_trading.json"),
        ("paper_trading.json", "paper_trading"),
        ("portfolio.json", "portfolio.json"),
        ("portfolio.json", "portfolio"),
        ("alerts.json", "alerts.json"),
        ("alerts.json", "alerts"),
        ("ai_deep_read.json", "ai_deep_read.json"),
        ("ai_deep_read.json", "ai_deep_read"),
        ("production_health.json", "production_health.json"),
        ("production_health.json", "production_health"),
        ("asset_fingerprints.json", "asset_fingerprints.json"),
        ("asset_fingerprints.json", "asset_fingerprints"),
        ("conformal_bounds.json", "conformal_bounds.json"),
        ("conformal_bounds.json", "conformal_bounds"),
        ("cross_border_parity.json", "cross_border_parity.json"),
        ("cross_border_parity.json", "cross_border_parity"),
        ("cpcv_validation.json", "cpcv_validation.json"),
        ("cpcv_validation.json", "cpcv_validation"),
        ("shap_attributions.json", "shap_attributions.json"),
        ("shap_attributions.json", "shap_attributions"),
        ("idiosyncratic_risk_matrix.json", "idiosyncratic_risk_matrix.json"),
        ("idiosyncratic_risk_matrix.json", "idiosyncratic_risk_matrix"),
        ("quant_intel.json", "quant_intel.json"),
        ("quant_intel.json", "quant_intel"),
        ("meta_label_matrix.json", "meta_label_matrix.json"),
        ("meta_label_matrix.json", "meta_label_matrix"),
        ("meta_label_model.json", "meta_label_model.json"),
        ("meta_label_model.json", "meta_label_model"),
        ("meta_label_backtest.json", "meta_label_backtest.json"),
        ("meta_label_backtest.json", "meta_label_backtest"),
        ("macro_regime_matrix.json", "macro_regime_matrix.json"),
        ("macro_regime_matrix.json", "macro_regime_matrix"),
        ("microstructure_snapshot.json", "microstructure_snapshot.json"),
        ("microstructure_snapshot.json", "microstructure_snapshot"),
        ("portfolio_hrp.json", "portfolio_hrp.json"),
        ("portfolio_hrp.json", "portfolio_hrp"),
        ("portfolio_stress.json", "portfolio_stress.json"),
        ("portfolio_stress.json", "portfolio_stress"),
        ("portfolio_orchestration.json", "portfolio_orchestration.json"),
        ("portfolio_orchestration.json", "portfolio_orchestration"),
        ("factor_risk.json", "factor_risk.json"),
        ("factor_risk.json", "factor_risk"),
        ("factor_attribution.json", "factor_attribution.json"),
        ("factor_attribution.json", "factor_attribution"),
        ("portfolio_bayesian.json", "portfolio_bayesian.json"),
        ("portfolio_bayesian.json", "portfolio_bayesian"),
        ("cross_border_fx.json", "cross_border_fx.json"),
        ("cross_border_fx.json", "cross_border_fx"),
        ("options_intelligence.json", "options_intelligence.json"),
        ("options_intelligence.json", "options_intelligence"),
        ("sovereign_yield_curve.json", "sovereign_yield_curve.json"),
        ("sovereign_yield_curve.json", "sovereign_yield_curve"),
        ("dark_pool_liquidity.json", "dark_pool_liquidity.json"),
        ("dark_pool_liquidity.json", "dark_pool_liquidity"),
    ]

    for src_name, dest_name in core_feeds:
        src_file = FEEDS_DIR / src_name
        if src_file.exists():
            dest_file = API_DIR / dest_name
            shutil.copy2(src_file, dest_file)
            print(f"    ✓ Staged feed: /api/{dest_name}")

    # 4. Copy individual symbol files
    feed_symbols_dir = FEEDS_DIR / "symbols"
    symbol_count = 0
    if feed_symbols_dir.exists():
        for sym_file in feed_symbols_dir.glob("*.json"):
            shutil.copy2(sym_file, SYMBOLS_DIR / sym_file.name)
            symbol_count += 1
    print(f"    ✓ Staged {symbol_count} symbol dossiers in public/api/symbols/")

    print("\n" + "=" * 70)
    print(" FIREBASE HOSTING BUNDLE READY")
    print(" Directory: public/")
    print(f" Total files staged: {1 + len(core_feeds) * 2 + symbol_count}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    build_firebase_bundle()
