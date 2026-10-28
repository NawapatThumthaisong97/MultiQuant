"""
Alpha Engine Coordinator.
Coordinates rolling buffers, executes strategies concurrently, and routes AlphaSignals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine

from src.core.models import AlphaSignal, BarData
from src.layer2_alpha.base import BaseAlphaStrategy
from src.layer2_alpha.buffer import RollingBarBuffer

logger = logging.getLogger("multiquant.alpha_engine")


class AlphaEngine:
    """Manages strategy execution pipeline over incoming candle bars."""

    def __init__(
        self,
        buffer_size: int = 500,
        on_signal: Callable[[AlphaSignal], Coroutine[None, None, None]] | None = None,
    ) -> None:
        self.buffer = RollingBarBuffer(maxlen=buffer_size)
        self.strategies: list[BaseAlphaStrategy] = []
        self.on_signal = on_signal

    def register_strategy(self, strategy: BaseAlphaStrategy) -> None:
        """Add a trading strategy to the engine."""
        self.strategies.append(strategy)
        logger.info(
            "Registered Alpha Strategy: %s (warmup: %d)", strategy.name, strategy.warmup_period
        )

    async def on_bar(self, bar: BarData) -> list[AlphaSignal]:
        """
        Process a new candle bar:
        1. Store bar in rolling buffer.
        2. Convert to Polars DataFrame.
        3. Evaluate all registered strategies.
        4. Emit AlphaSignal events.
        """
        self.buffer.add_bar(bar)
        df = self.buffer.to_polars(bar.symbol)
        if df is None:
            return []

        generated_signals: list[AlphaSignal] = []

        for strat in self.strategies:
            try:
                signal = strat.generate_signal(bar.symbol, df)
                if signal is not None:
                    generated_signals.append(signal)
                    logger.info(
                        "Signal generated: [%s] %s for %s (strength=%.2f)",
                        strat.name,
                        signal.direction,
                        signal.symbol,
                        signal.strength,
                    )
                    if self.on_signal:
                        await self.on_signal(signal)
            except Exception as e:
                logger.error(
                    "Error evaluating strategy %s on %s: %s",
                    strat.name,
                    bar.symbol,
                    e,
                    exc_info=True,
                )

        return generated_signals
