"""
Daily Quantitative Scoring Engine Runner
Loads bars from canonical bar_1d, computes technical features,
evaluates 8-factor scores, and persists to database with attribution.
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


def run_scoring(horizon: str = "POSITION"):
    securities = db.execute_query(
        "SELECT security_id, symbol, exchange, country, currency, name FROM security WHERE is_active = 1;"
    )
    print(f"\n{'='*95}")
    print(f" US + CANADA DUAL-MARKET QUANTITATIVE INTELLIGENCE TERMINAL — {date.today().isoformat()}")
    print(f" Horizon: {horizon} | Model: 2026.1-factor8 | Provenance: 100% Deterministic")
    print(f"{'='*95}\n")

    results = []

    for s in securities:
        sec_id = s["security_id"]
        sym = s["symbol"]
        exch = s["exchange"]
        curr = s["currency"]
        name = s["name"]

        # Load bars from bar_1d
        bars_query = """
            SELECT trading_date, open, high, low, close, volume, adjusted_close
            FROM bar_1d
            WHERE security_id = ?
            ORDER BY trading_date ASC;
        """
        bars_data = db.execute_query(bars_query, (sec_id,))
        if len(bars_data) < 20:
            continue

        df = pd.DataFrame(bars_data)
        metrics = TechnicalAnalysisEngine.get_latest_feature_snapshot(df)

        # In Sprint 1, evaluate with baseline regime multiplier
        score_rec = scoring_engine.evaluate_security(
            security_id=sec_id,
            metrics=metrics,
            fundamental_score=65,  # Baseline neutral fundamental
            regime_state="WEAK_BULL",
            regime_multiplier=1.00,
            news_contribution=0.0,
            sector_contribution=0.0,
            horizon=horizon,
        )

        # Save to database
        db.execute_write(
            """
            INSERT INTO score (
                security_id, as_of_date, knowledge_at, technical_score, fundamental_score,
                regime_state, regime_multiplier, news_contribution, sector_contribution,
                risk_penalty, composite_score, confidence_tier, data_quality_floor,
                factor_attribution_json, model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(security_id, as_of_date) DO UPDATE SET
                technical_score=excluded.technical_score,
                composite_score=excluded.composite_score,
                confidence_tier=excluded.confidence_tier,
                factor_attribution_json=excluded.factor_attribution_json
            """,
            (
                sec_id,
                score_rec.as_of_date.isoformat(),
                score_rec.knowledge_at.isoformat(),
                score_rec.technical_score,
                score_rec.fundamental_score,
                score_rec.regime_state,
                score_rec.regime_multiplier,
                score_rec.news_contribution,
                score_rec.sector_contribution,
                score_rec.risk_penalty,
                score_rec.composite_score,
                score_rec.confidence_tier,
                score_rec.data_quality_floor,
                json.dumps(score_rec.factor_attribution_json),
                score_rec.model_version,
            ),
        )

        results.append({
            "symbol": sym,
            "exchange": exch,
            "currency": curr,
            "name": name,
            "price": metrics["close"],
            "score": score_rec.composite_score,
            "tier": score_rec.confidence_tier,
            "trend": score_rec.factor_attribution_json["trend_score"],
            "rs": score_rec.factor_attribution_json["relative_strength_score"],
            "setup": score_rec.factor_attribution_json["setup_geometry_score"],
            "vol": score_rec.factor_attribution_json["volume_participation_score"],
            "risk_pen": score_rec.factor_attribution_json["risk_penalty"],
            "rsi": round(metrics.get("rsi_14") or 50.0, 1),
            "rvol": round(metrics.get("rvol_20") or 1.0, 2),
            "prox_52w": round((metrics.get("proximity_52w_high") or 0.0) * 100, 1),
        })

    # Sort results by composite score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    header = f"{'RNK':<4} {'SYMBOL':<7} {'EXCH':<7} {'PRICE':<9} {'SCORE':<7} {'TIER':<5} {'TREND':<6} {'RS':<5} {'SETUP':<6} {'VOL':<5} {'PEN':<5} {'RSI':<6} {'RVOL':<6} {'52W%':<6} {'NAME'}"
    print(header)
    print("-" * 95)

    for idx, r in enumerate(results, start=1):
        price_str = f"{r['price']:.2f} {r['currency']}"
        score_str = f"{r['score']}/100"
        prox_str = f"{r['prox_52w']:+.1f}%"
        row = (
            f"{idx:<4} {r['symbol']:<7} {r['exchange']:<7} {price_str:<9} {score_str:<7} {r['tier']:<5} "
            f"{r['trend']:<6.1f} {r['rs']:<5.1f} {r['setup']:<6.1f} {r['vol']:<5.1f} {r['risk_pen']:<5.1f} "
            f"{r['rsi']:<6.1f} {r['rvol']:<6.2f} {prox_str:<6} {r['name']}"
        )
        print(row)

    print("-" * 95)
    print(f"\nEvaluation Complete: {len(results)} dual-market securities scored and persisted with full factor attribution.\n")


if __name__ == "__main__":
    run_scoring()
