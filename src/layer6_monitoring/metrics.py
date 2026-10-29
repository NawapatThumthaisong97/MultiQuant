"""
Prometheus Metrics Collector & HTTP Exporter.
Publishes real-time quant trading metrics for Prometheus scraping and Grafana visualization.
"""

from __future__ import annotations

import logging

from prometheus_client import Counter, Gauge, start_http_server

from src.core.models import FillEvent, OrderRequest, PortfolioState, TickData

logger = logging.getLogger("multiquant.monitoring.metrics")

# Prometheus Metrics Definitions
METRIC_PORTFOLIO_EQUITY = Gauge(
    "multiquant_portfolio_equity_usd",
    "Total portfolio equity in USD",
)
METRIC_PORTFOLIO_CASH = Gauge(
    "multiquant_portfolio_cash_usd",
    "Available cash balance in USD",
)
METRIC_PORTFOLIO_DRAWDOWN = Gauge(
    "multiquant_portfolio_drawdown_pct",
    "Current portfolio drawdown percentage (0.0 to 1.0)",
)
METRIC_REALIZED_PNL = Gauge(
    "multiquant_realized_pnl_usd",
    "Cumulative realized profit and loss in USD",
)
METRIC_UNREALIZED_PNL = Gauge(
    "multiquant_unrealized_pnl_usd",
    "Current unrealized profit and loss in USD",
)
METRIC_FEES_PAID = Counter(
    "multiquant_fees_paid_usd_total",
    "Cumulative trading fees paid in USD",
)
METRIC_SLIPPAGE_PAID = Counter(
    "multiquant_slippage_paid_usd_total",
    "Cumulative slippage incurred in USD",
)
METRIC_ORDERS_COUNT = Counter(
    "multiquant_orders_total",
    "Total orders created",
    ["symbol", "side", "order_type"],
)
METRIC_FILLS_COUNT = Counter(
    "multiquant_fills_total",
    "Total fills executed",
    ["symbol", "side"],
)
METRIC_TICKS_COUNT = Counter(
    "multiquant_market_ticks_total",
    "Total market ticks processed",
    ["symbol"],
)
METRIC_MARKET_PRICE = Gauge(
    "multiquant_market_price_usd",
    "Latest market price in USD",
    ["symbol"],
)
METRIC_ATR_VOLATILITY = Gauge(
    "multiquant_atr_volatility_usd",
    "Latest ATR volatility in USD",
    ["symbol"],
)
METRIC_POSITION_VALUE = Gauge(
    "multiquant_position_value_usd",
    "Position notional value in USD",
    ["symbol"],
)
METRIC_TRADES_WON = Counter(
    "multiquant_trades_won_total",
    "Total winning closed trades",
)
METRIC_TRADES_LOST = Counter(
    "multiquant_trades_lost_total",
    "Total losing closed trades",
)
METRIC_WIN_RATE = Gauge(
    "multiquant_win_rate_pct",
    "Win rate percentage (0 to 100)",
)


class MetricsCollector:
    """
    Manages Prometheus metrics updates and hosts the `/metrics` endpoint.
    """

    def __init__(self, port: int = 8000) -> None:
        self.port = port
        self._server_started = False

    def start_server(self) -> None:
        """Start the Prometheus HTTP metrics server."""
        if not self._server_started:
            try:
                start_http_server(self.port)
                self._server_started = True
                logger.info(
                    "Prometheus metrics server running on port %d (:8000/metrics)", self.port
                )
            except Exception as e:
                logger.warning("Could not start Prometheus server on port %d: %s", self.port, e)

    def record_tick(self, tick: TickData) -> None:
        """Increment market tick counter."""
        METRIC_TICKS_COUNT.labels(symbol=tick.symbol).inc()

    def record_order(self, order: OrderRequest) -> None:
        """Increment submitted order counter."""
        METRIC_ORDERS_COUNT.labels(
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
        ).inc()

    def record_fill(self, fill: FillEvent) -> None:
        """Increment fill counter and fees/slippage."""
        METRIC_FILLS_COUNT.labels(
            symbol=fill.symbol,
            side=fill.side.value,
        ).inc()
        METRIC_FEES_PAID.inc(fill.fee)
        METRIC_SLIPPAGE_PAID.inc(fill.slippage)

    def record_market_price(self, symbol: str, price: float, atr: float = 0.0) -> None:
        """Record latest market price and ATR volatility."""
        METRIC_MARKET_PRICE.labels(symbol=symbol).set(price)
        if atr > 0.0:
            METRIC_ATR_VOLATILITY.labels(symbol=symbol).set(atr)

    def record_trade_outcome(self, is_win: bool, win_count: int, loss_count: int) -> None:
        """Record trade win/loss and update win rate gauge."""
        if is_win:
            METRIC_TRADES_WON.inc()
        else:
            METRIC_TRADES_LOST.inc()

        total = win_count + loss_count
        if total > 0:
            METRIC_WIN_RATE.set((win_count / total) * 100.0)

    def record_portfolio(
        self,
        portfolio: PortfolioState,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
    ) -> None:
        """Update gauges for portfolio balances, positions, and drawdown."""
        METRIC_PORTFOLIO_EQUITY.set(portfolio.total_equity)
        METRIC_PORTFOLIO_CASH.set(portfolio.cash)
        METRIC_PORTFOLIO_DRAWDOWN.set(portfolio.drawdown_pct)
        METRIC_REALIZED_PNL.set(realized_pnl)
        METRIC_UNREALIZED_PNL.set(unrealized_pnl)

        for sym, pos in portfolio.positions.items():
            if pos.is_open:
                val = abs(pos.quantity) * pos.current_price
                METRIC_POSITION_VALUE.labels(symbol=sym).set(val)
            else:
                METRIC_POSITION_VALUE.labels(symbol=sym).set(0.0)
