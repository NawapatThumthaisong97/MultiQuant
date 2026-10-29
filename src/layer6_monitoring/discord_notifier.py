"""
Discord Webhook Notifier.
Asynchronous non-blocking alerting using rich Discord Embeds.
Color-coded for Fills, Risk Warnings, and Critical System Events.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp

from src.core.models import FillEvent, OrderSide, PortfolioState

logger = logging.getLogger("multiquant.monitoring.discord")

# Discord Embed Color Constants
COLOR_BUY = 0x2ECC71  # Emerald Green
COLOR_SELL = 0xE67E22  # Amber / Orange
COLOR_ALERT = 0xF1C40F  # Yellow Warning
COLOR_CRITICAL = 0xE74C3C  # Red Emergency
COLOR_INFO = 0x3498DB  # Blue Info


class DiscordNotifier:
    """
    Sends rich embeds to Discord via webhooks.
    Designed to never block or raise exceptions into the trading loop.
    """

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.0))
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def send_embed(
        self,
        title: str,
        description: str,
        color: int = COLOR_INFO,
        fields: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Send formatted embed payload to Discord."""
        if not self.webhook_url:
            logger.debug("Discord webhook URL not configured; alert suppressed: %s", title)
            return False

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                    "fields": fields or [],
                    "footer": {"text": "MultiQuant Institutional Trading Engine"},
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ]
        }

        try:
            session = await self._get_session()
            async with session.post(self.webhook_url, json=payload) as response:
                if response.status in (200, 204):
                    logger.debug("Discord alert dispatched successfully: %s", title)
                    return True
                else:
                    text = await response.text()
                    logger.warning("Discord webhook returned status %d: %s", response.status, text)
                    return False
        except Exception as e:
            logger.error("Failed to deliver Discord notification: %s", e)
            return False

    async def notify_startup(self, capital: float, symbols: list[str]) -> bool:
        """Send notification when trading engine starts up."""
        title = "🚀 MultiQuant Paper Trading Engine Started"
        description = "Quantitative trading system is now running live."
        fields = [
            {"name": "Initial Capital", "value": f"${capital:,.2f} USD", "inline": True},
            {"name": "Active Symbols", "value": ", ".join(symbols), "inline": True},
            {"name": "Mode", "value": "Paper Trading (Live Simulation)", "inline": True},
        ]
        return await self.send_embed(title=title, description=description, color=COLOR_INFO, fields=fields)

    async def notify_trade_open(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL" (LONG / SHORT)
        entry_price: float,
        quantity: float,
        position_value: float,
        stop_loss: float,
        take_profit: float,
        strategy_name: str = "TrendEMA_Cross",
        fees: float = 0.0,
    ) -> bool:
        """Send rich alert when a new position is OPENED."""
        is_long = side.upper() == "BUY"
        color = COLOR_BUY if is_long else COLOR_SELL
        direction_emoji = "🟢 LONG" if is_long else "🔴 SHORT"
        sl_pct = abs(entry_price - stop_loss) / entry_price * 100.0
        tp_pct = abs(take_profit - entry_price) / entry_price * 100.0

        title = f"{direction_emoji} POSITION OPENED: {symbol}"
        description = f"Opened a new {direction_emoji} position based on **{strategy_name}** signal."

        fields = [
            {"name": "Entry Price", "value": f"${entry_price:,.2f}", "inline": True},
            {"name": "Position Size", "value": f"{quantity:.4f} {symbol.replace('USDT', '')} (~${position_value:,.2f})", "inline": True},
            {"name": "Order Side", "value": f"{side.upper()}", "inline": True},
            {"name": "🛑 Stop Loss (SL)", "value": f"${stop_loss:,.2f} (-{sl_pct:.2f}%)", "inline": True},
            {"name": "🎯 Take Profit (TP)", "value": f"${take_profit:,.2f} (+{tp_pct:.2f}%)", "inline": True},
            {"name": "Risk : Reward", "value": "1 : 1.5", "inline": True},
            {"name": "Execution Fee", "value": f"${fees:.2f}", "inline": True},
        ]
        return await self.send_embed(title=title, description=description, color=color, fields=fields)

    async def notify_trade_close(
        self,
        symbol: str,
        closed_side: str,  # "LONG" or "SHORT" that was closed
        entry_price: float,
        exit_price: float,
        quantity: float,
        realized_pnl: float,
        exit_reason: str,
        total_equity: float,
        fees: float = 0.0,
        slippage: float = 0.0,
        win_count: int = 0,
        loss_count: int = 0,
    ) -> bool:
        """Send rich alert when an existing position is CLOSED."""
        is_win = realized_pnl >= 0
        color = 0x2ECC71 if is_win else 0xE74C3C  # Bright Green for Win, Red for Loss
        result_badge = "🏆 WIN" if is_win else "🛑 LOSS"
        pnl_pct = (realized_pnl / (entry_price * quantity)) * 100.0 if (entry_price * quantity) > 0 else 0.0

        title = f"{result_badge} POSITION CLOSED: {symbol} ({closed_side})"
        description = f"Position closed via **{exit_reason}**"

        total_trades = win_count + loss_count
        win_rate_str = f"{(win_count / total_trades * 100.0):.1f}% ({win_count}W/{loss_count}L)" if total_trades > 0 else "N/A"

        fields = [
            {"name": "Realized PnL", "value": f"**{'+' if is_win else ''}${realized_pnl:,.2f}** ({'+' if is_win else ''}{pnl_pct:.2f}%)", "inline": True},
            {"name": "Exit Price", "value": f"${exit_price:,.2f}", "inline": True},
            {"name": "Entry Price", "value": f"${entry_price:,.2f}", "inline": True},
            {"name": "Total Fees & Slip", "value": f"${fees + slippage:.2f}", "inline": True},
            {"name": "Current Equity", "value": f"${total_equity:,.2f}", "inline": True},
            {"name": "Track Record", "value": win_rate_str, "inline": True},
        ]
        return await self.send_embed(title=title, description=description, color=color, fields=fields)

    async def notify_fill(self, fill: FillEvent) -> bool:
        """Send notification when an order is executed."""
        color = COLOR_BUY if fill.side == OrderSide.BUY else COLOR_SELL
        title = f"⚡ Execution Fill: {fill.side.value} {fill.symbol}"
        description = f"Order `{fill.order_id}` has been filled on exchange."

        fields = [
            {"name": "Filled Qty", "value": f"{fill.filled_quantity:.6f}", "inline": True},
            {"name": "Fill Price", "value": f"${fill.filled_price:,.2f}", "inline": True},
            {"name": "Trading Fee", "value": f"${fill.fee:.4f}", "inline": True},
            {"name": "Slippage", "value": f"${fill.slippage:.4f}", "inline": True},
        ]
        return await self.send_embed(
            title=title, description=description, color=color, fields=fields
        )

    async def notify_circuit_breaker(
        self, status: str, drawdown_pct: float, total_equity: float
    ) -> bool:
        """Send critical alert when circuit breaker status changes."""
        title = f"🚨 CIRCUIT BREAKER TRIGGERED: {status}"
        description = f"Risk Manager has activated protective state **{status}**."

        fields = [
            {"name": "Current Drawdown", "value": f"{drawdown_pct * 100.0:.2f}%", "inline": True},
            {"name": "Current Equity", "value": f"${total_equity:,.2f}", "inline": True},
        ]
        return await self.send_embed(
            title=title, description=description, color=COLOR_CRITICAL, fields=fields
        )

    async def notify_portfolio_summary(self, portfolio: PortfolioState) -> bool:
        """Send periodic portfolio summary."""
        title = "📊 Portfolio Performance Update"
        description = f"Total Equity: **${portfolio.total_equity:,.2f}**"

        open_positions = len([p for p in portfolio.positions.values() if p.is_open])
        fields = [
            {"name": "Cash Balance", "value": f"${portfolio.cash:,.2f}", "inline": True},
            {"name": "Peak Equity", "value": f"${portfolio.peak_equity:,.2f}", "inline": True},
            {"name": "Drawdown", "value": f"{portfolio.drawdown_pct * 100.0:.2f}%", "inline": True},
            {"name": "Open Positions", "value": str(open_positions), "inline": True},
        ]
        return await self.send_embed(
            title=title, description=description, color=COLOR_INFO, fields=fields
        )
