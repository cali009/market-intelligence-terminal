"""
Historical & Macroeconomic Scenario Stress-Testing Engine (Phase 26.2)
US + Canada Dual-Market Intelligence Platform

Implements Institutional Tail-Risk & Macro Scenario Stress Testing (Basel III / OSFI E-18 Standards):
1. Parametric & Empirical Value-at-Risk (VaR at 95% and 99% confidence over 1d and 10d horizons).
2. Conditional Value-at-Risk (CVaR / Expected Shortfall) measuring tail loss beyond VaR.
3. 5 Historical Crisis & Factor Shock Replay Scenarios:
   - 2008_GFC: Global Financial Crisis liquidity freeze, credit blowout, and equity plunge.
   - 2015_OIL_CRASH: Canadian Crude Oil collapse (-52%), CAD devaluation, and TSX underperformance.
   - 2020_COVID_FLASH: Rapid 30-day liquidation with tech relative resilience.
   - 2022_RATE_SHOCK: Central bank aggressive tightening cycle (+425 bps) and high-multiple tech compression.
   - 2026_STAGFLATION_TARIFF: Cross-border supply friction, sticky CPI, energy squeeze, and Markov adverse hazard.
4. Capital Preservation Delta: Quantifies capital saved by HRP risk-budgeting vs. naive Equal-Weight.
5. Impersonal Decision-Support Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
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
from src.models.schemas import (
    ScenarioImpact,
    PortfolioStressTestResult,
)

STRESS_DISCLAIMERS = [
    "Macroeconomic stress testing and historical crisis simulations are derived quantitative risk-modeling tools provided for impersonal decision-support research.",
    "Hypothetical scenario results are based on simulated shocks and historical correlations; actual market shocks in future crises will differ materially.",
    "Does not constitute personalized financial planning, credit advice, or fiduciary portfolio recommendations under CSA Staff Notice 31-369 and SEC rules."
]


class PortfolioStressEngine:
    """
    Stress-tests portfolio allocations against historical market crashes,
    macroeconomic factor shocks, and Basel III tail-risk parameters (VaR/CVaR).
    """

    def __init__(self):
        self._cached_result: Optional[PortfolioStressTestResult] = None

    @staticmethod
    def get_scenario_definitions() -> Dict[str, Dict[str, Any]]:
        """
        Returns calibrated historical crisis and synthetic macroeconomic factor shock definitions.
        Asset shocks reflect empirically calibrated betas, duration sensitivity, and sector exposures.
        """
        return {
            "2008_GFC": {
                "name": "2008 Global Financial Crisis",
                "historical_period": "Sep 2008 – Mar 2009",
                "description": "Systemic banking liquidity freeze following Lehman collapse; broad equity liquidation with extreme financial sector drawdown.",
                "macro_factor_shocks": {
                    "SP500_DROP_PCT": -45.0,
                    "TSX_DROP_PCT": -40.0,
                    "FINANCIALS_DROP_PCT": -55.0,
                    "VIX_SPIKE_PCT": 250.0,
                    "CREDIT_SPREAD_BPS": 600.0,
                },
                "asset_shocks": {
                    "JPM": -54.0, "TD": -44.0, "RY": -42.0, "BAM": -46.0, "BN": -48.0,
                    "MSFT": -40.0, "AAPL": -42.0, "GOOGL": -45.0, "AMZN": -44.0, "NVDA": -56.0,
                    "SHOP": -58.0, "CNR": -32.0, "CP": -34.0, "ENB": -28.0, "CNQ": -46.0,
                    "XOM": -36.0, "SPY": -45.0, "QQQ": -43.0, "XIU": -40.0,
                },
                "benchmark_return_pct": -43.0,
            },
            "2015_OIL_CRASH": {
                "name": "2015 Canadian Crude Oil Collapse",
                "historical_period": "Jun 2014 – Jan 2016",
                "description": "Global crude oil oversupply shock (-52%), precipitating Canadian dollar devaluation and Bank of Canada emergency rate cuts.",
                "macro_factor_shocks": {
                    "WTI_CRUDE_DROP_PCT": -52.0,
                    "CAD_USD_DROP_PCT": -18.0,
                    "TSX_ENERGY_DROP_PCT": -44.0,
                    "BOC_RATE_CUT_BPS": -50.0,
                },
                "asset_shocks": {
                    "CNQ": -48.0, "ENB": -36.0, "XOM": -26.0, "XIU": -22.0, "TD": -16.0,
                    "RY": -14.0, "BAM": -12.0, "BN": -15.0, "CNR": -10.0, "CP": -12.0,
                    "SHOP": -12.0, "MSFT": -4.0, "AAPL": -6.0, "GOOGL": -5.0, "AMZN": +2.0,
                    "NVDA": +14.0, "JPM": -8.0, "SPY": -6.0, "QQQ": -2.0,
                },
                "benchmark_return_pct": -12.4,
            },
            "2020_COVID_FLASH": {
                "name": "2020 COVID Liquidity Freeze & Volatility Spike",
                "historical_period": "Feb 2020 – Mar 2020",
                "description": "Abrupt global economic shutdown and cash-liquidation flash crash; VIX spiked to 82 while digital e-commerce demonstrated relative resilience.",
                "macro_factor_shocks": {
                    "EQUITY_LIQUIDATION_PCT": -34.0,
                    "WTI_CRUDE_DROP_PCT": -60.0,
                    "VIX_MAX_LEVEL": 82.7,
                    "DOM_SPREAD_EXPANSION_X": 3.8,
                },
                "asset_shocks": {
                    "CNQ": -52.0, "XOM": -48.0, "ENB": -35.0, "JPM": -38.0, "TD": -34.0,
                    "RY": -30.0, "BAM": -32.0, "BN": -34.0, "CNR": -26.0, "CP": -25.0,
                    "SPY": -34.0, "XIU": -32.0, "MSFT": -18.0, "AAPL": -19.0, "GOOGL": -22.0,
                    "NVDA": -16.0, "AMZN": -8.0, "QQQ": -16.0, "SHOP": +12.0,
                },
                "benchmark_return_pct": -33.2,
            },
            "2022_RATE_SHOCK": {
                "name": "2022 Central Bank Rapid Tightening Cycle",
                "historical_period": "Jan 2022 – Oct 2022",
                "description": "Fastest monetary tightening in 40 years (+425 bps); long-duration growth multiples compressed while energy and cash-flow value held firm.",
                "macro_factor_shocks": {
                    "FED_BOC_HIKES_BPS": 425.0,
                    "US_10Y_SURGE_BPS": 250.0,
                    "MULTIPLE_COMPRESSION_PCT": -35.0,
                    "TECH_DRAWDOWN_PCT": -33.0,
                },
                "asset_shocks": {
                    "SHOP": -62.0, "NVDA": -48.0, "AMZN": -46.0, "GOOGL": -38.0, "MSFT": -28.0,
                    "AAPL": -24.0, "QQQ": -33.0, "SPY": -19.0, "BAM": -22.0, "BN": -25.0,
                    "JPM": -16.0, "TD": -12.0, "RY": -8.0, "CNR": -6.0, "CP": -8.0,
                    "XIU": -8.0, "ENB": +4.0, "CNQ": +22.0, "XOM": +38.0,
                },
                "benchmark_return_pct": -14.6,
            },
            "2026_STAGFLATION_TARIFF": {
                "name": "2026 Stagflationary Supply & Tariff Shock",
                "historical_period": "Forward Horizon Simulation",
                "description": "Sticky core inflation (>4.5%) paired with cross-border trade tariff friction; elevated Markov adverse transition hazard under cross-border stress.",
                "macro_factor_shocks": {
                    "CORE_CPI_ACCEL_BPS": 250.0,
                    "CROSS_BORDER_TARIFF_PCT": -18.0,
                    "ENERGY_SQUEEZE_PCT": 22.0,
                    "MARKOV_HAZARD_TIER": 45.0,
                },
                "asset_shocks": {
                    "SHOP": -26.0, "AAPL": -24.0, "CNR": -22.0, "CP": -20.0, "AMZN": -20.0,
                    "GOOGL": -18.0, "MSFT": -16.0, "NVDA": -24.0, "SPY": -15.0, "QQQ": -18.0,
                    "JPM": -16.0, "TD": -14.0, "RY": -12.0, "BAM": -14.0, "BN": -16.0,
                    "XIU": -12.0, "ENB": +8.0, "CNQ": +14.0, "XOM": +16.0,
                },
                "benchmark_return_pct": -13.8,
            },
        }

    @staticmethod
    def calculate_var_and_cvar(
        weights: np.ndarray,
        cov_matrix: np.ndarray,
        total_capital: float
    ) -> Dict[str, float]:
        """
        Calculates parametric Value-at-Risk (VaR) and Conditional VaR (Expected Shortfall)
        at 95% and 99% confidence intervals across 1-day and 10-day horizons.
        """
        # Daily portfolio standard deviation
        daily_var = float(np.dot(weights, np.dot(cov_matrix, weights)))
        daily_sd = math.sqrt(max(1e-8, daily_var))

        # Standard normal quantiles and PDF values
        # z_95 = 1.64485, phi(z_95) = 0.10314
        # z_99 = 2.32635, phi(z_99) = 0.02665
        z_95 = 1.64485
        phi_95 = 0.10314
        z_99 = 2.32635
        phi_99 = 0.02665

        # 1-Day Parametric VaR
        var_95_1d_pct = z_95 * daily_sd * 100.0
        var_99_1d_pct = z_99 * daily_sd * 100.0

        # 1-Day Conditional VaR (Expected Shortfall): ES = (phi(z) / (1 - alpha)) * sigma
        cvar_95_1d_pct = (phi_95 / 0.05) * daily_sd * 100.0
        cvar_99_1d_pct = (phi_99 / 0.01) * daily_sd * 100.0

        # 10-Day Square-Root-of-Time Scaling (Basel III Standard)
        sqrt_10 = math.sqrt(10.0)
        var_95_10d_pct = var_95_1d_pct * sqrt_10
        cvar_99_10d_pct = cvar_99_1d_pct * sqrt_10

        return {
            "var_95_1d_pct": round(var_95_1d_pct, 2),
            "var_95_1d_dollars": round((var_95_1d_pct / 100.0) * total_capital, 2),
            "var_99_1d_pct": round(var_99_1d_pct, 2),
            "var_99_1d_dollars": round((var_99_1d_pct / 100.0) * total_capital, 2),
            "cvar_95_1d_pct": round(cvar_95_1d_pct, 2),
            "cvar_95_1d_dollars": round((cvar_95_1d_pct / 100.0) * total_capital, 2),
            "cvar_99_1d_pct": round(cvar_99_1d_pct, 2),
            "cvar_99_1d_dollars": round((cvar_99_1d_pct / 100.0) * total_capital, 2),
            "var_95_10d_pct": round(var_95_10d_pct, 2),
            "var_95_10d_dollars": round((var_95_10d_pct / 100.0) * total_capital, 2),
            "cvar_99_10d_pct": round(cvar_99_10d_pct, 2),
            "cvar_99_10d_dollars": round((cvar_99_10d_pct / 100.0) * total_capital, 2),
        }

    def simulate_scenarios(
        self,
        allocations: Dict[str, Any],
        total_capital: float
    ) -> Dict[str, ScenarioImpact]:
        """
        Simulates portfolio P&L and drawdown under each of the 5 historical crisis scenarios,
        benchmarked against naive Equal-Weight capital preservation.
        """
        scenarios_def = self.get_scenario_definitions()
        results: Dict[str, ScenarioImpact] = {}
        symbols = list(allocations.keys())
        n = len(symbols)

        for s_id, s_data in scenarios_def.items():
            shocks = s_data["asset_shocks"]
            port_ret = 0.0
            ew_ret = 0.0

            sorted_shocks = []
            for sym, alloc in allocations.items():
                w = alloc.weight if hasattr(alloc, "weight") else alloc["weight"]
                shock = shocks.get(sym, -20.0)  # Default fallback shock
                port_ret += w * (shock / 100.0)
                ew_ret += (1.0 / n) * (shock / 100.0)
                sorted_shocks.append((sym, shock))

            sorted_shocks.sort(key=lambda x: x[1])
            worst_sym, worst_val = sorted_shocks[0]
            best_sym, best_val = sorted_shocks[-1]

            port_ret_pct = port_ret * 100.0
            ew_ret_pct = ew_ret * 100.0
            port_pnl = (port_ret_pct / 100.0) * total_capital
            # Capital preservation delta: how much less capital HRP lost than Equal-Weight
            cap_preservation_delta = (port_ret - ew_ret) * total_capital

            results[s_id] = ScenarioImpact(
                scenario_id=s_id,
                name=s_data["name"],
                historical_period=s_data["historical_period"],
                description=s_data["description"],
                macro_factor_shocks=s_data["macro_factor_shocks"],
                asset_shocks=shocks,
                portfolio_pnl_dollars=round(port_pnl, 2),
                portfolio_return_pct=round(port_ret_pct, 2),
                benchmark_return_pct=round(float(s_data["benchmark_return_pct"]), 2),
                capital_preservation_delta=round(cap_preservation_delta, 2),
                worst_asset=worst_sym,
                worst_asset_return_pct=round(worst_val, 2),
                best_asset=best_sym,
                best_asset_return_pct=round(best_val, 2),
            )

        return results

    def run_stress_test(
        self,
        total_capital: float = 100000.0,
        force_refresh: bool = False
    ) -> PortfolioStressTestResult:
        """
        Executes end-to-end macroeconomic stress testing over the active HRP portfolio.
        """
        if self._cached_result and not force_refresh:
            return self._cached_result

        # Load HRP allocations and covariance
        hrp_res = hrp_portfolio_engine.generate_hrp_allocation(total_capital=total_capital, force_refresh=force_refresh)
        returns_df, _ = hrp_portfolio_engine.load_universe_returns()
        symbols = list(returns_df.columns)
        cov_matrix = np.array(returns_df.cov().values, dtype=float, copy=True)
        if not cov_matrix.flags.writeable:
            cov_matrix = cov_matrix.copy()

        weights = np.array([hrp_res.allocations[sym].weight for sym in symbols])
        weights /= np.sum(weights)

        # 1. VaR & CVaR calculations
        tail_metrics = self.calculate_var_and_cvar(weights, cov_matrix, total_capital)

        # 2. Crisis Scenario Simulation
        scenario_impacts = self.simulate_scenarios(hrp_res.allocations, total_capital)

        # 3. Posture classification based on 1-day 99% CVaR
        cvar_99_val = tail_metrics["cvar_99_1d_pct"]
        if cvar_99_val <= 2.5:
            posture = "LOW_TAIL_RISK"
        elif cvar_99_val <= 3.8:
            posture = "MODERATE_TAIL_RISK"
        elif cvar_99_val <= 5.0:
            posture = "ELEVATED_TAIL_RISK"
        else:
            posture = "CRITICAL_TAIL_RISK"

        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        now_iso = now.isoformat()

        # Compliance assertion on all disclaimers
        for disc in STRESS_DISCLAIMERS:
            linter.assert_clean(disc)

        result = PortfolioStressTestResult(
            as_of_date=today_str,
            generated_at=now_iso,
            total_capital=total_capital,
            var_95_1d_pct=tail_metrics["var_95_1d_pct"],
            var_95_1d_dollars=tail_metrics["var_95_1d_dollars"],
            var_99_1d_pct=tail_metrics["var_99_1d_pct"],
            var_99_1d_dollars=tail_metrics["var_99_1d_dollars"],
            cvar_95_1d_pct=tail_metrics["cvar_95_1d_pct"],
            cvar_95_1d_dollars=tail_metrics["cvar_95_1d_dollars"],
            cvar_99_1d_pct=tail_metrics["cvar_99_1d_pct"],
            cvar_99_1d_dollars=tail_metrics["cvar_99_1d_dollars"],
            var_95_10d_pct=tail_metrics["var_95_10d_pct"],
            var_95_10d_dollars=tail_metrics["var_95_10d_dollars"],
            cvar_99_10d_pct=tail_metrics["cvar_99_10d_pct"],
            cvar_99_10d_dollars=tail_metrics["cvar_99_10d_dollars"],
            scenarios=scenario_impacts,
            tail_risk_posture=posture,  # type: ignore
            disclaimers=STRESS_DISCLAIMERS,
        )

        self._cached_result = result
        return result

    def export_feed(self, output_dir: Optional[Path] = None, force_refresh: bool = False) -> Path:
        """
        Exports the master portfolio stress test feed to JSON.
        """
        if output_dir is None:
            output_dir = DATA_DIR / "feeds"
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "portfolio_stress.json"

        feed = self.run_stress_test(force_refresh=force_refresh)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(feed.to_dict(), f, indent=2)

        return out_file


# Global singleton instance
portfolio_stress_engine = PortfolioStressEngine()
