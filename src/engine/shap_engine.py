"""
LightGBM Cross-Sectional Ranking & Exact TreeSHAP Attribution Engine (Phase 17)
US + Canada Market Intelligence Platform

Implements explainable machine learning scoring and exact TreeSHAP factor attributions:
1. Cross-sectional LightGBM decision tree ranking model across dual-market equities.
2. Exact TreeSHAP (Lundberg et al. 2020) feature attribution decomposition:
   Score(x) = phi_0 + sum(phi_i)
3. Zero black-box regulatory compliance: 100% mathematical transparency with exact additivity.
4. Identifies top positive drivers and top negative detractors per security.

Compliance: Impersonal quantitative research only (CSA Staff Notice 31-369 / SEC Publisher Exclusion).
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd
import lightgbm as lgb
import shap

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.models.schemas import (
    FeatureAttributionDetail,
    SymbolSHAPAttribution,
    GlobalFeatureImportance,
    SHAPValidationFeed,
)

FEATURE_LABELS = {
    "trend_sma200_ratio": "Trend vs 200DMA",
    "momentum_rsi": "14-Day RSI Momentum",
    "proximity_52w": "Proximity to 52W High",
    "mtf_confluence": "Multi-Timeframe Confluence",
    "roic_pct": "ROIC Earnings Quality",
    "pe_multiple": "P/E Valuation Multiple",
    "hurst_persistence": "Lo HAC Hurst Exponent",
    "fractional_d": "Fractional Memory Order d*",
    "amihud_illiquidity": "Amihud Price Impact",
    "conformal_rr": "Certified Conformal R:R",
    "dual_basis_bps": "Cross-Border Basis Spread",
}

DISCLAIMERS = [
    "TreeSHAP feature attributions represent exact mathematical Shapley value decompositions of the cross-sectional ranking model.",
    "Attribution values reflect additive point contributions to the composite score and do not constitute personalized investment recommendations or trade execution orders.",
    "Published pursuant to impersonal publisher exclusions under Canadian Securities Administrators (CSA) Staff Notice 31-369 and SEC publisher provisions.",
]


class LightGBMSHAPEngine:
    """
    Computes cross-sectional ranking models and exact TreeSHAP feature attributions.
    """

    def __init__(self):
        self.symbols_dir = DATA_DIR / "feeds" / "symbols"
        self._cached_feed: Optional[SHAPValidationFeed] = None

    def _extract_dataset(self) -> Tuple[pd.DataFrame, List[str]]:
        """Extract multi-discipline feature matrix from existing symbol dossiers."""
        rows = []
        sym_files = sorted(list(self.symbols_dir.glob("*.json")))

        for sf in sym_files:
            try:
                with open(sf, "r") as f:
                    d = json.load(f)
            except Exception:
                continue

            sym = d.get("symbol")
            if not sym:
                continue

            metrics = d.get("latest_metrics", {})
            tech = d.get("technical_dossier", {})
            fund = d.get("fundamental_dossier", {})
            qual = fund.get("quality_ratios", {}) if fund else {}
            val = fund.get("valuation_multiples", {}) if fund else {}
            fp = d.get("asset_fingerprint", {})
            cb = d.get("conformal_bounds", {})
            cb_par = d.get("cross_border_parity") or {}
            score_rec = d.get("score_record", {})

            close = float(metrics.get("close", 100.0))
            sma200 = float(metrics.get("sma_200", close))
            trend_ratio = close / max(1.0, sma200)
            rsi = float(metrics.get("rsi_14", 50.0))
            prox52 = float(metrics.get("proximity_52w_high", 0.0))
            confluence = float(tech.get("confluence_score", 50.0))

            roic = float(qual.get("roic") or 15.0)
            pe = float(val.get("pe_ratio") or 25.0)

            hurst = float(fp.get("hurst_exponent", 0.50))
            frac_d = float(fp.get("fractional_d_order", 0.40))
            amihud = float(fp.get("amihud_illiquidity", 0.001))

            conf_rr = float(cb.get("conformal_risk_reward_ratio", 1.0))
            basis_bps = float(cb_par.get("basis_spread_bps", 0.0))

            target_score = float(score_rec.get("composite_score", 50.0))

            rows.append({
                "symbol": sym,
                "trend_sma200_ratio": round(trend_ratio, 3),
                "momentum_rsi": round(rsi, 1),
                "proximity_52w": round(prox52, 3),
                "mtf_confluence": round(confluence, 1),
                "roic_pct": round(roic, 1),
                "pe_multiple": round(pe, 1),
                "hurst_persistence": round(hurst, 3),
                "fractional_d": round(frac_d, 2),
                "amihud_illiquidity": round(amihud, 4),
                "conformal_rr": round(conf_rr, 2),
                "dual_basis_bps": round(basis_bps, 1),
                "target_score": target_score,
            })

        df = pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)
        feature_cols = [c for c in df.columns if c not in ["symbol", "target_score"]]
        return df, feature_cols

    def calibrate_shap_attributions(self) -> SHAPValidationFeed:
        """
        Train LightGBM cross-sectional ranking model and compute exact TreeSHAP values.
        """
        for disc in DISCLAIMERS:
            linter.assert_clean(disc)

        df, feature_cols = self._extract_dataset()
        if df.empty or len(df) < 5:
            raise ValueError("Insufficient symbol records found to train cross-sectional ranker.")

        X = df[feature_cols]
        y = df["target_score"]

        # Train deterministic CPU LightGBM model
        model = lgb.LGBMRegressor(
            n_estimators=25,
            max_depth=3,
            learning_rate=0.08,
            num_leaves=7,
            min_child_samples=2,
            random_state=42,
            verbose=-1,
        )
        model.fit(X, y)

        # Compute exact TreeSHAP values
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)
        base_value = float(explainer.expected_value)

        # 1. Global Feature Importance (Mean Absolute SHAP)
        mean_abs_shaps = np.mean(np.abs(shap_values), axis=0)
        total_abs_shap = max(1e-6, np.sum(mean_abs_shaps))

        global_importance: List[GlobalFeatureImportance] = []
        for j, col in enumerate(feature_cols):
            m_abs = float(mean_abs_shaps[j])
            rel_pct = round((m_abs / total_abs_shap) * 100.0, 1)
            global_importance.append(
                GlobalFeatureImportance(
                    feature_name=col,
                    feature_label=FEATURE_LABELS.get(col, col),
                    mean_abs_shap=round(m_abs, 3),
                    relative_importance_pct=rel_pct,
                )
            )

        # Sort descending by importance
        global_importance.sort(key=lambda x: x.mean_abs_shap, reverse=True)

        # 2. Per-Symbol Attributions & Additivity Validation
        symbol_attributions: List[SymbolSHAPAttribution] = []
        max_additivity_error = 0.0

        for i, sym in enumerate(df["symbol"]):
            pred = float(model.predict(X.iloc[[i]])[0])
            sym_shaps = shap_values[i]
            reconstructed = base_value + float(np.sum(sym_shaps))
            err = abs(pred - reconstructed)
            if err > max_additivity_error:
                max_additivity_error = err

            all_attribs = {}
            contribs: List[FeatureAttributionDetail] = []
            for j, col in enumerate(feature_cols):
                sv = float(sym_shaps[j])
                raw_v = float(X.iloc[i][col])
                all_attribs[col] = round(sv, 2)
                direction = "POSITIVE" if sv > 0.05 else ("NEGATIVE" if sv < -0.05 else "NEUTRAL")
                contribs.append(
                    FeatureAttributionDetail(
                        feature_name=col,
                        feature_label=FEATURE_LABELS.get(col, col),
                        feature_value=raw_v,
                        shap_contribution=round(sv, 2),
                        direction=direction,
                    )
                )

            # Top drivers (highest positive contribution)
            top_drivers = sorted([c for c in contribs if c.shap_contribution > 0.05], key=lambda x: x.shap_contribution, reverse=True)[:3]
            # Top detractors (most negative contribution)
            top_detractors = sorted([c for c in contribs if c.shap_contribution < -0.05], key=lambda x: x.shap_contribution)[:3]

            symbol_attributions.append(
                SymbolSHAPAttribution(
                    symbol=sym,
                    base_value=round(base_value, 2),
                    model_score=round(pred, 1),
                    top_drivers=top_drivers,
                    top_detractors=top_detractors,
                    all_attributions=all_attribs,
                )
            )

        feed = SHAPValidationFeed(
            as_of_date="2026-09-25",
            model_type="LightGBM TreeSHAP (Lundberg et al. 2020)",
            base_value=round(base_value, 2),
            total_securities_scored=len(symbol_attributions),
            additivity_error_max=round(max_additivity_error, 8),
            global_feature_importance=global_importance,
            symbol_attributions=symbol_attributions,
            disclaimers=DISCLAIMERS,
        )

        self._cached_feed = feed
        return feed

    def get_attribution_for_symbol(self, symbol: str) -> Optional[SymbolSHAPAttribution]:
        """Retrieve SHAP attribution record for a single symbol."""
        if not self._cached_feed:
            self.calibrate_shap_attributions()
        if self._cached_feed:
            for s in self._cached_feed.symbol_attributions:
                if s.symbol == symbol:
                    return s
        return None

    def generate_feed(self) -> SHAPValidationFeed:
        """Alias for static edge export pipeline."""
        return self.calibrate_shap_attributions()


# Global singleton instance
shap_engine = LightGBMSHAPEngine()
