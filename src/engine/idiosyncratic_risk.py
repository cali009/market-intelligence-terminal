"""
Deterministic Idiosyncratic Risk & Invalidation Matrix Engine (Phase 18)
US + Canada Market Intelligence Platform

Synthesizes idiosyncratic asset models (Phases 13-17) into a deterministic risk matrix:
1. Hurst Memory Flip De-risking (Phase 13): Flags persistent -> anti-persistent transitions (H < 0.45).
2. Adaptive Conformal Trailing Stops (Phase 14): Ratchets stops using 90% finite-sample certified bounds.
3. Volatility Expansion De-leveraging (Phase 14): Disables aggressive sizing when ATR > 1.35x baseline.
4. Cross-Border Parity Basis Shock Shield (Phase 15): Protects against basis stretch (|z| >= 2.0).
5. TreeSHAP Detractor Drag (Phase 17): Penalizes securities with heavy negative factor drag.

Compliance: Impersonal risk protocol only (CSA Staff Notice 31-369 / SEC Publisher Exclusion).
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

from src.compliance.linter import linter
from src.engine.asset_fingerprint import asset_fingerprint_engine
from src.engine.conformal_bounds import adaptive_conformal_engine
from src.engine.cross_border_parity import cross_border_parity_engine, DUAL_LISTED_PAIRS
from src.engine.shap_engine import shap_engine
from src.models.schemas import IdiosyncraticRiskProfile, IdiosyncraticRiskMatrixFeed

DISCLAIMERS = [
    "Idiosyncratic risk matrix profiles provide mathematical risk parameters derived from statistical fingerprints and conformal bounds.",
    "Risk parameters reflect systematic quantitative model outputs and do not constitute personalized investment recommendations or trade orders.",
    "Published pursuant to impersonal publisher exclusions under Canadian Securities Administrators (CSA) Staff Notice 31-369 and SEC publisher provisions.",
]


class IdiosyncraticRiskEngine:
    """
    Synthesizes idiosyncratic quantitative metrics into deterministic risk states and size multipliers.
    """

    def evaluate_symbol_risk(self, symbol: str) -> IdiosyncraticRiskProfile:
        """
        Evaluate multi-discipline idiosyncratic risk profile for a single security.
        """
        # 1. Retrieve sub-model outputs
        fingerprint = asset_fingerprint_engine.generate_fingerprint_for_symbol(symbol)
        conformal = adaptive_conformal_engine.calibrate_bounds_for_symbol(symbol)
        parity = cross_border_parity_engine.compute_pair_parity(symbol) if symbol in DUAL_LISTED_PAIRS else None
        shap_rec = shap_engine.get_attribution_for_symbol(symbol)

        # 2. Extract metrics
        hurst = fingerprint.hurst_exponent
        hurst_flip = bool(hurst < 0.45)

        cur_price = conformal.current_price
        conf_stop = conformal.conformal_lower_stop
        vol_exp = conformal.volatility_expansion_warning

        basis_bps = parity.basis_spread_bps if parity else None
        basis_z = parity.basis_zscore_60d if parity else None
        basis_stretch = bool(parity and abs(parity.basis_zscore_60d) >= 2.0)

        shap_drag = 0.0
        if shap_rec and shap_rec.top_detractors:
            shap_drag = round(float(sum(d.shap_contribution for d in shap_rec.top_detractors)), 2)

        # 3. Ratchet stop calculation
        conformal_ratchet = bool(conf_stop > (cur_price * 0.90))
        effective_stop = max(conf_stop, round(cur_price * 0.90, 2))

        # 4. Multi-trigger evaluation
        warning_flags = []
        if hurst_flip:
            warning_flags.append(f"Hurst Memory Flip (H={hurst})")
        if vol_exp:
            warning_flags.append("Volatility Expansion Surge (>35% ATR)")
        if basis_stretch:
            warning_flags.append(f"Cross-Border Basis Stretch (Z={basis_z}σ)")
        if shap_drag <= -2.5:
            warning_flags.append(f"TreeSHAP Factor Detractor Drag ({shap_drag} pts)")

        # 5. Determine risk posture and size multiplier
        if cur_price <= conf_stop:
            risk_posture = "IMMEDIATE_INVALIDATION"
            size_multiplier = 0.0
            primary_driver = "Conformal Structural Stop Breached"
            mitigation = "Mathematical invalidation threshold breached; systematic risk protocol dictates position closure or standing aside."
        elif len(warning_flags) >= 2 or hurst_flip or vol_exp:
            risk_posture = "DEFENSIVE_DE_RISK"
            size_multiplier = 0.50
            primary_driver = warning_flags[0] if warning_flags else "Elevated Microstructure Risk"
            mitigation = "Idiosyncratic memory flip or volatility expansion active; 50% exposure haircut recommended by systematic risk budget."
        elif len(warning_flags) == 1 or basis_stretch or shap_drag < -1.0:
            risk_posture = "ELEVATED_CAUTION"
            size_multiplier = 0.75
            primary_driver = warning_flags[0] if warning_flags else "Minor Detractor Drag"
            mitigation = "Minor basis disparity or factor detractor identified; 25% exposure haircut recommended under risk rules."
        else:
            risk_posture = "NORMAL_EQUILIBRIUM"
            size_multiplier = 1.00
            primary_driver = "Stable Quantitative Fingerprint"
            mitigation = "All idiosyncratic bounds intact; nominal position sizing permitted under risk framework."

        # Lint narrative text strings
        linter.assert_clean(mitigation)
        linter.assert_clean(primary_driver)

        return IdiosyncraticRiskProfile(
            symbol=symbol,
            risk_posture=risk_posture,
            position_size_multiplier=size_multiplier,
            hurst_flip_alert=hurst_flip,
            hurst_exponent=round(hurst, 3),
            conformal_stop_price=round(conf_stop, 2),
            conformal_ratchet_recommended=conformal_ratchet,
            effective_stop_price=round(effective_stop, 2),
            volatility_expansion_alert=vol_exp,
            cross_border_parity_stretch=basis_stretch,
            basis_spread_bps=round(basis_bps, 1) if basis_bps is not None else None,
            basis_zscore=round(basis_z, 2) if basis_z is not None else None,
            shap_detractor_drag_pts=shap_drag,
            primary_risk_driver=primary_driver,
            actionable_mitigation=mitigation,
            as_of_date="2026-09-25",
        )

    def generate_feed(self, symbols: Optional[List[str]] = None) -> IdiosyncraticRiskMatrixFeed:
        """
        Generate master idiosyncratic risk matrix feed across all active securities.
        """
        for disc in DISCLAIMERS:
            linter.assert_clean(disc)

        if not symbols:
            # All 19 dual-market securities
            symbols = [
                "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "XOM", "SPY", "QQQ",
                "SHOP", "RY", "TD", "BN", "BAM", "CNQ", "ENB", "CP", "CNR", "XIU"
            ]

        profiles: List[IdiosyncraticRiskProfile] = []
        for sym in symbols:
            p = self.evaluate_symbol_risk(sym)
            profiles.append(p)

        norm_count = sum(1 for p in profiles if p.risk_posture == "NORMAL_EQUILIBRIUM")
        elev_count = sum(1 for p in profiles if p.risk_posture == "ELEVATED_CAUTION")
        def_count = sum(1 for p in profiles if p.risk_posture == "DEFENSIVE_DE_RISK")
        inval_count = sum(1 for p in profiles if p.risk_posture == "IMMEDIATE_INVALIDATION")

        avg_mult = round(float(np.mean([p.position_size_multiplier for p in profiles])), 2)

        return IdiosyncraticRiskMatrixFeed(
            as_of_date="2026-09-25",
            total_securities_monitored=len(profiles),
            normal_equilibrium_count=norm_count,
            elevated_caution_count=elev_count,
            defensive_de_risk_count=def_count,
            immediate_invalidation_count=inval_count,
            average_size_multiplier=avg_mult,
            profiles=profiles,
            disclaimers=DISCLAIMERS,
        )


# Global singleton instance
idiosyncratic_risk_engine = IdiosyncraticRiskEngine()
