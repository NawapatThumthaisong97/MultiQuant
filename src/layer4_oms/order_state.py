"""
Order Lifecycle State Machine.
Enforces strict institutional state transitions for all orders.
"""

from __future__ import annotations

import logging

from src.core.models import OrderStatus

logger = logging.getLogger("multiquant.oms.order_state")


# Valid state transitions dictionary
VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.CANCELED},
    OrderStatus.ACCEPTED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
    },
    OrderStatus.FILLED: set(),  # Terminal state
    OrderStatus.CANCELED: set(),  # Terminal state
    OrderStatus.REJECTED: set(),  # Terminal state
}


class InvalidOrderStateTransition(Exception):
    """Raised when an illegal order transition is attempted."""

    pass


def validate_transition(current_status: OrderStatus, new_status: OrderStatus) -> None:
    """
    Ensure the state transition conforms to exchange execution rules.
    Raises InvalidOrderStateTransition if illegal.
    """
    allowed_next_states = VALID_TRANSITIONS.get(current_status, set())
    if new_status not in allowed_next_states:
        err_msg = f"Illegal order transition from {current_status} to {new_status}"
        logger.error(err_msg)
        raise InvalidOrderStateTransition(err_msg)
