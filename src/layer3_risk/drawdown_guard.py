"""
Drawdown Guard & Circuit Breaker.
Monitors high-water mark, tracks maximum drawdown, and enforces capital protection rules.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from src.core.models import PortfolioState

logger = logging.getLogger("multiquant.drawdown_guard")


class CircuitBreakerStatus(StrEnum):
    NORMAL = "NORMAL"
    REDUCE_RISK = "REDUCE_RISK"
    HALT_TRADING = "HALT_TRADING"
    EMERGENCY_LIQUIDATE = "EMERGENCY_LIQUIDATE"


class DrawdownGuard:
    """
    Capital preservation circuit breaker system.
    Tracks equity vs High Water Mark (HWM) and activates graduated defense modes.
    """

    def __init__(
        self,
        warning_drawdown_pct: float = 0.08,  # 8% DD -> Reduce position sizing by 50%
        max_drawdown_pct: float = 0.15,  # 15% DD -> Halt all new orders
        emergency_drawdown_pct: float = 0.20,  # 20% DD -> Liquidate all positions
    ) -> None:
        self.warning_drawdown_pct = warning_drawdown_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.emergency_drawdown_pct = emergency_drawdown_pct
        self.peak_equity: float = 0.0
        self.current_status: CircuitBreakerStatus = CircuitBreakerStatus.NORMAL

    def update_equity(self, portfolio: PortfolioState) -> CircuitBreakerStatus:
        """Update peak equity and evaluate circuit breaker status."""
        current_equity = portfolio.total_equity

        # Update High-Water Mark (HWM)
        self.peak_equity = max(self.peak_equity, portfolio.peak_equity, current_equity)

        if self.peak_equity <= 0:
            self.current_status = CircuitBreakerStatus.NORMAL
            return self.current_status

        # Calculate current drawdown percentage
        drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity

        if drawdown_pct >= self.emergency_drawdown_pct:
            if self.current_status != CircuitBreakerStatus.EMERGENCY_LIQUIDATE:
                logger.critical(
                    "EMERGENCY CIRCUIT BREAKER TRIGGERED: Drawdown is %.2f%% (limit: %.2f%%). Liquidation required!",
                    drawdown_pct * 100.0,
                    self.emergency_drawdown_pct * 100.0,
                )
            self.current_status = CircuitBreakerStatus.EMERGENCY_LIQUIDATE

        elif drawdown_pct >= self.max_drawdown_pct:
            if self.current_status != CircuitBreakerStatus.HALT_TRADING:
                logger.error(
                    "TRADING HALTED: Drawdown is %.2f%% (limit: %.2f%%). No new orders allowed.",
                    drawdown_pct * 100.0,
                    self.max_drawdown_pct * 100.0,
                )
            self.current_status = CircuitBreakerStatus.HALT_TRADING

        elif drawdown_pct >= self.warning_drawdown_pct:
            if self.current_status != CircuitBreakerStatus.REDUCE_RISK:
                logger.warning(
                    "RISK REDUCTION ACTIVE: Drawdown is %.2f%% (threshold: %.2f%%). Sizing cut by 50%%.",
                    drawdown_pct * 100.0,
                    self.warning_drawdown_pct * 100.0,
                )
            self.current_status = CircuitBreakerStatus.REDUCE_RISK

        else:
            self.current_status = CircuitBreakerStatus.NORMAL

        return self.current_status

    def can_open_new_positions(self) -> bool:
        """Returns True if trading conditions permit opening new positions."""
        return self.current_status in (
            CircuitBreakerStatus.NORMAL,
            CircuitBreakerStatus.REDUCE_RISK,
        )

    def get_sizing_multiplier(self) -> float:
        """Returns risk multiplier based on current circuit breaker state."""
        if self.current_status == CircuitBreakerStatus.REDUCE_RISK:
            return 0.5  # Cut risk in half
        elif self.current_status in (
            CircuitBreakerStatus.HALT_TRADING,
            CircuitBreakerStatus.EMERGENCY_LIQUIDATE,
        ):
            return 0.0  # Zero size
        return 1.0
