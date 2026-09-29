"""
Active Style Tilts & Factor Return Attribution Engine (Phase 27.2)
US + Canada Dual-Market Intelligence Platform

Decomposes portfolio active return against cross-border benchmarks into
systematic factor return contributions and idiosyncratic stock selection alpha:
    Active Return = R_p - R_b = sum_k (Delta b_k * f_k) + alpha_selection
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Literal

import numpy as np
import pandas as pd

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.portfolio_hrp import hrp_portfolio_engine
from src.engine.factor_risk import (
    factor_risk_engine,
    FACTOR_KEYS,
    FACTOR_NAMES,
)
from src.models.schemas import (
    FactorTilt,
    BenchmarkAttribution,
    PortfolioFactorAttributionResult,
)

FACTOR_ATTRIBUTION_DISCLAIMERS = [
    "Factor return attribution, active style tilts, and specific alpha decompositions represent quantitative mathematical simulations provided strictly for impersonal decision-support research.",
    "Does not constitute personalized financial planning, fiduciary investment counsel, or individual portfolio advice under CSA Staff Notice 31-369 and SEC Publisher Exclusion rules.",
    "Historical factor returns, benchmark differentials, and active tracking error calculations are subject to market regime shifts and model estimation errors.",
]


class FactorAttributionEngine:
    """
    Computes active factor tilts and Brinson-Barra style PnL return attribution
    against US, Canadian, and cross-border blended benchmarks.
    """

    def __init__(self):
        self._cached_result: Optional[PortfolioFactorAttributionResult] = None

    @staticmethod
    def get_benchmark_weights(symbols: List[str], benchmark_type: str) -> np.ndarray:
        """
        Returns normalized weight vector for the designated benchmark.
        """
        w_b = np.zeros(len(symbols), dtype=float)

        if benchmark_type == "BLENDED_CROSS_BORDER":
            if "SPY" in symbols and "XIU" in symbols:
                w_b[symbols.index("SPY")] = 0.50
                w_b[symbols.index("XIU")] = 0.50
            elif "SPY" in symbols:
                w_b[symbols.index("SPY")] = 1.00
            elif "XIU" in symbols:
                w_b[symbols.index("XIU")] = 1.00
            else:
                w_b[:] = 1.0 / len(symbols)
        elif benchmark_type == "US_SP500_CORE":
            if "SPY" in symbols:
                w_b[symbols.index("SPY")] = 1.00
            else:
                w_b[:] = 1.0 / len(symbols)
        elif benchmark_type == "CA_TSX60_CORE":
            if "XIU" in symbols:
                w_b[symbols.index("XIU")] = 1.00
            else:
                w_b[:] = 1.0 / len(symbols)
        else:
            w_b[:] = 1.0 / len(symbols)

        return w_b

    def compute_single_benchmark_attribution(
        self,
        benchmark_name: str,
        w_p: np.ndarray,
        w_b: np.ndarray,
        B: np.ndarray,
        ret_df: pd.DataFrame,
        factors_df: pd.DataFrame,
    ) -> BenchmarkAttribution:
        """
        Calculates active factor tilts, factor contributions, and selection alpha for one benchmark.
        """
        # Ensure writeable arrays
        w_p = np.array(w_p, dtype=float, copy=True)
        w_b = np.array(w_b, dtype=float, copy=True)
        B = np.array(B, dtype=float, copy=True)

        # 1. Daily return streams
        r_p_daily = ret_df.values @ w_p
        r_b_daily = ret_df.values @ w_b
        r_active_daily = r_p_daily - r_b_daily

        # 2. Annualized returns
        r_p = float(np.mean(r_p_daily) * 252.0)
        r_b = float(np.mean(r_b_daily) * 252.0)
        active_ret = r_p - r_b

        # 3. Active risk & Information Ratio
        te = float(np.std(r_active_daily) * np.sqrt(252.0))
        ir = float(active_ret / te) if te > 1e-6 else 0.0

        # 4. Factor tilts: b_p, b_b, delta_b
        b_p = B.T @ w_p
        b_b = B.T @ w_b
        delta_b = b_p - b_b

        # 5. Annualized factor returns (f_k)
        f_ann = factors_df[FACTOR_KEYS].mean().values * 252.0

        tilts_dict = {}
        factor_contrib_sum = 0.0

        for k, factor_key in enumerate(FACTOR_KEYS):
            fname = FACTOR_NAMES[k]
            tilt_val = float(delta_b[k])
            f_ret = float(f_ann[k])
            pnl_contrib = float(tilt_val * f_ret)
            factor_contrib_sum += pnl_contrib

            # Direction classification
            if tilt_val >= 0.05:
                direction: Literal["OVERWEIGHT", "NEUTRAL", "UNDERWEIGHT"] = "OVERWEIGHT"
            elif tilt_val <= -0.05:
                direction = "UNDERWEIGHT"
            else:
                direction = "NEUTRAL"

            tilts_dict[factor_key] = FactorTilt(
                factor_key=factor_key,
                factor_name=fname,
                portfolio_exposure=round(float(b_p[k]), 4),
                benchmark_exposure=round(float(b_b[k]), 4),
                active_tilt=round(tilt_val, 4),
                tilt_direction=direction,
                factor_return_1y_pct=round(f_ret * 100.0, 2),
                pnl_contribution_pct=round(pnl_contrib * 100.0, 2),
            )

        specific_alpha = active_ret - factor_contrib_sum

        return BenchmarkAttribution(
            benchmark_name=benchmark_name,
            portfolio_return_pct=round(r_p * 100.0, 2),
            benchmark_return_pct=round(r_b * 100.0, 2),
            active_return_pct=round(active_ret * 100.0, 2),
            tracking_error_pct=round(te * 100.0, 2),
            information_ratio=round(ir, 2),
            factor_return_contribution_pct=round(factor_contrib_sum * 100.0, 2),
            specific_alpha_pct=round(specific_alpha * 100.0, 2),
            factor_tilts=tilts_dict,
        )

    def evaluate_attribution(
        self, total_capital: float = 100000.0, force_refresh: bool = False
    ) -> PortfolioFactorAttributionResult:
        """
        Executes multi-benchmark factor return attribution and detects unintentional factor biases.
        """
        if self._cached_result and not force_refresh:
            return self._cached_result

        # 1. Load universe returns and HRP weights
        returns_df, symbol_to_country = hrp_portfolio_engine.load_universe_returns()
        hrp_res = hrp_portfolio_engine.generate_hrp_allocation(
            total_capital=total_capital, force_refresh=force_refresh
        )

        symbols = list(returns_df.columns)
        w_p = np.array([hrp_res.allocations[s].weight for s in symbols], dtype=float)
        w_p /= np.sum(w_p)

        # 2. Factor returns and loadings
        factors_df = factor_risk_engine.construct_factor_returns(returns_df)
        B, _, _, _ = factor_risk_engine.compute_factor_loadings(
            returns_df, factors_df, symbol_to_country
        )

        # 3. Evaluate 3 benchmarks
        benchmark_configs = [
            ("BLENDED_CROSS_BORDER", "Blended US/CA (50% SPY / 50% XIU)"),
            ("US_SP500_CORE", "US Core Equities (100% SPY)"),
            ("CA_TSX60_CORE", "Canadian Large-Cap Core (100% XIU)"),
        ]

        benchmarks_result = {}
        for b_type, b_title in benchmark_configs:
            w_b = self.get_benchmark_weights(symbols, b_type)
            attrib = self.compute_single_benchmark_attribution(
                benchmark_name=b_title,
                w_p=w_p,
                w_b=w_b,
                B=B,
                ret_df=returns_df,
                factors_df=factors_df,
            )
            benchmarks_result[b_type] = attrib

        # Primary benchmark for ranking: BLENDED_CROSS_BORDER
        primary_attrib = benchmarks_result["BLENDED_CROSS_BORDER"]
        tilts_list = list(primary_attrib.factor_tilts.values())

        # Sort by pnl_contribution_pct
        sorted_by_pnl = sorted(tilts_list, key=lambda t: t.pnl_contribution_pct, reverse=True)
        top_pos = [f"{t.factor_name} (+{t.pnl_contribution_pct:.2f}%)" for t in sorted_by_pnl if t.pnl_contribution_pct > 0][:3]
        top_neg = [f"{t.factor_name} ({t.pnl_contribution_pct:.2f}%)" for t in sorted_by_pnl if t.pnl_contribution_pct < 0][-3:]

        # Unintentional factor biases (|active_tilt| >= 0.15)
        unintentional_biases = []
        for t in tilts_list:
            if abs(t.active_tilt) >= 0.15:
                bias_type = "OVEREXPOSURE" if t.active_tilt > 0 else "UNDEREXPOSURE"
                unintentional_biases.append(
                    {
                        "factor_key": t.factor_key,
                        "factor_name": t.factor_name,
                        "active_tilt": t.active_tilt,
                        "bias_type": bias_type,
                        "pnl_impact_pct": t.pnl_contribution_pct,
                        "observation": f"Active tilt of {t.active_tilt:+.2f} relative to balanced benchmark.",
                    }
                )

        # Lint compliance check
        for disc in FACTOR_ATTRIBUTION_DISCLAIMERS:
            linter.assert_clean(disc)

        now_iso = datetime.now(timezone.utc).isoformat()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        result = PortfolioFactorAttributionResult(
            as_of_date=today_str,
            generated_at=now_iso,
            total_capital=total_capital,
            evaluation_horizon="1_YEAR_TRAILING (252 Trading Days)",
            benchmarks=benchmarks_result,
            top_positive_factors=top_pos,
            top_negative_factors=top_neg,
            unintentional_biases=unintentional_biases,
            disclaimers=FACTOR_ATTRIBUTION_DISCLAIMERS,
        )

        self._cached_result = result
        return result

    def export_feed(self, output_dir: Optional[Path] = None, force_refresh: bool = False) -> Path:
        """
        Exports the master factor attribution feed to factor_attribution.json.
        """
        if output_dir is None:
            output_dir = DATA_DIR / "feeds"
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "factor_attribution.json"

        feed = self.evaluate_attribution(force_refresh=force_refresh)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(feed.model_dump(), f, indent=2)

        return out_file


# Global singleton instance
factor_attribution_engine = FactorAttributionEngine()
