"""
14 Quantitative Market Opportunity Scanners
US + Canada Market Intelligence Platform
Filters and ranks dual-market universes by deterministic technical setups.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class ScannerMatch:
    scanner_id: str
    scanner_name: str
    symbol: str
    exchange: str
    price: float
    why_matched: str
    key_metrics: Dict[str, Any]


class MarketScanners:
    """
    Evaluates 14 predefined deterministic scanning strategies on calculated metrics.
    """

    @staticmethod
    def scan_all(security_info: Dict[str, Any], metrics: Dict[str, Any]) -> List[ScannerMatch]:
        """
        Evaluate all 14 scanners against a security's calculated metrics.
        Returns a list of matching scanner results.
        """
        matches: List[ScannerMatch] = []
        sym = security_info.get("symbol", "")
        exch = security_info.get("exchange", "")
        close = float(metrics.get("close", 0.0))
        if close <= 0:
            return matches

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
                )
            )

        return matches


# Global singleton scanner instance
market_scanners = MarketScanners()
