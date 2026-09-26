"""
Static JSON & Edge Data Exporter
US + Canada Market Intelligence Platform
Compiles daily market intelligence snapshots for Cloudflare Pages & R2 edge distribution.
Zero egress fees, sub-50ms global CDN retrieval, 100% legal derived intelligence format.
"""

import json
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Dict, Any, List

from config.settings import DATA_DIR
from src.compliance.disclaimers import (
    DISCLAIMER_VERSION,
    CSA_31_369_GENERAL_ADVICE_DISCLAIMER,
    SEC_PUBLISHER_EXCLUSION_DISCLAIMER,
    HYPOTHETICAL_BACKTEST_DISCLAIMER,
)
from src.data.db import db
from src.engine.technicals import TechnicalAnalysisEngine
from src.engine.scoring import scoring_engine
from src.engine.regime import regime_classifier
from src.engine.scanners import market_scanners
from src.engine.signals import signal_engine
from src.engine.explanations import explanation_engine
from src.engine.news_engine import news_engine
from src.engine.journal import journal_engine
from src.engine.fundamentals import FundamentalAnalysisEngine

FEEDS_DIR = DATA_DIR / "feeds"
DIST_DIR = DATA_DIR / "dist"
SYMBOLS_DIR = FEEDS_DIR / "symbols"
DIST_SYMBOLS_DIR = DIST_DIR / "symbols"


