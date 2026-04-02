"""XGBoost ML Trade Scorer — trained on historical trade outcomes.

Features engineered from price action, order flow, volatility, and
strategy agreement. Walk-forward validation to prevent overfitting.

Falls back to a logistic-regression-style scorer when XGBoost is not
installed, so the system always works.
"""
from __future__ import annotations

import json
import logging
import pickle
import time
import numpy as np
from pathlib import Path
from .base import Strategy, StrategyResult

log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent.parent / "var" / "mes_intel" / "models"


def engineer_features(market_data: dict, strategy_results: list[dict] | None = None) -> np.ndarray:
    """Create feature vector from market data and strategy outputs.

    Features (20):
        0: return_1bar
        1: return_5bar
        2: return_10bar
        3: volatility_10bar
        4: volatility_20bar
        5: vol_ratio (10/20)
        6: rsi_14
        7: price_vs_vwap
        8: cumulative_delta_norm
        9: delta_trend (slope of last 10 deltas)
        10: volume_surge (current vs avg)
        11: poc_distance
        12-17: strategy agreement scores (6 strategies)
        18: num_strategies_long
        19: num_strategies_short
    """
    prices = np.array(market_data.get("prices", [0.0]))
    volumes = np.array(market_data.get("volumes", [0]))

    features = np.zeros(20)

    if len(prices) < 2:
        return features

    # Returns
    features[0] = (prices[-1] / prices[-2] - 1) if len(prices) >= 2 else 0
    features[1] = (prices[-1] / prices[-5] - 1) if len(prices) >= 5 else 0
    features[2] = (prices[-1] / prices[-10] - 1) if len(prices) >= 10 else 0

    # Volatility
    if len(prices) >= 10:
        rets = np.diff(np.log(prices[-11:]))
        features[3] = np.std(rets[-10:])
    if len(prices) >= 20:
        rets_20 = np.diff(np.log(prices[-21:]))
        features[4] = np.std(rets_20)
        features[5] = features[3] / max(features[4], 1e-10)

    # RSI
    if len(prices) >= 15:
        deltas = np.diff(prices[-15:])
        gains = np.mean(deltas[deltas > 0]) if np.any(deltas > 0) else 0
        losses = -np.mean(deltas[deltas < 0]) if np.any(deltas < 0) else 0
        rs = gains / max(losses, 1e-10)
        features[6] = (100 - 100 / (1 + rs)) / 100.0  # normalize to 0-1

    # VWAP
    vwap = market_data.get("vwap")
    if vwap and prices[-1] > 0:
        features[7] = (prices[-1] - vwap) / prices[-1]

    # Order flow
    cum_delta = market_data.get("cumulative_delta", 0)
    total_vol = max(sum(volumes[-20:]), 1)
    features[8] = cum_delta / max(total_vol, 1)

    recent_deltas = market_data.get("recent_deltas", [])
    if len(recent_deltas) >= 5:
        features[9] = np.polyfit(range(len(recent_deltas[-10:])), recent_deltas[-10:], 1)[0]

    if len(volumes) >= 20:
        features[10] = volumes[-1] / max(np.mean(volumes[-20:]), 1)

    poc = market_data.get("poc")
    if poc and prices[-1] > 0:
        features[11] = (prices[-1] - poc) / prices[-1]

    # Strategy scores
    if strategy_results:
        for i, sr in enumerate(strategy_results[:6]):
            features[12 + i] = sr.get("score", 0.0)
        features[18] = sum(1 for sr in strategy_results if sr.get("direction") == "LONG")
        features[19] = sum(1 for sr in strategy_results if sr.get("direction") == "SHORT")

    return features


