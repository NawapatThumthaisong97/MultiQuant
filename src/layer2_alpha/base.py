"""
Base Abstract Strategy Contract for Layer 2 Alpha Engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import polars as pl

from src.core.models import AlphaSignal


class BaseAlphaStrategy(ABC):
    """Abstract Base Class for all quantitative alpha generation strategies."""

    def __init__(self, name: str, warmup_period: int = 30) -> None:
        self.name = name
        self.warmup_period = warmup_period
        self.parameters: dict[str, Any] = {}

    @abstractmethod
    def generate_signal(self, symbol: str, df: pl.DataFrame) -> AlphaSignal | None:
        """
        Evaluate market data and generate AlphaSignal.
        Returns None if no signal or if data length is below warmup_period.
        """
        pass

    def is_warmed_up(self, df: pl.DataFrame) -> bool:
        """Check if DataFrame has enough rows to satisfy strategy warmup."""
        return len(df) >= self.warmup_period
