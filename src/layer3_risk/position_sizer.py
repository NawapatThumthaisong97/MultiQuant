"""
Volatility-Based Position Sizing.
Implements institutional ATR-based sizing, ensuring constant dollar risk across varying market regimes.
Supports micro accounts ($50+) with intelligent allocation capping and exchange minimums.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.core.models import AlphaSignal, PortfolioState, SignalDirection

logger = logging.getLogger("multiquant.position_sizer")


@dataclass(frozen=True)
class SizingResult:
    """Calculated position sizing output."""

    quantity: float
    dollar_risk: float
    stop_distance: float
    stop_loss_price: float
    take_profit_price: float
    is_valid: bool
    rejection_reason: str | None = None


class VolatilityPositionSizer:
    """
    Computes position sizes dynamically using Average True Range (ATR).

    Formula:
        Dollar Risk = Equity * Risk Fraction * Signal Strength
        Stop Distance = ATR * ATR Multiplier
        Quantity = Dollar Risk / Stop Distance
    """

    def __init__(
        self,
        risk_per_trade_pct: float = 0.02,  # 2.0% capital risk per trade
        atr_multiplier: float = 2.0,  # Stop loss = 2x ATR
        risk_reward_ratio: float = 1.5,  # Take profit = 1.5x Risk (3x ATR)
        max_allocation_pct: float = 0.40,  # Maximum 40% of portfolio notional in single asset
        min_order_usd: float = 5.0,  # Minimum order notional size ($5.0)
        min_dollar_risk: float = 0.05,  # Minimum dollar risk threshold ($0.05)
    ) -> None:
        self.risk_per_trade_pct = risk_per_trade_pct
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio
        self.max_allocation_pct = max_allocation_pct
        self.min_order_usd = min_order_usd
        self.min_dollar_risk = min_dollar_risk

    def calculate_size(
        self,
        signal: AlphaSignal,
        portfolio: PortfolioState,
        current_price: float,
    ) -> SizingResult:
        """
        Calculate units to buy/sell based on ATR volatility and portfolio equity.
        """
        if current_price <= 0:
            return SizingResult(0.0, 0.0, 0.0, 0.0, 0.0, False, "Invalid current price <= 0")

        # Extract ATR from signal metadata
        atr = float(signal.metadata.get("atr", 0.0))
        if atr <= 0:
            return SizingResult(
                0.0, 0.0, 0.0, 0.0, 0.0, False, "ATR missing or non-positive in signal metadata"
            )

        # 1. Determine Dollar Risk budget
        # Scaled by strategy confidence (strength: 0.0 to 1.0)
        dollar_risk = portfolio.total_equity * self.risk_per_trade_pct * signal.strength
        if dollar_risk < self.min_dollar_risk:
            return SizingResult(
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                False,
                f"Dollar risk (${dollar_risk:.2f}) below min threshold (${self.min_dollar_risk:.2f})",
            )

        # 2. Determine Stop Loss and Take Profit distances
        stop_distance = atr * self.atr_multiplier
        tp_distance = stop_distance * self.risk_reward_ratio

        if signal.direction == SignalDirection.LONG:
            stop_loss_price = max(0.0, current_price - stop_distance)
            take_profit_price = current_price + tp_distance
        elif signal.direction == SignalDirection.SHORT:
            stop_loss_price = current_price + stop_distance
            take_profit_price = max(0.0, current_price - tp_distance)
        else:
            return SizingResult(0.0, 0.0, 0.0, 0.0, 0.0, False, "Signal direction is FLAT")

        # 3. Calculate Raw Quantity (Dollar Risk / Stop Distance)
        raw_quantity = dollar_risk / stop_distance

        # 4. Cap notional value to max portfolio allocation
        max_notional = portfolio.total_equity * self.max_allocation_pct
        target_notional = raw_quantity * current_price

        if target_notional > max_notional:
            logger.info(
                "Position notional ($%.2f) exceeds max allocation ($%.2f). Capping quantity.",
                target_notional,
                max_notional,
            )
            final_quantity = max_notional / current_price
            dollar_risk = final_quantity * stop_distance
        elif target_notional < self.min_order_usd and self.min_order_usd <= max_notional:
            # For micro accounts: if raw order is below exchange minimum ($5), scale to min_order_usd
            final_quantity = self.min_order_usd / current_price
            dollar_risk = final_quantity * stop_distance
        else:
            final_quantity = raw_quantity

        # Final check against minimum order size in USD
        if final_quantity * current_price < self.min_order_usd:
            return SizingResult(
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                False,
                f"Order value (${final_quantity * current_price:.2f}) below minimum USD limit (${self.min_order_usd:.2f})",
            )

        return SizingResult(
            quantity=round(final_quantity, 6),
            dollar_risk=round(dollar_risk, 2),
            stop_distance=round(stop_distance, 4),
            stop_loss_price=round(stop_loss_price, 4),
            take_profit_price=round(take_profit_price, 4),
            is_valid=True,
        )
