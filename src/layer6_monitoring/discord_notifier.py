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
