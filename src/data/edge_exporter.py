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
from src.engine.exit_engine import exit_signal_engine
from src.engine.explanations import explanation_engine
from src.engine.news_engine import news_engine
from src.engine.journal import journal_engine
from src.engine.fundamentals import FundamentalAnalysisEngine
from src.engine.paper_trading import paper_trading_engine
from src.engine.portfolio import portfolio_engine
from src.engine.alerts import alert_engine
from src.engine.filing_deep_read import filing_deep_read_engine
from src.engine.production_scale import production_scale_engine
from src.engine.asset_fingerprint import asset_fingerprint_engine
from src.engine.conformal_bounds import adaptive_conformal_engine
from src.engine.cross_border_parity import cross_border_parity_engine, DUAL_LISTED_PAIRS
from src.engine.cpcv_engine import cpcv_engine
from src.engine.shap_engine import shap_engine
from src.engine.idiosyncratic_risk import idiosyncratic_risk_engine
from src.engine.quant_intel import quant_intel_engine
from src.engine.quant_intel_memory import quant_intel_memory_ledger
from src.engine.meta_label_dataset import meta_label_dataset_engine
from src.engine.meta_label_classifier import meta_label_classifier
from src.engine.backtester import backtest_engine
from src.engine.macro_regime import macro_regime_engine
from src.engine.microstructure import microstructure_engine
from src.engine.portfolio_hrp import hrp_portfolio_engine
from src.engine.portfolio_stress import portfolio_stress_engine
from src.engine.portfolio_orchestrator import portfolio_orchestrator_engine
from src.engine.factor_risk import factor_risk_engine
from src.engine.factor_attribution import factor_attribution_engine
from src.engine.portfolio_bayesian import portfolio_bayesian_engine
from src.engine.cross_border_fx import cross_border_fx_engine

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
        metrics_map: Dict[str, Any] = {}
        quant_intel_map: Dict[str, Any] = {}

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
                    "idiosyncratic_risk_multiplier": getattr(m, "idiosyncratic_risk_multiplier", 1.0),
                    "risk_posture": getattr(m, "risk_posture", "NORMAL_EQUILIBRIUM"),
                    "primary_risk_driver": getattr(m, "primary_risk_driver", "Stable Quantitative Fingerprint"),
                    "conviction_score": getattr(m, "conviction_score", 75.0),
                    "conviction_tier": getattr(m, "conviction_tier", "MODERATE"),
                    "conviction_breakdown": getattr(m, "conviction_breakdown", {}),
                })

            # Cache metrics for portfolio exit evaluation
            metrics_map[sym] = metrics

            # Phase 6 Entry Signal Engine: Output Contract with 6-Dimension Rationale & Predicates
            plan = signal_engine.generate_long_signal(
                security_id=sec_id,
                metrics=metrics,
                composite_score=score_rec.composite_score,
                confidence_tier=score_rec.confidence_tier,
                horizon="POSITION",
                fundamental_dossier=fund_dossier,
                technical_dossier=tech_dossier,
                recent_catalysts=sym_catalysts,
                regime_state=regime_name,
                sector=s.get("sector", "Technology"),
            )
            if plan:
                stop_dist = round(((plan.preferred_entry - plan.stop_loss) / plan.preferred_entry) * 100.0, 1)
                signals_list.append({
                    "symbol": sym,
                    "exchange": exch,
                    "name": name,
                    "currency": curr,
                    "composite_score": score_rec.composite_score,
                    "confidence_tier": plan.confidence_tier,
                    "direction": plan.direction,
                    "stance": "POTENTIAL LONG SETUP",
                    "setup_type": plan.setup_type,
                    "preferred_entry": plan.preferred_entry,
                    "alt_entry": plan.alt_entry,
                    "entry_zone": [plan.entry_zone_low, plan.entry_zone_high],
                    "structural_stop": plan.stop_loss,
                    "stop_distance_pct": stop_dist,
                    "targets": [
                        {"target": plan.target_1, "basis": plan.target_1_basis, "rr": plan.planned_rr_t1},
                        {"target": plan.target_2, "basis": plan.target_2_basis, "rr": plan.planned_rr_t2},
                        {"target": plan.target_3, "basis": plan.target_3_basis},
                    ],
                    "risk_reward_ratio": plan.risk_reward_ratio,
                    "planned_rr_t1": plan.planned_rr_t1,
                    "planned_rr_t2": plan.planned_rr_t2,
                    "realized_rr_t1": plan.realized_rr_t1,
                    "holding_period_desc": plan.holding_period_desc,
                    "invalidation_predicates": plan.invalidation_predicates,
                    "risk_notes": plan.risk_notes,
                    "rationale": plan.rationale,
                    "data_lineage": plan.data_lineage,
                })

            # AI Explanation with Numeral Verification Gate
            explanation = explanation_engine.generate_deterministic_explanation(
                security_info=dict(s),
                metrics=metrics,
                score_rec={
                    "composite_score": score_rec.composite_score,
                    "confidence_tier": score_rec.confidence_tier,
                    "technical_score": score_rec.technical_score,
                    "fundamental_score": score_rec.fundamental_score,
                    "risk_penalty": score_rec.risk_penalty,
                },
                regime_state=regime_name,
                fundamental_dossier=fund_dossier,
            )

            # Phase 13: Idiosyncratic Asset Fingerprint & Stationarity Model
            fingerprint = asset_fingerprint_engine.generate_fingerprint_for_symbol(sym)

            # Phase 14: Adaptive Conformal Prediction (ACI) Statistical Bounds
            conformal_bounds = adaptive_conformal_engine.calibrate_bounds_for_symbol(sym)

            # Phase 15: Cross-Border Dual-Listed Parity & Basis
            parity_rec = cross_border_parity_engine.compute_pair_parity(sym) if sym in DUAL_LISTED_PAIRS else None
            parity_dict = parity_rec.to_dict() if parity_rec else None

            # Phase 17: LightGBM & Exact TreeSHAP Factor Attributions
            shap_rec = shap_engine.get_attribution_for_symbol(sym)
            shap_dict = shap_rec.to_dict() if shap_rec else None

            # Phase 18: Deterministic Idiosyncratic Risk Matrix
            risk_prof = idiosyncratic_risk_engine.evaluate_symbol_risk(sym)
            risk_dict = risk_prof.to_dict() if risk_prof else None

            # Phase 22.1-22.3: QUANT INTEL High-Conviction Trade Dossier & Formatted Card
            try:
                qi_dossier = quant_intel_engine.evaluate_ticker(sym, as_of_dt=datetime.now(timezone.utc))
                qi_dict = qi_dossier.to_dict()
                quant_intel_map[sym] = qi_dict
            except Exception as e:
                print(f"Warning: Failed to generate Quant Intel for {sym}: {e}")
                qi_dict = None

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
                "archetype": fingerprint.archetype,
                "archetype_label": fingerprint.archetype_label,
                "hurst_exponent": fingerprint.hurst_exponent,
                "hurst_class": fingerprint.hurst_class,
                "fractional_d_order": fingerprint.fractional_d_order,
                "memory_retention_pct": fingerprint.memory_retention_pct,
                "amihud_illiquidity": fingerprint.amihud_illiquidity,
                "conformal_stop": conformal_bounds.conformal_lower_stop,
                "conformal_target": conformal_bounds.conformal_upper_target,
                "conformal_rr": conformal_bounds.conformal_risk_reward_ratio,
                "conformal_coverage_pct": conformal_bounds.realized_coverage_pct,
                "vol_expansion_warning": conformal_bounds.volatility_expansion_warning,
                "is_dual_listed": sym in DUAL_LISTED_PAIRS,
                "dual_listed_us_sym": parity_rec.us_symbol if parity_rec else None,
                "basis_spread_bps": parity_rec.basis_spread_bps if parity_rec else None,
                "basis_zscore": parity_rec.basis_zscore_60d if parity_rec else None,
                "primary_liquidity": parity_rec.primary_liquidity_center if parity_rec else None,
                "shap_score": shap_rec.model_score if shap_rec else None,
                "risk_posture": risk_prof.risk_posture if risk_prof else "NORMAL_EQUILIBRIUM",
                "size_multiplier": risk_prof.position_size_multiplier if risk_prof else 1.0,
                "quant_intel_grade": qi_dict["conviction_grade"] if qi_dict else "SKIP",
                "quant_intel_rr": qi_dict["trade_plan"]["risk_reward_ratio"] if qi_dict else 0.0,
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
                "asset_fingerprint": fingerprint.to_dict(),
                "conformal_bounds": conformal_bounds.to_dict(),
                "cross_border_parity": parity_dict,
                "shap_attribution": shap_dict,
                "idiosyncratic_risk": risk_dict,
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
                        "stance": "POTENTIAL LONG SETUP",
                        "setup_type": plan.setup_type,
                        "preferred_entry": plan.preferred_entry,
                        "alt_entry": plan.alt_entry,
                        "entry_zone": [plan.entry_zone_low, plan.entry_zone_high],
                        "structural_stop": plan.stop_loss,
                        "stop_distance_pct": round(((plan.preferred_entry - plan.stop_loss) / plan.preferred_entry) * 100.0, 1),
                        "targets": [
                            {"target": plan.target_1, "basis": plan.target_1_basis, "rr": plan.planned_rr_t1},
                            {"target": plan.target_2, "basis": plan.target_2_basis, "rr": plan.planned_rr_t2},
                            {"target": plan.target_3, "basis": plan.target_3_basis},
                        ],
                        "risk_reward_ratio": plan.risk_reward_ratio,
                        "planned_rr_t1": plan.planned_rr_t1,
                        "planned_rr_t2": plan.planned_rr_t2,
                        "realized_rr_t1": plan.realized_rr_t1,
                        "holding_period_desc": plan.holding_period_desc,
                        "invalidation": plan.invalidation_predicates,
                        "risk_notes": plan.risk_notes,
                        "rationale": plan.rationale,
                        "data_lineage": plan.data_lineage,
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
                "quant_intel": qi_dict,
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

            # Phase 1 & 6 MVP: Research Journal enriched with Exit Engine State Machine
            journal_payload = journal_engine.get_journal_summary()
            exit_verdicts_list = []
            for p in journal_payload.get("positions", []):
                p_sym = p.get("symbol")
                p_metrics = metrics_map.get(p_sym, {"close": p.get("entry_price", 100.0), "atr_14": 2.0})
                p_ctry = p.get("market", "US")
                p_regime = regimes_data.get(p_ctry, {}).get("regime_state", "WEAK_BULL")
                verdict = exit_signal_engine.evaluate_position(
                    position=p,
                    metrics=p_metrics,
                    macro_regime=p_regime,
                    current_cycle_state=p.get("lifecycle_state", "HOLD"),
                    consecutive_cycles_in_state=p.get("consecutive_cycles", 1),
                )
                p["lifecycle_state"] = verdict.recommended_state
                p["primary_trigger"] = verdict.primary_trigger
                p["active_triggers"] = verdict.active_triggers
                p["action_summary"] = verdict.action_summary
                p["target_allocation_pct"] = verdict.target_allocation_pct
                p["suggested_trailing_stop"] = verdict.suggested_trailing_stop
                p["exit_verdict"] = verdict.to_dict()
                exit_verdicts_list.append(verdict.to_dict())

            with open(d_dir / "journal.json", "w") as f:
                json.dump(journal_payload, f, indent=2)

            with open(d_dir / "exit_signals.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "generated_at": generated_at,
                        "total_positions_evaluated": len(exit_verdicts_list),
                        "exit_verdicts": exit_verdicts_list,
                    },
                    f,
                    indent=2,
                )

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

            # Phase 8: Systematic Paper Trading Scoreboard & Resolution Ledger
            paper_payload = paper_trading_engine.generate_paper_trading_ledger()
            with open(d_dir / "paper_trading.json", "w") as f:
                json.dump(paper_payload, f, indent=2)

            # Phase 9: Portfolio Intelligence & Risk Allocation Engine
            portfolio_payload = portfolio_engine.get_default_portfolios()
            with open(d_dir / "portfolio.json", "w") as f:
                json.dump(portfolio_payload, f, indent=2)

            # Phase 10: Multi-Channel Alerts & CASL/TCPA Consent Ledger
            alerts_payload = alert_engine.generate_alerts_feed()
            with open(d_dir / "alerts.json", "w") as f:
                json.dump(alerts_payload, f, indent=2)

            # Phase 11: Advanced AI & Regulatory Filing Deep-Read Engine
            ai_deep_read_payload = filing_deep_read_engine.generate_ai_deep_read_feed()
            with open(d_dir / "ai_deep_read.json", "w") as f:
                json.dump(ai_deep_read_payload, f, indent=2)

            # Phase 12: Production Scaling, Exchange Entitlements & DR Health
            production_payload = production_scale_engine.generate_production_health_feed()
            with open(d_dir / "production_health.json", "w") as f:
                json.dump(production_payload, f, indent=2)

            # Phase 13: Idiosyncratic Asset Fingerprints Feed
            fingerprints_payload = asset_fingerprint_engine.generate_asset_fingerprints_feed()
            with open(d_dir / "asset_fingerprints.json", "w") as f:
                json.dump(fingerprints_payload, f, indent=2)

            # Phase 14: Adaptive Conformal Prediction (ACI) Statistical Bounds Feed
            conformal_payload = adaptive_conformal_engine.generate_conformal_bounds_feed()
            with open(d_dir / "conformal_bounds.json", "w") as f:
                json.dump(conformal_payload, f, indent=2)

            # Phase 15: Cross-Border Dual-Listed Parity & Basis Feed
            parity_payload = cross_border_parity_engine.generate_feed().to_dict()
            with open(d_dir / "cross_border_parity.json", "w") as f:
                json.dump(parity_payload, f, indent=2)

            # Phase 16: Combinatorial Purged Cross-Validation (CPCV) & FDR Gating Feed
            cpcv_payload = cpcv_engine.generate_feed().to_dict()
            with open(d_dir / "cpcv_validation.json", "w") as f:
                json.dump(cpcv_payload, f, indent=2)

            # Phase 17: LightGBM TreeSHAP Factor Attributions Feed
            shap_payload = shap_engine.generate_feed().to_dict()
            with open(d_dir / "shap_attributions.json", "w") as f:
                json.dump(shap_payload, f, indent=2)

            # Phase 18: Deterministic Idiosyncratic Risk Matrix Feed
            risk_payload = idiosyncratic_risk_engine.generate_feed().to_dict()
            with open(d_dir / "idiosyncratic_risk_matrix.json", "w") as f:
                json.dump(risk_payload, f, indent=2)

            # Phase 22.3: QUANT INTEL Master Intelligence Feed
            with open(d_dir / "quant_intel.json", "w") as f:
                json.dump(
                    {
                        "as_of_date": as_of_date,
                        "generated_at": generated_at,
                        "total_tickers": len(quant_intel_map),
                        "a_grade_count": len([d for d in quant_intel_map.values() if d and d.get("conviction_grade") == "A"]),
                        "b_grade_count": len([d for d in quant_intel_map.values() if d and d.get("conviction_grade") == "B"]),
                        "c_grade_count": len([d for d in quant_intel_map.values() if d and d.get("conviction_grade") == "C"]),
                        "skip_count": len([d for d in quant_intel_map.values() if d and d.get("conviction_grade") == "SKIP"]),
                        "dossiers": quant_intel_map,
                    },
                    f,
                    indent=2,
                )

            # Phase 23.1: Triple-Barrier Meta-Labeling Dataset Matrix Feed
            meta_label_dataset_engine.export_feed(d_dir)

            # Phase 23.2: Machine Learning Meta-Labeling Model & Feature Feed
            meta_label_classifier.export_feed(d_dir)

            # Phase 23.3: Meta-Label Gated Walk-Forward Backtest Comparison Feed
            backtest_engine.export_meta_backtest_feed(d_dir)

            # Phase 24: Cross-Asset Macro Regime Transition Matrix Feed
            macro_regime_engine.export_feed(d_dir)

            # Phase 25: Real-Time Microstructure & Tick Snapshot Feed
            microstructure_engine.export_feed(d_dir)

            # Phase 26: Hierarchical Risk Parity (HRP) Portfolio Allocation Feed
            hrp_portfolio_engine.export_feed(d_dir)

            # Phase 26.2: Historical & Macroeconomic Stress-Testing Feed
            portfolio_stress_engine.export_feed(d_dir)

            # Phase 26.3: QUANT INTEL Portfolio Orchestrator Feed
            portfolio_orchestrator_engine.export_feed(d_dir)

            # Phase 27.1: Cross-Border Multi-Asset Factor Risk Feed
            factor_risk_engine.export_feed(d_dir)

            # Phase 27.2: Active Style Tilts & Factor Return Attribution Feed
            factor_attribution_engine.export_feed(d_dir)

            # Phase 28: Black-Litterman Bayesian View Blending & Turnover Rebalancer Feed
            portfolio_bayesian_engine.export_feed(d_dir / "portfolio_bayesian.json")

            # Phase 29: Cross-Border Dual-Currency & Dual-Listed Arbitrage Feed
            cross_border_fx_engine.export_feed(d_dir / "cross_border_fx.json")

        return {
            "dist_files": 29,
            "symbol_files": symbol_files_count,
            "total_matches": len(scanner_results),
            "total_signals": len(signals_list),
            "total_catalysts": len(master_catalysts),
            "total_journal_positions": len(journal_payload.get("positions", [])),
            "total_watchlists": len(watchlists_payload),
        }


# Global singleton exporter instance
edge_exporter = EdgeExporter()
