"""
Cross-Border Multi-Asset Factor Risk & Specific Variance Decomposition Engine (Phase 27.1)
US + Canada Dual-Market Intelligence Platform

Implements a 10-factor risk model spanning 7 equity style factors and 3 cross-border
macroeconomic factors (CAD/USD FX, WTI/WCS crude oil, and BoC/Fed yield curve spread).
Decomposes portfolio variance into systematic factor risk and idiosyncratic specific risk:
    sigma_p^2 = w^T (B Sigma_F B^T) w + w^T D_epsilon w
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from src.compliance.linter import linter
from config.settings import DATA_DIR
from src.engine.portfolio_hrp import hrp_portfolio_engine
from src.models.schemas import FactorLoading, FactorRiskSummary, PortfolioFactorRiskResult

FACTOR_NAMES = [
    "Market Beta",
    "Size (SMB)",
    "Value (HML)",
    "Momentum (UMD)",
    "Quality (RMW)",
    "Low Volatility (BAB)",
    "Liquidity (ILLIQ)",
    "CAD/USD FX Beta",
    "Crude Oil Beta",
    "Yield Curve Beta",
]

FACTOR_KEYS = [
    "market_beta",
    "size_smb",
    "value_hml",
    "momentum_umd",
    "quality_rmw",
    "low_volatility_bab",
    "liquidity_illiq",
    "cad_usd_fx_beta",
    "crude_oil_beta",
    "yield_curve_beta",
]

FACTOR_RISK_DISCLAIMERS = [
    "Multi-factor risk model metrics, active style tilts, and specific risk decomposition are quantitative simulations provided strictly for impersonal decision-support research.",
    "Does not constitute personalized financial planning, fiduciary investment management, or individualized advice under CSA Staff Notice 31-369 and SEC Publisher Exclusion rules.",
    "Factor loadings, idiosyncratic residuals, and factor covariances are derived from historical observations and may diverge during non-stationary market regimes.",
]


class FactorRiskEngine:
    """
    Constructs a cross-border multi-asset factor risk model and decomposes portfolio
    risk into systematic factor variance and specific idiosyncratic residual variance.
    """

    def __init__(self):
        self._cached_result: Optional[PortfolioFactorRiskResult] = None

    @staticmethod
    def construct_factor_returns(returns_df: pd.DataFrame) -> pd.DataFrame:
        """
        Synthesizes the 10 cross-border factor return series from market data:
        - 7 Style Factors: MKT, SMB, HML, UMD, RMW, BAB, ILLIQ
        - 3 Cross-Border Macro Factors: FX (CAD/USD), OIL (Crude), CURVE (Yield Curve)
        """
        cols = list(returns_df.columns)

        # 1. Market Factor: Equal-weighted composite of US and Canadian benchmark ETFs
        f_mkt = 0.5 * returns_df["SPY"] + 0.5 * returns_df["XIU"]

        # 2. Size (SMB): Mid-cap growth/resource spread vs mega-cap tech
        mid_caps = [s for s in ["SHOP", "CNQ", "CP", "BAM"] if s in cols]
        mega_caps = [s for s in ["AAPL", "MSFT", "NVDA", "GOOGL"] if s in cols]
        f_smb = returns_df[mid_caps].mean(axis=1) - returns_df[mega_caps].mean(axis=1)

        # 3. Value (HML): Financials & mature dividend midstream vs high multiple growth
        value_assets = [s for s in ["JPM", "RY", "TD", "ENB", "XOM"] if s in cols]
        growth_assets = [s for s in ["AAPL", "MSFT", "NVDA", "SHOP"] if s in cols]
        f_hml = returns_df[value_assets].mean(axis=1) - returns_df[growth_assets].mean(axis=1)

        # 4. Momentum (UMD): 12-month trailing winners vs laggards
        mom_leaders = [s for s in ["NVDA", "AAPL", "XOM"] if s in cols]
        mom_laggards = [s for s in ["TD", "ENB", "SHOP"] if s in cols]
        f_umd = returns_df[mom_leaders].mean(axis=1) - returns_df[mom_laggards].mean(axis=1)

        # 5. Quality (RMW): High ROE & balance sheet fortress vs capital-intensive cyclicals
        high_qual = [s for s in ["AAPL", "MSFT", "GOOGL", "CNR", "CP"] if s in cols]
        low_qual = [s for s in ["CNQ", "ENB", "SHOP"] if s in cols]
        f_rmw = returns_df[high_qual].mean(axis=1) - returns_df[low_qual].mean(axis=1)

        # 6. Low Volatility (BAB): Low realized volatility anchors vs high beta cyclicals
        low_vol = [s for s in ["ENB", "TD", "RY", "CNR", "SPY"] if s in cols]
        high_vol = [s for s in ["NVDA", "SHOP", "CNQ"] if s in cols]
        f_bab = returns_df[low_vol].mean(axis=1) - returns_df[high_vol].mean(axis=1)

        # 7. Liquidity (ILLIQ): Secondary liquidity issues vs top tier volume leaders
        lower_liq = [s for s in ["CP", "BAM", "BN", "CNR"] if s in cols]
        high_liq = [s for s in ["SPY", "QQQ", "AAPL", "NVDA"] if s in cols]
        f_illiq = returns_df[lower_liq].mean(axis=1) - returns_df[high_liq].mean(axis=1)

        # 8. CAD/USD Foreign Exchange: Currency return spread (XIU in CAD vs SPY in USD)
        f_fx = returns_df["XIU"] - returns_df["SPY"]

        # 9. Crude Oil Benchmark: Hydrocarbon sensitivity orthogonalized to market
        oil_assets = [s for s in ["CNQ", "ENB", "XOM"] if s in cols]
        f_oil = returns_df[oil_assets].mean(axis=1) - f_mkt

        # 10. Yield Curve Slope: Bank net-interest-margin sensitivity orthogonalized to market
        curve_assets = [s for s in ["RY", "TD", "JPM"] if s in cols]
        f_curve = returns_df[curve_assets].mean(axis=1) - f_mkt

        factors_df = pd.DataFrame(
            {
                "market_beta": f_mkt,
                "size_smb": f_smb,
                "value_hml": f_hml,
                "momentum_umd": f_umd,
                "quality_rmw": f_rmw,
                "low_volatility_bab": f_bab,
                "liquidity_illiq": f_illiq,
                "cad_usd_fx_beta": f_fx,
                "crude_oil_beta": f_oil,
                "yield_curve_beta": f_curve,
            },
            index=returns_df.index,
        )
        return factors_df

    def compute_factor_loadings(
        self, returns_df: pd.DataFrame, factors_df: pd.DataFrame, symbol_to_country: Dict[str, str]
    ) -> Tuple[np.ndarray, np.ndarray, Dict[str, FactorLoading], np.ndarray]:
        """
        Fits multi-factor regressions for each security:
            R_i = alpha_i + sum_k beta_{i,k} F_k + epsilon_i
        Returns:
            - Factor loading matrix B (N x 10)
            - Specific residual variance vector D_epsilon (N)
            - Dict of FactorLoading schemas
            - R-squared array (N)
        """
        symbols = list(returns_df.columns)
        B_list = []
        spec_vars_list = []
        loadings_dict = {}
        r2_list = []

        X = factors_df[FACTOR_KEYS]

        for sym in symbols:
            y = returns_df[sym]
            reg = Ridge(alpha=1e-4, fit_intercept=True)
            reg.fit(X, y)
            coefs = reg.coef_

            # Ensure writeable copy
            coefs_arr = np.array(coefs, dtype=float, copy=True)
            if not coefs_arr.flags.writeable:
                coefs_arr = coefs_arr.copy()

            preds = reg.predict(X)
            residuals = y.values - preds
            var_resid_annualized = float(max(1e-6, np.var(residuals) * 252.0))
            r2 = float(max(0.0, min(1.0, reg.score(X, y))))

            B_list.append(coefs_arr)
            spec_vars_list.append(var_resid_annualized)
            r2_list.append(r2)

            mkt_str = symbol_to_country.get(sym, "US")
            mkt_literal = "CA" if mkt_str == "CA" else "US"

            loading = FactorLoading(
                symbol=sym,
                market=mkt_literal,
                market_beta=round(float(coefs_arr[0]), 3),
                size_smb=round(float(coefs_arr[1]), 3),
                value_hml=round(float(coefs_arr[2]), 3),
                momentum_umd=round(float(coefs_arr[3]), 3),
                quality_rmw=round(float(coefs_arr[4]), 3),
                low_volatility_bab=round(float(coefs_arr[5]), 3),
                liquidity_illiq=round(float(coefs_arr[6]), 3),
                cad_usd_fx_beta=round(float(coefs_arr[7]), 3),
                crude_oil_beta=round(float(coefs_arr[8]), 3),
                yield_curve_beta=round(float(coefs_arr[9]), 3),
                specific_residual_variance=round(var_resid_annualized, 6),
                r_squared=round(r2, 4),
            )
            loadings_dict[sym] = loading

        B = np.array(B_list, dtype=float)
        spec_vars = np.array(spec_vars_list, dtype=float)
        r2_arr = np.array(r2_list, dtype=float)

        return B, spec_vars, loadings_dict, r2_arr

    def calculate_portfolio_factor_risk(
        self,
        weights: np.ndarray,
        B: np.ndarray,
        factor_cov: np.ndarray,
        spec_vars: np.ndarray,
        total_capital: float = 100000.0,
    ) -> Tuple[float, float, float, float, float, Dict[str, FactorRiskSummary]]:
        """
        Decomposes portfolio risk:
            Systematic variance: sigma_sys^2 = w^T B Sigma_F B^T w
            Specific variance:   sigma_spec^2 = sum_i w_i^2 sigma_{epsilon,i}^2
            Total variance:      sigma_p^2 = sigma_sys^2 + sigma_spec^2
        """
        # Ensure writeable arrays
        weights = np.array(weights, dtype=float, copy=True)
        factor_cov = np.array(factor_cov, dtype=float, copy=True)
        spec_vars = np.array(spec_vars, dtype=float, copy=True)

        # Portfolio factor exposure: b_p = B^T w (10,)
        b_p = B.T @ weights

        # Systematic variance
        sys_var = float(b_p.T @ factor_cov @ b_p)
        sys_var = max(1e-8, sys_var)

        # Specific (idiosyncratic) variance
        spec_var = float(np.sum((weights ** 2) * spec_vars))
        spec_var = max(1e-8, spec_var)

        total_var = sys_var + spec_var
        port_vol = float(np.sqrt(total_var))
        sys_vol = float(np.sqrt(sys_var))
        spec_vol = float(np.sqrt(spec_var))

        sys_risk_share_pct = round((sys_var / total_var) * 100.0, 2)
        spec_risk_share_pct = round((spec_var / total_var) * 100.0, 2)

        # Euler marginal factor risk contributions: MCR_F,k = (Sigma_F b_p)_k * b_p,k / sigma_p^2
        sigma_F_bp = factor_cov @ b_p
        summaries = {}

        for k, factor_key in enumerate(FACTOR_KEYS):
            fname = FACTOR_NAMES[k]
            f_var = float(factor_cov[k, k])
            f_exposure = float(b_p[k])

            # Marginal contribution to total portfolio variance
            marginal_contrib_variance = float(sigma_F_bp[k] * b_p[k])
            mcr_pct = (marginal_contrib_variance / total_var) * 100.0
            pct_of_sys = (marginal_contrib_variance / sys_var) * 100.0 if sys_var > 0 else 0.0

            summaries[factor_key] = FactorRiskSummary(
                factor_name=fname,
                factor_variance=round(f_var, 6),
                portfolio_factor_exposure=round(f_exposure, 4),
                marginal_contribution_to_risk_pct=round(mcr_pct, 2),
                percent_of_systematic_risk=round(pct_of_sys, 2),
            )

        return port_vol, sys_vol, spec_vol, sys_risk_share_pct, spec_risk_share_pct, summaries

    def evaluate_factor_risk(
        self, total_capital: float = 100000.0, force_refresh: bool = False
    ) -> PortfolioFactorRiskResult:
        """
        Executes end-to-end multi-factor risk decomposition over the dual-market universe.
        """
        if self._cached_result and not force_refresh:
            return self._cached_result

        # 1. Load universe returns and HRP portfolio allocations
        returns_df, symbol_to_country = hrp_portfolio_engine.load_universe_returns()
        hrp_res = hrp_portfolio_engine.generate_hrp_allocation(
            total_capital=total_capital, force_refresh=force_refresh
        )

        symbols = list(returns_df.columns)
        weights = np.array([hrp_res.allocations[s].weight for s in symbols], dtype=float)
        weights /= np.sum(weights)

        # 2. Synthesize 10 cross-border factor return series
        factors_df = self.construct_factor_returns(returns_df)

        # 3. Factor covariance matrix (annualized 252d)
        factor_cov_df = factors_df[FACTOR_KEYS].cov() * 252.0
        factor_cov = np.array(factor_cov_df.values, dtype=float, copy=True)
        if not factor_cov.flags.writeable:
            factor_cov = factor_cov.copy()

        # 4. Multi-factor regression loadings and specific residual variance
        B, spec_vars, loadings_dict, _ = self.compute_factor_loadings(
            returns_df, factors_df, symbol_to_country
        )

        # 5. Portfolio factor risk decomposition
        (
            port_vol,
            sys_vol,
            spec_vol,
            sys_risk_share,
            spec_risk_share,
            factor_summaries,
        ) = self.calculate_portfolio_factor_risk(
            weights=weights,
            B=B,
            factor_cov=factor_cov,
            spec_vars=spec_vars,
            total_capital=total_capital,
        )

        # Build factor covariance dictionary for JSON serialization
        cov_dict = {}
        for i, row_key in enumerate(FACTOR_KEYS):
            cov_dict[row_key] = {}
            for j, col_key in enumerate(FACTOR_KEYS):
                cov_dict[row_key][col_key] = round(float(factor_cov[i, j]), 6)

        # Lint compliance check
        for disc in FACTOR_RISK_DISCLAIMERS:
            linter.assert_clean(disc)

        now_iso = datetime.now(timezone.utc).isoformat()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        result = PortfolioFactorRiskResult(
            as_of_date=today_str,
            generated_at=now_iso,
            total_capital=total_capital,
            portfolio_annualized_volatility=round(port_vol, 4),
            systematic_volatility=round(sys_vol, 4),
            specific_volatility=round(spec_vol, 4),
            systematic_risk_share_pct=sys_risk_share,
            specific_risk_share_pct=spec_risk_share,
            factor_loadings=loadings_dict,
            factor_covariance_matrix=cov_dict,
            factor_risk_summaries=factor_summaries,
            disclaimers=FACTOR_RISK_DISCLAIMERS,
        )

        self._cached_result = result
        return result

    def export_feed(self, output_dir: Optional[Path] = None, force_refresh: bool = False) -> Path:
        """
        Exports the master factor risk feed to factor_risk.json.
        """
        if output_dir is None:
            output_dir = DATA_DIR / "feeds"
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "factor_risk.json"

        feed = self.evaluate_factor_risk(force_refresh=force_refresh)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(feed.model_dump(), f, indent=2)

        return out_file


# Global singleton instance
factor_risk_engine = FactorRiskEngine()
