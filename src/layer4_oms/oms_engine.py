"""
Order Management System (OMS) Core Engine.
Coordinates order lifecycle, execution simulation, stop-losses, and position accounting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine

from src.core.models import (
    FillEvent,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    TickData,
)
from src.layer4_oms.execution_sim import ExecutionSimulator
from src.layer4_oms.order_state import validate_transition
from src.layer4_oms.position_tracker import PositionTracker

logger = logging.getLogger("multiquant.oms.engine")


class OMSEngine:
    """
    Custom Simulated Order Management System (OMS).
    Handles order routing, synthetic exchange matching, and real-time position tracking.
    """

    def __init__(
        self,
        simulator: ExecutionSimulator | None = None,
        on_fill: Callable[[FillEvent], Coroutine[None, None, None]] | None = None,
    ) -> None:
        self.simulator = simulator or ExecutionSimulator()
        self.tracker = PositionTracker()
        self.on_fill = on_fill

        self._active_orders: dict[str, OrderRequest] = {}
        self._order_statuses: dict[str, OrderStatus] = {}
        self._execution_history: list[FillEvent] = []

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        return self._order_statuses.get(order_id)

    def get_open_orders(self) -> list[OrderRequest]:
        return list(self._active_orders.values())

    def get_positions(self) -> dict[str, Position]:
        return self.tracker.get_all_positions()

    async def submit_order(
        self,
        order: OrderRequest,
        current_price: float,
    ) -> FillEvent | None:
        """
        Submits an approved OrderRequest into the OMS.
        Market orders are filled immediately.
        Limit/Stop orders are enqueued into active book.
        """
        self._order_statuses[order.order_id] = OrderStatus.PENDING
        validate_transition(OrderStatus.PENDING, OrderStatus.ACCEPTED)
        self._order_statuses[order.order_id] = OrderStatus.ACCEPTED

        # 1. Market Order -> Execute immediately
        if order.order_type == OrderType.MARKET:
            fill = self.simulator.execute_market_order(order, current_price)
            validate_transition(OrderStatus.ACCEPTED, OrderStatus.FILLED)
            self._order_statuses[order.order_id] = OrderStatus.FILLED
            self._record_fill(fill)

            if self.on_fill:
                await self.on_fill(fill)

            return fill

        # 2. Resting Order (Limit / Stop Loss) -> Add to active book
        else:
            self._active_orders[order.order_id] = order
            logger.info(
                "Enqueued %s order %s for %s @ $%.2f",
                order.order_type,
                order.order_id,
                order.symbol,
                order.price or order.stop_price or 0.0,
            )
            return None

    async def process_market_tick(self, tick: TickData) -> list[FillEvent]:
        """
        Processes a real-time market tick:
        1. Updates mark price on active positions.
        2. Evaluates active resting orders (Limits & Stops) for execution.
        """
        self.tracker.update_mark_price(tick.symbol, tick.last_price)
        fills_generated: list[FillEvent] = []

        # Find orders matching this tick's symbol
        matching_orders = [o for o in self._active_orders.values() if o.symbol == tick.symbol]

        for order in matching_orders:
            fill = self.simulator.check_limit_or_stop_fill(order, tick)
            if fill is not None:
                validate_transition(OrderStatus.ACCEPTED, OrderStatus.FILLED)
                self._order_statuses[order.order_id] = OrderStatus.FILLED
                self._active_orders.pop(order.order_id, None)
                self._record_fill(fill)
                fills_generated.append(fill)

                if self.on_fill:
                    await self.on_fill(fill)

        return fills_generated

    def cancel_order(self, order_id: str) -> bool:
        """Cancels an active resting order."""
        order = self._active_orders.get(order_id)
        if not order:
            logger.warning("Cannot cancel order %s: Not found in active book.", order_id)
            return False

        current_status = self._order_statuses.get(order_id, OrderStatus.ACCEPTED)
        validate_transition(current_status, OrderStatus.CANCELED)
        self._order_statuses[order_id] = OrderStatus.CANCELED
        self._active_orders.pop(order_id, None)
        logger.info("Order %s for %s successfully canceled.", order_id, order.symbol)
        return True

    def _record_fill(self, fill: FillEvent) -> None:
        """Records fill into position tracker and history."""
        self._execution_history.append(fill)
        self.tracker.process_fill(fill)
