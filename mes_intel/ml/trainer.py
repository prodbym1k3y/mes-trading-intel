"""ModelTrainer — XGBoost training on historical MES trade data.

Orchestrates feature engineering, walk-forward validation, final model fit,
model persistence, event publishing, and degradation detection.

XGBoost is optional: if not installed the trainer falls back to a simple
heuristic scorer so the rest of the system continues to function.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

try:
    import xgboost as xgb
    from xgboost import XGBClassifier
    _XGB_AVAILABLE = True
except (ImportError, OSError, Exception):  # pragma: no cover
    # Catch ImportError and also OSError/Exception from dylib loading failures
    # e.g., missing libomp.dylib on macOS
    xgb = None  # type: ignore
    XGBClassifier = None  # type: ignore
    _XGB_AVAILABLE = False

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import classification_report as _sklearn_report
    _SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover
    StandardScaler = None  # type: ignore
    _sklearn_report = None  # type: ignore
    _SKLEARN_AVAILABLE = False

try:
    import joblib
    _JOBLIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    import pickle as joblib  # type: ignore
    _JOBLIB_AVAILABLE = False

from .features import FeatureEngine
from .validator import WalkForwardValidator, ValidationResult

log = logging.getLogger(__name__)

# ── result container ──────────────────────────────────────────────────────────

@dataclass
class TrainingResult:
    success: bool
    model_path: str = ""
    n_samples: int = 0
    n_features: int = 0
    validation_result: Optional[ValidationResult] = None
    feature_importances: Dict[str, float] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "model_path": self.model_path,
            "n_samples": self.n_samples,
            "n_features": self.n_features,
            "validation_result": (
                self.validation_result.to_dict()
                if self.validation_result else None
            ),
            "top_features": sorted(
                self.feature_importances.items(), key=lambda kv: -kv[1]
            )[:20],
            "timestamp": self.timestamp,
        }


# ── heuristic fallback ────────────────────────────────────────────────────────

class _HeuristicScorer:
    """Minimal fallback scorer used when XGBoost is unavailable.

    Averages the strategy score features (indices 35–41) as a proxy
    for trade quality, then clips to [0, 1].
    """

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        # Strategy scores live at fixed positions — use mean as confidence
        if X.shape[1] >= 42:
            raw = X[:, 35:42].mean(axis=1)
        else:
            raw = X.mean(axis=1)
        prob = np.clip((raw + 1.0) / 2.0, 0.0, 1.0)
        return np.column_stack([1 - prob, prob])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)

    def fit(self, X: np.ndarray, y: np.ndarray):
        return self  # stateless

    @property
    def feature_importances_(self) -> np.ndarray:
        return np.array([])


# ── MLTrainer ────────────────────────────────────────────────────────────────

class MLTrainer:
    """Train an XGBoost classifier on historical trade data.

    Args:
        config:    MLConfig dataclass (or any object/dict with the right attrs).
        db:        Database instance (mes_intel.database.Database).
        event_bus: Optional EventBus; if provided, training events are published.
    """

    XGB_PARAMS: dict = {
        "n_estimators": 200,
        "max_depth": 6,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "use_label_encoder": False,
        "eval_metric": "logloss",
        "random_state": 42,
        "verbosity": 0,
    }

    def __init__(self, config, db, event_bus=None):
        self.config = config
        self.db = db
        self.event_bus = event_bus

        # Resolve model path from config
        cfg_path = (
            config.model_path
            if hasattr(config, "model_path")
            else config.get("model_path", "models/xgb_scorer.pkl")
            if isinstance(config, dict)
            else "models/xgb_scorer.pkl"
        )
        # If relative, anchor to the var/mes_intel directory
        p = Path(cfg_path)
        if not p.is_absolute():
            base = Path(__file__).resolve().parent.parent.parent / "var" / "mes_intel"
            p = base / p
        self._model_path = p

        # Resolve walk_forward_splits
        self._n_splits = (
            config.walk_forward_splits
            if hasattr(config, "walk_forward_splits")
            else int(config.get("walk_forward_splits", 5))
            if isinstance(config, dict)
            else 5
        )

        self._retrain_threshold = (
            config.retrain_threshold
            if hasattr(config, "retrain_threshold")
            else float(config.get("retrain_threshold", 0.55))
            if isinstance(config, dict)
            else 0.55
        )

        self._feature_window = (
            config.feature_window
            if hasattr(config, "feature_window")
            else int(config.get("feature_window", 20))
            if isinstance(config, dict)
            else 20
        )

        self.feature_engine = FeatureEngine(feature_window=self._feature_window)
        self.validator = WalkForwardValidator(n_splits=self._n_splits, gap_size=10)

        self._model = None
        self._scaler: Optional[object] = None
        self._feature_importances: Dict[str, float] = {}

        if not _XGB_AVAILABLE:
            log.warning(
                "XGBoost not installed — using heuristic scorer fallback. "
                "Install with: pip install xgboost"
            )

    # ── training ──────────────────────────────────────────────────────────────

    def train(
        self, market_data_history: list, trade_history: list
    ) -> TrainingResult:
        """Full training pipeline.

        1. Build feature matrix from market_data_history + trade_history.
        2. Derive binary win/loss labels from trade PnL.
        3. Walk-forward validation.
        4. Fit final model on full dataset.
        5. Save model + scaler.
        6. Publish ML_TRAINING_COMPLETE event.

        Args:
            market_data_history: Chronological list of market_data bar dicts.
                Each dict should include at minimum 'close', 'timestamp',
                and a 'trade_id' or 'pnl' key so labels can be derived.
            trade_history: List of trade dicts (from db.get_trades()).

        Returns:
            TrainingResult dataclass.
        """
        self._publish_started()

        # ── 1. Build feature matrix ───────────────────────────────────────────
        try:
            X_df = self.feature_engine.compute_feature_matrix(
                market_data_history, trade_history
            )
        except Exception as exc:
            log.error("Feature engineering failed: %s", exc, exc_info=True)
            return TrainingResult(success=False)

        # ── 2. Build labels ───────────────────────────────────────────────────
        labels = self._build_labels(market_data_history, trade_history)

        if len(labels) != len(X_df):
            log.warning(
                "Label count (%d) != feature row count (%d); truncating.",
                len(labels), len(X_df),
            )
            min_len = min(len(labels), len(X_df))
            labels = labels[:min_len]
            X_df   = X_df.iloc[:min_len]

        X = X_df.values.astype(np.float32)
        y = np.array(labels, dtype=np.int32)

        # Check minimum samples
        min_samples = (
            self.config.min_samples
            if hasattr(self.config, "min_samples")
            else int(self.config.get("min_samples", 100))
            if isinstance(self.config, dict)
            else 100
        )
        if len(X) < min_samples:
            log.warning(
                "Not enough samples for training: %d < %d (min_samples).",
                len(X), min_samples,
            )
            return TrainingResult(success=False, n_samples=len(X))

        log.info(
            "Training on %d samples, %d features, win-rate=%.1f%%",
            len(X), X.shape[1], 100 * float(np.mean(y)),
        )

        # ── 3. Scale features ─────────────────────────────────────────────────
        if _SKLEARN_AVAILABLE:
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            self._scaler = scaler
        else:
            X_scaled = X
            self._scaler = None

        # ── 4. Walk-forward validation ────────────────────────────────────────
        probe_model = self._make_model()
        val_result: ValidationResult = self.validator.evaluate_model(
            probe_model, X_scaled, y
        )

        log.info(
            "Walk-forward result — acc=%.3f prec=%.3f rec=%.3f f1=%.3f "
            "degradation=%s retrain=%s",
            val_result.overall_accuracy,
            val_result.overall_precision,
            val_result.overall_recall,
            val_result.overall_f1,
            val_result.degradation_detected,
            val_result.recommended_retrain,
        )

        # ── 5. Train final model on full dataset ──────────────────────────────
        final_model = self._make_model()
        try:
            final_model.fit(X_scaled, y)
        except Exception as exc:
            log.error("Final model fit failed: %s", exc, exc_info=True)
            return TrainingResult(
                success=False,
                n_samples=len(X),
                n_features=X.shape[1],
                validation_result=val_result,
            )

        self._model = final_model

        # ── 6. Feature importances ────────────────────────────────────────────
        fi = self._extract_importances(final_model, X_df.columns.tolist())
        self._feature_importances = fi

        # ── 7. Save model + scaler ────────────────────────────────────────────
        model_path = self._save_model(final_model)

        # ── 8. Log to DB ──────────────────────────────────────────────────────
        self._log_to_db(val_result, len(X), len(fi))

        result = TrainingResult(
            success=True,
            model_path=str(model_path),
            n_samples=len(X),
            n_features=X.shape[1],
            validation_result=val_result,
            feature_importances=fi,
        )

        self._publish_complete(result)
        return result

    # ── prediction ────────────────────────────────────────────────────────────

    def predict(self, market_data: dict, trade_history: list) -> float:
        """Return probability (0–1) that the next trade will be a winner.

        Loads the model from disk if not already in memory.
        Falls back to heuristic scorer when no model exists.
        """
        if self._model is None:
            self.load_model()

        try:
            row = self.feature_engine.compute_features(market_data, trade_history)
            X = row.reshape(1, -1).astype(np.float32)

            if self._scaler is not None:
                X = self._scaler.transform(X)

            model = self._model if self._model is not None else _HeuristicScorer()
            prob = float(model.predict_proba(X)[0, 1])
            return float(np.clip(prob, 0.0, 1.0))
        except Exception as exc:
            log.warning("predict() failed: %s — returning 0.5 (neutral)", exc)
            return 0.5

    def load_model(self) -> bool:
        """Load model + scaler from disk. Returns True on success."""
        try:
            model_file = self._model_path
            if not model_file.exists():
                log.warning("Model file not found: %s", model_file)
                self._model = _HeuristicScorer()
                return False

            self._model = joblib.load(str(model_file))

            scaler_file = model_file.with_suffix(".scaler.pkl")
            if scaler_file.exists():
                self._scaler = joblib.load(str(scaler_file))

            fi_file = model_file.with_suffix(".importances.json")
            if fi_file.exists():
                with open(fi_file) as fh:
                    self._feature_importances = json.load(fh)

            log.info("Model loaded from %s", model_file)
            return True
        except Exception as exc:
            log.warning("load_model() failed: %s — using heuristic fallback", exc)
            self._model = _HeuristicScorer()
            return False

    # ── degradation detection ─────────────────────────────────────────────────

    def check_degradation(self, recent_trades: list) -> bool:
        """Return True when the model's recent win-rate is below retrain_threshold.

        recent_trades: list of trade dicts with 'pnl' and, optionally,
        'predicted_prob' (float, 0–1) stored by the signal engine.
        """
        if not recent_trades:
            return False

        correct = 0
        total   = 0
        for t in recent_trades:
            pnl = t.get("pnl")
            if pnl is None:
                continue
            actual_win = 1 if pnl > 0 else 0
            # Use predicted_prob if available, else fall back to predict()
            pred_prob = t.get("predicted_prob")
            if pred_prob is None:
                continue
            predicted_win = 1 if pred_prob >= 0.5 else 0
            correct += int(predicted_win == actual_win)
            total   += 1

        if total < 5:
            log.debug("Not enough graded trades (%d) to assess degradation.", total)
            return False

        recent_accuracy = correct / total
        degraded = recent_accuracy < self._retrain_threshold
        if degraded:
            log.warning(
                "Model degradation: recent accuracy %.3f < threshold %.3f "
                "(%d trades sampled)",
                recent_accuracy, self._retrain_threshold, total,
            )
        return degraded

    # ── feature importances ───────────────────────────────────────────────────

    def get_feature_importances(self) -> Dict[str, float]:
        """Return feature importances sorted descending by importance score."""
        return dict(
            sorted(self._feature_importances.items(), key=lambda kv: -kv[1])
        )

    # ── private helpers ───────────────────────────────────────────────────────

    def _make_model(self):
        """Instantiate a fresh model (XGB or heuristic fallback)."""
        if _XGB_AVAILABLE:
            return XGBClassifier(**self.XGB_PARAMS)
        return _HeuristicScorer()

    def _build_labels(
        self, market_data_history: list, trade_history: list
    ) -> List[int]:
        """Build binary win/loss labels aligned with market_data_history rows.

        Strategy:
          - If a bar dict contains 'pnl' directly → use it.
          - If a bar contains 'trade_id', look up pnl from trade_history.
          - If a bar contains 'signal_id', look up from trade_history by signal_id.
          - Default to 0 (loss) when no pnl is resolvable.
        """
        # Index trade history for O(1) lookups
        by_id: dict = {}
        by_signal: dict = {}
        for t in (trade_history or []):
            if isinstance(t, dict):
                tid = t.get("id")
                sid = t.get("signal_id")
                pnl = t.get("pnl", 0.0)
                if tid is not None:
                    by_id[tid] = pnl
                if sid is not None:
                    by_signal[sid] = pnl

        labels: List[int] = []
        for bar in market_data_history:
            pnl = bar.get("pnl")
            if pnl is None:
                trade_id = bar.get("trade_id")
                if trade_id is not None:
                    pnl = by_id.get(trade_id)
            if pnl is None:
                signal_id = bar.get("signal_id")
                if signal_id is not None:
                    pnl = by_signal.get(signal_id)
            if pnl is None:
                pnl = 0.0
            labels.append(1 if float(pnl) > 0 else 0)

        return labels

    def _save_model(self, model) -> Path:
        """Persist model, scaler, and feature importances to disk."""
        path = self._model_path
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            joblib.dump(model, str(path))
        except Exception as exc:
            import pickle
            with open(str(path), "wb") as fh:
                pickle.dump(model, fh)
            log.debug("Saved model with pickle (joblib failed: %s)", exc)

        if self._scaler is not None:
            scaler_path = path.with_suffix(".scaler.pkl")
            try:
                joblib.dump(self._scaler, str(scaler_path))
            except Exception:
                import pickle
                with open(str(scaler_path), "wb") as fh:
                    pickle.dump(self._scaler, fh)

        fi_path = path.with_suffix(".importances.json")
        with open(fi_path, "w") as fh:
            json.dump(self._feature_importances, fh, indent=2)

        log.info("Model saved to %s", path)
        return path

    def _extract_importances(
        self, model, feature_names: List[str]
    ) -> Dict[str, float]:
        """Extract feature importances from the trained model."""
        try:
            fi_raw: np.ndarray = model.feature_importances_
            if fi_raw is None or len(fi_raw) == 0:
                return {}
            names = feature_names[: len(fi_raw)]
            return {name: float(fi_raw[i]) for i, name in enumerate(names)}
        except AttributeError:
            return {}

    def _log_to_db(self, val_result: ValidationResult, n_samples: int, n_features: int):
        """Persist training metrics to the database."""
        try:
            self.db.log_model_performance({
                "timestamp": time.time(),
                "strategy_name": "ml_scorer",
                "accuracy": val_result.overall_accuracy,
                "precision_score": val_result.overall_precision,
                "recall": val_result.overall_recall,
                "f1": val_result.overall_f1,
                "sharpe": 0.0,
                "win_rate": val_result.overall_accuracy,
                "profit_factor": float(np.mean([
                    m.profit_factor for m in val_result.per_fold_metrics
                ])) if val_result.per_fold_metrics else 0.0,
                "sample_size": n_samples,
                "notes": json.dumps({
                    "n_features": n_features,
                    "degradation": val_result.degradation_detected,
                    "recommended_retrain": val_result.recommended_retrain,
                }),
            })

            # Also record in ml_training_runs table if it exists
            try:
                self.db.insert_training_run({
                    "timestamp": time.time(),
                    "model_name": "xgb_scorer",
                    "accuracy": val_result.overall_accuracy,
                    "precision_score": val_result.overall_precision,
                    "recall": val_result.overall_recall,
                    "f1": val_result.overall_f1,
                    "sharpe": 0.0,
                    "win_rate": val_result.overall_accuracy,
                    "profit_factor": float(np.mean([
                        m.profit_factor for m in val_result.per_fold_metrics
                    ])) if val_result.per_fold_metrics else 0.0,
                    "features_used": json.dumps(
                        list(self._feature_importances.keys())[:20]
                    ),
                    "hyperparams": json.dumps(self.XGB_PARAMS),
                    "notes": (
                        f"samples={n_samples} "
                        f"degradation={val_result.degradation_detected}"
                    ),
                })
            except Exception:
                pass  # ml_training_runs insert is best-effort

        except Exception as exc:
            log.warning("Failed to log training metrics to DB: %s", exc)

    def _publish_started(self):
        if self.event_bus is None:
            return
        try:
            from ..event_bus import Event, EventType
            self.event_bus.publish(Event(
                type=EventType.ML_TRAINING_STARTED,
                data={"status": "started"},
                source="ModelTrainer",
            ))
        except Exception as exc:
            log.debug("Event publish ML_TRAINING_STARTED failed: %s", exc)

    def _publish_complete(self, result: TrainingResult):
        if self.event_bus is None:
            return
        try:
            from ..event_bus import Event, EventType
            self.event_bus.publish(Event(
                type=EventType.ML_TRAINING_COMPLETE,
                data={
                    "success": result.success,
                    "n_samples": result.n_samples,
                    "n_features": result.n_features,
                    "model_path": result.model_path,
                    "overall_accuracy": (
                        result.validation_result.overall_accuracy
                        if result.validation_result else 0.0
                    ),
                    "recommended_retrain": (
                        result.validation_result.recommended_retrain
                        if result.validation_result else False
                    ),
                },
                source="ModelTrainer",
            ))
        except Exception as exc:
            log.debug("Event publish ML_TRAINING_COMPLETE failed: %s", exc)
