"""
8-Factor Quantitative Scoring Engine
US + Canada Market Intelligence Platform
Computes transparent 0–100 opportunity scores with full component attribution.
Enforces that Risk is strictly subtractive and that numbers are mathematically verified.
"""

from typing import Dict, Any, Optional
from datetime import date, datetime, timezone
from src.models.schemas import ScoreRecord


class ScoringEngine:
    """
    Evaluates market setups using an 8-factor regularized framework.
    """

    @staticmethod
    def calculate_trend_score(metrics: Dict[str, Any], horizon: str = "POSITION") -> float:
        """
        Trend structure: Moving average configuration, 200DMA slope, ADX, HH/HL count.
        Max score: 30 for SWING, 25 for POSITION.
        """
        close = metrics.get("close", 0.0)
        sma_20 = metrics.get("sma_20") or close
        sma_50 = metrics.get("sma_50") or close
        sma_200 = metrics.get("sma_200") or close
        slope_200 = metrics.get("sma_200_slope_20d") or 0.0
        adx = metrics.get("adx_14") or 20.0
        hh_hl = metrics.get("hh_hl_ratio_20") or 0.5

        points = 0.0

        # Close above 200DMA
        if close > sma_200:
            points += 7.0
        # Rising 200DMA slope
        if slope_200 > 0:
            points += 6.0
        # Close above 50DMA
        if close > sma_50:
            points += 6.0
        # 50DMA > 200DMA (Golden Cross structure)
        if sma_50 > sma_200:
            points += 4.0
        # Trend strength (ADX > 22)
        if adx >= 22.0:
            points += 4.0
        # Higher Highs / Higher Lows structure (> 0.55)
        if hh_hl >= 0.55:
            points += 3.0

        # Scale according to horizon
        max_possible = 30.0
        scale = 25.0 / 30.0 if horizon == "POSITION" else 1.0
        return min(max_possible * scale, points * scale)

    @staticmethod
    def calculate_relative_strength_score(metrics: Dict[str, Any], horizon: str = "POSITION") -> float:
        """
        Relative Strength & Proximity to 52-week Highs.
        Max score: 20 pts (both horizons).
        """
        prox_52w = metrics.get("proximity_52w_high") or -0.20
        ret_3m = metrics.get("return_3m") or 0.0
        mom_12_1 = metrics.get("momentum_12_1") or 0.0

        points = 0.0

        # Proximity to 52-week high (leaders trade near highs)
        if prox_52w >= -0.05:
            points += 10.0
        elif prox_52w >= -0.15:
            points += 6.0
        elif prox_52w >= -0.25:
            points += 3.0

        # Positive 3-month momentum
        if ret_3m > 0.10:
            points += 6.0
        elif ret_3m > 0.0:
            points += 3.0

        # Intermediate 12-1 momentum
        if mom_12_1 > 0:
            points += 4.0

        return min(20.0, points)

    @staticmethod
    def calculate_setup_geometry_score(metrics: Dict[str, Any], horizon: str = "POSITION") -> float:
        """
        Setup geometry: Base tightness, pullback to key moving averages, Bollinger squeeze.
        Max score: 20 for SWING, 10 for POSITION.
        """
        close = metrics.get("close", 0.0)
        sma_20 = metrics.get("sma_20") or close
        sma_50 = metrics.get("sma_50") or close
        bandwidth = metrics.get("bb_bandwidth") or 0.10
        rsi = metrics.get("rsi_14") or 50.0

        points = 0.0

        # Pullback into value support: close near 20DMA or 50DMA in an uptrend
        dist_sma20 = abs(close - sma_20) / sma_20 if sma_20 > 0 else 1.0
        dist_sma50 = abs(close - sma_50) / sma_50 if sma_50 > 0 else 1.0

        if dist_sma20 <= 0.02 or dist_sma50 <= 0.025:
            points += 8.0
        elif dist_sma20 <= 0.04:
            points += 4.0

        # Volatility squeeze / base tightness (low bandwidth indicates imminent expansion)
        if bandwidth < 0.08:
            points += 6.0
        elif bandwidth < 0.14:
            points += 3.0

        # Constructive RSI posture (45 to 65: constructive, not overbought)
        if 48.0 <= rsi <= 65.0:
            points += 6.0
        elif 40.0 <= rsi < 48.0:
            points += 3.0

        max_possible = 20.0
        scale = 10.0 / 20.0 if horizon == "POSITION" else 1.0
        return min(max_possible * scale, points * scale)

    @staticmethod
    def calculate_volume_participation_score(metrics: Dict[str, Any], horizon: str = "POSITION") -> float:
        """
        Volume & institutional participation: RVOL, OBV slope, Chaikin Money Flow.
        Max score: 15 for SWING, 10 for POSITION.
        """
        rvol = metrics.get("rvol_20") or 1.0
        obv_slope = metrics.get("obv_slope_20") or 0.0
        cmf = metrics.get("cmf_20") or 0.0

        points = 0.0

        # RVOL surge on up move or constructive volume
        if rvol >= 1.5:
            points += 7.0
        elif rvol >= 1.1:
            points += 4.0
        elif rvol >= 0.9:
            points += 2.0

        # Positive 20-day OBV accumulation
        if obv_slope > 0:
            points += 4.0

        # Institutional accumulation via Chaikin Money Flow (> 0.05)
        if cmf >= 0.08:
            points += 4.0
        elif cmf > 0.0:
            points += 2.0

        max_possible = 15.0
        scale = 10.0 / 15.0 if horizon == "POSITION" else 1.0
        return min(max_possible * scale, points * scale)

    @staticmethod
    def calculate_mtf_alignment_score(metrics: Dict[str, Any], horizon: str = "POSITION") -> float:
        """
        Multi-timeframe alignment (Fast vs Medium moving averages).
        Max score: 10 for SWING, 5 for POSITION.
        """
        ema_8 = metrics.get("ema_8") or 0.0
        ema_21 = metrics.get("ema_21") or 0.0
        close = metrics.get("close") or 0.0
        sma_50 = metrics.get("sma_50") or 0.0

        points = 0.0
        if ema_8 > ema_21:
            points += 5.0
        if close > sma_50:
            points += 5.0

        scale = 5.0 / 10.0 if horizon == "POSITION" else 1.0
        return min(10.0 * scale, points * scale)

    @staticmethod
    def calculate_volatility_fit_score(metrics: Dict[str, Any]) -> float:
        """
        Volatility fit: Target ATR% band (1.5% - 4.0% is optimal for swing/position).
        Too low = dead stock; Too high = unmanageable risk.
        Max score: 5 pts.
        """
        atr_pct = metrics.get("atr_pct") or 2.5
        if 1.5 <= atr_pct <= 4.0:
            return 5.0
        elif 1.0 <= atr_pct < 1.5 or 4.0 < atr_pct <= 5.5:
            return 3.0
        else:
            return 1.0

    @staticmethod
    def calculate_risk_penalty(metrics: Dict[str, Any]) -> float:
        """
        Risk Penalty: Subtractive deduction (0 to 25 pts).
        Penalizes extreme volatility, declining structural trend, or severe drawdown.
        """
        penalty = 0.0
        close = metrics.get("close", 0.0)
        sma_200 = metrics.get("sma_200") or close
        slope_200 = metrics.get("sma_200_slope_20d") or 0.0
        atr_pct = metrics.get("atr_pct") or 2.5
        prox_52w = metrics.get("proximity_52w_high") or 0.0

        # 1. Structural breakdown: Below 200DMA and declining slope
        if close < sma_200 and slope_200 < 0:
            penalty += 8.0
        elif close < sma_200:
            penalty += 4.0

        # 2. Extreme volatility risk (ATR% > 5.5%)
        if atr_pct > 6.0:
            penalty += 8.0
        elif atr_pct > 4.5:
            penalty += 4.0

        # 3. Severe drawdown penalty (> 35% from 52-week high)
        if prox_52w < -0.35:
            penalty += 6.0
        elif prox_52w < -0.25:
            penalty += 3.0

        return min(25.0, penalty)

    def evaluate_security(
        self,
        security_id: int,
        metrics: Dict[str, Any],
        fundamental_score: Optional[int] = None,
        regime_state: str = "WEAK_BULL",
        regime_multiplier: float = 1.00,
        news_contribution: float = 0.0,
        sector_contribution: float = 0.0,
        horizon: str = "POSITION",
    ) -> ScoreRecord:
        """
        Compute complete 0-100 composite score with full factor attribution.
        """
        # 1. Calculate technical sub-scores
        trend = round(self.calculate_trend_score(metrics, horizon), 1)
        rs = round(self.calculate_relative_strength_score(metrics, horizon), 1)
        setup = round(self.calculate_setup_geometry_score(metrics, horizon), 1)
        vol_part = round(self.calculate_volume_participation_score(metrics, horizon), 1)
        mtf = round(self.calculate_mtf_alignment_score(metrics, horizon), 1)
        vol_fit = round(self.calculate_volatility_fit_score(metrics), 1)

        # Technical Score (sum of all 6 technical sub-factors, max 100 for Swing, max 75 for Position)
        tech_score = round(trend + rs + setup + vol_part + mtf + vol_fit)
        if horizon == "SWING":
            tech_score = min(100, max(0, tech_score))
            raw_score = float(tech_score)
        else:
            # Position: 75% Technical + 25% Fundamental
            if fundamental_score is not None:
                raw_score = (tech_score * 0.75) + (fundamental_score * 0.25)
            else:
                # Renormalize 100% to technical when fundamental is bypassed (e.g. Index ETFs)
                raw_score = tech_score * (100.0 / 75.0)

        # 2. Modulation & Risk Penalty
        modulated = (raw_score * regime_multiplier) + news_contribution + sector_contribution
        risk_pen = round(self.calculate_risk_penalty(metrics), 1)
        final_score = int(round(modulated - risk_pen))
        final_score = max(0, min(100, final_score))

        # 3. Hard Gates & Caps
        close = metrics.get("close", 0.0)
        sma_200 = metrics.get("sma_200") or close
        slope_200 = metrics.get("sma_200_slope_20d") or 0.0
        sma_20 = metrics.get("sma_20") or close

        # Structural Gate: Price below declining 200DMA and below 20DMA caps at 40
        if close < sma_200 and slope_200 < 0 and close < sma_20:
            final_score = min(40, final_score)

        # 4. Assign Evidence-Based Confidence Tier (A, B, C, D)
        data_quality = metrics.get("data_confidence", "HIGH")
        if data_quality == "HIGH" and final_score >= 70:
            confidence_tier = "A"
        elif data_quality in ("HIGH", "MEDIUM") and final_score >= 50:
            confidence_tier = "B"
        elif data_quality == "LOW":
            confidence_tier = "C"
        else:
            confidence_tier = "B" if final_score >= 50 else "C"

        attribution = {
            "horizon": horizon,
            "trend_score": trend,
            "relative_strength_score": rs,
            "setup_geometry_score": setup,
            "volume_participation_score": vol_part,
            "mtf_alignment_score": mtf,
            "volatility_fit_score": vol_fit,
            "technical_score": tech_score,
            "fundamental_score": fundamental_score,
            "regime_multiplier": regime_multiplier,
            "news_contribution": news_contribution,
            "sector_contribution": sector_contribution,
            "risk_penalty": risk_pen,
            "key_metrics": {
                "close": close,
                "rsi_14": metrics.get("rsi_14"),
                "rvol_20": metrics.get("rvol_20"),
                "atr_pct": metrics.get("atr_pct"),
                "proximity_52w_high": metrics.get("proximity_52w_high"),
            },
        }

        return ScoreRecord(
            security_id=security_id,
            as_of_date=date.today(),
            knowledge_at=datetime.now(timezone.utc),
            technical_score=min(100, tech_score),
            fundamental_score=fundamental_score,
            regime_state=regime_state,
            regime_multiplier=regime_multiplier,
            news_contribution=news_contribution,
            sector_contribution=sector_contribution,
            risk_penalty=risk_pen,
            composite_score=final_score,
            confidence_tier=confidence_tier,
            data_quality_floor=data_quality,
            factor_attribution_json=attribution,
            model_version="2026.1-factor8",
        )


# Global singleton scoring engine
scoring_engine = ScoringEngine()
