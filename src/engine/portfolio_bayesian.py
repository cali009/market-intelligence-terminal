"""
Black-Litterman Bayesian View Blending & Turnover-Constrained Rebalancer (Phase 28.1 & 28.2)
US + Canada Dual-Market Intelligence Platform

Bridges Quant Intel security conviction signals with rigorous Bayesian asset allocation:
1. Market Equilibrium Implied Returns (Pi = lambda * Sigma * w_mkt).
2. Quant Intel View Extraction (P matrix, Q return vector, Omega diagonal uncertainty matrix).
3. Black-Litterman Master Combined Distribution:
       mu_BL = [ (tau * Sigma)^-1 + P^T Omega^-1 P ]^-1 [ (tau * Sigma)^-1 Pi + P^T Omega^-1 Q ]
       Sigma_BL = Sigma + [ (tau * Sigma)^-1 + P^T Omega^-1 P ]^-1
4. Turnover-Constrained Quadratic Rebalancer:
       max_w [ w^T mu_BL - (lambda / 2) w^T Sigma_BL w - lambda_turnover ||w - w_0||_1 - lambda_impact sum kappa_i (w_i - w_{i,0})^2 ]
       subject to sum w_i = 1.0, 0 <= w_i <= 0.18, 0.5 * ||w - w_0||_1 <= MaxTurnover
5. Efficient Turnover Frontier: Expected Posterior Return vs Portfolio Turnover.
6. Statutory Impersonal Research Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Literal

import numpy as np
import pandas as pd
import scipy.optimize as sco

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.portfolio_hrp import hrp_portfolio_engine
from src.engine.quant_intel import quant_intel_engine
from src.models.schemas import (
    BlackLittermanView,
    BayesianAssetAllocation,
    TurnoverFrontierPoint,
    PortfolioBayesianResult,
)

BAYESIAN_DISCLAIMERS = [
    "Black-Litterman Bayesian allocations, subjective view blending, and turnover-constrained rebalancing represent impersonal quantitative mathematical simulations.",
    "Does not constitute personalized financial planning, fiduciary asset management, or individualized investment advice under CSA Staff Notice 31-369 and SEC Publisher Exclusion rules.",
    "Implied equilibrium priors, quantitative view confidences, and estimated transaction costs are model approximations subject to execution slippage and parameter instability.",
]


class PortfolioBayesianEngine:
    """
    Implements Black-Litterman Bayesian view blending and turnover-regularized
    quadratic portfolio optimization across the dual-market 19-asset universe.
    """

    def __init__(
        self,
        risk_aversion: float = 2.50,
        tau: float = 0.05,
        turnover_penalty_lambda: float = 0.0025,
        market_impact_lambda: float = 0.0005,
        max_single_weight: float = 0.18,
    ):
        self.risk_aversion = risk_aversion
        self.tau = tau
        self.turnover_penalty_lambda = turnover_penalty_lambda
        self.market_impact_lambda = market_impact_lambda
        self.max_single_weight = max_single_weight
        self._cached_result: Optional[PortfolioBayesianResult] = None

    def load_equilibrium_prior(self) -> Tuple[List[str], np.ndarray, np.ndarray, np.ndarray, Dict[str, str]]:
        """
        Loads asset returns, computes sample covariance matrix, and calculates
        the reverse-optimized implied equilibrium prior return vector Pi.
        """
        returns_df, market_map = hrp_portfolio_engine.load_universe_returns()
        symbols = sorted(list(returns_df.columns))

        # Sample annualized covariance matrix
        cov_df = returns_df[symbols].cov() * 252.0
        cov_matrix = np.array(cov_df.values, dtype=float, copy=True)
        if not cov_matrix.flags.writeable:
            cov_matrix = cov_matrix.copy()

        # Regularize diagonal for numerical stability (Tikhonov shrinkage 1e-4)
        cov_matrix += np.eye(len(symbols)) * 1e-5

        # Use HRP baseline weights as the equilibrium prior weights w_0
        hrp_res = hrp_portfolio_engine.generate_hrp_allocation(force_refresh=False)
        w_prior = np.array([hrp_res.allocations.get(s).weight if s in hrp_res.allocations else 1.0 / len(symbols) for s in symbols], dtype=float)
        w_prior = w_prior / np.sum(w_prior)

        # Implied market equilibrium return vector Pi = lambda * Sigma * w_0
        pi_returns = self.risk_aversion * (cov_matrix @ w_prior)

        return symbols, cov_matrix, w_prior, pi_returns, market_map

    def extract_quant_intel_views(
        self,
        symbols: List[str],
        pi_returns: np.ndarray,
        symbol_to_idx: Dict[str, int],
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[BlackLittermanView]]:
        """
        Translates Quant Intel conviction grades and scores into structured
        Black-Litterman subjective views (P matrix, Q vector, and Omega diagonal).
        """
        dossiers = quant_intel_engine.evaluate_all()

        views_list: List[BlackLittermanView] = []
        p_rows = []
        q_vals = []
        confidences = []

        view_counter = 1

        # 1. Absolute Views for Top High-Conviction Securities
        for sym, dossier in dossiers.items():
            if sym not in symbol_to_idx:
                continue

            grade = dossier.conviction_grade
            aligned = dossier.aligned_layers_count
            idx = symbol_to_idx[sym]
            prior_ret = pi_returns[idx]

            if grade in ("A", "GRADE_A") or (grade in ("B", "GRADE_B") and aligned >= 5):
                # High conviction bullish tilt (+3.2% annualized alpha, 85% confidence)
                view_ret = prior_ret + 0.032
                conf = 0.85
                row = np.zeros(len(symbols), dtype=float)
                row[idx] = 1.0
                p_rows.append(row)
                q_vals.append(view_ret)
                confidences.append(conf)

                views_list.append(
                    BlackLittermanView(
                        view_id=f"VIEW_{view_counter:02d}",
                        symbol=sym,
                        direction="OUTPERFORM",
                        view_type="ABSOLUTE",
                        relative_symbol=None,
                        view_return_pct=round(view_ret * 100.0, 2),
                        confidence_pct=round(conf * 100.0, 1),
                        implied_prior_return_pct=round(prior_ret * 100.0, 2),
                        posterior_expected_return_pct=0.0,  # Updated after posterior solve
                        attribution_source="QUANT_INTEL_HIGH_CONVICTION",
                    )
                )
                view_counter += 1

            elif grade in ("B", "GRADE_B") and aligned == 4 and sym in ("MSFT", "QQQ"):
                # Moderate conviction tilt (+1.5% annualized alpha, 70% confidence)
                view_ret = prior_ret + 0.015
                conf = 0.70
                row = np.zeros(len(symbols), dtype=float)
                row[idx] = 1.0
                p_rows.append(row)
                q_vals.append(view_ret)
                confidences.append(conf)

                views_list.append(
                    BlackLittermanView(
                        view_id=f"VIEW_{view_counter:02d}",
                        symbol=sym,
                        direction="BULLISH",
                        view_type="ABSOLUTE",
                        relative_symbol=None,
                        view_return_pct=round(view_ret * 100.0, 2),
                        confidence_pct=round(conf * 100.0, 1),
                        implied_prior_return_pct=round(prior_ret * 100.0, 2),
                        posterior_expected_return_pct=0.0,
                        attribution_source="QUANT_INTEL_MODERATE",
                    )
                )
                view_counter += 1

            elif grade in ("C", "GRADE_C") and aligned <= 3 and sym in ("AMZN", "ENB"):
                # Conservative underweight tilt (-1.8% annualized alpha, 60% confidence)
                view_ret = prior_ret - 0.018
                conf = 0.60
                row = np.zeros(len(symbols), dtype=float)
                row[idx] = 1.0
                p_rows.append(row)
                q_vals.append(view_ret)
                confidences.append(conf)

                views_list.append(
                    BlackLittermanView(
                        view_id=f"VIEW_{view_counter:02d}",
                        symbol=sym,
                        direction="UNDERPERFORM",
                        view_type="ABSOLUTE",
                        relative_symbol=None,
                        view_return_pct=round(view_ret * 100.0, 2),
                        confidence_pct=round(conf * 100.0, 1),
                        implied_prior_return_pct=round(prior_ret * 100.0, 2),
                        posterior_expected_return_pct=0.0,
                        attribution_source="QUANT_INTEL_CONSERVATIVE",
                    )
                )
                view_counter += 1

        # 2. Cross-Border Relative View: NVDA vs GOOGL (AI Hardware vs Search)
        if "NVDA" in symbol_to_idx and "GOOGL" in symbol_to_idx:
            idx_nvda = symbol_to_idx["NVDA"]
            idx_googl = symbol_to_idx["GOOGL"]
            rel_row = np.zeros(len(symbols), dtype=float)
            rel_row[idx_nvda] = 1.0
            rel_row[idx_googl] = -1.0
            p_rows.append(rel_row)
            # View: NVDA will outperform GOOGL by +2.5% annualized
            q_vals.append(0.025)
            confidences.append(0.75)

            views_list.append(
                BlackLittermanView(
                    view_id=f"VIEW_{view_counter:02d}",
                    symbol="NVDA",
                    direction="OUTPERFORM",
                    view_type="RELATIVE",
                    relative_symbol="GOOGL",
                    view_return_pct=2.50,
                    confidence_pct=75.0,
                    implied_prior_return_pct=round((pi_returns[idx_nvda] - pi_returns[idx_googl]) * 100.0, 2),
                    posterior_expected_return_pct=0.0,
                    attribution_source="CROSS_BORDER_SECTOR_PAIR",
                )
            )
            view_counter += 1

        # 3. Canadian Banking Relative View: RY vs TD (Tier-1 Banking Quality Spread)
        if "RY" in symbol_to_idx and "TD" in symbol_to_idx:
            idx_ry = symbol_to_idx["RY"]
            idx_td = symbol_to_idx["TD"]
            rel_row = np.zeros(len(symbols), dtype=float)
            rel_row[idx_ry] = 1.0
            rel_row[idx_td] = -1.0
            p_rows.append(rel_row)
            # View: RY will outperform TD by +1.8% annualized
            q_vals.append(0.018)
            confidences.append(0.70)

            views_list.append(
                BlackLittermanView(
                    view_id=f"VIEW_{view_counter:02d}",
                    symbol="RY",
                    direction="OUTPERFORM",
                    view_type="RELATIVE",
                    relative_symbol="TD",
                    view_return_pct=1.80,
                    confidence_pct=70.0,
                    implied_prior_return_pct=round((pi_returns[idx_ry] - pi_returns[idx_td]) * 100.0, 2),
                    posterior_expected_return_pct=0.0,
                    attribution_source="CANADIAN_BANKING_RELATIVE_SPREAD",
                )
            )

        P = np.array(p_rows, dtype=float)
        Q = np.array(q_vals, dtype=float)
        C = np.array(confidences, dtype=float)

        return P, Q, C, views_list

    def compute_black_litterman_posterior(
        self,
        cov_matrix: np.ndarray,
        pi_returns: np.ndarray,
        P: np.ndarray,
        Q: np.ndarray,
        C: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        """
        Solves the Black-Litterman master posterior distribution (mu_BL, Sigma_BL)
        using the numerically stable Woodbury-Sherman-Morrison formulation.
        """
        K = len(Q)
        N = len(pi_returns)

        if K == 0:
            # Null views: posterior collapses identically to market equilibrium prior
            return pi_returns.copy(), cov_matrix.copy(), np.zeros(N), 1.0

        # Construct diagonal view uncertainty matrix Omega (Idzorek 2005 / He-Litterman 1999)
        # Omega_k = P_k * (tau * Sigma) * P_k^T * (1 - C_k) / C_k
        tau_sigma = self.tau * cov_matrix
        omega_diag = np.zeros(K, dtype=float)
        for k in range(K):
            p_k = P[k, :]
            var_k = float(p_k @ tau_sigma @ p_k)
            conf_k = max(0.01, min(0.99, C[k]))
            # Scale uncertainty inversely with confidence
            omega_diag[k] = var_k * ((1.0 - conf_k) / conf_k)
            if omega_diag[k] < 1e-6:
                omega_diag[k] = 1e-6

        Omega = np.diag(omega_diag)

        # Numerically stable alternative formulation:
        # A = P * (tau * Sigma) * P^T + Omega
        # K_gain = (tau * Sigma) * P^T * A^-1
        # mu_BL = Pi + K_gain * (Q - P * Pi)
        # M = tau * Sigma - K_gain * P * (tau * Sigma)
        # Sigma_BL = Sigma + M
        A = P @ tau_sigma @ P.T + Omega
        A_inv = np.linalg.inv(A)

        K_gain = tau_sigma @ P.T @ A_inv
        delta_q = Q - (P @ pi_returns)
        mu_bl = pi_returns + (K_gain @ delta_q)

        M = tau_sigma - (K_gain @ P @ tau_sigma)
        sigma_bl = cov_matrix + M

        # Symmetrize posterior covariance matrix
        sigma_bl = 0.5 * (sigma_bl + sigma_bl.T)

        # Unconstrained optimal weights w_unc = (1 / lambda) * Sigma_BL^-1 * mu_BL
        sigma_bl_inv = np.linalg.inv(sigma_bl)
        w_unconstrained = (1.0 / self.risk_aversion) * (sigma_bl_inv @ mu_bl)
        # Clip long-only and normalize
        w_unconstrained = np.clip(w_unconstrained, 0.0, None)
        if np.sum(w_unconstrained) > 0:
            w_unconstrained = w_unconstrained / np.sum(w_unconstrained)
        else:
            w_unconstrained = np.ones(N) / N

        # Estimation error trace ratio: Tr(Sigma_BL) / Tr(Sigma)
        trace_ratio = float(np.trace(sigma_bl) / np.trace(cov_matrix))

        return mu_bl, sigma_bl, w_unconstrained, trace_ratio

    def rebalance_portfolio(
        self,
        w_prior: np.ndarray,
        mu_bl: np.ndarray,
        sigma_bl: np.ndarray,
        market_map: Dict[str, str],
        symbols: List[str],
        turnover_cap: float = 0.20,
    ) -> Tuple[np.ndarray, float, float, float]:
        """
        Executes turnover-regularized quadratic optimization balancing posterior expected utility
        against L1 turnover penalties and L2 market impact frictions.
        """
        N = len(symbols)

        # Objective function to minimize:
        # -w^T mu_bl + 0.5 * lambda * w^T Sigma_bl w + lambda_turnover * sum |w - w_prior| + lambda_impact * sum (w - w_prior)^2
        def objective(w):
            utility = -float(w @ mu_bl) + 0.5 * self.risk_aversion * float(w @ sigma_bl @ w)
            l1_turnover = self.turnover_penalty_lambda * np.sum(np.abs(w - w_prior))
            l2_impact = self.market_impact_lambda * np.sum((w - w_prior) ** 2)
            return utility + l1_turnover + l2_impact

        # Constraints:
        # 1. Fully invested: sum(w) = 1.0
        # 2. One-way turnover cap: 0.5 * sum(|w - w_prior|) <= turnover_cap
        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
            {"type": "ineq", "fun": lambda w: turnover_cap - 0.5 * np.sum(np.abs(w - w_prior))},
        ]

        # Bounds: [0.0, max_single_weight]
        bounds = [(0.0, self.max_single_weight) for _ in range(N)]

        init_w = w_prior.copy()
        res = sco.minimize(
            objective,
            init_w,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 200, "ftol": 1e-7},
        )

        if res.success:
            w_opt = res.x
        else:
            w_opt = w_prior.copy()

        # Clean numerical precision
        w_opt = np.clip(w_opt, 0.0, self.max_single_weight)
        w_opt = w_opt / np.sum(w_opt)

        one_way_turnover = 0.5 * float(np.sum(np.abs(w_opt - w_prior)))

        # Calculate transaction cost drag (spread cross + broker/exchange fees + impact)
        # Dual-market friction: US ~4 bps, CA ~6 bps average round-trip
        tc_bps = 0.0
        for i, sym in enumerate(symbols):
            delta_w = abs(w_opt[i] - w_prior[i])
            mkt = market_map.get(sym, "US")
            base_bps = 4.0 if mkt == "US" else 6.0
            # Quadratic market impact penalty for larger turnover
            impact_bps = base_bps + (delta_w * 100.0) * 1.5
            tc_bps += delta_w * impact_bps

        tc_dollars = (tc_bps / 10000.0) * 100000.0  # Normalized to $100k capital

        return w_opt, one_way_turnover, tc_bps, tc_dollars

    def generate_turnover_frontier(
        self,
        w_prior: np.ndarray,
        mu_bl: np.ndarray,
        sigma_bl: np.ndarray,
        symbols: List[str],
        market_map: Dict[str, str],
    ) -> List[TurnoverFrontierPoint]:
        """
        Traces the efficient turnover frontier by sweeping turnover limits from 2% to 50%.
        """
        turnover_limits = [0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.35, 0.50]
        frontier = []

        for t_lim in turnover_limits:
            w_t, turnover, tc_bps, _ = self.rebalance_portfolio(
                w_prior=w_prior,
                mu_bl=mu_bl,
                sigma_bl=sigma_bl,
                market_map=market_map,
                symbols=symbols,
                turnover_cap=t_lim,
            )

            port_ret = float(w_t @ mu_bl) * 100.0
            port_vol = math.sqrt(float(w_t @ sigma_bl @ w_t)) * 100.0
            rf_rate = 4.0  # 4% risk-free rate
            sharpe = (port_ret - rf_rate) / port_vol if port_vol > 0 else 0.0
            active_share = 0.5 * float(np.sum(np.abs(w_t - w_prior))) * 100.0

            frontier.append(
                TurnoverFrontierPoint(
                    turnover_limit_pct=round(t_lim * 100.0, 1),
                    portfolio_expected_return_pct=round(port_ret, 2),
                    portfolio_volatility_pct=round(port_vol, 2),
                    sharpe_ratio=round(sharpe, 2),
                    one_way_turnover_pct=round(turnover * 100.0, 2),
                    estimated_transaction_cost_bps=round(tc_bps, 1),
                    active_share_pct=round(active_share, 2),
                )
            )

        return frontier

    def evaluate_bayesian_portfolio(
        self,
        total_capital: float = 100000.0,
        turnover_cap: float = 0.20,
        force_refresh: bool = False,
    ) -> PortfolioBayesianResult:
        """
        Executes the complete Black-Litterman Bayesian View Blending & Turnover Rebalancer pipeline.
        """
        if self._cached_result and not force_refresh:
            return self._cached_result

        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        now_iso = now.isoformat()

        # 1. Load equilibrium market priors
        symbols, cov_matrix, w_prior, pi_returns, market_map = self.load_equilibrium_prior()
        symbol_to_idx = {s: i for i, s in enumerate(symbols)}

        # 2. Extract subjective Quant Intel views
        P, Q, C, views = self.extract_quant_intel_views(symbols, pi_returns, symbol_to_idx)

        # 3. Solve Black-Litterman posterior returns and covariance
        mu_bl, sigma_bl, w_unc, trace_ratio = self.compute_black_litterman_posterior(
            cov_matrix, pi_returns, P, Q, C
        )

        # 4. Update views with solved posterior expectations
        for v in views:
            if v.view_type == "ABSOLUTE":
                idx = symbol_to_idx[v.symbol]
                v.posterior_expected_return_pct = round(mu_bl[idx] * 100.0, 2)
            elif v.view_type == "RELATIVE" and v.relative_symbol:
                idx1 = symbol_to_idx[v.symbol]
                idx2 = symbol_to_idx[v.relative_symbol]
                v.posterior_expected_return_pct = round((mu_bl[idx1] - mu_bl[idx2]) * 100.0, 2)

        # 5. Turnover-Constrained Rebalancing Optimization
        w_opt, turnover, tc_bps, tc_dollars = self.rebalance_portfolio(
            w_prior=w_prior,
            mu_bl=mu_bl,
            sigma_bl=sigma_bl,
            market_map=market_map,
            symbols=symbols,
            turnover_cap=turnover_cap,
        )

        # Scale transaction cost to user capital
        tc_dollars_scaled = (tc_bps / 10000.0) * total_capital

        # 6. Trace efficient turnover frontier
        frontier = self.generate_turnover_frontier(w_prior, mu_bl, sigma_bl, symbols, market_map)

        # 7. Package allocations dictionary
        allocations: Dict[str, BayesianAssetAllocation] = {}
        for i, sym in enumerate(symbols):
            mkt: Literal["US", "CA"] = "CA" if market_map.get(sym) == "CA" else "US"
            w_p = float(w_prior[i])
            w_u = float(w_unc[i])
            w_c = float(w_opt[i])
            delta_w = (w_c - w_p) * 100.0
            turnover_contrib = 0.5 * abs(w_c - w_p) * 100.0
            marg_utility = float(mu_bl[i] - self.risk_aversion * (sigma_bl @ w_opt)[i])

            allocations[sym] = BayesianAssetAllocation(
                symbol=sym,
                market=mkt,
                prior_weight=round(w_p, 4),
                unconstrained_weight=round(w_u, 4),
                constrained_weight=round(w_c, 4),
                target_capital=round(w_c * total_capital, 2),
                prior_expected_return_pct=round(float(pi_returns[i]) * 100.0, 2),
                posterior_expected_return_pct=round(float(mu_bl[i]) * 100.0, 2),
                delta_weight_pct=round(delta_w, 2),
                turnover_contribution_pct=round(turnover_contrib, 2),
                marginal_utility=round(marg_utility, 4),
            )

        prior_ret = float(w_prior @ pi_returns) * 100.0
        post_ret = float(w_opt @ mu_bl) * 100.0
        prior_vol = math.sqrt(float(w_prior @ cov_matrix @ w_prior)) * 100.0
        post_vol = math.sqrt(float(w_opt @ sigma_bl @ w_opt)) * 100.0

        for disc in BAYESIAN_DISCLAIMERS:
            linter.assert_clean(disc)

        result = PortfolioBayesianResult(
            as_of_date=today_str,
            generated_at=now_iso,
            total_capital=total_capital,
            risk_aversion=self.risk_aversion,
            tau=self.tau,
            views_count=len(views),
            views=views,
            allocations=allocations,
            prior_portfolio_return_pct=round(prior_ret, 2),
            posterior_portfolio_return_pct=round(post_ret, 2),
            prior_portfolio_volatility_pct=round(prior_vol, 2),
            posterior_portfolio_volatility_pct=round(post_vol, 2),
            total_one_way_turnover_pct=round(turnover * 100.0, 2),
            total_transaction_cost_bps=round(tc_bps, 1),
            total_transaction_cost_dollars=round(tc_dollars_scaled, 2),
            estimation_error_trace_ratio=round(trace_ratio, 3),
            turnover_frontier=frontier,
            disclaimers=BAYESIAN_DISCLAIMERS,
        )

        self._cached_result = result
        return result

    def export_feed(self, output_path: Optional[Path] = None, force_refresh: bool = False) -> Path:
        """
        Exports master Black-Litterman Bayesian rebalancing feed to disk.
        """
        if output_path is None:
            output_path = DATA_DIR / "feeds" / "portfolio_bayesian.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        res = self.evaluate_bayesian_portfolio(force_refresh=force_refresh)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(res.model_dump(), f, indent=2)

        return output_path


# Global singleton instance
portfolio_bayesian_engine = PortfolioBayesianEngine()
