"""
QUANT INTEL Portfolio Orchestrator & Multi-Asset Risk Orchestration Engine (Phase 26.3)
US + Canada Dual-Market Intelligence Platform

Bridges Security-Level Conviction Grades with Portfolio-Level Hierarchical Risk Parity:
1. Conviction-Weighted HRP Blending:
   - Grade A: 1.25x alpha tilt
   - Grade B: 1.00x benchmark risk parity
   - Grade C: 0.50x conservative risk de-weighting
   - Grade SKIP: 0.00x excluded from active risk capital (cash preservation)
2. Dynamic Macro Hazard Governor Modulation (Phase 24 Bayesian Markov Transition Matrix).
3. Microstructure & Execution TCA Sizing (Phase 25.3 child order parameters).
4. Portfolio-Level Sentinel Invariant Governance:
   - PORTFOLIO_CONCENTRATION_BREACH (>18.0% single asset or >75.0% single country)
   - CORRELATION_SPIKE_WARNING (average cross-asset correlation > 0.70)
   - CVAR_TAIL_RISK_BREACH (1-day 99% Expected Shortfall > 3.50%)
5. Dual-Market Capital Deployment (US USD & Canadian CAD).
6. Impersonal Decision-Support Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Literal
import numpy as np
import pandas as pd

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.portfolio_hrp import hrp_portfolio_engine
from src.engine.portfolio_stress import portfolio_stress_engine
from src.engine.factor_risk import factor_risk_engine
from src.engine.factor_attribution import factor_attribution_engine
from src.engine.portfolio_bayesian import portfolio_bayesian_engine
from src.engine.cross_border_fx import cross_border_fx_engine
from src.engine.options_surface_gex import cross_border_options_engine
from src.engine.sovereign_yield_curve import sovereign_yield_curve_engine
from src.engine.dark_pool_liquidity import dark_pool_liquidity_engine
from src.engine.volatility_forecast_vrp import volatility_forecast_vrp_engine
from src.engine.quant_intel import quant_intel_engine
from src.engine.quant_intel_sentinel import quant_intel_sentinel
from src.execution.execution_telemetry import compute_execution_telemetry


def _safe_execution_telemetry():
    """
    Session telemetry for Predicate 28, or None if it cannot be computed.

    Returns None rather than raising: execution telemetry is advisory, and a missing or
    partially migrated ledger must not prevent portfolio orchestration from running.
    """
    try:
        return compute_execution_telemetry()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Warning: execution telemetry unavailable, skipping Predicate 28: {exc}")
        return None
from src.models.schemas import (
    OrchestratedPosition,
    PortfolioOrchestrationResult,
)

ORCHESTRATOR_DISCLAIMERS = [
    "Portfolio orchestration, conviction-weighted risk allocations, and cash reserve sizing are quantitative models provided for impersonal decision-support research.",
    "Does not constitute personalized financial planning, fiduciary asset management, or discretionary trade execution under CSA Staff Notice 31-369 and SEC rules.",
    "Target fills and execution parameters are derived from order book depth simulations; actual market fills and slippage in live trading will vary."
]


class PortfolioOrchestratorEngine:
    """
    Synthesizes security-level QUANT INTEL dossiers, Markov macro hazard governors,
    and HRP risk parity weights into an optimal, risk-governed portfolio plan.
    """

    def __init__(self):
        self._cached_result: Optional[PortfolioOrchestrationResult] = None

    def evaluate_orchestration(
        self,
        total_capital: float = 100000.0,
        min_cash_reserve_pct: float = 5.0,
        force_refresh: bool = False
    ) -> PortfolioOrchestrationResult:
        """
        Executes end-to-end portfolio orchestration across the active dual-market universe.
        """
        if self._cached_result and not force_refresh:
            return self._cached_result

        # 1. Obtain baseline HRP risk-parity weights
        hrp_res = hrp_portfolio_engine.generate_hrp_allocation(total_capital=total_capital, force_refresh=force_refresh)
        symbols = list(hrp_res.allocations.keys())

        # 2. Evaluate/load QUANT INTEL dossiers for all active securities
        qi_dossiers = {}
        for sym in symbols:
            try:
                qi_dossiers[sym] = quant_intel_engine.evaluate_ticker(sym, portfolio_size=total_capital)
            except Exception:
                pass

        # 3. Calculate conviction & macro hazard scalars
        raw_weighted = {}
        scalar_map = {}
        for sym in symbols:
            hrp_w = hrp_res.allocations[sym].weight
            dossier = qi_dossiers.get(sym)

            if not dossier:
                grade = "B"
                conviction_scalar = 1.0
                hazard_scalar = 1.0
            else:
                grade = dossier.conviction_grade
                if grade == "A":
                    conviction_scalar = 1.25
                elif grade == "B":
                    conviction_scalar = 1.00
                elif grade == "C":
                    conviction_scalar = 0.50
                else:  # SKIP
                    conviction_scalar = 0.00

                # Macro hazard scalar from Layer 1 regime
                l1 = dossier.layer_1_regime
                h_tier = getattr(l1, "hazard_tier", "LOW_HAZARD")
                h_20d = getattr(l1, "adverse_hazard_rate_20d", 0.0)
                c_posture = getattr(l1, "cross_border_macro_divergence_posture", "SYNCHRONIZED_EXPANSION")

                if h_tier == "SEVERE_HAZARD" or h_20d >= 0.40:
                    hazard_scalar = 0.65
                elif h_tier == "ELEVATED_HAZARD" or h_20d >= 0.20:
                    hazard_scalar = 0.80
                elif c_posture == "CROSS_BORDER_STRESS":
                    hazard_scalar = 0.85
                else:
                    hazard_scalar = 1.00

            blended_scalar = conviction_scalar * hazard_scalar
            scalar_map[sym] = (grade, blended_scalar)
            raw_weighted[sym] = hrp_w * blended_scalar

        # 4. Normalize deployed weights and determine Cash Reserve
        sum_raw = sum(raw_weighted.values())
        max_deploy_pct = (100.0 - min_cash_reserve_pct) / 100.0

        if sum_raw > 0:
            target_deploy = min(sum_raw, max_deploy_pct)
            norm_factor = target_deploy / sum_raw
            final_weights = {s: raw_weighted[s] * norm_factor for s in symbols}
        else:
            final_weights = {s: 0.0 for s in symbols}
            target_deploy = 0.0

        cash_reserve_pct = round((1.0 - sum(final_weights.values())) * 100.0, 2)
        cash_reserve_dollars = round((cash_reserve_pct / 100.0) * total_capital, 2)
        deployed_dollars = round(total_capital - cash_reserve_dollars, 2)

        # 5. Build position objects with execution TCA telemetry
        positions: Dict[str, OrchestratedPosition] = {}
        us_weight = 0.0
        ca_weight = 0.0

        for sym in symbols:
            fw = final_weights[sym]
            if fw <= 0.0001:
                continue

            country = hrp_res.allocations[sym].country
            if country == "US":
                us_weight += fw
            else:
                ca_weight += fw

            dollar_alloc = round(fw * total_capital, 2)
            dossier = qi_dossiers.get(sym)

            if dossier and dossier.trade_plan:
                plan = dossier.trade_plan
                arr_price = plan.entry_zone_ideal
                eff_price = plan.tca_effective_fill_price or arr_price
                slip_bps = plan.tca_expected_slippage_bps or 0.0
                exec_strat = plan.tca_execution_strategy or "VWAP"
            else:
                arr_price = 100.0
                eff_price = 100.0
                slip_bps = 0.0
                exec_strat = "VWAP"

            target_shares = math.floor(dollar_alloc / eff_price) if eff_price > 0 else 0
            grade, b_scalar = scalar_map[sym]

            positions[sym] = OrchestratedPosition(
                symbol=sym,
                country=country,  # type: ignore
                conviction_grade=grade,
                hrp_base_weight=round(hrp_res.allocations[sym].weight, 4),
                conviction_scalar=round(b_scalar, 2),
                final_weight=round(fw, 4),
                dollar_allocation=dollar_alloc,
                target_shares=target_shares,
                arrival_price=round(arr_price, 2),
                effective_execution_price=round(eff_price, 2),
                expected_slippage_bps=round(slip_bps, 2),
                execution_strategy=exec_strat,
            )

        # 6. Evaluate Portfolio-Level Sentinels
        stress_res = portfolio_stress_engine.run_stress_test(total_capital=total_capital)
        returns_df, _ = hrp_portfolio_engine.load_universe_returns()
        corr_matrix = np.array(returns_df.corr().values, dtype=float, copy=True)
        if not corr_matrix.flags.writeable:
            corr_matrix = corr_matrix.copy()
        np.fill_diagonal(corr_matrix, np.nan)
        avg_pairwise_corr = float(np.nanmean(corr_matrix))

        factor_risk_res = factor_risk_engine.evaluate_factor_risk(total_capital=total_capital)
        factor_attrib_res = factor_attribution_engine.evaluate_attribution(total_capital=total_capital)
        bayesian_res = portfolio_bayesian_engine.evaluate_bayesian_portfolio(total_capital=total_capital)
        cross_border_res = cross_border_fx_engine.evaluate_cross_border_fx()
        options_res = cross_border_options_engine.evaluate_options_intelligence()
        sovereign_res = sovereign_yield_curve_engine.evaluate_sovereign_curves()
        # DECISION 1(b): commercial orchestration uses the entitlement-gated build,
        # which omits research-only short metrics entirely.
        dark_pool_res = dark_pool_liquidity_engine.evaluate_dark_pool_liquidity()
        volatility_res = volatility_forecast_vrp_engine.evaluate_volatility_forecasts()

        portfolio_alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=positions,  # type: ignore
            stress_metrics=stress_res.model_dump(),
            avg_pairwise_corr=avg_pairwise_corr,
            factor_risk_metrics=factor_risk_res.model_dump(),
            factor_attribution_metrics=factor_attrib_res.model_dump(),
            bayesian_metrics=bayesian_res.model_dump(),
            cross_border_fx_metrics=cross_border_res.model_dump(),
            options_intelligence_metrics=options_res.model_dump(),
            sovereign_yield_metrics=sovereign_res.model_dump(),
            dark_pool_metrics=dark_pool_liquidity_engine.compute_sentinel_metrics(dark_pool_res),
            volatility_vrp_metrics=volatility_forecast_vrp_engine.compute_sentinel_metrics(volatility_res),
            # Phase 34.5: governor rejection telemetry drives Predicate 28. Wrapped so a
            # platform that has never run the governor cannot take down orchestration.
            execution_telemetry=_safe_execution_telemetry(),
        )

        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        now_iso = now.isoformat()

        # Compliance assertion on disclaimers
        for disc in ORCHESTRATOR_DISCLAIMERS:
            linter.assert_clean(disc)

        result = PortfolioOrchestrationResult(
            as_of_date=today_str,
            generated_at=now_iso,
            total_capital=total_capital,
            active_positions_count=len(positions),
            deployed_capital=deployed_dollars,
            deployed_pct=round(100.0 - cash_reserve_pct, 2),
            cash_reserve=cash_reserve_dollars,
            cash_reserve_pct=cash_reserve_pct,
            us_weight_pct=round(us_weight * 100.0, 2),
            ca_weight_pct=round(ca_weight * 100.0, 2),
            positions=positions,
            portfolio_alerts=[a.to_dict() for a in portfolio_alerts],
            disclaimers=ORCHESTRATOR_DISCLAIMERS,
        )

        self._cached_result = result
        return result

    def export_feed(self, output_dir: Optional[Path] = None, force_refresh: bool = False) -> Path:
        """
        Exports the master portfolio orchestration feed to JSON.
        """
        if output_dir is None:
            output_dir = DATA_DIR / "feeds"
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "portfolio_orchestration.json"

        feed = self.evaluate_orchestration(force_refresh=force_refresh)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(feed.to_dict(), f, indent=2)

        return out_file


# Global singleton instance
portfolio_orchestrator_engine = PortfolioOrchestratorEngine()
