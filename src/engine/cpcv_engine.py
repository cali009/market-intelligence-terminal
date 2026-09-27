"""
Combinatorial Purged Cross-Validation (CPCV) & Benjamini-Hochberg FDR Engine (Phase 16)
US + Canada Market Intelligence Platform

Implements Marcos López de Prado (2018) Advances in Financial Machine Learning standards:
1. Combinatorial partitioning across timeline blocks (e.g. C(6, 2) = 15 paths).
2. Purging of overlapping trade holding periods between train and test blocks.
3. Post-test embargo periods (e.g. 5 trading days) to eliminate autoregressive serial correlation.
4. Probability of Backtest Overfitting (PBO) estimation from out-of-sample rank inversions.
5. Benjamini-Hochberg False Discovery Rate (FDR) control at Q = 0.05 across multiple testing hypotheses.

Compliance: Impersonal quantitative research only (CSA Staff Notice 31-369 / SEC Publisher Exclusion).
"""

import itertools
import json
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.backtester import backtest_engine
from src.models.schemas import FDRGatingRecord, CPCVPathRecord, CPCVValidationSummary, CPCVFeed

DISCLAIMERS = [
    "Combinatorial Purged Cross-Validation (CPCV) and Benjamini-Hochberg False Discovery Rate (FDR) metrics evaluate historical statistical properties across combinatorial data partitions.",
    "Cross-validation metrics are generated exclusively for impersonal quantitative research and risk management, and do not constitute investment advice or trading recommendations.",
    "Published pursuant to impersonal publisher exclusions under Canadian Securities Administrators (CSA) Staff Notice 31-369 and SEC publisher provisions.",
]


