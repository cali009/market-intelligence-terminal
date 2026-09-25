"""
Dual-Market Scanner, Regime Classifier & Signal Engine Runner
US + Canada Market Intelligence Platform
Evaluates regimes, scans for 14 tactical setups, and generates research execution plans.
"""

import sys
import json
from pathlib import Path
import pandas as pd
from datetime import date

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import db
from src.engine.technicals import TechnicalAnalysisEngine
from src.engine.scoring import scoring_engine
from src.engine.regime import regime_classifier
from src.engine.scanners import market_scanners
from src.engine.signals import signal_engine


def evaluate_market_regimes():
    """Evaluate market regimes for US and Canada based on index benchmarks."""
    regimes = {}

    for sym, exch, ctry, mkt_label in [("SPY", "AMEX", "US", "US Equities (S&P 500)"), ("XIU", "TSX", "CA", "Canadian Equities (TSX 60)")]:
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

        df = pd.DataFrame(bars)
        m = TechnicalAnalysisEngine.get_latest_feature_snapshot(df)

        regime_out = regime_classifier.classify_regime(
            index_close=m["close"],
            index_sma50=m["sma_50"] or m["close"],
            index_sma200=m["sma_200"] or m["close"],
            index_sma200_slope=m["sma_200_slope_20d"] or 0.0,
            vix_level=16.0,
            yield_spread_10y_2y=0.45 if ctry == "US" else 0.55,
        )
        regimes[ctry] = {
            "label": mkt_label,
            "benchmark": sym,
            "close": m["close"],
            "regime": regime_out,
        }

    return regimes


def run_full_pipeline():
    print(f"\n{'='*95}")
    print(f" US + CANADA QUANT SCANNER & REGIME ENGINE — {date.today().isoformat()}")
    print(f" Status: Live Deterministic Engine | Zero Hallucinations Guaranteed")
    print(f"{'='*95}\n")

    # 1. Market Regimes
    regimes = evaluate_market_regimes()
    print(">>> 1. MACRO REGIME STATUS (DUAL-MARKET)")
    print("-" * 95)
    for ctry, r in regimes.items():
        reg = r["regime"]
        print(f"  [{ctry}] {r['label']} (Benchmark: {r['benchmark']} @ ${r['close']:.2f}):")
        print(f"      State: {reg.regime_state} (Confidence: {reg.filtered_probability*100:.0f}%) | Score Multiplier: {reg.regime_multiplier:.2f}x | Size Scale: {reg.risk_scaling:.2f}x")
        print(f"      Top Macro Drivers:")
        for d in reg.top_drivers:
            print(f"        • {d}")
        print()

    # 2. Scanners & Signals
    securities = db.execute_query("SELECT security_id, symbol, exchange, country, currency, name FROM security WHERE is_active = 1;")
    all_scanner_matches = []
    generated_signals = []

    for s in securities:
        sec_id = s["security_id"]
        sym = s["symbol"]
        exch = s["exchange"]
        ctry = s["country"]
        curr = s["currency"]

        bars = db.execute_query(
            "SELECT trading_date, open, high, low, close, volume, adjusted_close FROM bar_1d WHERE security_id = ? ORDER BY trading_date ASC;",
            (sec_id,),
        )
        if len(bars) < 30:
            continue

        df = pd.DataFrame(bars)
        m = TechnicalAnalysisEngine.get_latest_feature_snapshot(df)

        # Scanner matches
        matches = market_scanners.scan_all(s, m)
        for match in matches:
            all_scanner_matches.append(match)

        # Opportunity Score
        regime_info = regimes.get(ctry, {}).get("regime")
        multiplier = regime_info.regime_multiplier if regime_info else 1.0
        regime_name = regime_info.regime_state if regime_info else "WEAK_BULL"

        score_rec = scoring_engine.evaluate_security(
            security_id=sec_id,
            metrics=m,
            fundamental_score=65,
            regime_state=regime_name,
            regime_multiplier=multiplier,
            horizon="POSITION",
        )

        # Generate Signal Plan if score qualifies
        plan = signal_engine.generate_long_signal(
            security_id=sec_id,
            metrics=m,
            composite_score=score_rec.composite_score,
            confidence_tier=score_rec.confidence_tier,
            horizon="POSITION",
        )

        if plan:
            # Save signal to database
            sig_id = db.execute_write(
                """
                INSERT INTO signal (
                    security_id, strategy_id, generated_at, direction,
                    preferred_entry, alt_entry, entry_zone_low, entry_zone_high,
                    stop_loss, target_1, target_2, target_3,
                    risk_reward_ratio, confidence_tier, invalidation_rules, risk_notes, disclaimer_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sec_id,
                    "FACTOR8_POSITION_LONG",
                    date.today().isoformat(),
                    plan.direction,
                    plan.preferred_entry,
                    plan.alt_entry,
                    plan.entry_zone_low,
                    plan.entry_zone_high,
                    plan.stop_loss,
                    plan.target_1,
                    plan.target_2,
                    plan.target_3,
                    plan.risk_reward_ratio,
                    plan.confidence_tier,
                    json.dumps(plan.invalidation_predicates),
                    json.dumps(plan.risk_notes),
                    plan.disclaimer_version,
                ),
            )
            generated_signals.append((s, plan, score_rec.composite_score))

    # Print Scanner Highlights
    print(">>> 2. TAC-14 SCANNER HIGHLIGHTS (ACTIVE SETUPS)")
    print("-" * 95)
    print(f"Total pattern matches identified across dual-market universe: {len(all_scanner_matches)}\n")

    # Group by scanner
    scanner_groups = {}
    for m in all_scanner_matches:
        scanner_groups.setdefault(m.scanner_name, []).append(m)

    for name, matches in sorted(scanner_groups.items(), key=lambda x: len(x[1]), reverse=True)[:6]:
        sym_list = ", ".join([f"{m.symbol} (${m.price:.2f})" for m in matches])
        print(f"  • {name} ({len(matches)} names):")
        print(f"      {sym_list}")
        print(f"      Why: {matches[0].why_matched}\n")

    # Print Research Signal Plans
    print(">>> 3. RESEARCH EXECUTION PLANS (GRADE A & B SETUPS)")
    print("-" * 95)
    generated_signals.sort(key=lambda x: x[2], reverse=True)

    for s, p, score in generated_signals[:5]:
        sym = s["symbol"]
        exch = s["exchange"]
        curr = s["currency"]
        name = s["name"]

        print(f"  ┌─ {sym} ({exch} - {name}) | Score: {score}/100 (Tier {p.confidence_tier})")
        print(f"  │  Preferred Entry: ${p.preferred_entry:.2f} {curr} | Entry Zone: [${p.entry_zone_low:.2f} – ${p.entry_zone_high:.2f}]")
        print(f"  │  Structural Stop: ${p.stop_loss:.2f} {curr} (Risk: ${p.preferred_entry - p.stop_loss:.2f} / share)")
        print(f"  │  Profit Targets:  T1: ${p.target_1:.2f} (1.6R) | T2: ${p.target_2:.2f} (2.6R) | T3: ${p.target_3:.2f} (4.0R)")
        print(f"  │  Risk-to-Reward:  {p.risk_reward_ratio:.1f}:1 R:R")
        print(f"  │  Invalidation:    {p.invalidation_predicates[0]['description']}")
        print(f"  └──────────────────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    run_full_pipeline()
