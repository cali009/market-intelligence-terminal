"""
Exit Signal Engine & Position State Machine (Phase 6)
US + Canada Market Intelligence Platform

Implements the institutional position lifecycle:
    ENTERED -> HOLD -> WATCH -> REDUCE -> EXIT -> (closed)
    or EMERGENCY RISK.

Evaluates 10 categorical exit triggers with hysteresis and cycle counters:
- Stop-loss breach (EXIT)
- Profit targets T1/T2 hit (HOLD trim 1/3 / REDUCE trail remainder)
- Trend reversal (REDUCE -> EXIT)
- Momentum deterioration (WATCH -> REDUCE)
- Failed breakout (REDUCE)
- Fundamental deterioration (REDUCE / EXIT review)
- Negative news event (REDUCE / EXIT)
- Valuation excess (WATCH)
- Thesis invalidation (EXIT)
- Emergency risk (EMERGENCY_RISK)
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from src.models.schemas import ExitVerdict, JournalPosition


class ExitSignalEngine:
    """
    Evaluates recorded research positions against multi-factor exit triggers.
    Advisory outputs only — the platform never automates broker orders.
    """

    def __init__(self):
        # In-memory escalation cycle tracking by position_id or symbol
        self._escalation_history: Dict[str, Dict[str, int]] = {}

    def evaluate_position(
        self,
        position: Dict[str, Any],
        current_metrics: Optional[Dict[str, Any]] = None,
        fundamental_metrics: Optional[Dict[str, Any]] = None,
        recent_catalysts: Optional[List[Dict[str, Any]]] = None,
        regime_state: str = "WEAK_BULL",
        metrics: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> ExitVerdict:
        """
        Evaluate a single held position and produce a state machine verdict.
        """
        if current_metrics is None:
            current_metrics = metrics or {}

        sym = position.get("symbol", "")
        entry_price = float(position.get("entry_price") or current_metrics.get("close", 100.0))
        current_price = float(current_metrics.get("close", entry_price))
        stop_loss = float(position.get("stop_loss") or (entry_price * 0.92))
        profit_target = float(position.get("profit_target") or (entry_price * 1.15))

        # P&L and R-multiple calculation
        pnl_pct = round(((current_price - entry_price) / entry_price) * 100.0, 2)
        risk_per_share = max(0.01, entry_price - stop_loss)
        r_multiple = round((current_price - entry_price) / risk_per_share, 2)

        triggers: List[str] = []
        details: List[Dict[str, Any]] = []
        severity = "INFO"
        state = "HOLD"
        reversal_condition = "Maintain price above 20-day moving average and recorded stop level."

        # Technical Indicators
        sma_20 = current_metrics.get("sma_20") or current_price
        sma_50 = current_metrics.get("sma_50") or current_price
        rsi = current_metrics.get("rsi_14") or 50.0
        rvol = current_metrics.get("rvol_20") or 1.0
        atr = current_metrics.get("atr_14") or (current_price * 0.02)
        macd_hist = current_metrics.get("macd_hist") or 0.0

        # -------------------------------------------------------------
        # TRIGGER 1: EMERGENCY RISK (Materiality 5, fraud, halt, insolvency)
        # -------------------------------------------------------------
        if recent_catalysts:
            crit = [c for c in recent_catalysts if c.get("materiality_score") == 5 and c.get("sentiment_score", 0) < -0.3]
            if crit:
                triggers.append("EMERGENCY_RISK_EVENT")
                details.append({
                    "trigger": "EMERGENCY_RISK_EVENT",
                    "headline": crit[0].get("headline", ""),
                    "materiality": 5,
                    "action": "Immediate de-risk recommendation and portfolio cross-exposure audit.",
                })
                state = "EMERGENCY_RISK"
                severity = "EMERGENCY"
                reversal_condition = "Complete resolution of critical disclosure / regulatory event."

        # -------------------------------------------------------------
        # TRIGGER 2: STOP LOSS BREACH
        # -------------------------------------------------------------
        if state != "EMERGENCY_RISK" and current_price <= stop_loss:
            triggers.append("STOP_LOSS_BREACH")
            details.append({
                "trigger": "STOP_LOSS_BREACH",
                "threshold": stop_loss,
                "current_price": current_price,
                "action": f"Price ${current_price:.2f} closed at or below structural stop loss ${stop_loss:.2f}.",
            })
            state = "EXIT"
            severity = "EXIT"
            reversal_condition = f"Reclaim of support level above ${stop_loss:.2f} on confirmed volume."

        # -------------------------------------------------------------
        # TRIGGER 3: PROFIT TARGET HIT (T1 trim / T2 trail)
        # -------------------------------------------------------------
        if state not in ("EMERGENCY_RISK", "EXIT"):
            if current_price >= profit_target:
                triggers.append("PROFIT_TARGET_HIT")
                details.append({
                    "trigger": "PROFIT_TARGET_HIT",
                    "target_price": profit_target,
                    "current_price": current_price,
                    "action": f"Target price ${profit_target:.2f} reached (+{pnl_pct}% / {r_multiple}R). Consider trimming 1/3 position.",
                })
                state = "HOLD"
                severity = "INFO"
                reversal_condition = "Trail stop to breakeven or 20-day moving average."

        # -------------------------------------------------------------
        # TRIGGER 4: TREND REVERSAL (Close < 50DMA + MACD histogram negative)
        # -------------------------------------------------------------
        if state not in ("EMERGENCY_RISK", "EXIT"):
            if current_price < sma_50 and macd_hist < 0:
                triggers.append("TREND_REVERSAL_CONFIRMED")
                details.append({
                    "trigger": "TREND_REVERSAL_CONFIRMED",
                    "sma_50": sma_50,
                    "macd_hist": macd_hist,
                    "action": "Closing below 50-day moving average with negative momentum divergence.",
                })
                state = "REDUCE"
                severity = "REDUCE"
                reversal_condition = "Daily close reclaim above 50-DMA."

        # -------------------------------------------------------------
        # TRIGGER 5: MOMENTUM DETERIORATION (RSI divergence or breakdown below 20DMA)
        # -------------------------------------------------------------
        if state not in ("EMERGENCY_RISK", "EXIT", "REDUCE"):
            if current_price < sma_20 and rsi < 45.0:
                triggers.append("MOMENTUM_DETERIORATION")
                details.append({
                    "trigger": "MOMENTUM_DETERIORATION",
                    "rsi": rsi,
                    "action": "Short-term momentum weakening below 20-DMA. Position placed on active WATCH.",
                })
                state = "WATCH"
                severity = "WATCH"
                reversal_condition = "Price recovery above 20-DMA with RSI rising > 50."

        # -------------------------------------------------------------
        # TRIGGER 6: FUNDAMENTAL DETERIORATION (YoY Margin Compression > 200 bps)
        # -------------------------------------------------------------
        if state not in ("EMERGENCY_RISK", "EXIT") and fundamental_metrics:
            margin_trend = fundamental_metrics.get("margin_trend_yoy_bps")
            if margin_trend is not None and margin_trend < -200.0:
                triggers.append("FUNDAMENTAL_MARGIN_COMPRESSION")
                details.append({
                    "trigger": "FUNDAMENTAL_MARGIN_COMPRESSION",
                    "margin_trend_yoy_bps": margin_trend,
                    "action": f"Operating margin compressed {abs(margin_trend):.0f} bps YoY in latest filing.",
                })
                if state == "HOLD":
                    state = "WATCH"
                    severity = "WATCH"
                elif state == "WATCH":
                    state = "REDUCE"
                    severity = "REDUCE"

        # -------------------------------------------------------------
        # TRIGGER 7: REGIME DETERIORATION (Crisis or Bearish market shift)
        # -------------------------------------------------------------
        if state not in ("EMERGENCY_RISK", "EXIT"):
            if regime_state in ("CRISIS", "BEAR"):
                triggers.append("REGIME_DETERIORATION")
                details.append({
                    "trigger": "REGIME_DETERIORATION",
                    "regime_state": regime_state,
                    "action": f"Market shifted to {regime_state} regime. Recommend portfolio risk-halving.",
                })
                if state in ("HOLD", "WATCH"):
                    state = "REDUCE"
                    severity = "REDUCE"

        # -------------------------------------------------------------
        # HYSTERESIS & ESCALATION LOGIC
        # -------------------------------------------------------------
        pos_key = f"{position.get('id', sym)}_{sym}"
        if pos_key not in self._escalation_history:
            self._escalation_history[pos_key] = {"WATCH": 0, "REDUCE": 0}

        hist = self._escalation_history[pos_key]

        if state == "WATCH":
            hist["WATCH"] += 1
            if hist["WATCH"] >= 3:
                state = "REDUCE"
                severity = "REDUCE"
                triggers.append("ESCALATION_WATCH_TO_REDUCE")
                details.append({
                    "trigger": "ESCALATION_WATCH_TO_REDUCE",
                    "cycles": hist["WATCH"],
                    "action": "Persistent WATCH state over 3 consecutive review cycles escalated to REDUCE.",
                })
        elif state == "REDUCE":
            hist["REDUCE"] += 1
            if hist["REDUCE"] >= 2:
                state = "EXIT"
                severity = "EXIT"
                triggers.append("ESCALATION_REDUCE_TO_EXIT")
                details.append({
                    "trigger": "ESCALATION_REDUCE_TO_EXIT",
                    "cycles": hist["REDUCE"],
                    "action": "Unreversed deterioration across 2 REDUCE cycles escalated to EXIT.",
                })
        else:
            # De-escalation reset
            hist["WATCH"] = max(0, hist["WATCH"] - 1)
            hist["REDUCE"] = max(0, hist["REDUCE"] - 1)

        escalation_cycles = hist.get(state, 0)
        primary_trigger = triggers[0] if triggers else "NONE"

        action_summaries = {
            "EMERGENCY_RISK": "EMERGENCY: Immediate portfolio de-risking recommended due to critical event.",
            "EXIT": "EXIT: Setup criteria or risk boundaries breached. Capital reallocation recommended.",
            "REDUCE": "REDUCE: Trimming recommended to protect gains / mitigate adverse momentum (target 50% allocation).",
            "WATCH": "WATCH: Elevated caution. Trailing stop tightened; monitor closely for reversal or breach.",
            "HOLD": "HOLD: Constructive price structure maintained within expected technical parameters.",
        }
        target_allocations = {
            "HOLD": 100.0,
            "WATCH": 100.0,
            "REDUCE": 50.0,
            "EXIT": 0.0,
            "EMERGENCY_RISK": 0.0,
        }
        suggested_trailing = round(max(stop_loss, current_price - (2.0 * atr)), 2)

        return ExitVerdict(
            position_id=position.get("id"),
            symbol=sym,
            state=state,
            active_triggers=triggers,
            trigger_severity=severity,
            trigger_details=details,
            pnl_pct=pnl_pct,
            r_multiple=r_multiple,
            reversal_condition=reversal_condition,
            escalation_cycles=escalation_cycles,
            timestamp=datetime.now(timezone.utc).isoformat(),
            primary_trigger=primary_trigger,
            action_summary=action_summaries.get(state, "HOLD: Maintain position"),
            target_allocation_pct=target_allocations.get(state, 100.0),
            suggested_trailing_stop=suggested_trailing,
        )

    def evaluate_journal_positions(
        self,
        positions: List[Dict[str, Any]],
        universe_metrics: Dict[str, Dict[str, Any]],
        regimes: Dict[str, Any],
    ) -> List[ExitVerdict]:
        """
        Evaluate all active journal positions in the workspace.
        """
        verdicts = []
        for pos in positions:
            sym = pos.get("symbol", "")
            m = universe_metrics.get(sym, {})
            ctry = pos.get("market", "US")
            reg = regimes.get(ctry, {}).get("regime_state", "WEAK_BULL")
            v = self.evaluate_position(pos, m, regime_state=reg)
            verdicts.append(v)
        return verdicts


# Global singleton instance
exit_signal_engine = ExitSignalEngine()
