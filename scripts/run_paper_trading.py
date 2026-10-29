"""
Interactive Live Paper Trading Runner with Visual Terminal Dashboard.
Connects all 6 layers live, executes simulated trades against real Binance feeds,
and displays real-time portfolio metrics, positions, and live fills.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config.settings import settings
from src.core.models import (
    AlphaSignal,
    BarData,
    FillEvent,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    SignalDirection,
    TickData,
)
from src.layer1_connectivity.websocket_client import BinanceWebSocketFeed
from src.layer2_alpha.buffer import RollingBarBuffer
from src.layer2_alpha.indicators import compute_atr, compute_ema
from src.layer3_risk.risk_manager import PreTradeRiskManager
from src.layer4_oms.oms_engine import OMSEngine
from src.layer5_state.state_manager import StateManager
from src.layer6_monitoring.discord_notifier import DiscordNotifier
from src.layer6_monitoring.metrics import MetricsCollector

# Silence noisy third-party debug logs
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


class PaperTradingApp:
    """Full live paper trading application with terminal visualization."""

    def __init__(self) -> None:
        self.notifier = DiscordNotifier(webhook_url=settings.DISCORD_WEBHOOK_URL)
        self.metrics = MetricsCollector(port=settings.PROMETHEUS_METRICS_PORT)
        self.state_mgr = StateManager(
            db_path=settings.SQLITE_DB_PATH,
            redis_host=settings.REDIS_HOST,
            redis_port=settings.REDIS_PORT,
        )
        self.oms = OMSEngine(on_fill=self._on_fill_event)
        self.risk_manager = PreTradeRiskManager()

        self.symbols = ["BTCUSDT", "ETHUSDT"]
        self.buffers: dict[str, RollingBarBuffer] = {
            s: RollingBarBuffer(maxlen=50) for s in self.symbols
        }

        self.portfolio = PortfolioState(
            total_equity=100_000.0,
            cash=100_000.0,
            initial_capital=100_000.0,
            peak_equity=100_000.0,
        )
        self.latest_prices: dict[str, float] = {}
        self.latest_atrs: dict[str, float] = {}
        self.last_fill_summary: str = "No fills yet. Listening for signals..."
        self.feed: BinanceWebSocketFeed | None = None
        self._running = False

    async def start(self) -> None:
        """Start paper trading loop."""
        self._running = True
        self.metrics.start_server()
        await self.state_mgr.initialize()

        print("\n" + "=" * 80)
        print("🚀 MULTIQUANT LIVE PAPER TRADING ENGINE INITIALIZED")
        print(f"   Listening on: {self.symbols}")
        print(f"   Initial Paper Capital: ${self.portfolio.total_equity:,.2f} USD")
        if settings.DISCORD_WEBHOOK_URL:
            print("   🔔 Discord Webhooks: ENABLED (Alerts will be sent on fills)")
        else:
            print("   🔕 Discord Webhooks: DISABLED (Set DISCORD_WEBHOOK_URL in .env to enable)")
        print(f"   📈 Prometheus Metrics: http://localhost:{settings.PROMETHEUS_METRICS_PORT}/metrics")
        print("=" * 80 + "\n")

        # Send Startup Discord Notification
        await self.notifier.notify_startup(self.portfolio.total_equity, self.symbols)

        self.feed = BinanceWebSocketFeed(
            symbols=self.symbols,
            stream_type="trade",
            on_tick=self._on_tick,
        )
        await self.feed.connect()

        # Start background dashboard rendering loop
        dashboard_task = asyncio.create_task(self._dashboard_loop())

        try:
            while self._running:
                await asyncio.sleep(1.0)
        finally:
            dashboard_task.cancel()
            await self._shutdown()

    async def _shutdown(self) -> None:
        if self.feed:
            await self.feed.disconnect()
        await self.state_mgr.shutdown()
        await self.notifier.close()
        print("\n⏹️ Paper trading engine stopped cleanly.")

    async def _on_tick(self, tick: TickData) -> None:
        sym = tick.symbol
        self.latest_prices[sym] = tick.last_price
        self.metrics.record_tick(tick)

        buf = self.buffers.get(sym)
        if not buf:
            return

        # Synthesize real-time micro-bar
        synthetic_bar = BarData(
            symbol=sym,
            timestamp=tick.timestamp,
            open=tick.last_price,
            high=tick.last_price + 0.2,
            low=tick.last_price - 0.2,
            close=tick.last_price,
            volume=tick.volume,
            timeframe="1s",
        )
        buf.add_bar(synthetic_bar)

        # Check resting stop orders in OMS
        await self.oms.process_market_tick(tick)

        # Once buffer has enough bars, compute indicators and evaluate alpha
        if buf.count(sym) >= 15:
            df = buf.to_polars(sym)
            if df is not None:
                df_atr = compute_atr(df, period=14)
                df_calc = compute_ema(df_atr, period=5, column="close", out_col="ema_fast")
                df_calc = compute_ema(df_calc, period=12, column="close", out_col="ema_slow")

                latest_atr = float(df_calc["atr_14"][-1])
                self.latest_atrs[sym] = latest_atr

                # Simple live demo trigger: Trend crossover
                fast_ema = float(df_calc["ema_fast"][-1])
                slow_ema = float(df_calc["ema_slow"][-1])
                direction = SignalDirection.LONG if fast_ema > slow_ema else SignalDirection.SHORT

                # Check if we already have an open position for this symbol
                pos = self.oms.get_positions().get(sym)
                has_open_pos = pos is not None and pos.is_open

                if not has_open_pos:
                    signal = AlphaSignal(
                        symbol=sym,
                        direction=direction,
                        strength=0.85,
                        strategy_name="LiveTrendAlpha",
                        metadata={"atr": latest_atr, "close": tick.last_price},
                    )
                    approved_order = self.risk_manager.evaluate_signal(signal, self.portfolio, tick.last_price)
                    if approved_order:
                        await self.oms.submit_order(approved_order, current_price=tick.last_price)
                else:
                    # Active Position Management: Dynamic Take Profit and Stop Loss
                    stop_dist = max(latest_atr * 2.0, 1.0)
                    tp_dist = stop_dist * 1.5

                    should_close = False
                    exit_reason = ""
                    if pos.side == SignalDirection.LONG:
                        if tick.last_price <= (pos.entry_price - stop_dist):
                            should_close = True
                            exit_reason = "Stop Loss"
                        elif tick.last_price >= (pos.entry_price + tp_dist):
                            should_close = True
                            exit_reason = "Take Profit"
                    elif pos.side == SignalDirection.SHORT:
                        if tick.last_price >= (pos.entry_price + stop_dist):
                            should_close = True
                            exit_reason = "Stop Loss"
                        elif tick.last_price <= (pos.entry_price - tp_dist):
                            should_close = True
                            exit_reason = "Take Profit"

                    if should_close:
                        close_side = OrderSide.SELL if pos.side == SignalDirection.LONG else OrderSide.BUY
                        close_order = OrderRequest(
                            order_id=f"close_{uuid.uuid4().hex[:8]}",
                            symbol=sym,
                            side=close_side,
                            order_type=OrderType.MARKET,
                            quantity=abs(pos.quantity),
                            price=tick.last_price,
                            client_tag=exit_reason,
                        )
                        await self.oms.submit_order(close_order, current_price=tick.last_price)

    async def _on_fill_event(self, fill: FillEvent) -> None:
        """Handle execution fill from OMS."""
        self.last_fill_summary = (
            f"⚡ {fill.side.value} {fill.filled_quantity:.4f} {fill.symbol} @ ${fill.filled_price:,.2f} "
            f"(Fee: ${fill.fee:.2f} | Slippage: ${fill.slippage:.2f})"
        )
        self.metrics.record_fill(fill)
        await self.state_mgr.record_fill(fill)
        await self.notifier.notify_fill(fill)

        # Update Portfolio State
        self.portfolio.positions = self.oms.get_positions()
        realized_pnl = self.oms.tracker.total_realized_pnl
        unrealized_pnl = sum(p.unrealized_pnl for p in self.portfolio.positions.values())
        self.portfolio.total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl

        await self.state_mgr.record_portfolio_snapshot(self.portfolio)
        self.metrics.record_portfolio(self.portfolio, realized_pnl=realized_pnl)

    async def _dashboard_loop(self) -> None:
        """Render live dashboard to terminal every 2 seconds."""
        while self._running:
            await asyncio.sleep(2.0)
            self._render_dashboard()

    def _render_dashboard(self) -> None:
        """Print clean live status table to console."""
        realized_pnl = self.oms.tracker.total_realized_pnl
        positions = self.oms.get_positions()
        unrealized_pnl = sum(p.unrealized_pnl for p in positions.values())
        total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl

        pnl_color = "+" if unrealized_pnl >= 0 else "-"

        print("\n" + "=" * 80)
        print(f"📊 MULTIQUANT PAPER TRADING MONITOR — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        print("=" * 80)
        print(
            f"💰 Total Equity : ${total_equity:,.2f} | Cash: ${self.portfolio.cash:,.2f}\n"
            f"📈 Realized PnL : ${realized_pnl:,.2f} | Unrealized PnL: {pnl_color}${abs(unrealized_pnl):,.2f}\n"
            f"🛡️ Circuit Breaker: {self.risk_manager.guard.current_status.value} (Max Drawdown: {self.portfolio.drawdown_pct * 100:.2f}%)"
        )
        print("-" * 80)
        print("🛒 ACTIVE POSITIONS:")
        open_count = 0
        for sym, p in positions.items():
            if p.is_open:
                open_count += 1
                curr_price = self.latest_prices.get(sym, p.current_price)
                p.current_price = curr_price
                print(
                    f"   • {sym:<8}: {p.side.value:<5} {abs(p.quantity):.4f} units | "
                    f"Entry: ${p.entry_price:,.2f} | Mark: ${curr_price:,.2f} | "
                    f"PnL: ${p.unrealized_pnl:+,.2f}"
                )
        if open_count == 0:
            print("   (No open positions currently. Risk Manager is monitoring entry setups...)")

        print("-" * 80)
        print("📡 LIVE MARKET PRICES & ATR VOLATILITY:")
        for sym, price in self.latest_prices.items():
            atr = self.latest_atrs.get(sym, 0.0)
            print(f"   • {sym:<8}: ${price:,.2f} (Live ATR: ${atr:.2f})")

        print("-" * 80)
        print(f"🔔 LAST EXECUTION RECEIPT:\n   {self.last_fill_summary}")
        print("=" * 80)


if __name__ == "__main__":
    app = PaperTradingApp()
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        pass

