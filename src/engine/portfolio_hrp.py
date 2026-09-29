"""
Hierarchical Risk Parity (HRP) Portfolio Engine (Phase 26.1)
US + Canada Dual-Market Intelligence Platform

Implements Marcos López de Prado (2016) Hierarchical Risk Parity:
1. Tree Clustering: Non-Euclidean correlation distance matrix transform d_{i,j} = sqrt(0.5 * (1 - rho_{i,j})).
2. Quasi-Diagonalization (Seriation): Topological dendrogram ordering without matrix inversion.
3. Recursive Bisection: Top-down inverse-variance cluster variance weighting.
4. Risk Budgeting & Decomposition: Euler Marginal Contribution to Risk (MCR) & component risk shares.
5. Benchmark Parity Analytics: Evaluates HRP against Equal-Weight (EW) and Inverse-Variance (IVP).
6. Impersonal Decision-Support Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Literal
import numpy as np
import pandas as pd
import scipy.cluster.hierarchy as sch
import scipy.spatial.distance as ssd

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.data.db import db
from src.models.schemas import (
    HrpAssetAllocation,
    HrpAllocationResult,
)

HRP_DISCLAIMERS = [
    "Hierarchical Risk Parity (HRP) portfolio models, correlation clustering, and risk budgeting allocations represent quantitative simulations provided for impersonal decision-support research.",
    "Does not constitute personalized portfolio management, fiduciary advice, or financial planning counsel under CSA Staff Notice 31-369 and SEC rules.",
    "Historical correlations, covariances, and asset returns are non-stationary and subject to structural regime transitions in live markets."
]


class HrpPortfolioEngine:
    """
    Constructs robust, matrix-inversion-free risk parity allocations across
    the dual-market equity universe using graph-theoretic tree clustering.
    """

    def __init__(self):
        self._cached_result: Optional[HrpAllocationResult] = None

    def load_universe_returns(self, min_bars: int = 120) -> Tuple[pd.DataFrame, Dict[str, str]]:
        """
        Loads daily closing prices for all active securities and calculates log returns.
        Returns (returns_df, symbol_to_country_map).
        """
        sec_rows = db.execute_query(
            "SELECT security_id, symbol, country, exchange FROM security ORDER BY symbol;"
        )
        if not sec_rows:
            raise ValueError("No securities found in database.")

        symbol_to_country = {row["symbol"]: row["country"] for row in sec_rows}
        sec_id_to_symbol = {row["security_id"]: row["symbol"] for row in sec_rows}

        bars_rows = db.execute_query(
            "SELECT security_id, trading_date, close FROM bar_1d ORDER BY trading_date ASC;"
        )
        if not bars_rows:
            raise ValueError("No historical daily bars found in database.")

        df_bars = pd.DataFrame(bars_rows)
        df_bars["symbol"] = df_bars["security_id"].map(sec_id_to_symbol)
        df_bars = df_bars.dropna(subset=["symbol"])

        # Pivot to date x symbol
        df_pivot = df_bars.pivot(index="trading_date", columns="symbol", values="close")
        df_pivot = df_pivot.ffill().bfill()

        # Compute log returns
        returns_df = np.log(df_pivot / df_pivot.shift(1)).dropna()

        # Keep columns with sufficient observations
        valid_cols = [c for c in returns_df.columns if len(returns_df[c].dropna()) >= min_bars]
        returns_df = returns_df[valid_cols]

        return returns_df, symbol_to_country

    @staticmethod
    def compute_correlation_distance(returns: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """
        Computes Pearson correlation matrix C and transforms to correlation distance matrix D:
        d_{i,j} = sqrt(0.5 * (1 - rho_{i,j})) in [0, 1].
        """
        corr = returns.corr().values
        # Handle tiny floating point noise ensuring diagonal is strictly 1.0 and bounds [-1, 1]
        np.fill_diagonal(corr, 1.0)
        corr = np.clip(corr, -1.0, 1.0)

        dist = np.sqrt(0.5 * (1.0 - corr))
        np.fill_diagonal(dist, 0.0)
        return corr, dist

    @staticmethod
    def build_hierarchical_tree(dist_matrix: np.ndarray, method: str = "single") -> np.ndarray:
        """
        Builds agglomerative hierarchical clustering linkage matrix from distance matrix.
        """
        # Compress square distance matrix to condensed vector form
        condensed_dist = ssd.squareform(dist_matrix, checks=False)
        linkage_matrix = sch.linkage(condensed_dist, method=method)
        return linkage_matrix

    @staticmethod
    def quasi_diagonalize(linkage_matrix: np.ndarray) -> List[int]:
        """
        Recovers sorted quasi-diagonal leaf order from hierarchical tree linkage.
        """
        root_node = sch.to_tree(linkage_matrix)
        if root_node is None:
            return []
        return root_node.pre_order()

    @staticmethod
    def recursive_bisection(cov_matrix: np.ndarray, ordered_indices: List[int]) -> np.ndarray:
        """
        Allocates inverse-variance capital top-down across hierarchical tree clusters.
        """
        num_assets = len(ordered_indices)
        weights = pd.Series(1.0, index=ordered_indices)
        clusters = [ordered_indices]

        while len(clusters) > 0:
            new_clusters = []
            for cluster in clusters:
                if len(cluster) <= 1:
                    continue
                mid = len(cluster) // 2
                c1 = cluster[:mid]
                c2 = cluster[mid:]

                # Compute variance of cluster 1
                cov1 = cov_matrix[np.ix_(c1, c1)]
                ivp1 = 1.0 / np.diag(cov1)
                ivp1 /= np.sum(ivp1)
                var1 = float(np.dot(ivp1, np.dot(cov1, ivp1)))

                # Compute variance of cluster 2
                cov2 = cov_matrix[np.ix_(c2, c2)]
                ivp2 = 1.0 / np.diag(cov2)
                ivp2 /= np.sum(ivp2)
                var2 = float(np.dot(ivp2, np.dot(cov2, ivp2)))

                # Cluster allocation factor (inverse variance of clusters)
                alpha1 = 1.0 - (var1 / (var1 + var2))
                alpha2 = 1.0 - alpha1

                # Scale child cluster weights
                weights[c1] *= alpha1
                weights[c2] *= alpha2

                if len(c1) > 1:
                    new_clusters.append(c1)
                if len(c2) > 1:
                    new_clusters.append(c2)

            clusters = new_clusters

        # Return weights aligned by original index 0..N-1
        final_weights = np.zeros(num_assets)
        for orig_idx, w in weights.items():
            final_weights[orig_idx] = w

        final_weights /= np.sum(final_weights)
        return final_weights

    @staticmethod
    def compute_risk_metrics(weights: np.ndarray, cov_matrix: np.ndarray) -> Tuple[float, np.ndarray, np.ndarray]:
        """
        Calculates annualized portfolio volatility, marginal contributions to risk (MCR),
        and percentage risk contributions using Euler's decomposition theorem.
        """
        ann_cov = cov_matrix * 252.0
        port_var = float(np.dot(weights, np.dot(ann_cov, weights)))
        port_vol = math.sqrt(max(1e-8, port_var))

        # Marginal Contribution to Risk: MCR = (Cov * w) / sigma_p
        marginal_risk = np.dot(ann_cov, weights) / port_vol

        # Risk Share: (w_i * MCR_i) / sigma_p
        risk_shares = (weights * marginal_risk) / port_vol

        return round(port_vol, 4), marginal_risk, risk_shares

    def compute_benchmarks(
        self,
        symbols: List[str],
        cov_matrix: np.ndarray,
        returns: pd.DataFrame
    ) -> Dict[str, Dict[str, float]]:
        """
        Computes comparative risk metrics for Equal-Weight (EW) and Inverse-Variance (IVP).
        """
        n = len(symbols)
        ann_cov = cov_matrix * 252.0
        stds = np.sqrt(np.diag(ann_cov))

        # 1. Equal-Weight (EW)
        w_ew = np.ones(n) / n
        var_ew = float(np.dot(w_ew, np.dot(ann_cov, w_ew)))
        vol_ew = math.sqrt(max(1e-8, var_ew))
        div_ratio_ew = float(np.dot(w_ew, stds) / vol_ew) if vol_ew > 0 else 1.0
        n_eff_ew = 1.0 / float(np.sum(w_ew ** 2))

        # 2. Inverse-Variance (IVP)
        ivp_raw = 1.0 / np.diag(ann_cov)
        w_ivp = ivp_raw / np.sum(ivp_raw)
        var_ivp = float(np.dot(w_ivp, np.dot(ann_cov, w_ivp)))
        vol_ivp = math.sqrt(max(1e-8, var_ivp))
        div_ratio_ivp = float(np.dot(w_ivp, stds) / vol_ivp) if vol_ivp > 0 else 1.0
        n_eff_ivp = 1.0 / float(np.sum(w_ivp ** 2))

        return {
            "EQUAL_WEIGHT": {
                "portfolio_volatility_annualized": round(vol_ew, 4),
                "diversification_ratio": round(div_ratio_ew, 2),
                "effective_constituents": round(n_eff_ew, 1),
                "max_weight_pct": round(float(np.max(w_ew) * 100.0), 2),
            },
            "INVERSE_VARIANCE": {
                "portfolio_volatility_annualized": round(vol_ivp, 4),
                "diversification_ratio": round(div_ratio_ivp, 2),
                "effective_constituents": round(n_eff_ivp, 1),
                "max_weight_pct": round(float(np.max(w_ivp) * 100.0), 2),
            }
        }

    def generate_hrp_allocation(
        self,
        total_capital: float = 100000.0,
        force_refresh: bool = False
    ) -> HrpAllocationResult:
        """
        Executes end-to-end Hierarchical Risk Parity allocation over the universe.
        """
        if self._cached_result and not force_refresh:
            return self._cached_result

        returns_df, symbol_to_country = self.load_universe_returns()
        symbols = list(returns_df.columns)
        n = len(symbols)

        cov_matrix = returns_df.cov().values
        corr_matrix, dist_matrix = self.compute_correlation_distance(returns_df)
        linkage_matrix = self.build_hierarchical_tree(dist_matrix, method="single")
        ordered_indices = self.quasi_diagonalize(linkage_matrix)
        ordered_symbols = [symbols[i] for i in ordered_indices]

        # Allocate weights via recursive bisection
        hrp_weights = self.recursive_bisection(cov_matrix, ordered_indices)

        # Risk metrics
        port_vol, mcr, risk_shares = self.compute_risk_metrics(hrp_weights, cov_matrix)

        # Asset-level allocations
        ann_stds = np.sqrt(np.diag(cov_matrix * 252.0))
        allocations: Dict[str, HrpAssetAllocation] = {}

        for i, sym in enumerate(symbols):
            w = float(hrp_weights[i])
            country = symbol_to_country.get(sym, "US")
            allocations[sym] = HrpAssetAllocation(
                symbol=sym,
                country=country,  # type: ignore
                weight=round(w, 4),
                dollar_allocation=round(w * total_capital, 2),
                volatility_annualized=round(float(ann_stds[i]), 4),
                marginal_risk_contribution=round(float(mcr[i]), 4),
                risk_share_pct=round(float(risk_shares[i] * 100.0), 2),
            )

        # Diversification metrics
        div_ratio_hrp = float(np.dot(hrp_weights, ann_stds) / port_vol) if port_vol > 0 else 1.0
        n_eff_hrp = 1.0 / float(np.sum(hrp_weights ** 2))

        # Benchmark comparisons
        benchmarks = self.compute_benchmarks(symbols, cov_matrix, returns_df)
        benchmarks["HIERARCHICAL_RISK_PARITY"] = {
            "portfolio_volatility_annualized": round(port_vol, 4),
            "diversification_ratio": round(div_ratio_hrp, 2),
            "effective_constituents": round(n_eff_hrp, 1),
            "max_weight_pct": round(float(np.max(hrp_weights) * 100.0), 2),
        }

        # Serialized linkage for dendrogram visualization: [[left, right, dist, count], ...]
        serializable_linkage = [
            [int(row[0]), int(row[1]), round(float(row[2]), 4), int(row[3])]
            for row in linkage_matrix
        ]

        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        now_iso = now.isoformat()

        # Compliance assertion on all disclaimers
        for disc in HRP_DISCLAIMERS:
            linter.assert_clean(disc)

        result = HrpAllocationResult(
            as_of_date=today_str,
            generated_at=now_iso,
            universe_size=n,
            total_capital=total_capital,
            allocations=allocations,
            ordered_symbols=ordered_symbols,
            portfolio_volatility_annualized=round(port_vol, 4),
            effective_constituents=round(n_eff_hrp, 1),
            diversification_ratio=round(div_ratio_hrp, 2),
            benchmark_comparisons=benchmarks,
            cluster_linkage=serializable_linkage,
            disclaimers=HRP_DISCLAIMERS,
        )

        self._cached_result = result
        return result

    def export_feed(self, output_dir: Optional[Path] = None, force_refresh: bool = False) -> Path:
        """
        Exports the master HRP portfolio allocation feed to JSON.
        """
        if output_dir is None:
            output_dir = DATA_DIR / "feeds"
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "portfolio_hrp.json"

        feed = self.generate_hrp_allocation(force_refresh=force_refresh)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(feed.to_dict(), f, indent=2)

        return out_file


# Global singleton instance
hrp_portfolio_engine = HrpPortfolioEngine()
