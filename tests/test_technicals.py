"""
Tests for Vectorized Technical Analysis Engine
Verifies mathematical correctness of indicators, moving averages, and oscillators.
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta
from src.engine.technicals import TechnicalAnalysisEngine


def generate_synthetic_bars(n: int = 100, trend: float = 0.5) -> pd.DataFrame:
    """Generate synthetic consistent OHLCV bars."""
    start_date = date(2025, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(n)]

    # Steady upward trending price series
    base = 100.0 + np.cumsum(np.full(n, trend) + np.sin(np.linspace(0, 10, n)))
    close = base
    high = base + 1.5
    low = base - 1.2
    open_p = (high + low) / 2.0
    volume = np.full(n, 1_000_000.0)

    return pd.DataFrame({
        "trading_date": dates,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "adjusted_close": close,
    })


def test_technical_indicators_calculation():
    df = generate_synthetic_bars(n=60, trend=0.5)
    features = TechnicalAnalysisEngine.compute_all_features(df)

    assert "sma_20" in features.columns
    assert "sma_50" in features.columns
    assert "rsi_14" in features.columns
    assert "macd_line" in features.columns
    assert "atr_14" in features.columns
    assert "bb_upper" in features.columns
    assert "rvol_20" in features.columns

    # Verify RSI is within bounds [0, 100]
    valid_rsi = features["rsi_14"].dropna()
    assert (valid_rsi >= 0.0).all() and (valid_rsi <= 100.0).all()

    # In upward trending series, SMA20 should be below close
    assert features["close"].iloc[-1] > features["sma_20"].iloc[-1]

    # Verify Bollinger Bands ordering: lower <= sma_20 <= upper
    last_row = features.iloc[-1]
    assert last_row["bb_lower"] <= last_row["sma_20"] <= last_row["bb_upper"]

    # Verify ATR is positive
    assert last_row["atr_14"] > 0
    assert last_row["atr_pct"] > 0


def test_latest_feature_snapshot():
    df = generate_synthetic_bars(n=40)
    snapshot = TechnicalAnalysisEngine.get_latest_feature_snapshot(df)
    assert isinstance(snapshot, dict)
    assert "rsi_14" in snapshot
    assert "rvol_20" in snapshot
    assert snapshot["close"] > 0


def test_volume_profile_calculation():
    df = generate_synthetic_bars(n=80, trend=0.2)
    vp = TechnicalAnalysisEngine.compute_volume_profile(df, bins_count=20, window=60)
    assert "poc_price" in vp
    assert "vah_price" in vp
    assert "val_price" in vp
    assert "value_area_state" in vp
    assert vp["val_price"] <= vp["poc_price"] <= vp["vah_price"]
    assert vp["value_area_state"] in ("ABOVE_VALUE", "INSIDE_VALUE", "BELOW_VALUE")
    assert len(vp["profile_bins"]) == 20
    assert any(b["is_poc"] for b in vp["profile_bins"])


def test_support_resistance_clustering():
    df = generate_synthetic_bars(n=100, trend=0.1)
    sr = TechnicalAnalysisEngine.compute_support_resistance_levels(df)
    assert "support_1" in sr
    assert "resistance_1" in sr
    assert "dist_support_pct" in sr
    assert "dist_resistance_pct" in sr
    assert sr["support_1"] <= df["close"].iloc[-1] <= sr["resistance_1"]
    assert sr["dist_support_pct"] >= 0
    assert sr["dist_resistance_pct"] >= 0


def test_trend_structure_detection():
    df_up = generate_synthetic_bars(n=60, trend=0.8)
    ts_up = TechnicalAnalysisEngine.compute_trend_structure(df_up)
    assert "trend_structure" in ts_up
    assert "structure_label" in ts_up
    assert "msb_detected" in ts_up
    assert ts_up["trend_structure"] in ("STRUCTURAL_UPTREND", "CONSOLIDATION_RANGE")


def test_anchored_vwap_calculation():
    df = generate_synthetic_bars(n=80, trend=0.3)
    avwap = TechnicalAnalysisEngine.compute_anchored_vwap(df)
    assert "ytd_vwap" in avwap
    assert "dist_ytd_vwap_pct" in avwap
    assert "high_52w_vwap" in avwap
    assert avwap["ytd_vwap"] > 0
    assert avwap["high_52w_vwap"] > 0


def test_multi_timeframe_matrix_and_confluence():
    from src.engine.technicals import CONFLUENCE_MAX_POINTS, CONFLUENCE_TIMEFRAMES

    df = generate_synthetic_bars(n=120, trend=0.5)
    mtf = TechnicalAnalysisEngine.compute_multi_timeframe_matrix(df)
    assert "confluence_score" in mtf
    assert 0 <= mtf["confluence_score"] <= CONFLUENCE_MAX_POINTS
    assert "confluence_label" in mtf
    assert "timeframes" in mtf

    tfs = mtf["timeframes"]
    # Only the three frames that are actually resampled from real bars. The platform
    # ingests end-of-day bars, so there is no hourly frame to measure and none is
    # reported. A synthesised 1h leg used to contribute up to 25 of 100 points here.
    assert set(tfs.keys()) == {"1d", "1w", "1m"}
    assert "1h" not in tfs
    assert mtf["confluence_max_score"] == CONFLUENCE_MAX_POINTS
    assert list(mtf["confluence_timeframes"]) == list(CONFLUENCE_TIMEFRAMES)

    for tf_key, tf_data in tfs.items():
        assert "trend" in tf_data
        assert tf_data["trend"] in ("BULLISH", "NEUTRAL", "BEARISH")
        assert "rsi" in tf_data
        assert "adx" in tf_data

    # The published score must be exactly the sum of the points its own declared
    # timeframes earn. This is the invariant the fabricated 1h leg broke: the score
    # carried points from a frame that was not in the payload.
    expected = 0
    for tf in tfs.values():
        if tf["trend"] == "BULLISH":
            expected += 20
        elif tf["trend"] == "NEUTRAL":
            expected += 10
        if tf["ema_8_21_cross"] == "BULLISH":
            expected += 5
    assert mtf["confluence_score"] == min(CONFLUENCE_MAX_POINTS, expected)


def test_confluence_never_reports_an_intraday_frame():
    """Regression guard: no intraday frame may be invented from daily bars."""
    df = generate_synthetic_bars(n=120, trend=0.5)
    mtf = TechnicalAnalysisEngine.compute_multi_timeframe_matrix(df)
    for tf in mtf["timeframes"].values():
        assert tf["timeframe"] in ("1D", "1W", "1M"), tf["timeframe"]
    # The old leg derived its RSI as daily RSI +/- 2.5. If any frame other than 1D
    # reports an RSI exactly 2.5 away from the daily RSI on every input, the offset
    # is back. Weekly and monthly RSI are computed on their own resampled series.
    daily_rsi = mtf["timeframes"]["1d"]["rsi"]
    for key in ("1w", "1m"):
        rsi = mtf["timeframes"][key]["rsi"]
        assert not (abs(abs(rsi - daily_rsi) - 2.5) < 1e-9 and abs(rsi - daily_rsi) > 1e-9), (
            f"{key} RSI {rsi} is a +/-2.5 offset of the daily RSI {daily_rsi}"
        )


def test_feature_store_persistence():
    from src.data.db import db
    df = generate_synthetic_bars(n=50, trend=0.3)
    from src.engine.technicals import CONFLUENCE_MAX_POINTS, FEATURE_VERSION

    dossier = TechnicalAnalysisEngine.compile_full_technical_dossier(df)
    # Previously asserted > 0, which only held because the synthesised 1H leg always
    # contributed points. A bearish alignment on all three real frames legitimately
    # scores 0, so the honest invariant is the range, not a positive score.
    assert 0 <= dossier["confluence_score"] <= CONFLUENCE_MAX_POINTS
    assert dossier["feature_version"] == FEATURE_VERSION

    sec_row = db.execute_query("SELECT security_id, symbol FROM security LIMIT 1;")
    if sec_row:
        sec_id = sec_row[0]["security_id"]
        sym = sec_row[0]["symbol"]
        fid = TechnicalAnalysisEngine.persist_to_feature_store(sec_id, sym, "2026-09-26", dossier)
        assert fid > 0

        res = db.execute_query("SELECT symbol, poc_price, mtf_confluence_score FROM feature_store WHERE security_id = ? AND as_of_date = '2026-09-26';", (sec_id,))
        assert len(res) > 0
        assert res[0]["symbol"] == sym

