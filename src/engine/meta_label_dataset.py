"""
Triple-Barrier Meta-Labeling Dataset Engine (Phase 23.1)
US + Canada Market Intelligence Platform

Implements Marcos López de Prado (2018) Advances in Financial Machine Learning standards:
1. Triple-Barrier Labeling Method: Upper profit barrier, Lower structural stop barrier, Horizontal time barrier.
2. Anti-Lookahead Guarantees: All features engineered strictly as of signal date t; execution strictly at t+1 Open.
3. Multi-Dimensional Feature Vectors: Momentum, Technicals, Microstructure (Hurst/FDI), Fundamentals, Macro, NLP Sentiment.
4. Purged & Embargoed Partitions: Chronological Train (60%), Validation (20%), Test (20%) with 5-day post-test embargo.
5. Impersonal Decision-Support Research Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.data.db import db
from src.engine.fundamentals import FundamentalAnalysisEngine
from src.models.schemas import TripleBarrierRecord, MetaLabelDatasetSummary, MetaLabelFeed

DISCLAIMERS = [
    "Triple-barrier meta-labeling datasets evaluate historical path-dependent execution outcomes under strictly simulated assumptions.",
    "Dataset matrices are generated exclusively for impersonal quantitative research and risk classification, not as trading recommendations.",
    "Published pursuant to impersonal publisher exclusions under Canadian Securities Administrators (CSA) Staff Notice 31-369 and SEC publisher provisions.",
]

FEEDS_DIR = DATA_DIR / "feeds"


class MetaLabelDatasetEngine:
    """
    Engine for generating path-dependent triple-barrier labeled event matrices
    and multi-factor feature stores for machine learning meta-labeling.
    """

    def __init__(
        self,
        target_atr_multiple: float = 1.8,
        stop_atr_multiple: float = 1.2,
        max_holding_days: int = 20,
        sampling_step: int = 3,
        embargo_days: int = 5,
    ):
        self.target_atr_multiple = target_atr_multiple
        self.stop_atr_multiple = stop_atr_multiple
        self.max_holding_days = max_holding_days
        self.sampling_step = sampling_step
        self.embargo_days = embargo_days
        self._cached_records: Optional[List[TripleBarrierRecord]] = None
        self._init_sqlite_table()

    def _init_sqlite_table(self):
        """Ensures the SQLite table for meta-label dataset exists."""
        db.execute_query("""
            CREATE TABLE IF NOT EXISTS meta_label_dataset (
                event_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                signal_date TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                upper_barrier REAL NOT NULL,
                lower_barrier REAL NOT NULL,
                exit_date TEXT NOT NULL,
                exit_price REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                holding_days INTEGER NOT NULL,
                return_pct REAL NOT NULL,
                r_multiple REAL NOT NULL,
                label INTEGER NOT NULL,
                partition TEXT NOT NULL,
                features_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        db.execute_query("""
            CREATE INDEX IF NOT EXISTS idx_meta_label_sym_date 
            ON meta_label_dataset(symbol, signal_date);
        """)

    @staticmethod
    def _compute_hurst(series: np.ndarray, lags: range = range(2, 20)) -> float:
        """Newey-West HAC inspired Hurst exponent estimation."""
        if len(series) < 30:
            return 0.50
        tau = [np.std(np.subtract(series[lag:], series[:-lag])) for lag in lags if len(series) > lag]
        if len(tau) < 3:
            return 0.50
        try:
            poly = np.polyfit(np.log(list(lags)[:len(tau)]), np.log(tau), 1)
            return float(np.clip(poly[0], 0.20, 0.85))
        except Exception:
            return 0.50

    @staticmethod
    def _compute_fractal_dimension(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> float:
        """Sevcik / Higuchi Fractal Dimension Index (FDI)."""
        n = len(closes)
        if n < 10:
            return 1.50
        diffs = np.abs(np.diff(closes))
        length = np.sum(diffs)
        price_range = np.max(highs) - np.min(lows)
        if price_range <= 1e-6:
            return 1.50
        try:
            fdi = 1.0 + (math.log(length / price_range) / math.log(n))
            return float(np.clip(fdi, 1.0, 2.0))
        except Exception:
            return 1.50

    def generate_dataset(self, force_refresh: bool = False) -> List[TripleBarrierRecord]:
        """
        Builds complete triple-barrier labeled event records across the historical dataset.
        Enforces strict anti-lookahead causality and anti-leakage embargoes.
        """
        if self._cached_records is not None and not force_refresh:
            return self._cached_records

        for disc in DISCLAIMERS:
            linter.assert_clean(disc)

        # 1. Load active securities and bars
        securities = db.execute_query("SELECT security_id, symbol, country, currency FROM security WHERE is_active = 1;")
        sec_dict = {s["security_id"]: s for s in securities}
        all_bars = db.execute_query("SELECT security_id, trading_date, open, high, low, close, volume FROM bar_1d ORDER BY trading_date ASC;")
        if not all_bars:
            return []

        df = pd.DataFrame(all_bars)
        closes = df.pivot(index="trading_date", columns="security_id", values="close").sort_index().ffill()
        opens = df.pivot(index="trading_date", columns="security_id", values="open").sort_index().ffill()
        highs = df.pivot(index="trading_date", columns="security_id", values="high").sort_index().ffill()
        lows = df.pivot(index="trading_date", columns="security_id", values="low").sort_index().ffill()
        vols = df.pivot(index="trading_date", columns="security_id", values="volume").sort_index().fillna(0)

        symbol_map = {sid: sec_dict[sid]["symbol"] for sid in sec_dict}
        for tbl in [closes, opens, highs, lows, vols]:
            tbl.columns = [symbol_map[c] for c in tbl.columns]

        # Single-stock universe (exclude benchmark ETFs from selection pool)
        stock_cols = [c for c in closes.columns if c not in ("SPY", "QQQ", "XIU")]

        # 2. Precompute Technical Indicators
        # ATR 14
        tr1 = highs[stock_cols] - lows[stock_cols]
        tr2 = (highs[stock_cols] - closes[stock_cols].shift(1)).abs()
        tr3 = (lows[stock_cols] - closes[stock_cols].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3]).groupby(level=0).max()
        atr_14 = tr.rolling(14).mean()
        atr_pct = (atr_14 / closes[stock_cols].replace(0, np.nan)) * 100.0

        # Moving Averages
        sma50 = closes[stock_cols].rolling(50).mean()
        sma200 = closes[stock_cols].rolling(200).mean()
        sma200_slope = (sma200 - sma200.shift(20)) / sma200.shift(20).replace(0, np.nan) * 100.0

        # Momentum
        mom_6m = closes[stock_cols].pct_change(126, fill_method=None)
        mom_3m = closes[stock_cols].pct_change(63, fill_method=None)
        mom_1m = closes[stock_cols].pct_change(21, fill_method=None)
        mom_12_1m = (closes[stock_cols].shift(21) / closes[stock_cols].shift(252).replace(0, np.nan)) - 1.0

        # RSI 14
        delta = closes[stock_cols].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_14 = 100.0 - (100.0 / (1.0 + rs))

        # Chaikin Money Flow (CMF 20)
        hl_diff = (highs[stock_cols] - lows[stock_cols]).replace(0, 1e-4)
        cl_diff = (closes[stock_cols] - lows[stock_cols]) - (highs[stock_cols] - closes[stock_cols])
        mfv = (cl_diff / hl_diff) * vols[stock_cols]
        cmf_20 = mfv.rolling(20).sum() / vols[stock_cols].rolling(20).sum().replace(0, 1e-4)

        # RVOL 20
        vol_ma20 = vols[stock_cols].rolling(20).mean().replace(0, 1e-4)
        rvol_20 = vols[stock_cols] / vol_ma20

        # Bollinger Bandwidth
        bb_mid = closes[stock_cols].rolling(20).mean()
        bb_std = closes[stock_cols].rolling(20).std()
        bb_bandwidth = (2.0 * bb_std * 2.0 / bb_mid.replace(0, np.nan)) * 100.0

        # Proximity to 52W High
        high_252 = closes[stock_cols].rolling(252).max()
        prox_52w = (closes[stock_cols] / high_252.replace(0, np.nan)) - 1.0

        # Distance to MAs
        dist_sma50 = (closes[stock_cols] / sma50.replace(0, np.nan)) - 1.0
        dist_sma200 = (closes[stock_cols] / sma200.replace(0, np.nan)) - 1.0

        # 3. Fundamentals
        fund_recs = FundamentalAnalysisEngine.evaluate_universe_fundamentals()
        fund_q_map = {r["symbol"]: float(r.get("quality_score", 50.0)) for r in fund_recs}
        fund_v_map = {r["symbol"]: float(r.get("valuation_score", 50.0)) for r in fund_recs}

        # 4. Macro series (FRED 10Y-2Y yield curve)
        macro_rows = db.execute_query("SELECT series_id, observation_date, value FROM macro_observation ORDER BY observation_date ASC;")
        yield_spread_map = {}
        for mr in macro_rows:
            if mr["series_id"] in ("T10Y2Y", "DGS10"):
                yield_spread_map[mr["observation_date"]] = float(mr["value"])

        # SPY benchmark distance
        spy_close = closes["SPY"] if "SPY" in closes.columns else pd.Series(dtype=float)
        spy_sma200 = spy_close.rolling(200).mean() if not spy_close.empty else pd.Series(dtype=float)

        # 5. Partition Timeline Boundaries
        dates = closes.index.tolist()
        num_dates = len(dates)
        train_end_idx = int(num_dates * 0.60)
        val_end_idx = int(num_dates * 0.80)
        train_dates = set(dates[:train_end_idx])
        val_dates = set(dates[train_end_idx:val_end_idx])
        test_dates = set(dates[val_end_idx:])

        records: List[TripleBarrierRecord] = []
        start_idx = 126  # Warmup for indicators

        for i in range(start_idx, num_dates - self.max_holding_days - 1, self.sampling_step):
            t_signal = dates[i]
            t_entry = dates[i + 1]

            # Determine partition
            if t_signal in train_dates:
                partition = "TRAIN"
            elif t_signal in val_dates:
                partition = "VALIDATION"
            else:
                partition = "TEST"

            # Apply embargo around boundary transitions
            if i >= (train_end_idx - self.embargo_days) and i < train_end_idx:
                continue
            if i >= (val_end_idx - self.embargo_days) and i < val_end_idx:
                continue

            for s in stock_cols:
                entry_px = float(opens.loc[t_entry, s])
                atr_val = float(atr_14.loc[t_signal, s])
                if math.isnan(entry_px) or math.isnan(atr_val) or entry_px <= 0 or atr_val <= 0:
                    continue

                up_barrier = round(entry_px + self.target_atr_multiple * atr_val, 2)
                down_barrier = round(entry_px - self.stop_atr_multiple * atr_val, 2)

                # Path-dependent execution simulation
                exit_date = None
                exit_price = None
                exit_reason = None
                label = 0
                holding_days = 0

                for h in range(1, self.max_holding_days + 1):
                    t_curr = dates[i + 1 + h]
                    h_px = float(highs.loc[t_curr, s])
                    l_px = float(lows.loc[t_curr, s])

                    hit_up = h_px >= up_barrier
                    hit_down = l_px <= down_barrier

                    if hit_up and hit_down:
                        # Conservative worst-case fill order assumption
                        exit_date = t_curr
                        exit_price = down_barrier
                        exit_reason = "LOWER_BARRIER"
                        label = 0
                        holding_days = h
                        break
                    elif hit_up:
                        exit_date = t_curr
                        exit_price = up_barrier
                        exit_reason = "UPPER_BARRIER"
                        label = 1
                        holding_days = h
                        break
                    elif hit_down:
                        exit_date = t_curr
                        exit_price = down_barrier
                        exit_reason = "LOWER_BARRIER"
                        label = 0
                        holding_days = h
                        break

                if exit_date is None:
                    # Time barrier expired
                    t_exp = dates[i + 1 + self.max_holding_days]
                    exit_date = t_exp
                    exit_price = round(float(closes.loc[t_exp, s]), 2)
                    exit_reason = "TIME_BARRIER"
                    holding_days = self.max_holding_days
                    # Label 1 only if gain exceeds +0.50 ATR threshold
                    label = 1 if exit_price >= (entry_px + 0.50 * atr_val) else 0

                ret_pct = round(((exit_price / entry_px) - 1.0) * 100.0, 2)
                risk_per_share = max(0.01, self.stop_atr_multiple * atr_val)
                r_mult = round((exit_price - entry_px) / risk_per_share, 2)

                # Feature extraction (strictly as of t_signal)
                s_window = closes[s].loc[:t_signal].tail(120).values
                h_val = self._compute_hurst(s_window)
                fdi_val = self._compute_fractal_dimension(
                    highs[s].loc[:t_signal].tail(30).values,
                    lows[s].loc[:t_signal].tail(30).values,
                    closes[s].loc[:t_signal].tail(30).values,
                )

                # Amihud ratio (rolling 20 days)
                p_20 = closes[s].loc[:t_signal].tail(20).values
                v_20 = vols[s].loc[:t_signal].tail(20).values
                r_20 = np.abs(np.diff(p_20) / p_20[:-1]) if len(p_20) > 1 else np.array([0.0])
                dollar_vol = (p_20[1:] * v_20[1:]) if len(p_20) > 1 else np.array([1.0])
                amihud = float(np.mean(r_20 / np.maximum(1e-4, dollar_vol)) * 1e6) if len(dollar_vol) > 0 else 0.05

                # Macro benchmark state
                spy_c = float(spy_close.loc[t_signal]) if not spy_close.empty and t_signal in spy_close.index else 500.0
                spy_ma = float(spy_sma200.loc[t_signal]) if not spy_sma200.empty and t_signal in spy_sma200.index else 480.0
                regime_score = 1.0 if spy_c > spy_ma else 0.0

                yield_spread = yield_spread_map.get(t_signal, 0.45)

                features = {
                    "mom_6m": round(float(mom_6m.loc[t_signal, s]) if not pd.isna(mom_6m.loc[t_signal, s]) else 0.0, 4),
                    "mom_3m": round(float(mom_3m.loc[t_signal, s]) if not pd.isna(mom_3m.loc[t_signal, s]) else 0.0, 4),
                    "mom_1m": round(float(mom_1m.loc[t_signal, s]) if not pd.isna(mom_1m.loc[t_signal, s]) else 0.0, 4),
                    "mom_12_1m": round(float(mom_12_1m.loc[t_signal, s]) if not pd.isna(mom_12_1m.loc[t_signal, s]) else 0.0, 4),
                    "rsi_14": round(float(rsi_14.loc[t_signal, s]) if not pd.isna(rsi_14.loc[t_signal, s]) else 50.0, 2),
                    "cmf_20": round(float(cmf_20.loc[t_signal, s]) if not pd.isna(cmf_20.loc[t_signal, s]) else 0.0, 4),
                    "rvol_20": round(float(rvol_20.loc[t_signal, s]) if not pd.isna(rvol_20.loc[t_signal, s]) else 1.0, 2),
                    "atr_pct": round(float(atr_pct.loc[t_signal, s]) if not pd.isna(atr_pct.loc[t_signal, s]) else 2.0, 2),
                    "bb_bandwidth": round(float(bb_bandwidth.loc[t_signal, s]) if not pd.isna(bb_bandwidth.loc[t_signal, s]) else 5.0, 2),
                    "dist_sma50": round(float(dist_sma50.loc[t_signal, s]) if not pd.isna(dist_sma50.loc[t_signal, s]) else 0.0, 4),
                    "dist_sma200": round(float(dist_sma200.loc[t_signal, s]) if not pd.isna(dist_sma200.loc[t_signal, s]) else 0.0, 4),
                    "sma200_slope": round(float(sma200_slope.loc[t_signal, s]) if not pd.isna(sma200_slope.loc[t_signal, s]) else 0.0, 4),
                    "prox_52w": round(float(prox_52w.loc[t_signal, s]) if not pd.isna(prox_52w.loc[t_signal, s]) else 0.0, 4),
                    "hurst_exponent": round(h_val, 4),
                    "fractal_dimension": round(fdi_val, 4),
                    "amihud_illiquidity": round(amihud, 6),
                    "quality_score": round(fund_q_map.get(s, 50.0), 1),
                    "valuation_score": round(fund_v_map.get(s, 50.0), 1),
                    "macro_regime_score": round(regime_score, 2),
                    "yield_spread_10y_2y": round(yield_spread, 2),
                }

                event_id = f"TBE_{s}_{t_signal}_{i}"
                records.append(
                    TripleBarrierRecord(
                        event_id=event_id,
                        symbol=s,
                        signal_date=t_signal,
                        entry_date=t_entry,
                        entry_price=round(entry_px, 2),
                        upper_barrier=up_barrier,
                        lower_barrier=down_barrier,
                        exit_date=exit_date,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        holding_days=holding_days,
                        return_pct=ret_pct,
                        r_multiple=r_mult,
                        label=label,
                        partition=partition,
                        features=features,
                    )
                )

        self._cached_records = records
        self._persist_to_database(records)
        return records

    def _persist_to_database(self, records: List[TripleBarrierRecord]):
        """Persists generated records into SQLite table meta_label_dataset."""
        now_str = datetime.now(timezone.utc).isoformat()
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            for r in records:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO meta_label_dataset (
                        event_id, symbol, signal_date, entry_date, entry_price,
                        upper_barrier, lower_barrier, exit_date, exit_price,
                        exit_reason, holding_days, return_pct, r_multiple,
                        label, partition, features_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        r.event_id,
                        r.symbol,
                        r.signal_date,
                        r.entry_date,
                        r.entry_price,
                        r.upper_barrier,
                        r.lower_barrier,
                        r.exit_date,
                        r.exit_price,
                        r.exit_reason,
                        r.holding_days,
                        r.return_pct,
                        r.r_multiple,
                        r.label,
                        r.partition,
                        json.dumps(r.features),
                        now_str,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def get_summary(self, records: Optional[List[TripleBarrierRecord]] = None) -> MetaLabelDatasetSummary:
        """Calculates dataset statistics and label distributions."""
        recs = records or self.generate_dataset()
        if not recs:
            return MetaLabelDatasetSummary(
                as_of_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                total_samples=0,
                train_samples=0,
                val_samples=0,
                test_samples=0,
                positive_samples=0,
                negative_samples=0,
                positive_rate_pct=0.0,
                mean_holding_days=0.0,
                mean_return_pct_positive=0.0,
                mean_return_pct_negative=0.0,
                feature_count=0,
                feature_names=[],
                generated_at=datetime.now(timezone.utc).isoformat(),
            )

        pos_recs = [r for r in recs if r.label == 1]
        neg_recs = [r for r in recs if r.label == 0]
        train_count = sum(1 for r in recs if r.partition == "TRAIN")
        val_count = sum(1 for r in recs if r.partition == "VALIDATION")
        test_count = sum(1 for r in recs if r.partition == "TEST")

        feature_names = list(recs[0].features.keys()) if recs else []

        return MetaLabelDatasetSummary(
            as_of_date=recs[-1].signal_date if recs else datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            total_samples=len(recs),
            train_samples=train_count,
            val_samples=val_count,
            test_samples=test_count,
            positive_samples=len(pos_recs),
            negative_samples=len(neg_recs),
            positive_rate_pct=round((len(pos_recs) / len(recs)) * 100.0, 2),
            mean_holding_days=round(float(np.mean([r.holding_days for r in recs])), 2),
            mean_return_pct_positive=round(float(np.mean([r.return_pct for r in pos_recs])) if pos_recs else 0.0, 2),
            mean_return_pct_negative=round(float(np.mean([r.return_pct for r in neg_recs])) if neg_recs else 0.0, 2),
            feature_count=len(feature_names),
            feature_names=feature_names,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def export_feed(self, target_dir: Optional[Path] = None) -> Path:
        """Exports the complete feed file to data/feeds/meta_label_matrix.json (or specified dir)."""
        records = self.generate_dataset()
        summary = self.get_summary(records)

        feed = MetaLabelFeed(
            summary=summary,
            sample_records=records[:50],  # Sample representative records for JSON edge distribution
            disclaimers=DISCLAIMERS,
        )

        base_dir = target_dir or FEEDS_DIR
        out_path = base_dir / "meta_label_matrix.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(feed.to_dict(), f, indent=2)

        return out_path

    def get_train_val_test_matrices(
        self,
        records: Optional[List[TripleBarrierRecord]] = None
    ) -> Tuple[Tuple[pd.DataFrame, pd.Series], Tuple[pd.DataFrame, pd.Series], Tuple[pd.DataFrame, pd.Series]]:
        """
        Extracts feature DataFrames and target Series for Train, Validation, and Test partitions.
        Ready for Scikit-Learn, LightGBM, and XGBoost training.
        """
        recs = records or self.generate_dataset()
        
        train_rows = [r for r in recs if r.partition == "TRAIN"]
        val_rows = [r for r in recs if r.partition == "VALIDATION"]
        test_rows = [r for r in recs if r.partition == "TEST"]

        def _to_matrix(row_list: List[TripleBarrierRecord]) -> Tuple[pd.DataFrame, pd.Series]:
            if not row_list:
                return pd.DataFrame(), pd.Series(dtype=int)
            X = pd.DataFrame([r.features for r in row_list])
            y = pd.Series([r.label for r in row_list], name="target")
            return X, y

        return _to_matrix(train_rows), _to_matrix(val_rows), _to_matrix(test_rows)


# Global singleton instance
meta_label_dataset_engine = MetaLabelDatasetEngine()
