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
        if regime and (getattr(regime, "regime_fit", "YES") == "NO" or getattr(regime, "trend_type", "Bull") in ("Bear", "Volatile")):
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
        if sentiment and (getattr(sentiment, "skip_triggered", False) or getattr(sentiment, "net_sentiment_score", 0.0) < -0.15):
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

        # 6. PREDICATE: ADVERSE_REGIME_HAZARD_SPIKE (Elevated Markov transition hazard)
        hazard_tier = getattr(regime, "hazard_tier", "LOW_HAZARD")
        hazard_20d = getattr(regime, "adverse_hazard_rate_20d", 0.0)
        cross_posture = getattr(regime, "cross_border_macro_divergence_posture", "SYNCHRONIZED_EXPANSION")
        if hazard_tier in ("ELEVATED_HAZARD", "SEVERE_HAZARD") or hazard_20d >= 0.25 or cross_posture == "CROSS_BORDER_STRESS":
            headline = f"{symbol}: Elevated Forward Macro Regime Hazard ({hazard_tier})"
            body = (
                f"Bayesian Markov projection indicates a {hazard_20d * 100.0:.1f}% adverse transition hazard over 20 sessions "
                f"under {cross_posture} posture. Defensive risk calibration is warranted."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="ADVERSE_REGIME_HAZARD_SPIKE",
                    severity="WARNING",
                    trigger_level=hazard_20d,
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="DE_RISK_OR_TIGHTEN_STOP",
                    timestamp=now_str,
                )
            )

        # 7. PREDICATE: LIQUIDITY_VOID_SPIKE (Flash spread blowout > 3.0x nominal)
        micro = getattr(dossier, "microstructure_metrics", None)
        if micro and getattr(micro, "liquidity_state", "NORMAL") == "LIQUIDITY_VOID":
            headline = f"{symbol}: Flash Liquidity Void Detected (Spread Blowout)"
            body = (
                f"Top-of-book depth shows extreme spread expansion with thin quotes. "
                f"Execution risk elevated; limit orders mandatory to avoid market impact."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="LIQUIDITY_VOID_SPIKE",
                    severity="WARNING",
                    trigger_level=getattr(micro, "order_book_imbalance", 0.0),
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="USE_LIMIT_ORDERS_MANDATORY",
                    timestamp=now_str,
                )
            )

        # 8. PREDICATE: MICROSTRUCTURE_SELL_SWEEP (Aggressive selling with ask replenishment)
        if micro and getattr(micro, "cvd_1m_delta", 0) < -1500 and getattr(micro, "order_book_imbalance", 0.0) < -0.25:
            headline = f"{symbol}: Aggressive Seller Sweep Detected"
            body = (
                f"Sub-second trade tape indicates aggressive selling absorption (OBI {micro.order_book_imbalance:.2f}). "
                f"Watch support pivot carefully for structural failure."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="MICROSTRUCTURE_SELL_SWEEP",
                    severity="WARNING",
                    trigger_level=float(micro.cvd_1m_delta),
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="DE_RISK_OR_TIGHTEN_STOP",
                    timestamp=now_str,
                )
            )

        # 9. PREDICATE: EXECUTION_SLIPPAGE_WARNING (Projected market impact > 15 bps on recommended sizing)
        plan = getattr(dossier, "trade_plan", None)
        slip_bps = getattr(plan, "tca_expected_slippage_bps", 0.0) if plan else 0.0
        if slip_bps >= 15.0:
            headline = f"{symbol}: High Execution Slippage Warning ({slip_bps:.1f} bps)"
            body = (
                f"Projected market impact exceeds 15.0 bps under current visible liquidity. "
                f"Direct market execution discouraged; route via {getattr(plan, 'tca_execution_strategy', 'VWAP')} algorithmic slicing."
            )
            alerts.append(
                InvalidationAlert(
                    symbol=symbol,
                    predicate_type="EXECUTION_SLIPPAGE_WARNING",
                    severity="NOTICE",
                    trigger_level=slip_bps,
                    current_price=close,
                    headline=headline,
                    body=body,
                    action_required="ROUTE_VIA_ALGO_SLICING",
                    timestamp=now_str,
                )
            )

        return alerts

    def evaluate_portfolio_level_sentinels(
        self,
        positions: Dict[str, Any],
        stress_metrics: Dict[str, Any],
        avg_pairwise_corr: float = 0.45,
        factor_risk_metrics: Optional[Dict[str, Any]] = None,
        factor_attribution_metrics: Optional[Dict[str, Any]] = None,
        bayesian_metrics: Optional[Dict[str, Any]] = None,
        cross_border_fx_metrics: Optional[Dict[str, Any]] = None,
        options_intelligence_metrics: Optional[Dict[str, Any]] = None,
        sovereign_yield_metrics: Optional[Dict[str, Any]] = None,
        dark_pool_metrics: Optional[Dict[str, Any]] = None,
    ) -> List[InvalidationAlert]:
        """
        Evaluates portfolio-level risk invariants:
        - Concentration caps & country balance (Predicate 10)
        - Correlation spikes (Predicate 11)
        - CVaR tail limits (Predicate 12)
        - Factor crowding limits (Predicate 13)
        - FX / Commodity overexposure (Predicate 14)
        - Specific alpha erosion (Predicate 15)
        - Excessive turnover limits (Predicate 16)
        - Bayesian estimation error spike (Predicate 17)
        - Dual-listed parity dislocations (Predicate 18)
        - Unhedged currency volatility drag (Predicate 19)
        - Gamma flip dealer regime transitions (Predicate 20)
        - Volatility skew tail inversion (Predicate 21)
        """
        alerts = []
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 10. PREDICATE: PORTFOLIO_CONCENTRATION_BREACH (>18.0% single asset or >75.0% single country)
        total_weight = sum(p.final_weight if hasattr(p, "final_weight") else p.get("final_weight", 0) for p in positions.values())
        us_weight = sum(p.final_weight if hasattr(p, "final_weight") else p.get("final_weight", 0) for p in positions.values() if (getattr(p, "country", None) or p.get("country")) == "US")
        ca_weight = sum(p.final_weight if hasattr(p, "final_weight") else p.get("final_weight", 0) for p in positions.values() if (getattr(p, "country", None) or p.get("country")) == "CA")

        for sym, pos in positions.items():
            w = pos.final_weight if hasattr(pos, "final_weight") else pos.get("final_weight", 0)
            if w > 0.180:
                headline = f"Portfolio Concentration Limit Exceeded: {sym} ({w * 100:.1f}%)"
                body = (
                    f"Asset {sym} holds {w * 100:.1f}% of total portfolio capital, breaching the 18.0% single-asset risk ceiling. "
                    f"Trimming and rebalancing indicated."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol=sym,
                        predicate_type="PORTFOLIO_CONCENTRATION_BREACH",
                        severity="WARNING",
                        trigger_level=0.180,
                        current_price=getattr(pos, "effective_execution_price", 0.0),
                        headline=headline,
                        body=body,
                        action_required="REBALANCE_CONCENTRATION_CAP",
                        timestamp=now_str,
                    )
                )

        if total_weight > 0:
            us_pct = (us_weight / total_weight) * 100.0
            ca_pct = (ca_weight / total_weight) * 100.0
            if us_pct > 75.0 or ca_pct > 75.0:
                dom_country = "US" if us_pct > 75.0 else "CA"
                dom_pct = max(us_pct, ca_pct)
                headline = f"Cross-Border Country Concentration Exceeded: {dom_country} ({dom_pct:.1f}%)"
                body = (
                    f"Total portfolio capital allocated to {dom_country} reached {dom_pct:.1f}%, exceeding the 75.0% dual-market diversification bound. "
                    f"Cross-border rebalancing indicated."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol="PORTFOLIO",
                        predicate_type="PORTFOLIO_CONCENTRATION_BREACH",
                        severity="WARNING",
                        trigger_level=75.0,
                        current_price=dom_pct,
                        headline=headline,
                        body=body,
                        action_required="REBALANCE_CROSS_BORDER",
                        timestamp=now_str,
                    )
                )

        # 11. PREDICATE: CORRELATION_SPIKE_WARNING (Average cross-asset pairwise correlation > 0.70)
        if avg_pairwise_corr > 0.70:
            headline = f"Cross-Asset Systemic Correlation Spike ({avg_pairwise_corr:.2f})"
            body = (
                f"Average pairwise asset correlation jumped to {avg_pairwise_corr:.2f} (>0.70 threshold). "
                f"Diversification benefits impaired; macro systemic risk elevated."
            )
            alerts.append(
                InvalidationAlert(
                    symbol="PORTFOLIO",
                    predicate_type="CORRELATION_SPIKE_WARNING",
                    severity="WARNING",
                    trigger_level=0.70,
                    current_price=avg_pairwise_corr,
                    headline=headline,
                    body=body,
                    action_required="DE_RISK_SYSTEMIC_CORRELATION",
                    timestamp=now_str,
                )
            )

        # 12. PREDICATE: CVAR_TAIL_RISK_BREACH (1-day 99% Expected Shortfall > 3.50%)
        cvar_val = stress_metrics.get("cvar_99_1d_pct", 0.0) if isinstance(stress_metrics, dict) else getattr(stress_metrics, "cvar_99_1d_pct", 0.0)
        if cvar_val > 3.50:
            headline = f"1-Day 99% Expected Shortfall Breach ({cvar_val:.2f}%)"
            body = (
                f"Projected 1-day 99% Conditional Value-at-Risk of {cvar_val:.2f}% exceeds the 3.50% risk budget. "
                f"Systematic trimming of high-beta growth weights indicated."
            )
            alerts.append(
                InvalidationAlert(
                    symbol="PORTFOLIO",
                    predicate_type="CVAR_TAIL_RISK_BREACH",
                    severity="CRITICAL",
                    trigger_level=3.50,
                    current_price=cvar_val,
                    headline=headline,
                    body=body,
                    action_required="TRIM_HIGH_BETA_RISK_BUDGET",
                    timestamp=now_str,
                )
            )

        # 13. PREDICATE: FACTOR_CROWDING_BREACH (Any non-market factor > 45% of systematic risk)
        if factor_risk_metrics:
            f_summaries = factor_risk_metrics.get("factor_risk_summaries", {})
            for f_key, f_sum in f_summaries.items():
                if f_key == "market_beta":
                    continue
                pct_sys = f_sum.get("percent_of_systematic_risk", 0.0) if isinstance(f_sum, dict) else getattr(f_sum, "percent_of_systematic_risk", 0.0)
                f_name = f_sum.get("factor_name", f_key) if isinstance(f_sum, dict) else getattr(f_sum, "factor_name", f_key)
                if abs(pct_sys) > 45.0:
                    headline = f"Factor Risk Crowding Detected: {f_name} ({pct_sys:.1f}% of Systematic Risk)"
                    body = (
                        f"Systematic risk concentration in {f_name} reached {pct_sys:.1f}%, exceeding the 45.0% factor crowding limit. "
                        f"Diversification across style factors impaired."
                    )
                    alerts.append(
                        InvalidationAlert(
                            symbol="PORTFOLIO",
                            predicate_type="FACTOR_CROWDING_BREACH",
                            severity="WARNING",
                            trigger_level=45.0,
                            current_price=round(abs(pct_sys), 2),
                            headline=headline,
                            body=body,
                            action_required="REBALANCE_FACTOR_TILT",
                            timestamp=now_str,
                        )
                    )

        # 14. PREDICATE: FX_COMMODITY_OVEREXPOSURE (Crude oil tilt > 0.40 or FX tilt > 0.35)
        if factor_attribution_metrics:
            benchmarks = factor_attribution_metrics.get("benchmarks", {})
            primary_b = benchmarks.get("BLENDED_CROSS_BORDER", {})
            tilts = primary_b.get("factor_tilts", {}) if isinstance(primary_b, dict) else getattr(primary_b, "factor_tilts", {})

            oil_tilt_obj = tilts.get("crude_oil_beta", {})
            oil_tilt = oil_tilt_obj.get("active_tilt", 0.0) if isinstance(oil_tilt_obj, dict) else getattr(oil_tilt_obj, "active_tilt", 0.0)

            fx_tilt_obj = tilts.get("cad_usd_fx_beta", {})
            fx_tilt = fx_tilt_obj.get("active_tilt", 0.0) if isinstance(fx_tilt_obj, dict) else getattr(fx_tilt_obj, "active_tilt", 0.0)

            if abs(oil_tilt) > 0.40 or abs(fx_tilt) > 0.35:
                headline = f"Cross-Border Commodity or FX Overexposure: Crude Oil ({oil_tilt:+.2f}) / CAD/USD ({fx_tilt:+.2f})"
                body = (
                    f"Cross-border commodity or foreign exchange active tilt exceeded safe threshold (Crude Oil: {oil_tilt:+.2f}, CAD/USD: {fx_tilt:+.2f}). "
                    f"Macro volatility transmission elevated."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol="PORTFOLIO",
                        predicate_type="FX_COMMODITY_OVEREXPOSURE",
                        severity="WARNING",
                        trigger_level=0.40,
                        current_price=round(max(abs(oil_tilt), abs(fx_tilt)), 2),
                        headline=headline,
                        body=body,
                        action_required="REDUCE_COMMODITY_EXPOSURE",
                        timestamp=now_str,
                    )
                )

        # 15. PREDICATE: ALPHA_EROSION_WARNING (Specific stock selection alpha < -1.50%)
        if factor_attribution_metrics:
            benchmarks = factor_attribution_metrics.get("benchmarks", {})
            primary_b = benchmarks.get("BLENDED_CROSS_BORDER", {})
            spec_alpha = primary_b.get("specific_alpha_pct", 0.0) if isinstance(primary_b, dict) else getattr(primary_b, "specific_alpha_pct", 0.0)

            if spec_alpha < -1.50:
                headline = f"Specific Stock Selection Alpha Erosion ({spec_alpha:.2f}%)"
                body = (
                    f"Idiosyncratic stock-selection alpha degraded to {spec_alpha:.2f}%, indicating negative security-level attribution. "
                    f"Fundamental factor scoring and memory weights under review."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol="PORTFOLIO",
                        predicate_type="ALPHA_EROSION_WARNING",
                        severity="WARNING",
                        trigger_level=-1.50,
                        current_price=round(spec_alpha, 2),
                        headline=headline,
                        body=body,
                        action_required="AUDIT_SECURITY_SELECTION",
                        timestamp=now_str,
                    )
                )

        # 16. PREDICATE: EXCESSIVE_TURNOVER_BREACH (One-way rebalancing turnover > 25.0%)
        if bayesian_metrics:
            turnover_pct = bayesian_metrics.get("total_one_way_turnover_pct", 0.0) if isinstance(bayesian_metrics, dict) else getattr(bayesian_metrics, "total_one_way_turnover_pct", 0.0)
            if turnover_pct > 25.0:
                headline = f"Excessive Portfolio Rebalancing Turnover ({turnover_pct:.1f}%)"
                body = (
                    f"One-way portfolio turnover of {turnover_pct:.1f}% exceeds the 25.0% institutional friction threshold. "
                    f"Transaction cost drag elevated."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol="PORTFOLIO",
                        predicate_type="EXCESSIVE_TURNOVER_BREACH",
                        severity="WARNING",
                        trigger_level=25.0,
                        current_price=round(turnover_pct, 2),
                        headline=headline,
                        body=body,
                        action_required="THROTTLE_PORTFOLIO_TURNOVER",
                        timestamp=now_str,
                    )
                )

        # 17. PREDICATE: ESTIMATION_ERROR_SPIKE (Posterior variance trace ratio > 1.40x)
        if bayesian_metrics:
            trace_ratio = bayesian_metrics.get("estimation_error_trace_ratio", 1.0) if isinstance(bayesian_metrics, dict) else getattr(bayesian_metrics, "estimation_error_trace_ratio", 1.0)
            if trace_ratio > 1.40:
                headline = f"Bayesian Estimation Error Uncertainty Spike ({trace_ratio:.2f}x Trace Inflation)"
                body = (
                    f"Posterior covariance trace ratio inflated to {trace_ratio:.2f}x prior trace, exceeding the 1.40x stability limit. "
                    f"View uncertainty amplification detected."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol="PORTFOLIO",
                        predicate_type="ESTIMATION_ERROR_SPIKE",
                        severity="WARNING",
                        trigger_level=1.40,
                        current_price=round(trace_ratio, 3),
                        headline=headline,
                        body=body,
                        action_required="INCREASE_PRIOR_SHRINKAGE",
                        timestamp=now_str,
                    )
                )

        # 18. PREDICATE: CROSS_BORDER_PARITY_DISLOCATION (Dual-listed basis spread > 45.0 bps)
        if cross_border_fx_metrics:
            arb_opps = cross_border_fx_metrics.get("dual_listed_arbitrage", [])
            for arb in arb_opps:
                spread = arb.get("basis_spread_bps", 0.0) if isinstance(arb, dict) else getattr(arb, "basis_spread_bps", 0.0)
                sym = arb.get("symbol", "UNKNOWN") if isinstance(arb, dict) else getattr(arb, "symbol", "UNKNOWN")
                if abs(spread) > 45.0:
                    headline = f"Cross-Border Dual-Listed Parity Dislocation: {sym} ({spread:+.1f} bps)"
                    body = (
                        f"Dual-listed basis spread between TSX and NYSE for {sym} reached {spread:+.1f} bps, "
                        f"exceeding the 45.0 bps threshold. Intermarket liquidity dislocation observed."
                    )
                    alerts.append(
                        InvalidationAlert(
                            symbol=sym,
                            predicate_type="CROSS_BORDER_PARITY_DISLOCATION",
                            severity="WARNING",
                            trigger_level=45.0,
                            current_price=round(abs(spread), 1),
                            headline=headline,
                            body=body,
                            action_required="INVESTIGATE_VENUE_ARBITRAGE",
                            timestamp=now_str,
                        )
                    )

        # 19. PREDICATE: UNHEDGED_CURRENCY_DRAG (Unhedged vol exceeds hedged vol by > 2.0%)
        if cross_border_fx_metrics:
            cad_h = cross_border_fx_metrics.get("cad_base_hedging", {})
            unhedged_vol = cad_h.get("unhedged_portfolio_volatility_pct", 0.0) if isinstance(cad_h, dict) else getattr(cad_h, "unhedged_portfolio_volatility_pct", 0.0)
            hedged_vol = cad_h.get("fully_hedged_portfolio_volatility_pct", 0.0) if isinstance(cad_h, dict) else getattr(cad_h, "fully_hedged_portfolio_volatility_pct", 0.0)
            drag = unhedged_vol - hedged_vol
            if drag > 2.0:
                headline = f"Unhedged Currency Volatility Drag (+{drag:.2f}% Risk Elevation)"
                body = (
                    f"Unhedged foreign currency exchange rate fluctuations elevated portfolio annualized volatility "
                    f"by +{drag:.2f}% over hedged baseline. Currency risk hedging indicated."
                )
                alerts.append(
                    InvalidationAlert(
                        symbol="PORTFOLIO",
                        predicate_type="UNHEDGED_CURRENCY_DRAG",
                        severity="WARNING",
                        trigger_level=2.0,
                        current_price=round(drag, 2),
                        headline=headline,
                        body=body,
                        action_required="IMPLEMENT_CURRENCY_HEDGE",
                        timestamp=now_str,
                    )
                )

        # 20. PREDICATE: GAMMA_FLIP_REGIME_TRANSITION (Spot breaches below Gamma Flip with negative Net GEX)
        if options_intelligence_metrics:
            dossiers = options_intelligence_metrics.get("dossiers", {})
            for sym, d in dossiers.items():
                regime = d.get("gamma_regime") if isinstance(d, dict) else getattr(d, "gamma_regime", "LONG_GAMMA")
                net_gex = d.get("net_gex_millions", 0.0) if isinstance(d, dict) else getattr(d, "net_gex_millions", 0.0)
                flip = d.get("gamma_flip_strike", 0.0) if isinstance(d, dict) else getattr(d, "gamma_flip_strike", 0.0)
                mkt = d.get("exchange_market", "US_CBOE") if isinstance(d, dict) else getattr(d, "exchange_market", "US_CBOE")
                gex_threshold = -5.0 if mkt == "CA_MX" else -50.0

                if regime == "SHORT_GAMMA" and net_gex < gex_threshold:
                    headline = f"Gamma Flip Negative Dealer Regime: {sym} (${net_gex:.1f}M GEX)"
                    body = (
                        f"Spot price for {sym} breached below the dealer Gamma Flip level (${flip:.2f}). "
                        f"Market makers are net short gamma, transitioning from volatility-dampening to volatility-amplifying posture."
                    )
                    alerts.append(
                        InvalidationAlert(
                            symbol=sym,
                            predicate_type="GAMMA_FLIP_REGIME_TRANSITION",
                            severity="WARNING",
                            trigger_level=round(gex_threshold, 1),
                            current_price=round(net_gex, 1),
                            headline=headline,
                            body=body,
                            action_required="WIDEN_STOP_THRESHOLDS",
                            timestamp=now_str,
                        )
                    )

        # 21. PREDICATE: VOLATILITY_SKEW_TAIL_INVERSION (25-Delta Put Skew Z-Score > +2.50σ)
        if options_intelligence_metrics:
            dossiers = options_intelligence_metrics.get("dossiers", {})
            for sym, d in dossiers.items():
                zscore = d.get("skew_zscore", 0.0) if isinstance(d, dict) else getattr(d, "skew_zscore", 0.0)
                skew_val = d.get("thirty_day_skew_pct", 0.0) if isinstance(d, dict) else getattr(d, "thirty_day_skew_pct", 0.0)
                pctl = d.get("skew_percentile_1y", 50.0) if isinstance(d, dict) else getattr(d, "skew_percentile_1y", 50.0)

                if zscore > 2.50:
                    headline = f"Volatility Skew Tail Inversion: {sym} (+{zscore:.2f}σ Z-Score)"
                    body = (
                        f"25-delta put implied volatility for {sym} expanded to {skew_val:.2f}%, "
                        f"reaching the {pctl:.1f}th historical percentile (+{zscore:.2f}σ). Elevated crash protection pricing detected."
                    )
                    alerts.append(
                        InvalidationAlert(
                            symbol=sym,
                            predicate_type="VOLATILITY_SKEW_TAIL_INVERSION",
                            severity="WARNING",
                            trigger_level=2.50,
                            current_price=round(zscore, 2),
                            headline=headline,
                            body=body,
                            action_required="SCRUTINIZE_TAIL_RISK_PROTECTION",
                            timestamp=now_str,
                        )
                    )

        # 22. PREDICATE: SOVEREIGN_YIELD_CURVE_INVERSION (2Y10Y < -50 bps or 3M10Y < -75 bps)
        if sovereign_yield_metrics:
            for key, label in (("us_profile", "US"), ("ca_profile", "CA")):
                prof = sovereign_yield_metrics.get(key)
                if not prof:
                    continue
                slope_2y10y = prof.get("slope_2y10y_bps", 0.0) if isinstance(prof, dict) else getattr(prof, "slope_2y10y_bps", 0.0)
                slope_3m10y = prof.get("slope_3m10y_bps", 0.0) if isinstance(prof, dict) else getattr(prof, "slope_3m10y_bps", 0.0)
                if slope_2y10y < -50.0 or slope_3m10y < -75.0:
                    headline = f"Sovereign Yield Curve Inversion Warning: {label} ({slope_2y10y:.1f} bps)"
                    body = (
                        f"{label} sovereign yield curve inverted with 2Y10Y slope reaching {slope_2y10y:.1f} bps "
                        f"and 3M10Y slope at {slope_3m10y:.1f} bps, breaching the -50.0/-75.0 bps hazard boundaries. "
                        "Late-cycle economic tightening and elevated recession risk indicated."
                    )
                    alerts.append(
                        InvalidationAlert(
                            symbol=f"{label}_SOVEREIGN",
                            predicate_type="SOVEREIGN_YIELD_CURVE_INVERSION",
                            severity="WARNING",
                            trigger_level=-50.0,
                            current_price=round(slope_2y10y, 1),
                            headline=headline,
                            body=body,
                            action_required="DEFENSIVE_DURATION_BIAS",
                            timestamp=now_str,
                        )
                    )

            # 23. PREDICATE: TERM_PREMIUM_SHOCK (10Y ACM term premium +35 bps over 10 sessions)
            for key, label in (("us_profile", "US"), ("ca_profile", "CA")):
                prof = sovereign_yield_metrics.get(key)
                if not prof:
                    continue
                tps = prof.get("term_premium_decompositions", []) if isinstance(prof, dict) else getattr(prof, "term_premium_decompositions", [])
                for tp in tps:
                    tenor = tp.get("tenor") if isinstance(tp, dict) else getattr(tp, "tenor", "")
                    if tenor != "10Y":
                        continue
                    chg = tp.get("ten_day_change_bps", 0.0) if isinstance(tp, dict) else getattr(tp, "ten_day_change_bps", 0.0)
                    if chg > 35.0:
                        headline = f"Term Premium Expansion Shock: {label} 10Y (+{chg:.1f} bps)"
                        body = (
                            f"10-year {label} sovereign term premium expanded by +{chg:.1f} bps over 10 sessions, "
                            "exceeding the +35.0 bps shock threshold. Upward discount rate pressure and equity "
                            "valuation multiple compression indicated."
                        )
                        alerts.append(
                            InvalidationAlert(
                                symbol=f"{label}_SOVEREIGN",
                                predicate_type="TERM_PREMIUM_SHOCK",
                                severity="WARNING",
                                trigger_level=35.0,
                                current_price=round(chg, 1),
                                headline=headline,
                                body=body,
                                action_required="COMPRESS_EQUITY_VALUATION_MULTIPLES",
                                timestamp=now_str,
                            )
                        )

        # 24. PREDICATE: DARK_POOL_STEALTH_DISTRIBUTION
        #     Off-exchange share > 60% AND z(OVR) > 1.5 AND signed block impression is
        #     DISTRIBUTION AND price rangebound near resistance.
        #     NOTE (Decision 2b): ATS participation share is deliberately absent from
        #     dark_pool_metrics, so it cannot contribute to this predicate.
        if dark_pool_metrics:
            per_symbol = dark_pool_metrics.get("per_symbol", {})
            for sym, m in per_symbol.items():
                ovr = m.get("ovr_pct", 0.0)
                ovr_z = m.get("ovr_zscore_60d", 0.0)
                impression = m.get("signed_impression", "NEUTRAL")
                near_res = m.get("rangebound_near_resistance", False)

                if ovr > 60.0 and ovr_z > 1.5 and impression == "DISTRIBUTION" and near_res:
                    headline = f"Dark Pool Stealth Distribution Warning: {sym} ({ovr:.1f}% Off-Exchange)"
                    body = (
                        f"{sym} off-exchange volume share reached {ovr:.1f}% of consolidated volume "
                        f"(z-score +{ovr_z:.2f} vs 60-day baseline) while institutional block prints carried a "
                        "DISTRIBUTION impression and price held rangebound near resistance. Off-exchange "
                        "distribution ahead of a lit breakout attempt indicated."
                    )
                    alerts.append(
                        InvalidationAlert(
                            symbol=sym,
                            predicate_type="DARK_POOL_STEALTH_DISTRIBUTION",
                            severity="WARNING",
                            trigger_level=60.0,
                            current_price=round(ovr, 2),
                            headline=headline,
                            body=body,
                            action_required="SCRUTINIZE_BUY_SIGNALS",
                            timestamp=now_str,
                        )
                    )

                # 25. PREDICATE: SHORT_VOLUME_SQUEEZE_SPIKE
                #     z(SVR) > 2.0 AND days-to-cover > 5.0 AND elevated/cascade squeeze regime.
                #     RESEARCH-ONLY (Decision 1b): short metrics are only present when the
                #     caller holds a research-only entitlement, so this predicate cannot fire
                #     on a commercial payload because the fields simply are not there.
                svr_z = m.get("svr_zscore_60d")
                dtc = m.get("days_to_cover")
                if svr_z is None or dtc is None:
                    continue

                squeeze_regime = m.get("squeeze_regime", "DORMANT")
                if svr_z > 2.0 and dtc > 5.0 and squeeze_regime in ("ELEVATED", "CASCADE_RISK"):
                    headline = f"Short Volume Squeeze Spike Warning: {sym} (z +{svr_z:.2f}, DTC {dtc:.1f}d)"
                    body = (
                        f"{sym} short volume ratio reached z-score +{svr_z:.2f} against its own 60-day "
                        f"distribution with {dtc:.1f} days-to-cover and a {squeeze_regime.replace('_', ' ').title()} "
                        "squeeze composite. Short-covering cascade conditions indicated. Short volume is measured "
                        "against off-exchange volume and is not consolidated with exchange prints."
                    )
                    alerts.append(
                        InvalidationAlert(
                            symbol=sym,
                            predicate_type="SHORT_VOLUME_SQUEEZE_SPIKE",
                            severity="WARNING",
                            trigger_level=2.0,
                            current_price=round(svr_z, 2),
                            headline=headline,
                            body=body,
                            action_required="FLAG_SHORT_COVERING_CASCADE",
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

            if "STOP" in inv.predicate_type or "BREAKOUT_FAILURE" in inv.predicate_type or "SWEEP" in inv.predicate_type:
                taxonomy = "EXIT_TRIGGER_ESCALATION"
            elif "GAMMA" in inv.predicate_type:
                taxonomy = "REGIME_SHIFT"
            elif "SKEW" in inv.predicate_type:
                taxonomy = "PORTFOLIO_CIRCUIT_BREAKER"
            elif "REGIME" in inv.predicate_type:
                taxonomy = "REGIME_SHIFT"
            elif "CATALYST" in inv.predicate_type:
                taxonomy = "CATALYST_MATERIALITY"
            elif "LIQUIDITY" in inv.predicate_type:
                taxonomy = "TECHNICAL_BREAKOUT"
            elif "CONCENTRATION" in inv.predicate_type or "CORRELATION" in inv.predicate_type or "CVAR" in inv.predicate_type or "FACTOR" in inv.predicate_type or "COMMODITY" in inv.predicate_type or "ALPHA" in inv.predicate_type or "TURNOVER" in inv.predicate_type or "ESTIMATION" in inv.predicate_type or "PARITY" in inv.predicate_type or "CURRENCY" in inv.predicate_type:
                taxonomy = "PORTFOLIO_CIRCUIT_BREAKER"
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
