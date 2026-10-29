"""
MultiQuant: Central Production Orchestrator.
Integrates all 6 decoupled layers into an asynchronous institutional event loop:
- Layer 1: Market Connectivity (WebSockets)
- Layer 2: Alpha Engine (Polars)
- Layer 3: Risk & Position Sizing (Volatility/ATR)
- Layer 4: Order Management System (Simulated OMS)
- Layer 5: State & Persistence (SQLite WAL + Redis Cache)
- Layer 6: Monitoring & Alerting (Discord + Prometheus/Grafana)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from config.settings import settings
from src.core.models import (
    AlphaSignal,
    BarData,
    FillEvent,
    OrderStatus,
    PortfolioState,
    TickData,
)
from src.layer1_connectivity.websocket_client import BinanceWebSocketFeed
from src.layer2_alpha.engine import AlphaEngine
from src.layer2_alpha.strategies import BollingerMeanReversionAlpha, TrendVolatilityAlpha
from src.layer3_risk.risk_manager import PreTradeRiskManager
from src.layer4_oms.oms_engine import OMSEngine
from src.layer5_state.state_manager import StateManager
from src.layer6_monitoring.discord_notifier import DiscordNotifier
from src.layer6_monitoring.metrics import MetricsCollector

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Configure Root Logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("multiquant.main")


class MultiQuantEngine:
    """End-to-end quantitative trading application."""

    def __init__(self) -> None:
        logger.info("Initializing MultiQuant Trading System...")

        # Layer 6: Monitoring & Metrics
        self.metrics = MetricsCollector(port=settings.PROMETHEUS_METRICS_PORT)
        self.notifier = DiscordNotifier(webhook_url=settings.DISCORD_WEBHOOK_URL)

        # Layer 5: State & Persistence
        self.state_mgr = StateManager(
            db_path=settings.SQLITE_DB_PATH,
            redis_host=settings.REDIS_HOST,
            redis_port=settings.REDIS_PORT,
            redis_password=settings.REDIS_PASSWORD,
        )

        # Layer 4: Order Management System (OMS)
        self.oms = OMSEngine(on_fill=self._handle_fill_event)

        # Layer 3: Risk Manager & Position Sizer
        self.risk_manager = PreTradeRiskManager()

        # Layer 2: Alpha Engine with Polars
        self.alpha_engine = AlphaEngine(on_signal=self._handle_alpha_signal)
        self.alpha_engine.register_strategy(TrendVolatilityAlpha(warmup_period=20))
        self.alpha_engine.register_strategy(BollingerMeanReversionAlpha(warmup_period=20))

        # Layer 1: Market Connectivity Feed
        self.feed = BinanceWebSocketFeed(
            symbols=settings.DEFAULT_SYMBOLS,
            stream_type="trade",
            on_tick=self._handle_market_tick,
            on_bar=self._handle_market_bar,
        )

        # Portfolio Balance Tracking
        cap = settings.INITIAL_CAPITAL
        self.portfolio = PortfolioState(
            total_equity=cap,
            cash=cap,
            initial_capital=cap,
            peak_equity=cap,
        )

        self._running = False

    async def start(self) -> None:
        """Start all subsystem layers concurrently."""
        self._running = True

        # 1. Start Prometheus HTTP server
        self.metrics.start_server()

        # 2. Connect Database & State Cache
        await self.state_mgr.initialize()

        # 3. Synchronize Initial Portfolio
        await self.state_mgr.record_portfolio_snapshot(self.portfolio)
        self.metrics.record_portfolio(self.portfolio)

        # 4. Connect Market Data WebSocket Feed
        logger.info("Starting Layer 1 Market Feed for symbols: %s", settings.DEFAULT_SYMBOLS)
        await self.feed.connect()

        logger.info("✅ MultiQuant 6-Layer Architecture is running live!")

    async def stop(self) -> None:
        """Gracefully shut down all trading layers."""
        logger.info("Shutting down MultiQuant engine...")
        self._running = False

        await self.feed.disconnect()
        await self.state_mgr.shutdown()
        await self.notifier.close()
        logger.info("MultiQuant shutdown completed cleanly.")

    async def _handle_market_tick(self, tick: TickData) -> None:
        """Handle incoming market ticks from Layer 1."""
        # 1. Update Metrics
        self.metrics.record_tick(tick)

        # 2. Update Fast Cache
        await self.state_mgr.cache_market_tick(tick)

        # 3. Feed into OMS to trigger resting Limit / Stop orders
        fills = await self.oms.process_market_tick(tick)
        for fill in fills:
            await self._handle_fill_event(fill)

        # 4. Synthesize rolling bars for Alpha Engine
        bar = BarData(
            symbol=tick.symbol,
            timestamp=tick.timestamp,
            open=tick.last_price,
            high=tick.last_price,
            low=tick.last_price,
            close=tick.last_price,
            volume=tick.volume,
            timeframe="1s",
        )
        await self._handle_market_bar(bar)

    async def _handle_market_bar(self, bar: BarData) -> None:
        """Feed completed candle bars into Layer 2 Alpha Engine."""
        await self.alpha_engine.on_bar(bar)

    async def _handle_alpha_signal(self, signal: AlphaSignal) -> None:
        """Route AlphaSignal from Layer 2 through Layer 3 Pre-Trade Risk Manager."""
        current_tick = await self.state_mgr.get_latest_tick(signal.symbol)
        if current_tick is None:
            return

        # Pre-Trade Risk Validation & ATR Sizing
        approved_order = self.risk_manager.evaluate_signal(
            signal,
            self.portfolio,
            current_price=current_tick.last_price,
        )

        if approved_order is not None:
            # Record order into State & Metrics
            await self.state_mgr.record_order(approved_order, status=OrderStatus.ACCEPTED)
            self.metrics.record_order(approved_order)

            # Submit order to Layer 4 OMS
            await self.oms.submit_order(approved_order, current_price=current_tick.last_price)

    async def _handle_fill_event(self, fill: FillEvent) -> None:
        """Handle execution fill receipt from Layer 4 OMS."""
        # Record fill to SQLite
        await self.state_mgr.record_fill(fill)

        # Update Metrics & Alerts
        self.metrics.record_fill(fill)
        pos_val = fill.filled_quantity * fill.filled_price
        stop_dist = fill.filled_price * 0.015
        tp_dist = stop_dist * 1.5

        if fill.side.value.upper() == "BUY":
            sl = fill.filled_price - stop_dist
            tp = fill.filled_price + tp_dist
        else:
            sl = fill.filled_price + stop_dist
            tp = fill.filled_price - tp_dist

        await self.notifier.notify_trade_open(
            symbol=fill.symbol,
            side=fill.side.value,
            entry_price=fill.filled_price,
            quantity=fill.filled_quantity,
            position_value=pos_val,
            stop_loss=sl,
            take_profit=tp,
            strategy_name="TrendVolatilityAlpha",
            fees=fill.fee,
        )

        # Update Portfolio State
        realized_pnl = self.oms.tracker.total_realized_pnl
        self.portfolio.positions = self.oms.tracker.get_all_positions()
        unrealized_pnl = sum(p.unrealized_pnl for p in self.portfolio.positions.values())
        self.portfolio.total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl

        # Persist snapshot
        await self.state_mgr.record_portfolio_snapshot(self.portfolio)
        self.metrics.record_portfolio(self.portfolio, realized_pnl=realized_pnl)


async def main() -> None:
    engine = MultiQuantEngine()
    loop = asyncio.get_running_loop()

    # Graceful shutdown handler
    stop_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Signal received, initiating shutdown...")
        stop_event.set()

    if sys.platform != "win32":
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, signal_handler)

    try:
        await engine.start()
        if sys.platform == "win32":
            # On Windows, keep running until cancelled
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
        else:
            await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