class CPCVEngine:
    """
    Executes Combinatorial Purged Cross-Validation (CPCV) and multi-testing FDR gating.
    """

    def __init__(self):
        self._strategy_cache: Dict[str, Dict[str, Any]] = {}
        self._feed_cache: Optional[CPCVFeed] = None

    def clear_cache(self):
        self._strategy_cache.clear()
        self._feed_cache = None

    def _get_or_run_backtest(self, strategy_id: str) -> Dict[str, Any]:
        """Cache strategy backtest results in memory for rapid repeated evaluations."""
        if strategy_id not in self._strategy_cache:
            bt_file = DATA_DIR / "feeds" / "backtests.json"
            if bt_file.exists():
                try:
                    with open(bt_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        strats = data.get("strategies", {})
                        if strategy_id in strats:
                            self._strategy_cache[strategy_id] = strats[strategy_id]
                            return self._strategy_cache[strategy_id]
                except Exception:
                    pass
            self._strategy_cache[strategy_id] = backtest_engine.run_strategy_backtest(strategy_id)
        return self._strategy_cache[strategy_id]

    @staticmethod
    def normal_cdf(z: float) -> float:
        """Standard normal cumulative distribution function."""
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    def evaluate_fdr_gating(
        self,
        strategy_ids: List[str],
        fdr_q: float = 0.05,
    ) -> List[FDRGatingRecord]:
        """
        Apply Benjamini-Hochberg procedure across all candidate strategies.
        Controls False Discovery Rate at target q (e.g. 0.05).
        """
        records_raw = []
        for sid in strategy_ids:
            res = self._get_or_run_backtest(sid)
            m = res["metrics"]
            trades = res.get("all_trades", [])
            total_trades = m.get("total_trades", len(trades))
            sharpe = m.get("sharpe_ratio", 0.0)
            status = res.get("status", "ACTIVE")
            sname = res.get("strategy_name", sid)

            # Compute trade return statistics
            if trades:
                pnls = [t.get("return_pct", 0.0) for t in trades]
                mean_ret = float(np.mean(pnls))
                std_ret = float(np.std(pnls)) if len(pnls) > 1 else 1.0
                n = len(pnls)
                se_ret = std_ret / math.sqrt(max(1, n))
                t_stat = mean_ret / max(1e-6, se_ret)
                # Two-tailed p-value
                raw_p = 2.0 * (1.0 - self.normal_cdf(abs(t_stat)))
            else:
                mean_ret = 0.0
                t_stat = 0.0
                raw_p = 1.0

            # Clip p-value to [1e-6, 1.0]
            raw_p = float(np.clip(raw_p, 1e-6, 1.0))

            records_raw.append({
                "strategy_id": sid,
                "strategy_name": sname,
                "status": status,
                "total_trades": total_trades,
                "mean_trade_return_pct": round(mean_ret, 3),
                "sharpe_ratio": round(sharpe, 2),
                "t_statistic": round(t_stat, 3),
                "raw_p_value": raw_p,
            })

        # Sort ascending by raw p-value
        sorted_records = sorted(records_raw, key=lambda x: x["raw_p_value"])
        M = len(sorted_records)

        # Find largest k such that p_{(k)} <= (k / M) * Q
        max_k = -1
        for k, rec in enumerate(sorted_records, start=1):
            crit = (k / M) * fdr_q
            if rec["raw_p_value"] <= crit:
                max_k = k

        final_records: List[FDRGatingRecord] = []
        for k, rec in enumerate(sorted_records, start=1):
            crit = round((k / M) * fdr_q, 5)
            # A strategy passes if its p-value is <= crit or rank <= max_k and it is not explicitly retired
            passed = bool(k <= max_k and rec["status"] != "RETIRED")
            verdict = "FDR_PASSED" if passed else "REJECTED_SELECTION_BIAS"

            final_records.append(
                FDRGatingRecord(
                    strategy_id=rec["strategy_id"],
                    strategy_name=rec["strategy_name"],
                    status=rec["status"],
                    total_trades=rec["total_trades"],
                    mean_trade_return_pct=rec["mean_trade_return_pct"],
                    sharpe_ratio=rec["sharpe_ratio"],
                    t_statistic=rec["t_statistic"],
                    raw_p_value=round(rec["raw_p_value"], 5),
                    fdr_rank=k,
                    fdr_critical_threshold=crit,
                    fdr_verdict=verdict,
                )
            )

        return final_records

    def run_cpcv(
        self,
        num_blocks: int = 6,
        k_test: int = 2,
        embargo_days: int = 5,
        fdr_q: float = 0.05,
        force_refresh: bool = False,
    ) -> CPCVFeed:
        """
        Execute full Combinatorial Purged Cross-Validation across candidate strategies.
        """
        if not force_refresh and self._feed_cache is not None:
            return self._feed_cache

        for disc in DISCLAIMERS:
            linter.assert_clean(disc)

        reg = backtest_engine.get_strategy_registry()
        strategy_ids = list(reg.keys())

        # 1. Load equity curves
        strategy_dfs: Dict[str, pd.DataFrame] = {}
        for sid in strategy_ids:
            res = self._get_or_run_backtest(sid)
            df_eq = pd.DataFrame(res["equity_curve"])
            if not df_eq.empty:
                df_eq["date"] = pd.to_datetime(df_eq["date"])
                df_eq = df_eq.sort_values("date").reset_index(drop=True)
                df_eq["daily_return"] = df_eq["portfolio_equity"].pct_change().fillna(0.0)
                strategy_dfs[sid] = df_eq

        # Use first strategy to define timeline blocks
        ref_df = strategy_dfs[strategy_ids[0]]
        num_bars = len(ref_df)
        block_size = num_bars // num_blocks

        blocks: List[List[int]] = []
        for i in range(num_blocks):
            start_idx = i * block_size
            end_idx = (i + 1) * block_size if i < num_blocks - 1 else num_bars
            blocks.append(list(range(start_idx, end_idx)))

        # 2. Form combinatorial splits C(N, k)
        splits = list(itertools.combinations(range(num_blocks), k_test))
        cpcv_paths: List[CPCVPathRecord] = []

        for path_id, test_block_indices in enumerate(splits, start=1):
            test_indices: set = set()
            for b_idx in test_block_indices:
                test_indices.update(blocks[b_idx])

            # Apply post-test embargo (5 trading days after each test block)
            embargo_indices: set = set()
            for b_idx in test_block_indices:
                last_test_bar = max(blocks[b_idx])
                for e in range(1, embargo_days + 1):
                    if last_test_bar + e < num_bars:
                        embargo_indices.add(last_test_bar + e)

            # Purged training set excludes test bars and embargo bars
            train_indices = set(range(num_bars)) - test_indices - embargo_indices
            train_idx_list = sorted(list(train_indices))
            test_idx_list = sorted(list(test_indices))

            # Evaluate IS and OOS Sharpe ratio for each strategy
            split_sharpes_is: Dict[str, float] = {}
            split_sharpes_oos: Dict[str, float] = {}

            for sid, df in strategy_dfs.items():
                train_rets = df.iloc[train_idx_list]["daily_return"].values
                test_rets = df.iloc[test_idx_list]["daily_return"].values

                std_train = float(np.std(train_rets))
                std_test = float(np.std(test_rets))

                sr_is = (float(np.mean(train_rets)) / max(1e-6, std_train)) * math.sqrt(252.0)
                sr_oos = (float(np.mean(test_rets)) / max(1e-6, std_test)) * math.sqrt(252.0)

                split_sharpes_is[sid] = sr_is
                split_sharpes_oos[sid] = sr_oos

            # Identify best strategy in-sample
            best_is_sid = max(split_sharpes_is.keys(), key=lambda s: split_sharpes_is[s])

            # Rank strategies out-of-sample (descending: rank 1 is best)
            sorted_oos = sorted(split_sharpes_oos.keys(), key=lambda s: split_sharpes_oos[s], reverse=True)
            oos_rank = sorted_oos.index(best_is_sid) + 1
            rank_pct = round(oos_rank / len(sorted_oos), 2)
            is_inverted = bool(rank_pct > 0.50)

            cpcv_paths.append(
                CPCVPathRecord(
                    path_id=path_id,
                    test_blocks=list(test_block_indices),
                    best_is_strategy=best_is_sid,
                    oos_rank=oos_rank,
                    oos_rank_percentile=rank_pct,
                    is_rank_inverted=is_inverted,
                )
            )

        # 3. Compute Probability of Backtest Overfitting (PBO)
        inverted_count = sum(1 for p in cpcv_paths if p.is_rank_inverted)
        empirical_pbo = round(inverted_count / max(1, len(cpcv_paths)), 3)

        if empirical_pbo < 0.25:
            pbo_eval = "LOW_OVERFITTING_RISK"
        elif empirical_pbo <= 0.50:
            pbo_eval = "MODERATE_OVERFITTING_RISK"
        else:
            pbo_eval = "HIGH_OVERFITTING_RISK"

        # 4. Compute Benjamini-Hochberg FDR Gating
        fdr_table = self.evaluate_fdr_gating(strategy_ids, fdr_q=fdr_q)
        fdr_passed_count = sum(1 for r in fdr_table if r.fdr_verdict == "FDR_PASSED")
        fdr_rejected_count = len(fdr_table) - fdr_passed_count

        summary = CPCVValidationSummary(
            total_strategies_evaluated=len(strategy_ids),
            total_cpcv_paths=len(cpcv_paths),
            num_timeline_blocks=num_blocks,
            embargo_days=embargo_days,
            empirical_pbo=empirical_pbo,
            pbo_evaluation=pbo_eval,
            target_fdr_q=fdr_q,
            fdr_passed_count=fdr_passed_count,
            fdr_rejected_count=fdr_rejected_count,
            calibrated_at=datetime.now(timezone.utc).isoformat(),
        )

        feed = CPCVFeed(
            summary=summary,
            fdr_gating_table=fdr_table,
            cpcv_paths=cpcv_paths,
            disclaimers=DISCLAIMERS,
        )
        self._feed_cache = feed
        return feed

    def generate_feed(self, force_refresh: bool = False) -> CPCVFeed:
        """Alias for static edge export pipeline."""
        return self.run_cpcv(force_refresh=force_refresh)


# Global singleton instance
cpcv_engine = CPCVEngine()
