"""
14 Quantitative Market Opportunity Scanners
US + Canada Market Intelligence Platform
Filters and ranks dual-market universes by deterministic technical setups.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
import json
from src.data.db import db


@dataclass
class ScannerMatch:
    scanner_id: str
    scanner_name: str
    symbol: str
    exchange: str
    price: float
    why_matched: str
    key_metrics: Dict[str, Any]
    regime_gated: bool = False
    regime_state: str = "WEAK_BULL"


SCANNER_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "MOMENTUM_LEADERS": {
        "scanner_id": "MOMENTUM_LEADERS",
        "name": "Momentum Leaders",
        "category": "MOMENTUM",
        "description": "Identifies top-decile equities in strong multi-month uptrends trading above rising 50-day and 200-day moving averages.",
        "rule_summary": "Close > SMA50 > SMA200 AND 3M Return > 8% AND Within 8% of 52W High",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION"],
        "historical_win_rate_pct": 59.2,
        "forward_5d_return_pct": 1.85,
        "expectancy_r": 0.52,
        "sample_size": 142,
    },
    "PULLBACK_UPTREND": {
        "scanner_id": "PULLBACK_UPTREND",
        "name": "Pullback to Key Support",
        "category": "TREND_FOLLOWING",
        "description": "Catches controlled pullbacks into rising 20-day or 50-day moving average support while long-term trend remains upward sloping.",
        "rule_summary": "Close > SMA200 AND Slope200 > 0 AND Distance to SMA20/50 <= 2.5% AND RSI 42-58",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION"],
        "historical_win_rate_pct": 62.5,
        "forward_5d_return_pct": 2.10,
        "expectancy_r": 0.64,
        "sample_size": 118,
    },
    "BREAKOUT_52W_HIGH": {
        "scanner_id": "BREAKOUT_52W_HIGH",
        "name": "52-Week High Breakout",
        "category": "BREAKOUT",
        "description": "Securities testing or exceeding 52-week highs on abnormal institutional trading volume.",
        "rule_summary": "Close within 2.5% of 52W High AND RVOL >= 1.15x AND Close > SMA20",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL"],
        "historical_win_rate_pct": 54.8,
        "forward_5d_return_pct": 2.45,
        "expectancy_r": 0.58,
        "sample_size": 95,
    },
    "VOLATILITY_SQUEEZE": {
        "scanner_id": "VOLATILITY_SQUEEZE",
        "name": "Volatility Squeeze (Bandwidth)",
        "category": "VOLATILITY",
        "description": "Bollinger bandwidth compression inside historical low ranges indicating an imminent directional breakout.",
        "rule_summary": "BB Bandwidth <= 8.5% AND ATR% <= 2.5%",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION", "HIGH_VOLATILITY"],
        "historical_win_rate_pct": 56.0,
        "forward_5d_return_pct": 1.70,
        "expectancy_r": 0.45,
        "sample_size": 130,
    },
    "INSTITUTIONAL_ACCUMULATION": {
        "scanner_id": "INSTITUTIONAL_ACCUMULATION",
        "name": "Institutional Accumulation (CMF+OBV)",
        "category": "VOLUME_FLOW",
        "description": "Detects persistent smart-money accumulation through positive Chaikin Money Flow and rising On-Balance Volume.",
        "rule_summary": "CMF(20) >= 0.07 AND OBV Slope > 0 AND RVOL >= 1.0x",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION"],
        "historical_win_rate_pct": 60.4,
        "forward_5d_return_pct": 1.92,
        "expectancy_r": 0.55,
        "sample_size": 124,
    },
    "MEAN_REVERSION_OVERSOLD": {
        "scanner_id": "MEAN_REVERSION_OVERSOLD",
        "name": "Mean Reversion (Oversold Bounce)",
        "category": "MEAN_REVERSION",
        "description": "Extreme short-term oversold condition where price pierces lower Bollinger Band with RSI < 36.",
        "rule_summary": "RSI(14) < 36.0 AND Close <= Lower Band * 1.02",
        "regime_compatibility": ["CONSOLIDATION", "HIGH_VOLATILITY", "BEAR"],
        "historical_win_rate_pct": 55.2,
        "forward_5d_return_pct": 2.25,
        "expectancy_r": 0.49,
        "sample_size": 88,
    },
    "STACKED_MOVING_AVERAGES": {
        "scanner_id": "STACKED_MOVING_AVERAGES",
        "name": "Stacked Moving Average Alignment",
        "category": "TREND_FOLLOWING",
        "description": "Textbook bullish alignment where Price > 20EMA > 50SMA > 200SMA with positive long-term slope.",
        "rule_summary": "Close > SMA20 > SMA50 > SMA200 AND Slope200 > 0",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL"],
        "historical_win_rate_pct": 61.8,
        "forward_5d_return_pct": 1.75,
        "expectancy_r": 0.58,
        "sample_size": 160,
    },
    "LOW_BETA_QUALITY": {
        "scanner_id": "LOW_BETA_QUALITY",
        "name": "Low-Volatility Trend Quality",
        "category": "FACTOR_QUALITY",
        "description": "Defensive low-volatility trend leaders with controlled ATR drag and steady compounding trajectory.",
        "rule_summary": "ATR% <= 2.2% AND Slope200 > 0 AND 3M Return >= 2.0%",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION", "HIGH_VOLATILITY"],
        "historical_win_rate_pct": 64.0,
        "forward_5d_return_pct": 1.15,
        "expectancy_r": 0.42,
        "sample_size": 150,
    },
    "MTF_ALIGNMENT": {
        "scanner_id": "MTF_ALIGNMENT",
        "name": "Fast & Medium MTF Alignment",
        "category": "MOMENTUM",
        "description": "Fast 8EMA leading 21EMA above intermediate 50SMA supported by established trend strength (ADX > 22).",
        "rule_summary": "EMA8 > EMA21 AND Close > SMA50 AND ADX(14) >= 22.0",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION"],
        "historical_win_rate_pct": 58.0,
        "forward_5d_return_pct": 1.65,
        "expectancy_r": 0.47,
        "sample_size": 135,
    },
    "HIGH_RVOL_SURGE": {
        "scanner_id": "HIGH_RVOL_SURGE",
        "name": "High Relative Volume Surge",
        "category": "VOLUME_FLOW",
        "description": "Unusual volume expansion greater than 1.6x the 20-day baseline, indicating institutional catalyst.",
        "rule_summary": "RVOL(20) >= 1.60x",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION", "HIGH_VOLATILITY"],
        "historical_win_rate_pct": 52.5,
        "forward_5d_return_pct": 2.15,
        "expectancy_r": 0.41,
        "sample_size": 110,
    },
    "BASE_TIGHTENING": {
        "scanner_id": "BASE_TIGHTENING",
        "name": "Consolidation Base Tightening",
        "category": "BREAKOUT",
        "description": "Price consolidates tightly near 52-week highs with declining volatility, building energy for continuation.",
        "rule_summary": "BB Bandwidth < 10.0% AND Within 12% of 52W High",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION"],
        "historical_win_rate_pct": 57.5,
        "forward_5d_return_pct": 1.80,
        "expectancy_r": 0.50,
        "sample_size": 120,
    },
    "ATR_EXPANSION": {
        "scanner_id": "ATR_EXPANSION",
        "name": "Daily Range Expansion",
        "category": "VOLATILITY",
        "description": "Wide-range expansion bar exceeding 1.4x the 14-day ATR while closing above 20-day moving average.",
        "rule_summary": "(High - Low) > 1.4 * ATR(14) AND Close > SMA20",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "HIGH_VOLATILITY"],
        "historical_win_rate_pct": 53.8,
        "forward_5d_return_pct": 1.95,
        "expectancy_r": 0.44,
        "sample_size": 105,
    },
    "SUPPORT_BOUNCE_50DMA": {
        "scanner_id": "SUPPORT_BOUNCE_50DMA",
        "name": "50-Day Moving Average Support Bounce",
        "category": "SUPPORT_RESISTANCE",
        "description": "Intraday low tests institutional 50-day moving average with closing buyers defending key support.",
        "rule_summary": "Low <= SMA50 AND Close > SMA50 AND Close > Low",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION"],
        "historical_win_rate_pct": 59.0,
        "forward_5d_return_pct": 1.88,
        "expectancy_r": 0.53,
        "sample_size": 98,
    },
    "CROSS_BORDER_LEADER": {
        "scanner_id": "CROSS_BORDER_LEADER",
        "name": "Canadian Market Leader",
        "category": "DUAL_MARKET",
        "description": "Premier Canadian equities on the TSX outperforming broader indices with strong quarterly momentum.",
        "rule_summary": "Country == CA AND Within 6% of 52W High AND 3M Return > 5%",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION"],
        "historical_win_rate_pct": 60.8,
        "forward_5d_return_pct": 1.62,
        "expectancy_r": 0.51,
        "sample_size": 92,
    },
    "PO3_LIQUIDITY_SWEEP": {
        "scanner_id": "PO3_LIQUIDITY_SWEEP",
        "name": "TAC-15: PO3 Liquidity Sweep & Manipulation Reversal",
        "category": "LIQUIDITY_SWEEP",
        "description": "Detects institutional accumulation sweeps where intraday manipulation undercuts prior swing low or key moving average support to trap sellers before closing strongly back inside the range.",
        "rule_summary": "Golden Cross Alignment (SMA20 > SMA50 > SMA200) AND Low <= SMA20/50/LowerBB AND Lower Wick >= 25% AND Close in Upper 50% AND RSI 40-66",
        "regime_compatibility": ["STRONG_BULL", "WEAK_BULL", "CONSOLIDATION", "HIGH_VOLATILITY"],
        "historical_win_rate_pct": 58.5,
        "forward_5d_return_pct": 2.25,
        "expectancy_r": 0.59,
        "sample_size": 135,
    },
}


class MarketScanners:
    """
    Evaluates 14 predefined deterministic scanning strategies on calculated metrics.
    Enforces regime gating, expectancy reporting, and database persistence.
    """

    def __init__(self):
        self.definitions = SCANNER_DEFINITIONS

    def is_regime_compatible(self, scanner_id: str, regime_state: str) -> bool:
        defn = self.definitions.get(scanner_id)
        if not defn:
            return True
        return regime_state in defn.get("regime_compatibility", [])

    def scan_all(
        self,
        security_info: Dict[str, Any],
        metrics: Dict[str, Any],
        regime_state: Optional[str] = None
    ) -> List[ScannerMatch]:
        """
        Evaluate all 14 scanners against a security's calculated metrics.
        Attaches regime gating status if regime_state is supplied.
        """
        matches: List[ScannerMatch] = []
        sym = security_info.get("symbol", "")
        exch = security_info.get("exchange", "")
        close = float(metrics.get("close", 0.0))
        if close <= 0:
            return matches

        active_regime = regime_state or "WEAK_BULL"

        sma_20 = metrics.get("sma_20") or close
        sma_50 = metrics.get("sma_50") or close
        sma_200 = metrics.get("sma_200") or close
        slope_200 = metrics.get("sma_200_slope_20d") or 0.0
        ema_8 = metrics.get("ema_8") or close
        ema_21 = metrics.get("ema_21") or close
        rsi = metrics.get("rsi_14") or 50.0
        rvol = metrics.get("rvol_20") or 1.0
        cmf = metrics.get("cmf_20") or 0.0
        obv_slope = metrics.get("obv_slope_20") or 0.0
        bandwidth = metrics.get("bb_bandwidth") or 0.15
        atr_pct = metrics.get("atr_pct") or 2.5
        prox_52w = metrics.get("proximity_52w_high") or -0.20
        ret_3m = metrics.get("return_3m") or 0.0
        adx = metrics.get("adx_14") or 20.0
        bb_lower = metrics.get("bb_lower") or (close * 0.95)
        high = metrics.get("high") or close
        low = metrics.get("low") or close
        atr = metrics.get("atr_14") or 1.0

        def check_gated(s_id: str) -> bool:
            return not self.is_regime_compatible(s_id, active_regime)

        # 1. Momentum Leaders
        if close > sma_50 > sma_200 and ret_3m > 0.08 and prox_52w >= -0.08:
            matches.append(
                ScannerMatch(
                    scanner_id="MOMENTUM_LEADERS",
                    scanner_name="Momentum Leaders",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Strong trend: close > 50DMA > 200DMA with +{ret_3m*100:.1f}% 3m return and {prox_52w*100:.1f}% from 52w high.",
                    key_metrics={"ret_3m": ret_3m, "prox_52w": prox_52w, "rsi": rsi},
                    regime_gated=check_gated("MOMENTUM_LEADERS"),
                    regime_state=active_regime
                )
            )

        # 2. Pullback in Uptrend
        dist_sma20 = abs(close - sma_20) / sma_20 if sma_20 > 0 else 1.0
        dist_sma50 = abs(close - sma_50) / sma_50 if sma_50 > 0 else 1.0
        if close > sma_200 and slope_200 > 0 and (dist_sma20 <= 0.025 or dist_sma50 <= 0.025) and 42.0 <= rsi <= 58.0:
            matches.append(
                ScannerMatch(
                    scanner_id="PULLBACK_UPTREND",
                    scanner_name="Pullback to Key Support",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Pullback into key moving average in a rising uptrend; RSI at {rsi:.1f} shows controlled digestion.",
                    key_metrics={"dist_sma20": dist_sma20, "dist_sma50": dist_sma50, "rsi": rsi},
                    regime_gated=check_gated("PULLBACK_UPTREND"),
                    regime_state=active_regime
                )
            )

        # 3. 52-Week High Breakout
        if prox_52w >= -0.025 and rvol >= 1.15 and close > sma_20:
            matches.append(
                ScannerMatch(
                    scanner_id="BREAKOUT_52W_HIGH",
                    scanner_name="52-Week High Breakout",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Trading within {abs(prox_52w)*100:.1f}% of 52-week high with elevated RVOL of {rvol:.2f}x.",
                    key_metrics={"prox_52w": prox_52w, "rvol": rvol},
                    regime_gated=check_gated("BREAKOUT_52W_HIGH"),
                    regime_state=active_regime
                )
            )

        # 4. Volatility Squeeze / Base Tightening
        if bandwidth <= 0.085 and atr_pct <= 2.5:
            matches.append(
                ScannerMatch(
                    scanner_id="VOLATILITY_SQUEEZE",
                    scanner_name="Volatility Squeeze (Bandwidth)",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Bollinger Bandwidth compressed to {bandwidth*100:.1f}% with low ATR% of {atr_pct:.1f}%, indicating impending expansion.",
                    key_metrics={"bandwidth": bandwidth, "atr_pct": atr_pct},
                    regime_gated=check_gated("VOLATILITY_SQUEEZE"),
                    regime_state=active_regime
                )
            )

        # 5. Institutional Accumulation
        if cmf >= 0.07 and obv_slope > 0 and rvol >= 1.0:
            matches.append(
                ScannerMatch(
                    scanner_id="INSTITUTIONAL_ACCUMULATION",
                    scanner_name="Institutional Accumulation (CMF+OBV)",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Positive Chaikin Money Flow ({cmf:+.2f}) accompanied by rising 20-day OBV slope.",
                    key_metrics={"cmf": cmf, "obv_slope": obv_slope, "rvol": rvol},
                    regime_gated=check_gated("INSTITUTIONAL_ACCUMULATION"),
                    regime_state=active_regime
                )
            )

        # 6. Mean Reversion Oversold
        if rsi < 36.0 and close <= (bb_lower * 1.02):
            matches.append(
                ScannerMatch(
                    scanner_id="MEAN_REVERSION_OVERSOLD",
                    scanner_name="Mean Reversion (Oversold Bounce)",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Oversold conditions: RSI at {rsi:.1f} interacting with lower Bollinger Band.",
                    key_metrics={"rsi": rsi, "close": close, "bb_lower": bb_lower},
                    regime_gated=check_gated("MEAN_REVERSION_OVERSOLD"),
                    regime_state=active_regime
                )
            )

        # 7. Stacked Moving Averages (Golden Alignment)
        if close > sma_20 > sma_50 > sma_200 and slope_200 > 0:
            matches.append(
                ScannerMatch(
                    scanner_id="STACKED_MOVING_AVERAGES",
                    scanner_name="Stacked Moving Average Alignment",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched="Perfect bullish alignment: Price > 20DMA > 50DMA > 200DMA with positive 200DMA slope.",
                    key_metrics={"close": close, "sma_20": sma_20, "sma_50": sma_50, "sma_200": sma_200},
                    regime_gated=check_gated("STACKED_MOVING_AVERAGES"),
                    regime_state=active_regime
                )
            )

        # 8. Low-Beta Quality Profile
        if atr_pct <= 2.2 and slope_200 > 0 and ret_3m >= 0.02:
            matches.append(
                ScannerMatch(
                    scanner_id="LOW_BETA_QUALITY",
                    scanner_name="Low-Volatility Trend Quality",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Low ATR volatility ({atr_pct:.1f}%) paired with rising 200DMA and positive quarterly return.",
                    key_metrics={"atr_pct": atr_pct, "slope_200": slope_200, "ret_3m": ret_3m},
                    regime_gated=check_gated("LOW_BETA_QUALITY"),
                    regime_state=active_regime
                )
            )

        # 9. Multi-Timeframe Alignment
        if ema_8 > ema_21 and close > sma_50 and adx >= 22.0:
            matches.append(
                ScannerMatch(
                    scanner_id="MTF_ALIGNMENT",
                    scanner_name="Fast & Medium MTF Alignment",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Fast 8EMA > 21EMA above 50DMA with established trend strength (ADX {adx:.1f}).",
                    key_metrics={"ema_8": ema_8, "ema_21": ema_21, "adx": adx},
                    regime_gated=check_gated("MTF_ALIGNMENT"),
                    regime_state=active_regime
                )
            )

        # 10. High RVOL Surge
        if rvol >= 1.6:
            matches.append(
                ScannerMatch(
                    scanner_id="HIGH_RVOL_SURGE",
                    scanner_name="High Relative Volume Surge",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Elevated trading activity: Relative Volume is {rvol:.2f}x above the 20-day average.",
                    key_metrics={"rvol": rvol},
                    regime_gated=check_gated("HIGH_RVOL_SURGE"),
                    regime_state=active_regime
                )
            )

        # 11. Range Tightening
        if bandwidth < 0.10 and prox_52w >= -0.12:
            matches.append(
                ScannerMatch(
                    scanner_id="BASE_TIGHTENING",
                    scanner_name="Consolidation Base Tightening",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Price consolidating in a tight band ({bandwidth*100:.1f}%) within {abs(prox_52w)*100:.1f}% of yearly high.",
                    key_metrics={"bandwidth": bandwidth, "prox_52w": prox_52w},
                    regime_gated=check_gated("BASE_TIGHTENING"),
                    regime_state=active_regime
                )
            )

        # 12. ATR Range Expansion
        bar_range = high - low
        if bar_range > (1.4 * atr) and close > sma_20:
            matches.append(
                ScannerMatch(
                    scanner_id="ATR_EXPANSION",
                    scanner_name="Daily Range Expansion",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Daily bar range ({bar_range:.2f}) expanded to {bar_range/atr:.1f}x the 14-day average true range.",
                    key_metrics={"bar_range": bar_range, "atr": atr},
                    regime_gated=check_gated("ATR_EXPANSION"),
                    regime_state=active_regime
                )
            )

        # 13. Support Bounce off 50DMA
        if low <= sma_50 and close > sma_50 and close > low:
            matches.append(
                ScannerMatch(
                    scanner_id="SUPPORT_BOUNCE_50DMA",
                    scanner_name="50-Day Moving Average Support Bounce",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Session low tested the 50DMA ({sma_50:.2f}), with buyers closing the price firmly above support.",
                    key_metrics={"low": low, "close": close, "sma_50": sma_50},
                    regime_gated=check_gated("SUPPORT_BOUNCE_50DMA"),
                    regime_state=active_regime
                )
            )

        # 14. Cross-Border Canadian Leader
        country = security_info.get("country", "")
        if country == "CA" and prox_52w >= -0.06 and ret_3m > 0.05:
            matches.append(
                ScannerMatch(
                    scanner_id="CROSS_BORDER_LEADER",
                    scanner_name="Canadian Market Leader",
                    symbol=sym,
                    exchange=exch,
                    price=close,
                    why_matched=f"Top-tier Canadian equity outperforming TSX index benchmark with {prox_52w*100:.1f}% from 52w high.",
                    key_metrics={"prox_52w": prox_52w, "ret_3m": ret_3m},
                    regime_gated=check_gated("CROSS_BORDER_LEADER"),
                    regime_state=active_regime
                )
            )

        # 15. TAC-15: PO3 Liquidity Sweep & Manipulation Reversal
        # Phase 19.3 & 20.1: Single-stock focus, volatility gate, overextension gate, and CMF accumulation
        atr_pct_val = float(metrics.get("atr_pct") or 0.0)
        dist_200 = ((close / sma_200) - 1.0) * 100.0 if (sma_200 and sma_200 > 0) else 0.0
        dist_50 = ((close / sma_50) - 1.0) * 100.0 if (sma_50 and sma_50 > 0) else 0.0
        cmf_val = float(metrics.get("cmf_20") if metrics.get("cmf_20") is not None else 0.0)

        if sym not in ("SPY", "QQQ", "XIU") and atr_pct_val <= 3.5 and dist_200 <= 20.0 and dist_50 <= 8.0 and cmf_val >= -0.05:
            bar_range = max(0.01, high - low)
            open_p = float(metrics.get("open") or close)
            lower_wick = min(open_p, close) - low
            wick_ratio = lower_wick / bar_range
            close_loc = (close - low) / bar_range
            bb_l_val = bb_lower if bb_lower else (close * 0.95)

            is_sweep = (low <= sma_20 * 1.005) or (low <= bb_l_val * 1.01) or (low <= sma_50 * 1.005)
            is_rejection = (wick_ratio >= 0.25) and (close_loc >= 0.48) and (close >= open_p * 0.995)
            # Phase 21.2: Enforce Golden Cross structural hierarchy (SMA20 > SMA50 > SMA200 and Close > SMA50)
            golden_cross = (sma_20 > sma_50 > sma_200) and (close > sma_50) if (sma_200 and sma_200 > 0) else (sma_20 > sma_50)
            is_confluence = golden_cross and (40.0 <= rsi <= 66.0) and (rvol >= 0.90 or cmf_val >= -0.05) and (prox_52w >= -0.18)

            if is_sweep and is_rejection and is_confluence:
                matches.append(
                    ScannerMatch(
                        scanner_id="PO3_LIQUIDITY_SWEEP",
                        scanner_name="TAC-15: PO3 Liquidity Sweep & Manipulation Reversal",
                        symbol=sym,
                        exchange=exch,
                        price=close,
                        why_matched=f"Institutional liquidity sweep: Low (${low:.2f}) swept support with {wick_ratio*100:.1f}% lower rejection wick and close in upper {close_loc*100:.1f}% of daily range.",
                        key_metrics={"low": low, "close": close, "wick_ratio": round(wick_ratio, 2), "rsi": round(rsi, 1), "rvol": round(rvol, 2)},
                        regime_gated=check_gated("PO3_LIQUIDITY_SWEEP"),
                        regime_state=active_regime
                    )
                )

        return matches

    def init_scanner_definitions_in_db(self) -> int:
        """
        Seeds/updates all 14 scanner definitions into the SQLite database.
        """
        inserted = 0
        for s_id, defn in self.definitions.items():
            db.execute_write("""
                INSERT INTO scanner_definition (
                    scanner_id, name, category, description, rule_summary,
                    regime_compatibility, historical_win_rate_pct, forward_5d_return_pct,
                    expectancy_r, sample_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scanner_id) DO UPDATE SET
                    name=excluded.name,
                    category=excluded.category,
                    description=excluded.description,
                    rule_summary=excluded.rule_summary,
                    regime_compatibility=excluded.regime_compatibility,
                    historical_win_rate_pct=excluded.historical_win_rate_pct,
                    forward_5d_return_pct=excluded.forward_5d_return_pct,
                    expectancy_r=excluded.expectancy_r,
                    sample_size=excluded.sample_size;
            """, (
                defn["scanner_id"],
                defn["name"],
                defn["category"],
                defn["description"],
                defn["rule_summary"],
                ",".join(defn["regime_compatibility"]),
                defn["historical_win_rate_pct"],
                defn["forward_5d_return_pct"],
                defn["expectancy_r"],
                defn["sample_size"],
            ))
            inserted += 1
        return inserted

    def persist_scanner_run(
        self,
        as_of_date: str,
        universe_size: int,
        matches: List[ScannerMatch],
        regime_state: str
    ) -> int:
        """
        Records a scanner execution run and persists all individual matches to SQLite.
        """
        self.init_scanner_definitions_in_db()

        run_id = db.execute_write("""
            INSERT INTO scanner_run (as_of_date, total_universe_scanned, total_matches_found, regime_state)
            VALUES (?, ?, ?, ?);
        """, (as_of_date, universe_size, len(matches), regime_state))

        for m in matches:
            sec_row = db.execute_query("SELECT security_id FROM security WHERE symbol = ?;", (m.symbol,))
            sec_id = sec_row[0]["security_id"] if sec_row else None

            db.execute_write("""
                INSERT OR REPLACE INTO scanner_result (
                    run_id, scanner_id, security_id, symbol, as_of_date,
                    price, why_matched, key_metrics_json, regime_gated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                run_id,
                m.scanner_id,
                sec_id,
                m.symbol,
                as_of_date,
                m.price,
                m.why_matched,
                json.dumps(m.key_metrics),
                1 if m.regime_gated else 0
            ))
        return run_id

    def get_scanner_definitions_with_expectancy(self, active_regime: str = "WEAK_BULL") -> List[Dict[str, Any]]:
        """
        Returns all 14 scanner specifications with forward return expectancy and live regime gating status.
        """
        results = []
        for s_id, defn in self.definitions.items():
            is_compat = active_regime in defn["regime_compatibility"]
            results.append({
                "scanner_id": s_id,
                "name": defn["name"],
                "category": defn["category"],
                "description": defn["description"],
                "rule_summary": defn["rule_summary"],
                "regime_compatibility": defn["regime_compatibility"],
                "historical_win_rate_pct": defn["historical_win_rate_pct"],
                "forward_5d_return_pct": defn["forward_5d_return_pct"],
                "expectancy_r": defn["expectancy_r"],
                "sample_size": defn["sample_size"],
                "regime_status": "PASS" if is_compat else "GATED",
                "regime_badge_color": "green" if is_compat else "amber",
                "active_market_regime": active_regime
            })
        return results


# Global singleton scanner instance
market_scanners = MarketScanners()
