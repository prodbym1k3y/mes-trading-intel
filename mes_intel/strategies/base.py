"""Base class for all quantitative strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyResult:
    """Output from a strategy evaluation."""
    name: str
    score: float           # -1.0 (strong short) to +1.0 (strong long), 0 = neutral
    confidence: float      # 0.0 to 1.0
    direction: str         # "LONG", "SHORT", or "FLAT"
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    meta: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.direction != "FLAT" and self.confidence > 0.3


class Strategy(ABC):
    """Base class for quantitative strategies."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    def evaluate(self, market_data: dict) -> StrategyResult:
        """Evaluate the strategy given current market data.

        Args:
            market_data: Dict with keys like 'prices', 'volumes', 'vwap',
                         'orderflow', 'gex', etc. depending on strategy needs.

        Returns:
            StrategyResult with score, confidence, and direction.
        """
        ...

    @abstractmethod
    def required_data(self) -> list[str]:
        """List of market_data keys this strategy needs."""
        ...
