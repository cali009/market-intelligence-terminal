"""
QUANT INTEL Automated Invalidation Sentinel & Live Monitoring Engine (Phase 22.5)
US + Canada Market Intelligence Platform

Continuously evaluates live equities against active QUANT INTEL trade theses.
Detects:
  1. STOP_LOSS_BREACH (Price closing below ATR structural stop loss)
  2. BREAKOUT_FAILURE (Consecutive closes below structural support pivot)
  3. REGIME_DOWNGRADE (Macro shift to BEARISH / CRISIS / HIGH_VOLATILITY)
  4. MATERIAL_ADVERSE_CATALYST (High-materiality filing / negative sentiment shift)
  5. TARGET_1_HIT_RATCHET (Target 1 achieved -> 40% trim & breakeven stop ratchet)
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import pandas as pd

from src.data.db import db
from src.compliance.linter import linter
from src.models.schemas import AlertRecord
from src.engine.quant_intel_memory import quant_intel_memory_ledger


@dataclass
class InvalidationAlert:
    symbol: str
    predicate_type: str       # "STOP_LOSS_BREACH", "BREAKOUT_FAILURE", "REGIME_DOWNGRADE", "ADVERSE_CATALYST", "TARGET_1_HIT_RATCHET"
    severity: str             # "CRITICAL", "WARNING", "NOTICE"
    trigger_level: float
    current_price: float
    headline: str
    body: str
    action_required: str      # "IMMEDIATE_EXIT", "TIGHTEN_STOP_TO_BREAKEVEN", "DE_RISK_50_PCT", "CANCEL_PENDING_ORDER"
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QuantIntelSentinel:
    """
    Live monitoring engine tracking active QUANT INTEL setups and dispatching
    deterministic invalidation alerts when thesis conditions fail or targets trigger.
    """

    def __init__(self):
        self.disclaimer_text = (
            "Impersonal decision-support research alert based on quantitative models. "
            "Does not provide personalized investment recommendations. Verify liquidity and risk before execution."
        )

    def evaluate_ticker_sentinel(
        self,
        symbol: str,
        dossier: Any,
        recent_bars: Optional[List[Dict[str, Any]]] = None
    ) -> List[InvalidationAlert]:
        """
        Evaluates a single ticker dossier against all active invalidation predicates.
        """
        alerts = []
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = dossier.trade_plan
        tech = dossier.layer_3_technical
        regime = dossier.layer_1_regime
        sentiment = dossier.layer_5_sentiment

        # Load recent bars if not provided
        if not recent_bars:
            sec_rows = db.execute_query("SELECT security_id FROM security WHERE symbol = ?;", (symbol,))
            if sec_rows:
                sec_id = sec_rows[0]["security_id"]
                recent_bars = db.execute_query(
                    "SELECT trading_date, open, high, low, close, volume FROM bar_1d WHERE security_id = ? ORDER BY trading_date DESC LIMIT 5;",
                    (sec_id,)
                )

        if not recent_bars:
            return alerts

        latest_bar = recent_bars[0]
        close = float(latest_bar["close"])
        high = float(latest_bar["high"])
        low = float(latest_bar["low"])

        # 1. PREDICATE: STOP_LOSS_BREACH (Close below structural stop)
        if close < p.stop_loss:
            headline = f"{symbol}: Structural Stop Loss Breached (${close:.2f} < ${p.stop_loss:.2f})"
            body = (
                f"Closing price of ${close:.2f} has penetrated the structural stop level of ${p.stop_loss:.2f} "
                f"(-{p.stop_distance_pct:.1f}% risk threshold). The QUANT INTEL long thesis is mathematically invalidated."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="STOP_LOSS_BREACH",
                    severity="CRITICAL",
                    trigger_level=p.stop_loss,
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="IMMEDIATE_EXIT",
                    timestamp=now_str,
                )
            )

        # 2. PREDICATE: BREAKOUT_FAILURE (Two consecutive closes below entry support)
        if len(recent_bars) >= 2:
            prev_close = float(recent_bars[1]["close"])
            if close < p.entry_zone_ideal and prev_close < p.entry_zone_ideal and close >= p.stop_loss:
                headline = f"{symbol}: Support Pivot Slipped — Breakout Failure Warning"
                body = (
                    f"Two consecutive closes (${prev_close:.2f}, ${close:.2f}) below ideal entry pivot of ${p.entry_zone_ideal:.2f}. "
                    f"Setup momentum has stalled; consider reducing position size by 50% or moving stop to defense."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol=symbol,
                        predicate_type="BREAKOUT_FAILURE",
                        severity="WARNING",
                        trigger_level=p.entry_zone_ideal,
                        current_price=close,
                        headline=headline,
                        body=body,
                        action_required="DE_RISK_50_PCT",
                        timestamp=now_str,
                    )
                )

        # 3. PREDICATE: REGIME_DOWNGRADE (Macro shift to BEARISH / CRISIS / HIGH_VOLATILITY)
        if regime.regime_fit == "NO" or regime.trend_type in ("Bear", "Volatile"):
            headline = f"{symbol}: Macro Regime Mismatch Triggered ({regime.regime_state})"
            body = (
                f"Macro environment shifted to {regime.regime_state} with liquidity environment at {regime.liquidity_env}. "
                f"Long setup conviction degraded; stops should be widened or position exposure curtailed."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="REGIME_DOWNGRADE",
                    severity="WARNING",
                    trigger_level=0.0,
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="WIDEN_STOP_OR_DE_RISK",
                    timestamp=now_str,
                )
            )

        # 4. PREDICATE: MATERIAL_ADVERSE_CATALYST (Sentiment turned negative)
        if sentiment.skip_triggered or sentiment.net_sentiment_score < -0.15:
            headline = f"{symbol}: Material Adverse Catalyst Detected (Sentiment: {sentiment.net_sentiment_score:+.2f})"
            body = (
                f"Recent regulatory filings or news disclosures turned negative. Headline: '{sentiment.key_headline}'. "
                f"Net sentiment decay score dropped to {sentiment.net_sentiment_score:.2f}, canceling trade thesis."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="ADVERSE_CATALYST",
                    severity="WARNING",
                    trigger_level=0.0,
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="REVIEW_FOR_INVALIDATION",
                    timestamp=now_str,
                )
            )

        # 5. EXECUTION SENTINEL: TARGET_1_HIT_RATCHET (Take 40% trim & ratchet stop to breakeven)
        if high >= p.target_1:
            headline = f"{symbol}: Target 1 Achieved (${high:.2f} >= ${p.target_1:.2f}) — Stop Ratchet to Breakeven"
            body = (
                f"Price has attained Target 1 (${p.target_1:.2f}). Systematic rules mandate taking 40% off "
                f"and ratcheting stop loss up to Breakeven (${p.entry_zone_ideal:.2f}) to eliminate left-tail capital risk."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="TARGET_1_HIT_RATCHET",
                    severity="NOTICE",
                    trigger_level=p.target_1,
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="TIGHTEN_STOP_TO_BREAKEVEN",
                    timestamp=now_str,
                )
            )

        return alerts

    def evaluate_universe_sentinels(self, quant_intel_dossiers: Dict[str, Any]) -> List[InvalidationAlert]:
        """
        Runs invalidation checks across the entire active universe.
        """
        all_alerts = []
        for sym, dossier in quant_intel_dossiers.items():
            alerts = self.evaluate_ticker_sentinel(sym, dossier)
            all_alerts.extend(alerts)
        return all_alerts

    def convert_to_system_alerts(self, invalidation_alerts: List[InvalidationAlert]) -> List[AlertRecord]:
        """
        Converts internal InvalidationAlert objects to formal AlertRecord entities
        compliant with the platform's multi-channel dispatcher and impersonal compliance linter.
        """
        records = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for inv in invalidation_alerts:
            linter.assert_clean(inv.headline)
            linter.assert_clean(inv.body)

            channels = ["IN_APP"]
            if inv.severity == "CRITICAL":
                channels = ["IN_APP", "EMAIL", "SMS"]
            elif inv.severity == "WARNING":
                channels = ["IN_APP", "EMAIL", "WEB_PUSH"]

            if "STOP" in inv.predicate_type or "BREAKOUT_FAILURE" in inv.predicate_type:
                taxonomy = "EXIT_TRIGGER_ESCALATION"
            elif "REGIME" in inv.predicate_type:
                taxonomy = "REGIME_SHIFT"
            elif "CATALYST" in inv.predicate_type:
                taxonomy = "CATALYST_MATERIALITY"
            else:
                taxonomy = "TECHNICAL_BREAKOUT"

            rec = AlertRecord(
                alert_id=f"ALT_{today}_{inv.symbol}_{inv.predicate_type[:10]}",
                timestamp=inv.timestamp,
                symbol=inv.symbol,
                taxonomy=taxonomy,
                severity=inv.severity,
                headline=inv.headline,
                body=inv.body,
                channels=channels,
                dedupe_key=f"{inv.symbol}:{inv.predicate_type}:{today}",
                data_payload={
                    "predicate": inv.predicate_type,
                    "action": inv.action_required,
                    "trigger_level": inv.trigger_level,
                    "current_price": inv.current_price,
                },
                is_quiet_hours_eligible=(inv.severity != "CRITICAL"),
                disclaimer=self.disclaimer_text,
            )
            records.append(rec)

        return records


# Global singleton instance
quant_intel_sentinel = QuantIntelSentinel()