class MLScorer(Strategy):
    name = "ml_scorer"
    description = "XGBoost ML trade quality scorer"

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or str(MODEL_DIR / "xgb_scorer.pkl")
        self.model = None
        self.feature_names = [
            "ret_1", "ret_5", "ret_10", "vol_10", "vol_20", "vol_ratio",
            "rsi", "vwap_dist", "cum_delta_norm", "delta_trend",
            "vol_surge", "poc_dist",
            "strat_0", "strat_1", "strat_2", "strat_3", "strat_4", "strat_5",
            "n_long", "n_short",
        ]
        self._load_model()

    def _load_model(self):
        path = Path(self.model_path)
        if path.exists():
            try:
                with open(path, "rb") as f:
                    self.model = pickle.load(f)
                log.info("Loaded ML model from %s", path)
            except Exception as e:
                log.warning("Failed to load ML model: %s", e)
                self.model = None

    def save_model(self):
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self.model, f)

    def required_data(self) -> list[str]:
        return ["prices", "volumes"]

    def _fallback_score(self, features: np.ndarray) -> tuple[float, float]:
        """Simple heuristic scorer when no trained model exists."""
        # Weighted combination of key features
        weights = np.array([
            0.1, 0.15, 0.1,     # returns (momentum)
            -0.1, -0.05, -0.05, # volatility (penalize high vol)
            0.0, 0.1, 0.15,     # RSI neutral, VWAP, delta
            0.1, 0.05, 0.05,    # delta trend, volume, POC
            0.15, 0.15, 0.1, 0.1, 0.1, 0.1,  # strategy agreement
            0.2, -0.2,          # n_long, n_short
        ])
        raw = float(np.dot(features, weights[:len(features)]))
        score = float(np.clip(raw * 5, -1.0, 1.0))
        confidence = min(abs(score), 1.0) * 0.5  # lower confidence for heuristic
        return score, confidence

    def evaluate(self, market_data: dict) -> StrategyResult:
        strategy_results = market_data.get("strategy_results", [])
        features = engineer_features(market_data, strategy_results)

        if self.model is not None:
            try:
                X = features.reshape(1, -1)
                # XGBoost predict_proba returns [P(loss), P(win)]
                proba = self.model.predict_proba(X)[0]
                win_prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
                score = (win_prob - 0.5) * 2  # map [0,1] → [-1,1]
                confidence = abs(win_prob - 0.5) * 2  # distance from 0.5
            except Exception as e:
                log.warning("ML prediction failed, using fallback: %s", e)
                score, confidence = self._fallback_score(features)
        else:
            score, confidence = self._fallback_score(features)

        if score > 0.3:
            direction = "LONG"
        elif score < -0.3:
            direction = "SHORT"
        else:
            direction = "FLAT"

        return StrategyResult(
            name=self.name,
            score=float(score),
            confidence=float(confidence),
            direction=direction,
            entry_price=float(market_data.get("prices", [0])[-1]),
            meta={
                "has_trained_model": self.model is not None,
                "features": {name: float(features[i]) for i, name in enumerate(self.feature_names)},
            },
        )

    def train(self, X: np.ndarray, y: np.ndarray, walk_forward_splits: int = 5):
        """Train the model with walk-forward validation.

        Args:
            X: Feature matrix (n_samples, 20)
            y: Labels (1=winning trade, 0=losing trade)
            walk_forward_splits: Number of walk-forward splits
        """
        try:
            from xgboost import XGBClassifier
        except ImportError:
            log.warning("XGBoost not installed. Install with: pip install xgboost")
            return None

        n = len(X)
        split_size = n // walk_forward_splits
        accuracies = []

        for i in range(walk_forward_splits - 1):
            train_end = (i + 1) * split_size
            test_end = min((i + 2) * split_size, n)

            X_train, y_train = X[:train_end], y[:train_end]
            X_test, y_test = X[train_end:test_end], y[train_end:test_end]

            model = XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=5,
                reg_alpha=0.1,
                reg_lambda=1.0,
                random_state=42,
                use_label_encoder=False,
                eval_metric="logloss",
            )
            model.fit(X_train, y_train, verbose=False)
            acc = float(np.mean(model.predict(X_test) == y_test))
            accuracies.append(acc)

        # Final model on all data
        self.model = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=5,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            use_label_encoder=False,
            eval_metric="logloss",
        )
        self.model.fit(X, y, verbose=False)
        self.save_model()

        avg_acc = float(np.mean(accuracies)) if accuracies else 0
        log.info("ML model trained. Walk-forward accuracy: %.3f", avg_acc)
        return {"walk_forward_accuracy": avg_acc, "accuracies": accuracies}