class EdgeExporter:
    """
    Compiles database records into production static JSON files for frontend consumption.
    """

    def __init__(self):
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        DIST_SYMBOLS_DIR.mkdir(parents=True, exist_ok=True)
        FEEDS_DIR.mkdir(parents=True, exist_ok=True)
        SYMBOLS_DIR.mkdir(parents=True, exist_ok=True)

    def export_all(self) -> Dict[str, int]:
        """
        Executes complete compilation of edge artifacts.
        Returns dictionary of file counts generated.
        """
        as_of_date = date.today().isoformat()
        generated_at = datetime.now(timezone.utc).isoformat()

        # 1. Macro Regimes
        regimes_data = {}
        for sym, exch, ctry, label in [("SPY", "AMEX", "US", "US Equities"), ("XIU", "TSX", "CA", "Canadian Equities")]:
            sec_rows = db.execute_query("SELECT security_id FROM security WHERE symbol = ? AND exchange = ?;", (sym, exch))
            if not sec_rows:
                continue
            sec_id = sec_rows[0]["security_id"]
            bars = db.execute_query(
                "SELECT trading_date, open, high, low, close, volume, adjusted_close FROM bar_1d WHERE security_id = ? ORDER BY trading_date ASC;",
                (sec_id,),
            )
            if len(bars) < 30:
                continue
            import pandas as pd
            df = pd.DataFrame(bars)
            m = TechnicalAnalysisEngine.get_latest_feature_snapshot(df)
            reg_out = regime_classifier.classify_regime(
                index_close=m["close"],
                index_sma50=m["sma_50"] or m["close"],
                index_sma200=m["sma_200"] or m["close"],
                index_sma200_slope=m["sma_200_slope_20d"] or 0.0,
                yield_spread_10y_2y=0.45 if ctry == "US" else 0.55,
            )
            regimes_data[ctry] = {
                "market_label": label,
                "benchmark_symbol": sym,
                "benchmark_price": m["close"],
                "regime_state": reg_out.regime_state,
                "filtered_probability": reg_out.filtered_probability,
                "score_multiplier": reg_out.regime_multiplier,
                "risk_scaling": reg_out.risk_scaling,
                "top_drivers": reg_out.top_drivers,
            }

        # 2. Leaderboard & Symbol Details
        securities = db.execute_query("SELECT * FROM security WHERE is_active = 1;")
        leaderboard_items = []
        scanner_results = []
        signals_list = []
        symbol_files_count = 0

        # Compile master news & SEC filings intelligence feed
        master_catalysts = news_engine.compile_master_catalyst_feed()
        news_summary = news_engine.get_summary_metrics(master_catalysts)
        category_expectancy = news_engine.compute_category_expectancy(master_catalysts)
        event_calendar = news_engine.compile_event_calendar()
        news_engine.persist_news_to_database(master_catalysts)
        news_engine.persist_calendar_to_database(event_calendar)

        # Step 2: Evaluate Point-In-Time Fundamentals & Valuation Ratios
        fund_records = FundamentalAnalysisEngine.evaluate_universe_fundamentals(as_of_date)
        fund_map = {}
        for fr in fund_records:
            sec_id = fr["security_id"]
            fund_map[sec_id] = fr
            if not fr.get("is_etf"):
                try:
                    FundamentalAnalysisEngine.persist_fundamental_metric(fr)
                except Exception as e:
                    print(f"Warning: Failed to persist fundamental_metric for {fr['symbol']}: {e}")

        for s in securities:
            sec_id = s["security_id"]
            sym = s["symbol"]
            exch = s["exchange"]
            ctry = s["country"]
            curr = s["currency"]
            name = s["name"]

            # Load bars
            bars_raw = db.execute_query(
                "SELECT trading_date, open, high, low, close, volume, adjusted_close FROM bar_1d WHERE security_id = ? ORDER BY trading_date ASC;",
                (sec_id,),
            )
            if len(bars_raw) < 20:
                continue

            import pandas as pd
            df = pd.DataFrame(bars_raw)
            metrics = TechnicalAnalysisEngine.get_latest_feature_snapshot(df)

            # Phase 4 Technical Engine: MTF Confluence, Volume Profile, S/R & Trend Structure
            tech_dossier = TechnicalAnalysisEngine.compile_full_technical_dossier(df)
            try:
                TechnicalAnalysisEngine.persist_to_feature_store(
                    security_id=sec_id,
                    symbol=sym,
                    as_of_date=as_of_date,
                    dossier=tech_dossier,
                )
            except Exception as e:
                print(f"Warning: Failed to persist feature_store for {sym}: {e}")

            # Phase 5 Fundamental Engine: Quality Ratios, Valuation Multiples & Dividend Safety
            fr = fund_map.get(sec_id)
            fund_dossier = FundamentalAnalysisEngine.compile_fundamental_dossier(fr) if fr else None
            fund_score = fr["composite_fundamental_score"] if (fr and not fr.get("is_etf")) else None

            # Catalysts & news contribution for this security
            sym_catalysts = [e.model_dump() for e in master_catalysts if e.symbol == sym]
            news_pts = round(sum(e.get("score_impact_pts", 0.0) for e in sym_catalysts[:3]), 1)
            news_pts = max(-12.0, min(12.0, news_pts))

            # Score
            regime_info = regimes_data.get(ctry, {})
            multiplier = regime_info.get("score_multiplier", 1.0)
            regime_name = regime_info.get("regime_state", "WEAK_BULL")

            score_rec = scoring_engine.evaluate_security(
                security_id=sec_id,
                metrics=metrics,
                fundamental_score=fund_score,
                regime_state=regime_name,
                regime_multiplier=multiplier,
                news_contribution=news_pts,
                horizon="POSITION",
            )

            # Scanners
            matches = market_scanners.scan_all(dict(s), metrics, regime_state=regime_name)
            for m in matches:
                scanner_results.append({
                    "scanner_id": m.scanner_id,
                    "scanner_name": m.scanner_name,
                    "symbol": m.symbol,
                    "exchange": m.exchange,
                    "price": m.price,
                    "why_matched": m.why_matched,
                    "key_metrics": m.key_metrics,
                    "regime_gated": m.regime_gated,
                    "regime_state": m.regime_state,
                })

            # Signal
            plan = signal_engine.generate_long_signal(
                security_id=sec_id,
                metrics=metrics,
                composite_score=score_rec.composite_score,
                confidence_tier=score_rec.confidence_tier,
                horizon="POSITION",
            )
            if plan:
                signals_list.append({
                    "symbol": sym,
                    "exchange": exch,
                    "name": name,
                    "currency": curr,
                    "composite_score": score_rec.composite_score,
                    "confidence_tier": plan.confidence_tier,
                    "direction": plan.direction,
                    "preferred_entry": plan.preferred_entry,
                    "entry_zone": [plan.entry_zone_low, plan.entry_zone_high],
                    "structural_stop": plan.stop_loss,
                    "targets": [plan.target_1, plan.target_2, plan.target_3],
                    "risk_reward_ratio": plan.risk_reward_ratio,
                    "invalidation_predicates": plan.invalidation_predicates,
                    "risk_notes": plan.risk_notes,
                })

            # AI Explanation
            explanation = explanation_engine.generate_deterministic_explanation(
                security_info=dict(s),
                metrics=metrics,
                score_rec={
                    "composite_score": score_rec.composite_score,
                    "confidence_tier": score_rec.confidence_tier,
                    "technical_score": score_rec.technical_score,
                    "risk_penalty": score_rec.risk_penalty,
                },
                regime_state=regime_name,
            )

            # Add to leaderboard
            leaderboard_items.append({
                "symbol": sym,
                "exchange": exch,
                "country": ctry,
                "currency": curr,
                "name": name,
                "price": metrics["close"],
                "composite_score": score_rec.composite_score,
                "confidence_tier": score_rec.confidence_tier,
                "technical_score": score_rec.technical_score,
                "rsi_14": round(metrics.get("rsi_14", 50.0), 1),
                "rvol_20": round(metrics.get("rvol_20", 1.0), 2),
                "atr_pct": round(metrics.get("atr_pct", 2.0), 2),
                "proximity_52w_high": round((metrics.get("proximity_52w_high", 0.0)) * 100, 1),
                "scanner_tags": [m.scanner_name for m in matches],
                "mtf_confluence_score": tech_dossier["confluence_score"],
                "mtf_confluence_label": tech_dossier["confluence_label"],
                "value_area_state": tech_dossier["volume_profile"]["value_area_state"],
                "poc_price": tech_dossier["volume_profile"]["poc_price"],
                "trend_structure": tech_dossier["trend_structure"]["trend_structure"],
                "fundamental_score": fr["composite_fundamental_score"] if fr else None,
                "quality_score": fr["quality_score"] if fr else None,
                "valuation_score": fr["valuation_score"] if fr else None,
                "coverage_status": fr["coverage_status"] if fr else "UNKNOWN",
                "roic": fr["quality_ratios"].get("roic") if fr else None,
                "pe_ratio": fr["valuation_multiples"].get("pe_ratio") if fr else None,
            })

            # Write Symbol Detail JSON (includes last 120 bars for lightweight charting)
            chart_bars = [
                {
                    "time": b["trading_date"],
                    "open": b["open"],
                    "high": b["high"],
                    "low": b["low"],
                    "close": b["close"],
                    "volume": b["volume"],
                }
                for b in bars_raw[-120:]
            ]

            symbol_detail = {
                "symbol": sym,
                "exchange": exch,
                "country": ctry,
                "currency": curr,
                "name": name,
                "as_of_date": as_of_date,
                "generated_at": generated_at,
                "latest_metrics": metrics,
                "technical_dossier": tech_dossier,
                "fundamental_dossier": fund_dossier,
                "score_record": {
                    "composite_score": score_rec.composite_score,
                    "confidence_tier": score_rec.confidence_tier,
                    "technical_score": score_rec.technical_score,
                    "fundamental_score": score_rec.fundamental_score,
                    "risk_penalty": score_rec.risk_penalty,
                    "factor_attribution": score_rec.factor_attribution_json,
                },
                "signal_plan": (
                    {
                        "direction": plan.direction,
                        "preferred_entry": plan.preferred_entry,
                        "entry_zone": [plan.entry_zone_low, plan.entry_zone_high],
                        "stop_loss": plan.stop_loss,
                        "targets": [plan.target_1, plan.target_2, plan.target_3],
                        "risk_reward_ratio": plan.risk_reward_ratio,
                        "invalidation": plan.invalidation_predicates,
                    }
                    if plan
                    else None
                ),
                "ai_explanation": explanation.to_dict()["explanation"],
                "active_scanner_matches": [
                    {"id": m.scanner_id, "name": m.scanner_name, "why": m.why_matched}
                    for m in matches
                ],
                "catalysts_and_filings": sym_catalysts,
                "chart_bars": chart_bars,
                "disclaimers": {
                    "canada": CSA_31_369_GENERAL_ADVICE_DISCLAIMER,
                    "united_states": SEC_PUBLISHER_EXCLUSION_DISCLAIMER,
                    "version": DISCLAIMER_VERSION,
                },
            }

            for s_dir in [SYMBOLS_DIR, DIST_SYMBOLS_DIR]:
                with open(s_dir / f"{sym}.json", "w") as f:
                    json.dump(symbol_detail, f, indent=2)
            symbol_files_count += 1

        # Sort leaderboard
        leaderboard_items.sort(key=lambda x: x["composite_score"], reverse=True)

        # Write core edge files to both FEEDS_DIR and DIST_DIR
        for d_dir in [FEEDS_DIR, DIST_DIR]:
            with open(d_dir / "daily_summary.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "generated_at": generated_at,
                        "regimes": regimes_data,
                        "market_leaders": leaderboard_items[:5],
                        "total_securities_analyzed": len(leaderboard_items),
                        "news_intelligence_summary": news_summary,
                        "disclaimer_version": DISCLAIMER_VERSION,
                    },
                    f,
                    indent=2,
                )

            with open(d_dir / "leaderboard.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "generated_at": generated_at,
                        "securities": leaderboard_items,
                    },
                    f,
                    indent=2,
                )

            active_us_regime = regimes_data.get("US", {}).get("regime_state", "WEAK_BULL")
            scanner_defs = market_scanners.get_scanner_definitions_with_expectancy(active_us_regime)

            # Persist scanner run and definitions to SQLite
            market_scanners.init_scanner_definitions_in_db()

            with open(d_dir / "scanners.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "generated_at": generated_at,
                        "active_regime": active_us_regime,
                        "total_matches": len(scanner_results),
                        "scanner_definitions": scanner_defs,
                        "matches": scanner_results,
                    },
                    f,
                    indent=2,
                )

            with open(d_dir / "signals.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "generated_at": generated_at,
                        "total_active_signals": len(signals_list),
                        "signals": signals_list,
                    },
                    f,
                    indent=2,
                )

            with open(d_dir / "news_filings.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "generated_at": generated_at,
                        "summary": news_summary,
                        "total_catalysts": len(master_catalysts),
                        "category_expectancy": category_expectancy,
                        "event_calendar": event_calendar,
                        "catalysts": [e.model_dump() for e in master_catalysts],
                    },
                    f,
                    indent=2,
                )

            # Phase 1 MVP: Research Journal, Watchlists & Data Quality Health
            journal_payload = journal_engine.get_journal_summary()
            with open(d_dir / "journal.json", "w") as f:
                json.dump(journal_payload, f, indent=2)

            watchlists_payload = journal_engine.get_watchlists_with_metrics()
            with open(d_dir / "watchlists.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "total_watchlists": len(watchlists_payload),
                        "watchlists": watchlists_payload,
                    },
                    f,
                    indent=2,
                )

            feed_health_payload = journal_engine.compile_feed_health_status()
            with open(d_dir / "feed_health.json", "w") as f:
                json.dump(feed_health_payload, f, indent=2)

            # Phase 5 Fundamentals Coverage Report
            coverage_report = FundamentalAnalysisEngine.generate_coverage_report(as_of_date)
            with open(d_dir / "fundamentals_coverage.json", "w") as f:
                json.dump(coverage_report, f, indent=2)

        return {
            "dist_files": 9,
            "symbol_files": symbol_files_count,
            "total_matches": len(scanner_results),
            "total_signals": len(signals_list),
            "total_catalysts": len(master_catalysts),
            "total_journal_positions": len(journal_payload.get("positions", [])),
            "total_watchlists": len(watchlists_payload),
        }


# Global singleton exporter instance
edge_exporter = EdgeExporter()
