"""
Institutional-Grade Live Paper Trading Runner.
Connects real-time Binance 1-minute Candlestick (Kline) streams,
evaluates strict EMA crossovers with ATR volatility, enforces pre-trade risk gates,
and executes realistic simulated fills with dynamic TP/SL and trade cooldowns.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
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
    Position,
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

# Configure clean logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("multiquant.paper_trading")


class PaperTradingApp:
    """Professional live paper trading runner with visual dashboard and risk controls."""

    def __init__(
        self,
        symbols: list[str] | None = None,
        trade_cooldown_sec: float = 60.0,  # Minimum 60s cooldown between trades per symbol
    ) -> None:
        self.symbols = [s.upper() for s in (symbols or ["BTCUSDT", "ETHUSDT"])]
        self.trade_cooldown_sec = trade_cooldown_sec

        self.notifier = DiscordNotifier(webhook_url=settings.DISCORD_WEBHOOK_URL)
        self.metrics = MetricsCollector(port=settings.PROMETHEUS_METRICS_PORT)
        self.state_mgr = StateManager(
            db_path=settings.SQLITE_DB_PATH,
            redis_host=settings.REDIS_HOST,
            redis_port=settings.REDIS_PORT,
        )
        self.oms = OMSEngine(on_fill=self._on_fill_event)
        self.risk_manager = PreTradeRiskManager()

        self.buffers: dict[str, RollingBarBuffer] = {
            s: RollingBarBuffer(maxlen=100) for s in self.symbols
        }

        self.portfolio = PortfolioState(
            total_equity=100_000.0,
            cash=100_000.0,
            initial_capital=100_000.0,
            peak_equity=100_000.0,
        )

        self.latest_prices: dict[str, float] = {}
        self.latest_atrs: dict[str, float] = {}
        self.last_trade_time: dict[str, float] = {}
        self.last_crossover_state: dict[str, str] = {}  # "BULLISH", "BEARISH"
        self.planned_targets: dict[str, dict[str, Any]] = {}
        self.closing_contexts: dict[str, dict[str, Any]] = {}
        self.win_count: int = 0
        self.loss_count: int = 0
        self.last_fill_summary: str = "Awaiting trade setups..."
        self.feed: BinanceWebSocketFeed | None = None
        self._running = False

    async def start(self) -> None:
        """Initialize all layers and start the paper trading loop."""
        self._running = True
        self.metrics.start_server()
        await self.state_mgr.initialize()

        print("\n" + "=" * 80)
        print("🚀 MULTIQUANT INSTITUTIONAL PAPER TRADING ENGINE STARTED")
        print(f"   Universe: {self.symbols} | Timeframe: 1-Minute Klines")
        print(f"   Initial Capital: ${self.portfolio.total_equity:,.2f} USD")
        print(f"   Trade Cooldown: {self.trade_cooldown_sec:.0f}s per asset")
        if settings.DISCORD_WEBHOOK_URL:
            print("   🔔 Discord Alerts: ACTIVE")
        else:
            print("   🔕 Discord Alerts: INACTIVE (Missing DISCORD_WEBHOOK_URL in .env)")
        print(f"   📈 Live Grafana Dashboard: http://localhost:3000")
        print(f"   📊 Prometheus Endpoint: http://localhost:{settings.PROMETHEUS_METRICS_PORT}/metrics")
        print("=" * 80 + "\n")

        # Send Startup Discord Alert
        await self.notifier.notify_startup(self.portfolio.total_equity, self.symbols)

        # Connect to Binance 1-Minute Kline WebSocket stream
        self.feed = BinanceWebSocketFeed(
            symbols=self.symbols,
            stream_type="kline_1m",
            on_bar=self._on_kline_bar,
        )
        await self.feed.connect()

        # Start terminal dashboard rendering loop
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
        print("\n⏹️ Paper trading engine shut down cleanly.")

    async def _on_kline_bar(self, bar: BarData) -> None:
        """
        Receives real-time 1-minute candlestick updates from Binance.
        Updates price mark, evaluates resting stop loss / take profit,
        and checks for true EMA crossovers to open new trades.
        """
        sym = bar.symbol
        current_price = bar.close
        self.latest_prices[sym] = current_price

        # Update metrics tick and price
        mock_tick = TickData(
            symbol=sym,
            bid_price=current_price - 0.5,
            ask_price=current_price + 0.5,
            last_price=current_price,
            volume=bar.volume,
            timestamp=bar.timestamp,
        )
        self.metrics.record_tick(mock_tick)
        self.metrics.record_market_price(sym, current_price, self.latest_atrs.get(sym, 0.0))
        await self.state_mgr.cache_market_tick(mock_tick)

        # 1. Update buffer with real candle data
        buf = self.buffers.get(sym)
        if not buf:
            return
        buf.add_bar(bar)

        # 2. Manage Active Position (Check Realistic Take Profit & Stop Loss)
        pos = self.oms.get_positions().get(sym)
        if pos is not None and pos.is_open:
            await self._manage_open_position(sym, pos, current_price)
            return

        # 3. Check Trade Cooldown
        now = time.time()
        last_time = self.last_trade_time.get(sym, 0.0)
        if (now - last_time) < self.trade_cooldown_sec:
            return

        # 4. Once buffer has enough real bars, compute indicators and evaluate Alpha
        if buf.count(sym) >= 20:
            df = buf.to_polars(sym)
            if df is None:
                return

            df_atr = compute_atr(df, period=14)
            df_calc = compute_ema(df_atr, period=7, column="close", out_col="ema_fast")
            df_calc = compute_ema(df_calc, period=21, column="close", out_col="ema_slow")

            # Calculate real ATR with minimum sensible floor (0.6% of asset price)
            raw_atr = float(df_calc["atr_14"][-1])
            min_atr_floor = current_price * 0.006  # e.g. ~$480 on BTC, ~$14 on ETH
            effective_atr = max(raw_atr, min_atr_floor)
            self.latest_atrs[sym] = effective_atr

            # True Crossover Detection
            fast_curr = float(df_calc["ema_fast"][-1])
            slow_curr = float(df_calc["ema_slow"][-1])
            fast_prev = float(df_calc["ema_fast"][-2])
            slow_prev = float(df_calc["ema_slow"][-2])

            curr_state = "BULLISH" if fast_curr > slow_curr else "BEARISH"
            prev_state = self.last_crossover_state.get(sym)
            self.last_crossover_state[sym] = curr_state

            # Signal only on a FRESH crossover
            is_golden_cross = (fast_curr > slow_curr) and (fast_prev <= slow_prev)
            is_death_cross = (fast_curr < slow_curr) and (fast_prev >= slow_prev)

            # If this is the initial warmup bar, initialize state without trading
            if prev_state is None:
                return

            signal_dir: SignalDirection | None = None
            if is_golden_cross:
                signal_dir = SignalDirection.LONG
            elif is_death_cross:
                signal_dir = SignalDirection.SHORT

            if signal_dir is not None:
                signal = AlphaSignal(
                    symbol=sym,
                    direction=signal_dir,
                    strength=0.90,
                    strategy_name="TrendEMA_Cross",
                    metadata={"atr": effective_atr, "close": current_price},
                )
                approved_order = self.risk_manager.evaluate_signal(signal, self.portfolio, current_price)
                if approved_order:
                    # Calculate and cache planned institutional Stop Loss and Take Profit targets
                    stop_dist = effective_atr * 2.0
                    tp_dist = stop_dist * 1.5
                    if signal_dir == SignalDirection.LONG:
                        planned_sl = current_price - stop_dist
                        planned_tp = current_price + tp_dist
                    else:
                        planned_sl = current_price + stop_dist
                        planned_tp = current_price - tp_dist

                    self.planned_targets[approved_order.order_id] = {
                        "symbol": sym,
                        "direction": signal_dir.value,
                        "stop_loss": planned_sl,
                        "take_profit": planned_tp,
                        "strategy": "TrendEMA_Cross",
                    }

                    self.last_trade_time[sym] = now
                    await self.oms.submit_order(approved_order, current_price=current_price)

    async def _manage_open_position(self, sym: str, pos: Position, current_price: float) -> None:
        """
        Manages an active position with institutional risk-reward boundaries.
        Stop Loss: 2.0x ATR (~1.2% distance)
        Take Profit: 3.0x ATR (~1.8% distance, Risk:Reward 1:1.5)
        """
        atr = self.latest_atrs.get(sym, current_price * 0.006)
        stop_dist = atr * 2.0
        tp_dist = stop_dist * 1.5

        should_close = False
        exit_reason = ""

        if pos.side == SignalDirection.LONG:
            if current_price <= (pos.entry_price - stop_dist):
                should_close = True
                exit_reason = f"🛑 Stop Loss Triggered (-${pos.entry_price - current_price:.2f})"
            elif current_price >= (pos.entry_price + tp_dist):
                should_close = True
                exit_reason = f"🎯 Take Profit Triggered (+${current_price - pos.entry_price:.2f})"
        elif pos.side == SignalDirection.SHORT:
            if current_price >= (pos.entry_price + stop_dist):
                should_close = True
                exit_reason = f"🛑 Stop Loss Triggered (-${current_price - pos.entry_price:.2f})"
            elif current_price <= (pos.entry_price - tp_dist):
                should_close = True
                exit_reason = f"🎯 Take Profit Triggered (+${pos.entry_price - current_price:.2f})"

        if should_close:
            close_side = OrderSide.SELL if pos.side == SignalDirection.LONG else OrderSide.BUY
            order_id = f"close_{uuid.uuid4().hex[:8]}"

            # Save context for close notification
            self.closing_contexts[order_id] = {
                "symbol": sym,
                "closed_side": pos.side.value if pos.side else "LONG",
                "entry_price": pos.entry_price,
                "quantity": abs(pos.quantity),
                "reason": exit_reason,
            }

            close_order = OrderRequest(
                order_id=order_id,
                symbol=sym,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=abs(pos.quantity),
                price=current_price,
                client_tag=exit_reason,
            )
            self.last_trade_time[sym] = time.time()
            await self.oms.submit_order(close_order, current_price=current_price)

    async def _on_fill_event(self, fill: FillEvent) -> None:
        """Process execution fill from OMS and dispatch rich OPEN/CLOSE Discord alerts."""
        self.last_fill_summary = (
            f"⚡ {fill.side.value} {fill.filled_quantity:.4f} {fill.symbol} @ ${fill.filled_price:,.2f} "
            f"(Fee: ${fill.fee:.2f} | Slippage: ${fill.slippage:.2f})"
        )
        self.metrics.record_fill(fill)
        await self.state_mgr.record_fill(fill)

        # 1. Update Portfolio State
        self.portfolio.positions = self.oms.get_positions()
        realized_pnl = self.oms.tracker.total_realized_pnl
        unrealized_pnl = sum(p.unrealized_pnl for p in self.portfolio.positions.values())
        self.portfolio.total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl

        # 2. Check if this fill is a CLOSE or an OPEN
        if fill.order_id in self.closing_contexts:
            ctx = self.closing_contexts.pop(fill.order_id)
            closed_side = ctx["closed_side"]
            entry_price = ctx["entry_price"]
            exit_price = fill.filled_price
            qty = ctx["quantity"]
            exit_reason = ctx["reason"]

            if closed_side.upper() == "LONG":
                trade_pnl = (exit_price - entry_price) * qty - (fill.fee + fill.slippage)
            else:
                trade_pnl = (entry_price - exit_price) * qty - (fill.fee + fill.slippage)

            is_win = trade_pnl >= 0
            if is_win:
                self.win_count += 1
            else:
                self.loss_count += 1

            self.metrics.record_trade_outcome(is_win, self.win_count, self.loss_count)
            await self.notifier.notify_trade_close(
                symbol=fill.symbol,
                closed_side=closed_side,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=qty,
                realized_pnl=trade_pnl,
                exit_reason=exit_reason,
                total_equity=self.portfolio.total_equity,
                fees=fill.fee,
                slippage=fill.slippage,
                win_count=self.win_count,
                loss_count=self.loss_count,
            )

        elif fill.order_id in self.planned_targets:
            target = self.planned_targets.pop(fill.order_id)
            pos_val = fill.filled_quantity * fill.filled_price
            await self.notifier.notify_trade_open(
                symbol=fill.symbol,
                side=fill.side.value,
                entry_price=fill.filled_price,
                quantity=fill.filled_quantity,
                position_value=pos_val,
                stop_loss=target["stop_loss"],
                take_profit=target["take_profit"],
                strategy_name=target.get("strategy", "TrendEMA_Cross"),
                fees=fill.fee,
            )
        else:
            # Fallback simple fill notification
            await self.notifier.notify_fill(fill)

        await self.state_mgr.record_portfolio_snapshot(self.portfolio)
        self.metrics.record_portfolio(
            self.portfolio, realized_pnl=realized_pnl, unrealized_pnl=unrealized_pnl
        )

    async def _dashboard_loop(self) -> None:
        """Render clean live status dashboard to terminal every 2 seconds."""
        while self._running:
            await asyncio.sleep(2.0)
            self._render_dashboard()
            # Push periodic portfolio valuation to Prometheus
            realized_pnl = self.oms.tracker.total_realized_pnl
            positions = self.oms.get_positions()
            unrealized_pnl = sum(p.unrealized_pnl for p in positions.values())
            self.portfolio.total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl
            self.metrics.record_portfolio(
                self.portfolio, realized_pnl=realized_pnl, unrealized_pnl=unrealized_pnl
            )

    def _render_dashboard(self) -> None:
        """Print clean institutional status dashboard."""
        realized_pnl = self.oms.tracker.total_realized_pnl
        positions = self.oms.get_positions()
        unrealized_pnl = sum(p.unrealized_pnl for p in positions.values())
        total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl

        pnl_sign = "+" if unrealized_pnl >= 0 else "-"
        total_trades = self.win_count + self.loss_count
        win_rate = (self.win_count / total_trades * 100.0) if total_trades > 0 else 0.0

        print("\n" + "=" * 80)
        print(f"📊 MULTIQUANT PAPER TRADING MONITOR — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        print("=" * 80)
        print(
            f"💰 Total Equity : ${total_equity:,.2f} | Cash: ${self.portfolio.cash:,.2f}\n"
            f"📈 Realized PnL : ${realized_pnl:+,.2f} | Unrealized PnL: {pnl_sign}${abs(unrealized_pnl):,.2f}\n"
            f"🎯 Win Rate     : {win_rate:.1f}% ({self.win_count} Wins / {self.loss_count} Losses) | Trades: {total_trades}\n"
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
            print("   (No active positions. Scanning for 1-minute EMA Crossover setups...)")

        print("-" * 80)
        print("📡 LIVE MARKET PRICES & 1-MIN ATR VOLATILITY:")
        for sym, price in self.latest_prices.items():
            atr = self.latest_atrs.get(sym, 0.0)
            state = self.last_crossover_state.get(sym, "WARMING_UP")
            print(f"   • {sym:<8}: ${price:,.2f} | 1m ATR: ${atr:.2f} | Trend: {state}")

        print("-" * 80)
        print(f"🔔 LAST EXECUTION RECEIPT:\n   {self.last_fill_summary}")
        print("=" * 80)


if __name__ == "__main__":
    app = PaperTradingApp()
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        pass
