"""
Vectorized Technical Feature & Indicator Library
US + Canada Market Intelligence Platform
Computes Trend Structure, Relative Strength, Setup Geometry, and Volume Participation.
"""

from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


class TechnicalAnalysisEngine:
    """
    Computes deterministic technical features from canonical OHLCV bars.
    Requires at least 30 bars; optimal with 200+ bars for 200DMA.
    """

    @staticmethod
    def compute_all_features(df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute full suite of technical indicators on a DataFrame sorted ascending by trading_date.
        Expects columns: ['open', 'high', 'low', 'close', 'volume', 'adjusted_close']
        """
        if len(df) < 15:
            raise ValueError(f"Insufficient bars for technical analysis: {len(df)} bars (min 15 required)")

        out = df.copy()

        # 1. Moving Averages
        out["sma_20"] = out["close"].rolling(window=20, min_periods=5).mean()
        out["sma_50"] = out["close"].rolling(window=50, min_periods=10).mean()
        out["sma_200"] = out["close"].rolling(window=200, min_periods=20).mean()

        out["ema_8"] = out["close"].ewm(span=8, adjust=False).mean()
        out["ema_21"] = out["close"].ewm(span=21, adjust=False).mean()

        # 200DMA Slope over last 20 sessions (annualized % slope)
        if "sma_200" in out.columns:
            sma200_shift20 = out["sma_200"].shift(20)
            out["sma_200_slope_20d"] = np.where(
                sma200_shift20 > 0,
                ((out["sma_200"] / sma200_shift20) - 1.0) * (252 / 20) * 100,
                0.0,
            )
        else:
            out["sma_200_slope_20d"] = 0.0

        # 2. Average True Range (ATR 14) & ATR%
        high = out["high"]
        low = out["low"]
        close = out["close"]
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        out["atr_14"] = tr.rolling(window=14, min_periods=5).mean()
        out["atr_pct"] = (out["atr_14"] / out["close"]) * 100.0

        # 3. Relative Strength Index (RSI 14 - Wilder's Smoothing)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        out["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))
        out["rsi_14"] = out["rsi_14"].fillna(50.0)

        # 4. MACD (12, 26, 9)
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        out["macd_line"] = ema_12 - ema_26
        out["macd_signal"] = out["macd_line"].ewm(span=9, adjust=False).mean()
        out["macd_hist"] = out["macd_line"] - out["macd_signal"]

        # 5. Bollinger Bands (20, 2.0) & Bandwidth
        std_20 = close.rolling(window=20, min_periods=5).std()
        out["bb_upper"] = out["sma_20"] + (2.0 * std_20)
        out["bb_lower"] = out["sma_20"] - (2.0 * std_20)
        out["bb_bandwidth"] = np.where(
            out["sma_20"] > 0,
            (out["bb_upper"] - out["bb_lower"]) / out["sma_20"],
            0.0,
        )

        # 6. Volume & Participation: RVOL (Volume / 20-day Volume SMA)
        vol_sma_20 = out["volume"].rolling(window=20, min_periods=5).mean()
        out["rvol_20"] = np.where(vol_sma_20 > 0, out["volume"] / vol_sma_20, 1.0)

        # On-Balance Volume (OBV) & 20-day OBV slope
        obv_direction = np.sign(close.diff().fillna(0))
        out["obv"] = (obv_direction * out["volume"]).cumsum()
        out["obv_slope_20"] = out["obv"].diff(20)

        # Chaikin Money Flow (CMF 20)
        clv = np.where(
            (high - low) > 0,
            ((close - low) - (high - close)) / (high - low),
            0.0,
        )
        vol_clv = clv * out["volume"]
        cmf_num = vol_clv.rolling(window=20, min_periods=5).sum()
        cmf_den = out["volume"].rolling(window=20, min_periods=5).sum()
        out["cmf_20"] = np.where(cmf_den > 0, cmf_num / cmf_den, 0.0)

        # 7. Trend Structure: Higher Highs / Higher Lows count over last 20 sessions
        hh_count = (high > high.shift(1)).rolling(window=20, min_periods=5).sum()
        hl_count = (low > low.shift(1)).rolling(window=20, min_periods=5).sum()
        out["hh_hl_ratio_20"] = (hh_count + hl_count) / 40.0  # Normalized 0.0 - 1.0

        # ADX (14) - Average Directional Index
        plus_dm = (high.diff()).clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        plus_dm = np.where(plus_dm > minus_dm, plus_dm, 0.0)
        minus_dm = np.where(minus_dm > plus_dm, minus_dm, 0.0)

        tr_smooth = tr.rolling(window=14, min_periods=5).sum()
        plus_di = 100.0 * (pd.Series(plus_dm, index=out.index).rolling(window=14, min_periods=5).sum() / tr_smooth)
        minus_di = 100.0 * (pd.Series(minus_dm, index=out.index).rolling(window=14, min_periods=5).sum() / tr_smooth)
        dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0.0)
        out["adx_14"] = dx.rolling(window=14, min_periods=5).mean().fillna(20.0)

        # 8. Relative Strength & Momentum Returns
        # 1-month (21 days), 3-month (63 days), 1-year (252 days)
        out["return_1m"] = out["close"].pct_change(21)
        out["return_3m"] = out["close"].pct_change(63)
        out["return_12m"] = out["close"].pct_change(252)

        # 12-1 Momentum (12-month return minus last 1-month return)
        out["momentum_12_1"] = out["return_12m"] - out["return_1m"]

        # 52-Week High Proximity: (close / rolling_252_high) - 1.0
        rolling_52w_high = out["high"].rolling(window=252, min_periods=20).max()
        out["proximity_52w_high"] = np.where(
            rolling_52w_high > 0,
            (out["close"] / rolling_52w_high) - 1.0,
            0.0,
        )

        # Average Daily Dollar Volume (ADV$ 20)
        out["adv_dollar_20"] = (out["close"] * out["volume"]).rolling(window=20, min_periods=5).mean()

        return out

    @classmethod
    def get_latest_feature_snapshot(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Extract the most recent calculated row as a clean dictionary of metrics.
        """
        features_df = cls.compute_all_features(df)
        last_row = features_df.iloc[-1].to_dict()
        return {k: (None if pd.isna(v) else v) for k, v in last_row.items()}
