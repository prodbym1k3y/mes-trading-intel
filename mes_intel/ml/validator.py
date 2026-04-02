"""Walk-forward validator for MES Intel ML pipeline.

Time-series cross-validation that strictly respects temporal ordering.
A configurable gap between each train/test split prevents look-ahead bias.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterator, List, Tuple

import numpy as np

log = logging.getLogger(__name__)


# ── result containers ─────────────────────────────────────────────────────────

@dataclass
class FoldMetrics:
    """Per-fold evaluation metrics."""
    fold_idx: int
    train_size: int
    test_size: int
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    profit_factor: float = 0.0

    def to_dict(self) -> dict:
        return {
            "fold_idx": self.fold_idx,
            "train_size": self.train_size,
            "test_size": self.test_size,
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "profit_factor": self.profit_factor,
        }


@dataclass
class ValidationResult:
    """Aggregate results across all walk-forward folds."""
    per_fold_metrics: List[FoldMetrics] = field(default_factory=list)
    overall_accuracy: float = 0.0
    overall_precision: float = 0.0
    overall_recall: float = 0.0
    overall_f1: float = 0.0
    degradation_detected: bool = False
    recommended_retrain: bool = False

    def to_dict(self) -> dict:
        return {
            "overall_accuracy": self.overall_accuracy,
            "overall_precision": self.overall_precision,
            "overall_recall": self.overall_recall,
            "overall_f1": self.overall_f1,
            "degradation_detected": self.degradation_detected,
            "recommended_retrain": self.recommended_retrain,
            "folds": [m.to_dict() for m in self.per_fold_metrics],
        }


# ── helpers ───────────────────────────────────────────────────────────────────

def _classification_metrics(
    y_true: np.ndarray, y_pred: np.ndarray
) -> Tuple[float, float, float, float]:
    """Return (accuracy, precision, recall, f1) from binary arrays."""
    tp = int(np.sum((y_pred == 1) & (y_true == 1)))
    fp = int(np.sum((y_pred == 1) & (y_true == 0)))
    fn = int(np.sum((y_pred == 0) & (y_true == 1)))
    tn = int(np.sum((y_pred == 0) & (y_true == 0)))

    total = tp + tn + fp + fn
    accuracy  = (tp + tn) / max(total, 1)
    precision = tp / max(tp + fp, 1)
    recall    = tp / max(tp + fn, 1)
    denom     = precision + recall
    f1        = (2 * precision * recall / denom) if denom > 0 else 0.0
    return accuracy, precision, recall, f1


def _profit_factor_sim(
    probs: np.ndarray, y_true: np.ndarray,
    threshold: float = 0.6,
    win_r: float = 2.0,
    loss_r: float = 1.0,
) -> float:
    """Simulate trading: take trades where predicted_prob > threshold.

    Rules:
      - predicted_prob > threshold AND actual=1  → winning trade (R = win_r)
      - predicted_prob > threshold AND actual=0  → losing trade  (R = -loss_r)

    Returns profit_factor (gross_wins / gross_losses), or 0.0 if no trades.
    """
    mask = probs > threshold
    if not np.any(mask):
        return 0.0

    actuals = y_true[mask]
    wins   = float(np.sum(actuals == 1)) * win_r
    losses = float(np.sum(actuals == 0)) * loss_r
    if losses < 1e-9:
        return wins if wins > 0 else 0.0
    return wins / losses


# ── WalkForwardValidator ──────────────────────────────────────────────────────

class WalkForwardValidator:
    """Time-series walk-forward cross-validator.

    The dataset is never shuffled; each fold's test window comes strictly
    after its training window, separated by a gap to prevent look-ahead.

    Args:
        n_splits:   Number of folds (default 5).
        train_pct:  Fraction of data allocated to training in the first fold
                    (default 0.7). Subsequent folds grow the train set.
        gap_size:   Number of bars skipped between train end and test start
                    to avoid leakage (default 10).
    """

    def __init__(
        self,
        n_splits: int = 5,
        train_pct: float = 0.7,
        gap_size: int = 10,
    ):
        self.n_splits  = n_splits
        self.train_pct = train_pct
        self.gap_size  = gap_size

    # ── public API ────────────────────────────────────────────────────────────

    def split(
        self, X: np.ndarray, y: np.ndarray
    ) -> Iterator[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """Yield (train_X, test_X, train_y, test_y) for each fold.

        Walk-forward schedule:
          - The data is divided into (n_splits + 1) equal blocks.
          - Fold k trains on blocks [0 .. k] and tests on block [k+1],
            with gap_size samples removed from the boundary.
          - Indices are strictly increasing; no data is re-used in test.
        """
        n = len(X)
        if n < self.n_splits * 2 + self.gap_size:
            log.warning(
                "Dataset too small (%d rows) for %d splits with gap %d; "
                "reducing splits.", n, self.n_splits, self.gap_size
            )
            effective_splits = max(2, n // (2 + self.gap_size))
        else:
            effective_splits = self.n_splits

        block = n // (effective_splits + 1)

        for fold in range(effective_splits):
            train_end  = (fold + 1) * block
            test_start = train_end + self.gap_size
            test_end   = min(test_start + block, n)

            if test_start >= n or test_end <= test_start:
                continue

            yield (
                X[:train_end],
                X[test_start:test_end],
                y[:train_end],
                y[test_start:test_end],
            )

    def evaluate_model(self, model, X: np.ndarray, y: np.ndarray) -> ValidationResult:
        """Run walk-forward evaluation and return a ValidationResult.

        The model is re-fitted from scratch on each training split.

        Degradation is flagged when the per-fold accuracy is monotonically
        decreasing across folds (a reliable sign of concept drift).

        Retrain is recommended when degradation is detected OR when
        the final fold accuracy falls below 0.55.
        """
        fold_metrics: List[FoldMetrics] = []

        for fold_idx, (X_tr, X_te, y_tr, y_te) in enumerate(self.split(X, y)):
            if len(X_tr) < 2 or len(X_te) < 1:
                continue

            try:
                model.fit(X_tr, y_tr)
            except Exception as exc:
                log.warning("Fold %d: model.fit failed: %s", fold_idx, exc)
                continue

            # Predict class labels
            try:
                y_pred = model.predict(X_te)
            except Exception as exc:
                log.warning("Fold %d: model.predict failed: %s", fold_idx, exc)
                continue

            acc, prec, rec, f1 = _classification_metrics(y_te, y_pred)

            # Predict probabilities for profit_factor simulation
            try:
                probs = model.predict_proba(X_te)[:, 1]
            except (AttributeError, IndexError):
                # Fall back: treat predicted label as probability
                probs = y_pred.astype(np.float64)

            pf = _profit_factor_sim(probs, y_te)

            fm = FoldMetrics(
                fold_idx=fold_idx,
                train_size=len(X_tr),
                test_size=len(X_te),
                accuracy=acc,
                precision=prec,
                recall=rec,
                f1=f1,
                profit_factor=pf,
            )
            fold_metrics.append(fm)
            log.debug(
                "Fold %d — train=%d test=%d acc=%.3f prec=%.3f rec=%.3f "
                "f1=%.3f pf=%.2f",
                fold_idx, fm.train_size, fm.test_size,
                fm.accuracy, fm.precision, fm.recall, fm.f1, fm.profit_factor,
            )

        if not fold_metrics:
            return ValidationResult(recommended_retrain=True)

        accs  = [m.accuracy  for m in fold_metrics]
        precs = [m.precision for m in fold_metrics]
        recs  = [m.recall    for m in fold_metrics]
        f1s   = [m.f1        for m in fold_metrics]

        overall_acc  = float(np.mean(accs))
        overall_prec = float(np.mean(precs))
        overall_rec  = float(np.mean(recs))
        overall_f1   = float(np.mean(f1s))

        # Degradation: accuracy is strictly decreasing across folds
        degradation = len(accs) >= 3 and all(
            accs[i] > accs[i + 1] for i in range(len(accs) - 1)
        )

        # Retrain if degradation OR final fold accuracy is weak
        recommended_retrain = degradation or (accs[-1] < 0.55)

        return ValidationResult(
            per_fold_metrics=fold_metrics,
            overall_accuracy=overall_acc,
            overall_precision=overall_prec,
            overall_recall=overall_rec,
            overall_f1=overall_f1,
            degradation_detected=degradation,
            recommended_retrain=recommended_retrain,
        )
