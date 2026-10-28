"""
Central Pre-Trade Risk Manager.
The most critical layer in quantitative trading.
Enforces pre-trade risk gates, drawdown limits, and volatility-based sizing before orders reach OMS.
"""

from __future__ import annotations

import logging
import uuid

from src.core.models import (
    AlphaSignal,
    OrderRequest,
    OrderSide,
    OrderType,
    PortfolioState,
    SignalDirection,
)
from src.layer3_risk.drawdown_guard import DrawdownGuard
from src.layer3_risk.position_sizer import VolatilityPositionSizer

logger = logging.getLogger("multiquant.risk_manager")


class PreTradeRiskManager:
    """
    Central Pre-Trade Risk Gatekeeper.
    Intercepts AlphaSignals, runs risk verifications, and converts them to actionable OrderRequests.
    """

    def __init__(
        self,
        position_sizer: VolatilityPositionSizer | None = None,
        drawdown_guard: DrawdownGuard | None = None,
        max_open_positions: int = 5,
        max_leverage: float = 2.0,
    ) -> None:
        self.sizer = position_sizer or VolatilityPositionSizer()
        self.guard = drawdown_guard or DrawdownGuard()
        self.max_open_positions = max_open_positions
        self.max_leverage = max_leverage

    def evaluate_signal(
        self,
        signal: AlphaSignal,
        portfolio: PortfolioState,
        current_price: float,
    ) -> OrderRequest | None:
        """
        Evaluate AlphaSignal against pre-trade risk rules.
        Returns an approved OrderRequest if all risk gates pass; otherwise None.
        """
        # Gate 0: Validate basic signal properties
        if signal.direction == SignalDirection.FLAT:
            logger.debug("Risk Gate: FLAT signal for %s ignored.", signal.symbol)
            return None

        # Gate 1: Check Circuit Breaker & Drawdown state
        cb_status = self.guard.update_equity(portfolio)
        if not self.guard.can_open_new_positions():
            logger.warning(
                "Risk Gate 1 REJECT: Circuit breaker active (%s). Order for %s blocked.",
                cb_status,
                signal.symbol,
            )
            return None

        # Gate 2: Check Maximum Concurrent Positions
        active_positions = [p for p in portfolio.positions.values() if p.is_open]
        existing_pos = portfolio.positions.get(signal.symbol)

        # If opening a new symbol and already at max positions limit
        if (existing_pos is None or not existing_pos.is_open) and len(
            active_positions
        ) >= self.max_open_positions:
            logger.warning(
                "Risk Gate 2 REJECT: Maximum open positions (%d) reached. Order for %s blocked.",
                self.max_open_positions,
                signal.symbol,
            )
            return None

        # Gate 3: Calculate Volatility-Based Position Size
        sizing = self.sizer.calculate_size(signal, portfolio, current_price)
        if not sizing.is_valid:
            logger.warning(
                "Risk Gate 3 REJECT: Sizing calculation failed for %s: %s",
                signal.symbol,
                sizing.rejection_reason,
            )
            return None

        # Apply Circuit Breaker sizing modifier (e.g., 50% size if in warning zone)
        multiplier = self.guard.get_sizing_multiplier()
        adjusted_quantity = sizing.quantity * multiplier
        if adjusted_quantity <= 0.0:
            logger.warning("Risk Gate 3 REJECT: Adjusted quantity is 0 for %s", signal.symbol)
            return None

        # Gate 4: Portfolio Total Leverage Check
        total_notional = sum(p.notional_value for p in active_positions) + (
            adjusted_quantity * current_price
        )
        implied_leverage = total_notional / max(1.0, portfolio.total_equity)
        if implied_leverage > self.max_leverage:
            logger.warning(
                "Risk Gate 4 REJECT: Implied leverage (%.2fx) exceeds max limit (%.2fx) for %s.",
                implied_leverage,
                self.max_leverage,
                signal.symbol,
            )
            return None

        # All Gates Passed: Construct Strongly-Typed OrderRequest
        side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL
        order_id = f"ord_{uuid.uuid4().hex[:12]}"

        order = OrderRequest(
            order_id=order_id,
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=adjusted_quantity,
            price=current_price,
            stop_price=sizing.stop_loss_price,
            client_tag=f"{signal.strategy_name}|tp:{sizing.take_profit_price}",
        )

        logger.info(
            "Risk Gates PASSED: Created %s %s order for %.6f %s (Stop: $%.2f, Risk: $%.2f)",
            order.side,
            order.order_type,
            order.quantity,
            order.symbol,
            order.stop_price,
            sizing.dollar_risk * multiplier,
        )
        return order
