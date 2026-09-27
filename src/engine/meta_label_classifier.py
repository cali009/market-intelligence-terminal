"""
Machine Learning Meta-Labeling Model & Trade Gating Engine (Phase 23.2)
US + Canada Market Intelligence Platform

Implements Marcos López de Prado (2018) Advances in Financial Machine Learning standards:
1. Secondary ML Meta-Model: Predicts whether a primary trade setup will hit the upper profit barrier before the stop loss.
2. Calibrated Probability Ensemble: Combines L2-regularized logistic regression (linear baseline) with Random Forest (non-linear interactions).
3. Out-Of-Sample Generalization: Strict evaluation across chronologically isolated Train, Validation, and Test partitions.
4. Attribution & Drivers: Computes exact mathematical contribution z_i = ((x_i - mu_i) / sigma_i) * beta_i.
5. Dynamic Trade Gating: Throttles or gates trades where meta-probability P(Success) is below acceptable thresholds.
6. Impersonal Decision-Support Research Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, brier_score_loss

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.meta_label_dataset import meta_label_dataset_engine
from src.models.schemas import (
    FeatureImportanceItem,
    MetaGatingVerdict,
    MetaLabelModelMetrics,
    MetaLabelModelFeed,
)

DISCLAIMERS = [
    "Machine learning meta-labeling models generate statistical decision-support probabilities for risk management and trade filtering.",
    "Meta-probabilities reflect historical out-of-sample path dependencies and do not guarantee future returns or execution fills.",
    "Published pursuant to impersonal publisher exclusions under Canadian Securities Administrators (CSA) Staff Notice 31-369 and SEC publisher provisions.",
]

FEEDS_DIR = DATA_DIR / "feeds"


class MetaLabelClassifierEngine:
    """
    Trains, evaluates, and serves ML meta-labeling models to filter trade setups
    and dynamically scale position sizing based on execution success probability.
    """

    def __init__(self):
        self.scaler: Optional[StandardScaler] = None
        self.lr_model: Optional[LogisticRegression] = None
        self.rf_model: Optional[RandomForestClassifier] = None
        self.feature_names: List[str] = []
        self._metrics_cache: Optional[MetaLabelModelMetrics] = None

    def train_model(self, force_refresh: bool = False) -> MetaLabelModelMetrics:
        """
        Trains the ensemble meta-classifier on the chronological Train partition,
        evaluates on Validation and Test sets, and extracts feature importances.
        """
        if self._metrics_cache is not None and not force_refresh:
            return self._metrics_cache

        for disc in DISCLAIMERS:
            linter.assert_clean(disc)

        (X_tr, y_tr), (X_val, y_val), (X_te, y_te) = meta_label_dataset_engine.get_train_val_test_matrices()
        if len(X_tr) == 0:
            raise ValueError("Training dataset matrix is empty; cannot train meta-classifier.")

        self.feature_names = list(X_tr.columns)

        # 1. Feature Standardization
        self.scaler = StandardScaler()
        X_tr_scaled = pd.DataFrame(self.scaler.fit_transform(X_tr), columns=self.feature_names)
        X_val_scaled = pd.DataFrame(self.scaler.transform(X_val), columns=self.feature_names)
        X_te_scaled = pd.DataFrame(self.scaler.transform(X_te), columns=self.feature_names)

        # 2. Linear Baseline Model (L2 Regularized Logistic Regression)
        self.lr_model = LogisticRegression(C=0.08, penalty="l2", solver="lbfgs", max_iter=200, random_state=42)
        self.lr_model.fit(X_tr_scaled, y_tr)

        # 3. Non-Linear Tree Model (Constrained Random Forest to prevent overfitting)
        self.rf_model = RandomForestClassifier(
            n_estimators=120,
            max_depth=4,
            min_samples_leaf=20,
            max_features="sqrt",
            random_state=42,
        )
        self.rf_model.fit(X_tr, y_tr)

        # 4. Out-of-Sample Ensemble Probability Predictions
        tr_p_lr = self.lr_model.predict_proba(X_tr_scaled)[:, 1]
        tr_p_rf = self.rf_model.predict_proba(X_tr)[:, 1]
        tr_p_ens = 0.50 * tr_p_lr + 0.50 * tr_p_rf

        val_p_lr = self.lr_model.predict_proba(X_val_scaled)[:, 1]
        val_p_rf = self.rf_model.predict_proba(X_val)[:, 1]
        val_p_ens = 0.50 * val_p_lr + 0.50 * val_p_rf

        te_p_lr = self.lr_model.predict_proba(X_te_scaled)[:, 1]
        te_p_rf = self.rf_model.predict_proba(X_te)[:, 1]
        te_p_ens = 0.50 * te_p_lr + 0.50 * te_p_rf

        # Metrics
        tr_auc = round(float(roc_auc_score(y_tr, tr_p_ens)), 4)
        val_auc = round(float(roc_auc_score(y_val, val_p_ens)), 4)
        te_auc = round(float(roc_auc_score(y_te, te_p_ens)), 4)
        te_brier = round(float(brier_score_loss(y_te, te_p_ens)), 4)

        # 5. Feature Importances (Blended Tree Gini + Logistic Regression Absolute Beta)
        lr_coefs = self.lr_model.coef_[0]
        rf_importances = self.rf_model.feature_importances_

        # Normalize LR coefficients to sum to 1.0
        abs_lr = np.abs(lr_coefs)
        norm_lr = abs_lr / np.sum(abs_lr) if np.sum(abs_lr) > 0 else np.ones_like(abs_lr) / len(abs_lr)

        blended_importance = 0.50 * norm_lr + 0.50 * rf_importances
        sorted_indices = np.argsort(blended_importance)[::-1]

        top_features: List[FeatureImportanceItem] = []
        for idx in sorted_indices:
            fname = self.feature_names[idx]
            weight = float(blended_importance[idx])
            direction = "POSITIVE" if lr_coefs[idx] >= 0 else "NEGATIVE"
            top_features.append(
                FeatureImportanceItem(
                    feature_name=fname,
                    importance_weight=round(weight, 4),
                    directional_impact=direction,
                )
            )

        now_str = datetime.now(timezone.utc).isoformat()
        metrics = MetaLabelModelMetrics(
            model_id="ML_META_ENSEMBLE_V1",
            model_architecture="Ensemble (L2-Logistic Regression + Constrained Random Forest)",
            train_auc=tr_auc,
            validation_auc=val_auc,
            test_auc=te_auc,
            brier_score=te_brier,
            total_training_samples=len(X_tr),
            top_features=top_features,
            calibrated_at=now_str,
        )

        self._metrics_cache = metrics
        return metrics

    def predict_probability(self, features: Dict[str, float]) -> float:
        """
        Computes the calibrated meta-probability P(Success) for a single candidate trade.
        """
        if self.lr_model is None or self.rf_model is None or self.scaler is None:
            self.train_model()

        # Build DataFrame with explicit feature names
        f_df = pd.DataFrame([[features.get(col, 0.0) for col in self.feature_names]], columns=self.feature_names)
        f_scaled = pd.DataFrame(self.scaler.transform(f_df), columns=self.feature_names)

        p_lr = float(self.lr_model.predict_proba(f_scaled)[0, 1])
        p_rf = float(self.rf_model.predict_proba(f_df)[0, 1])

        p_ens = 0.50 * p_lr + 0.50 * p_rf
        return float(np.clip(p_ens, 0.05, 0.95))

    def evaluate_gating(self, symbol: str, features: Dict[str, float]) -> MetaGatingVerdict:
        """
        Evaluates a candidate setup against meta-labeling thresholds to determine
        whether to proceed, throttle, or gate (skip) the trade execution.
        """
        prob = self.predict_probability(features)

        if prob >= 0.53:
            action = "HIGH_CONVICTION_PROCEED"
            size_mult = 1.00
        elif prob >= 0.48:
            action = "MODERATE_PROCEED"
            size_mult = 0.85
        elif prob >= 0.44:
            action = "CAUTION_THROTTLE"
            size_mult = 0.50
        else:
            action = "GATED_EXCLUDE"
            size_mult = 0.00

        # Mathematical primary driver: largest z_score * beta impact
        f_df = pd.DataFrame([[features.get(col, 0.0) for col in self.feature_names]], columns=self.feature_names)
        f_scaled = self.scaler.transform(f_df)[0]
        betas = self.lr_model.coef_[0]
        impacts = f_scaled * betas

        best_idx = int(np.argmax(np.abs(impacts)))
        best_name = self.feature_names[best_idx]
        best_val = features.get(best_name, 0.0)
        best_impact = impacts[best_idx]
        impact_sign = "+" if best_impact >= 0 else "-"
        primary_driver = f"{best_name} ({best_val:+.2f}, impact {impact_sign})"

        return MetaGatingVerdict(
            symbol=symbol,
            meta_probability=round(prob, 4),
            action=action,
            size_multiplier=size_mult,
            primary_driver=primary_driver,
            calibrated_at=datetime.now(timezone.utc).isoformat(),
        )

    def export_feed(self, target_dir: Optional[Path] = None) -> Path:
        """
        Serializes model metrics and feature importance feed to data/feeds/meta_label_model.json.
        """
        base_dir = target_dir or FEEDS_DIR
        out_path = base_dir / "meta_label_model.json"
        master_path = FEEDS_DIR / "meta_label_model.json"

        if target_dir is not None and target_dir != FEEDS_DIR and master_path.exists():
            import shutil
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(master_path, out_path)
            return out_path

        metrics = self.train_model()
        feed = MetaLabelModelFeed(
            metrics=metrics,
            disclaimers=DISCLAIMERS,
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(feed.to_dict(), f, indent=2)

        return out_path


# Global singleton instance
meta_label_classifier = MetaLabelClassifierEngine()
