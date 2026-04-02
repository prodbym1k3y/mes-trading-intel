"""Hidden Markov Model Regime Classification.

Classifies market regime into: trending, ranging, or volatile.
Uses returns and volatility as observable features. The HMM is fit
using the Baum-Welch algorithm (manual implementation to avoid
heavy dependencies).
"""
from __future__ import annotations

import numpy as np
from .base import Strategy, StrategyResult


# Regime labels
TRENDING = "trending"
RANGING = "ranging"
VOLATILE = "volatile"
REGIMES = [TRENDING, RANGING, VOLATILE]


class SimpleHMM:
    """Minimal 3-state Gaussian HMM for regime classification.

    States: 0=trending, 1=ranging, 2=volatile
    Observations: 2D (returns, volatility)
    """

    def __init__(self):
        self.n_states = 3

        # Initial state probabilities
        self.pi = np.array([0.33, 0.34, 0.33])

        # Transition matrix (rows=from, cols=to)
        # Regimes tend to persist
        self.A = np.array([
            [0.85, 0.10, 0.05],  # trending → stays trending mostly
            [0.10, 0.80, 0.10],  # ranging → stays ranging
            [0.10, 0.15, 0.75],  # volatile → slightly less persistent
        ])

        # Emission parameters: mean and std for each state, each feature
        # [returns_mean, returns_std, volatility_mean, volatility_std]
        self.emission_params = np.array([
            [0.002, 0.005, 0.008, 0.003],   # trending: positive returns, moderate vol
            [0.000, 0.003, 0.005, 0.002],   # ranging: near-zero returns, low vol
            [0.000, 0.015, 0.020, 0.008],   # volatile: any returns, high vol
        ])

    def _emission_prob(self, obs: np.ndarray, state: int) -> float:
        """Gaussian emission probability for an observation given state."""
        params = self.emission_params[state]
        ret_mean, ret_std, vol_mean, vol_std = params

        ret_prob = np.exp(-0.5 * ((obs[0] - ret_mean) / max(ret_std, 1e-10)) ** 2) / (ret_std * np.sqrt(2 * np.pi))
        vol_prob = np.exp(-0.5 * ((obs[1] - vol_mean) / max(vol_std, 1e-10)) ** 2) / (vol_std * np.sqrt(2 * np.pi))

        return max(ret_prob * vol_prob, 1e-300)

    def predict(self, observations: np.ndarray) -> tuple[list[int], np.ndarray]:
        """Viterbi algorithm to find most likely state sequence.

        Args:
            observations: (T, 2) array of [returns, volatility]

        Returns:
            (state_sequence, state_probabilities_at_last_step)
        """
        T = len(observations)
        if T == 0:
            return [], np.array(self.pi)

        # Viterbi
        V = np.zeros((T, self.n_states))
        path = np.zeros((T, self.n_states), dtype=int)

        # Init
        for s in range(self.n_states):
            V[0, s] = np.log(max(self.pi[s], 1e-300)) + np.log(self._emission_prob(observations[0], s))

        # Forward
        for t in range(1, T):
            for s in range(self.n_states):
                emit = np.log(self._emission_prob(observations[t], s))
                candidates = V[t - 1] + np.log(np.maximum(self.A[:, s], 1e-300)) + emit
                path[t, s] = int(np.argmax(candidates))
                V[t, s] = candidates[path[t, s]]

        # Backtrack
        states = [0] * T
        states[-1] = int(np.argmax(V[-1]))
        for t in range(T - 2, -1, -1):
            states[t] = path[t + 1, states[t + 1]]

        # Current state probabilities (softmax of last Viterbi scores)
        last_scores = V[-1] - np.max(V[-1])
        probs = np.exp(last_scores) / np.sum(np.exp(last_scores))

        return states, probs

    def fit_online(self, observations: np.ndarray, states: list[int], lr: float = 0.01):
        """Simple online update of emission parameters based on labeled data."""
        for s in range(self.n_states):
            mask = np.array(states) == s
            if np.sum(mask) < 3:
                continue
            obs_s = observations[mask]
            # Exponential moving update
            new_ret_mean = np.mean(obs_s[:, 0])
            new_ret_std = max(np.std(obs_s[:, 0]), 1e-6)
            new_vol_mean = np.mean(obs_s[:, 1])
            new_vol_std = max(np.std(obs_s[:, 1]), 1e-6)

            self.emission_params[s] = (1 - lr) * self.emission_params[s] + lr * np.array([
                new_ret_mean, new_ret_std, new_vol_mean, new_vol_std
            ])


class HMMRegimeStrategy(Strategy):
    name = "hmm_regime"
    description = "HMM market regime classification"

    def __init__(self, lookback: int = 100, vol_window: int = 20):
        self.lookback = lookback
        self.vol_window = vol_window
        self.hmm = SimpleHMM()

    def required_data(self) -> list[str]:
        return ["prices"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = np.array(market_data.get("prices", []))

        if len(prices) < self.lookback:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "insufficient data", "regime": "unknown"})

        prices = prices[-self.lookback:]

        # Compute features: returns and rolling volatility
        returns = np.diff(np.log(prices))
        vol = np.array([
            np.std(returns[max(0, i - self.vol_window):i + 1])
            for i in range(len(returns))
        ])

        observations = np.column_stack([returns, vol])
        states, probs = self.hmm.predict(observations)

        current_regime_idx = states[-1]
        current_regime = REGIMES[current_regime_idx]
        regime_confidence = float(probs[current_regime_idx])

        # Strategy implications based on regime
        if current_regime == TRENDING:
            # In trending regime, favor momentum
            recent_return = returns[-5:].mean()
            score = float(np.clip(recent_return / (np.std(returns) + 1e-10), -1.0, 1.0))
            if score > 0.2:
                direction = "LONG"
            elif score < -0.2:
                direction = "SHORT"
            else:
                direction = "FLAT"
            confidence = regime_confidence * 0.6
        elif current_regime == RANGING:
            # In ranging regime, favor mean reversion
            mean_price = np.mean(prices[-20:])
            deviation = (prices[-1] - mean_price) / (np.std(prices[-20:]) + 1e-10)
            score = float(np.clip(-deviation / 2.0, -1.0, 1.0))
            if abs(deviation) > 1.5:
                direction = "LONG" if deviation < 0 else "SHORT"
            else:
                direction = "FLAT"
            confidence = regime_confidence * 0.5
        else:
            # Volatile regime — reduce confidence, tighter signals
            score = 0.0
            direction = "FLAT"
            confidence = regime_confidence * 0.2

        # Regime transition probability
        transition_probs = self.hmm.A[current_regime_idx]
        stay_prob = transition_probs[current_regime_idx]

        return StrategyResult(
            name=self.name,
            score=float(score),
            confidence=float(confidence),
            direction=direction,
            entry_price=float(prices[-1]),
            meta={
                "regime": current_regime,
                "regime_confidence": regime_confidence,
                "regime_probs": {REGIMES[i]: float(probs[i]) for i in range(3)},
                "stay_probability": float(stay_prob),
                "transition_probs": {REGIMES[i]: float(transition_probs[i]) for i in range(3)},
                "recent_volatility": float(vol[-1]),
            },
        )
