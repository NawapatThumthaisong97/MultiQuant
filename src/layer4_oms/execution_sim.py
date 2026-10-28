"""
Execution & Fill Simulator.
Realistic institutional execution simulator incorporating Slippage and Exchange Fee models.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.core.models import (
    FillEvent,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TickData,
)

logger = logging.getLogger("multiquant.oms.execution_sim")


class ExecutionSimulator:
    """
    Simulates exchange execution with configurable slippage and maker/taker fees.
    """

    def __init__(
        self,
        base_slippage_bps: float = 3.0,  # 3 basis points = 0.03%
        taker_fee_pct: float = 0.0005,  # 0.05% taker fee
        maker_fee_pct: float = 0.0002,  # 0.02% maker fee
    ) -> None:
        self.base_slippage_bps = base_slippage_bps
        self.taker_fee_pct = taker_fee_pct
        self.maker_fee_pct = maker_fee_pct

    def execute_market_order(
        self,
        order: OrderRequest,
        current_price: float,
    ) -> FillEvent:
        """
        Executes a Market Order immediately against the current market price with slippage.
        """
        slippage_fraction = self.base_slippage_bps / 10000.0

        if order.side == OrderSide.BUY:
            # Slippage pushes BUY fill price HIGHER
            fill_price = current_price * (1.0 + slippage_fraction)
            slippage_amount = fill_price - current_price
        else:
            # Slippage pushes SELL fill price LOWER
            fill_price = current_price * (1.0 - slippage_fraction)
            slippage_amount = current_price - fill_price

        # Fee calculation on executed notional
        notional_value = fill_price * order.quantity
        fee = notional_value * self.taker_fee_pct

        return FillEvent(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            filled_price=round(fill_price, 4),
            fee=round(fee, 4),
            slippage=round(slippage_amount, 4),
            timestamp=datetime.now(UTC),
            message=f"Filled Market order with {self.base_slippage_bps:.1f} bps slippage",
        )

    def check_limit_or_stop_fill(
        self,
        order: OrderRequest,
        tick: TickData,
    ) -> FillEvent | None:
        """
        Evaluates whether an active Limit or Stop order should trigger on the incoming tick.
        """
        if order.symbol != tick.symbol:
            return None

        # 1. Limit Order Evaluation
        if order.order_type == OrderType.LIMIT and order.price is not None:
            # Buy Limit triggers when Ask <= limit price
            if order.side == OrderSide.BUY and tick.ask_price <= order.price:
                fee = order.quantity * order.price * self.maker_fee_pct
                return FillEvent(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status=OrderStatus.FILLED,
                    filled_quantity=order.quantity,
                    filled_price=order.price,
                    fee=round(fee, 4),
                    slippage=0.0,
                    timestamp=tick.timestamp,
                    message="Limit Buy filled at limit price",
                )
            # Sell Limit triggers when Bid >= limit price
            elif order.side == OrderSide.SELL and tick.bid_price >= order.price:
                fee = order.quantity * order.price * self.maker_fee_pct
                return FillEvent(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status=OrderStatus.FILLED,
                    filled_quantity=order.quantity,
                    filled_price=order.price,
                    fee=round(fee, 4),
                    slippage=0.0,
                    timestamp=tick.timestamp,
                    message="Limit Sell filled at limit price",
                )

        # 2. Stop Loss Order Evaluation
        elif order.order_type == OrderType.STOP_LOSS and order.stop_price is not None:
            # Long stop triggers when price falls below stop price
            if order.side == OrderSide.SELL and tick.bid_price <= order.stop_price:
                return self.execute_market_order(order, tick.bid_price)
            # Short stop triggers when price rises above stop price
            elif order.side == OrderSide.BUY and tick.ask_price >= order.stop_price:
                return self.execute_market_order(order, tick.ask_price)

        return None
