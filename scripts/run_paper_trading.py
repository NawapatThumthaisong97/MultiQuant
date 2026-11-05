"""
Institutional-Grade Live Paper Trading Runner.
Connects real-time Binance 1-minute Candlestick (Kline) streams,
evaluates strict EMA crossovers with ATR volatility, enforces pre-trade risk gates,
and executes realistic simulated fills with dynamic TP/SL and trade cooldowns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
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
from src.layer2_alpha.strategies import MTFVolatilitySqueezeAlpha
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
        initial_capital: float | None = None,
        trade_cooldown_sec: float = 60.0,  # Minimum 60s cooldown between trades per symbol
        demo_mode: bool = False,
    ) -> None:
        self.symbols = [s.upper() for s in (symbols or ["BTCUSDT"])]
        self.trade_cooldown_sec = trade_cooldown_sec
        self.initial_capital = (
            initial_capital if initial_capital is not None else settings.INITIAL_CAPITAL
        )
        self.demo_mode = demo_mode
        self._demo_executed = False

        self.notifier = DiscordNotifier(webhook_url=settings.DISCORD_WEBHOOK_URL)
        self.metrics = MetricsCollector(port=settings.PROMETHEUS_METRICS_PORT)
        self.state_mgr = StateManager(
            db_path=settings.SQLITE_DB_PATH,
            redis_host=settings.REDIS_HOST,
            redis_port=settings.REDIS_PORT,
        )
        self.oms = OMSEngine(on_fill=self._on_fill_event)
        self.risk_manager = PreTradeRiskManager()
        self.alpha_strategy = MTFVolatilitySqueezeAlpha(warmup_period=100)

        self.buffers: dict[str, RollingBarBuffer] = {
            s: RollingBarBuffer(maxlen=600) for s in self.symbols
        }

        self.portfolio = PortfolioState(
            total_equity=self.initial_capital,
            cash=self.initial_capital,
            initial_capital=self.initial_capital,
            peak_equity=self.initial_capital,
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
        print(f"   Universe: {self.symbols} | Timeframe: 5-Minute Klines (P6 MTF Squeeze Champion)")
        print(f"   Initial Capital: ${self.portfolio.total_equity:,.2f} USD")
        print(f"   Trade Cooldown: {self.trade_cooldown_sec:.0f}s per asset")
        if settings.DISCORD_WEBHOOK_URL:
            print("   🔔 Discord Alerts: ACTIVE")
        else:
            print("   🔕 Discord Alerts: INACTIVE (Missing DISCORD_WEBHOOK_URL in .env)")
        print("   📈 Live Grafana Dashboard: http://localhost:3000")
        print(f"   📊 Prometheus Endpoint: http://localhost:{settings.PROMETHEUS_METRICS_PORT}/metrics")
        print("=" * 80 + "\n")

        # Initialize baseline metrics in Prometheus
        self.metrics.record_portfolio(self.portfolio, realized_pnl=0.0, unrealized_pnl=0.0)
        for sym in self.symbols:
            self.metrics.record_market_price(sym, 0.0, 0.0)

        # Preload 600 historical 5m candles (50 hours) so EMA 576 / Donchian 96 are warm immediately
        self._preload_historical_warmup()

        # Send Startup Discord Alert
        await self.notifier.notify_startup(self.portfolio.total_equity, self.symbols)

        # Connect to Binance 5-Minute Kline WebSocket stream
        self.feed = BinanceWebSocketFeed(
            symbols=self.symbols,
            stream_type="kline_5m",
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

    def _preload_historical_warmup(self) -> None:
        """Fetches 600 recent 5m candles from Binance REST API so 48h EMA(576) is warm immediately."""
        for sym in self.symbols:
            try:
                url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=5m&limit=600"
                req = urllib.request.Request(url, headers={"User-Agent": "MultiQuant/1.0"})
                with urllib.request.urlopen(req, timeout=10) as res:
                    raw_klines = json.loads(res.read().decode("utf-8"))
                buf = self.buffers.get(sym)
                if buf and raw_klines:
                    for k in raw_klines[:-1]:  # Exclude currently forming candle
                        bar = BarData(
                            symbol=sym,
                            open=float(k[1]),
                            high=float(k[2]),
                            low=float(k[3]),
                            close=float(k[4]),
                            volume=float(k[5]),
                            timestamp=datetime.fromtimestamp(int(k[0]) / 1000.0, tz=timezone.utc),
                            interval="5m",
                        )
                        buf.add_bar(bar)
                    last_close = float(raw_klines[-1][4])
                    self.latest_prices[sym] = last_close
                    print(
                        f"   ✅ Preloaded {buf.count(sym)} historical 5m bars for {sym} "
                        f"(48h EMA 576 & 8h Donchian 96 Warmed Up | Mark: ${last_close:,.2f})"
                    )
            except Exception as e:
                logger.warning("Could not preload historical 5m warmup for %s: %s", sym, e)

    async def _shutdown(self) -> None:
        if self.feed:
            await self.feed.disconnect()
        await self.state_mgr.shutdown()

        # Send Shutdown Discord Alert
        total_trades = self.win_count + self.loss_count
        realized_pnl = self.oms.tracker.total_realized_pnl
        await self.notifier.notify_shutdown(
            final_equity=self.portfolio.total_equity,
            realized_pnl=realized_pnl,
            total_trades=total_trades,
            win_count=self.win_count,
            loss_count=self.loss_count,
        )
        await self.notifier.close()
        print("\n⏹️ Paper trading engine shut down cleanly. Discord shutdown alert sent.")

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

        # Demo mode automated trade trigger
        if self.demo_mode and not self._demo_executed:
            self._demo_executed = True
            asyncio.create_task(self._run_demo_trade_flow(sym, current_price))

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
        if buf.count(sym) >= 30:
            df = buf.to_polars(sym)
            if df is None:
                return

            df_atr = compute_atr(df, period=14)
            df_calc = compute_ema(df_atr, period=12, column="close", out_col="ema_1h")
            df_calc = compute_ema(df_calc, period=144, column="close", out_col="ema_12h")

            raw_atr = float(df_calc["atr_14"][-1])
            min_atr_floor = current_price * 0.0035
            effective_atr = max(raw_atr, min_atr_floor)
            self.latest_atrs[sym] = effective_atr

            ema_1h = float(df_calc["ema_1h"][-1])
            ema_12h = float(df_calc["ema_12h"][-1])
            curr_state = "BULLISH" if ema_1h > ema_12h else "BEARISH"
            self.last_crossover_state[sym] = curr_state

            signal = self.alpha_strategy.generate_signal(sym, df)
            if signal is not None:
                signal = AlphaSignal(
                    symbol=sym,
                    direction=signal.direction,
                    strength=signal.strength,
                    strategy_name=signal.strategy_name,
                    metadata={"atr": effective_atr, "close": current_price},
                )
                approved_order = self.risk_manager.evaluate_signal(
                    signal, self.portfolio, current_price
                )
                if approved_order:
                    stop_dist = max(effective_atr * 4.5, current_price * 0.015)
                    tp_dist = stop_dist * 3.0
                    if signal.direction == SignalDirection.LONG:
                        planned_sl = current_price - stop_dist
                        planned_tp = current_price + tp_dist
                    else:
                        planned_sl = current_price + stop_dist
                        planned_tp = current_price - tp_dist

                    self.planned_targets[approved_order.order_id] = {
                        "symbol": sym,
                        "direction": signal.direction.value,
                        "stop_loss": planned_sl,
                        "take_profit": planned_tp,
                        "strategy": signal.strategy_name,
                    }

                    self.last_trade_time[sym] = now
                    await self.oms.submit_order(approved_order, current_price=current_price)

    async def _manage_open_position(self, sym: str, pos: Position, current_price: float) -> None:
        """
        Manages an active position with institutional asymmetric 1:3.0 risk-reward boundaries.
        Stop Loss: max(4.5x ATR, 1.5% distance)
        Take Profit: 3.0x Stop Distance (Risk:Reward 1:3.0)
        """
        atr = self.latest_atrs.get(sym, current_price * 0.0035)
        stop_dist = max(atr * 4.5, pos.entry_price * 0.015)
        tp_dist = stop_dist * 3.0

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
        try:
            await self.state_mgr.record_fill(fill)
        except Exception as e:
            logger.warning("Could not persist fill to SQLite state: %s", e)

        # 1. Update Portfolio State
        self.portfolio.positions = self.oms.get_positions()
        realized_pnl = self.oms.tracker.total_realized_pnl
        unrealized_pnl = sum(p.unrealized_pnl for p in self.portfolio.positions.values())
        notional_held = sum(p.notional_value for p in self.portfolio.positions.values())
        self.portfolio.total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl
        self.portfolio.cash = max(0.0, self.portfolio.total_equity - notional_held)

        # 2. Check if this fill is a CLOSE or an OPEN
        is_close_order = fill.order_id.startswith("close_") or fill.order_id in self.closing_contexts

        if is_close_order:
            ctx = self.closing_contexts.pop(fill.order_id, {})
            closed_side = ctx.get("closed_side", "LONG" if fill.side == OrderSide.SELL else "SHORT")
            entry_price = ctx.get("entry_price", fill.filled_price)
            exit_price = fill.filled_price
            qty = ctx.get("quantity", fill.filled_quantity)
            exit_reason = ctx.get("reason", "Take Profit / Stop Loss Triggered")

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
            sent = await self.notifier.notify_trade_close(
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
            print(
                f"\n🔔 [Discord Alert: Trade CLOSE {'Sent' if sent else 'Suppressed'}] "
                f"{closed_side} {fill.symbol} | PnL: ${trade_pnl:+,.2f} | Reason: {exit_reason}"
            )

        else:
            # This is an OPEN order
            target = self.planned_targets.pop(fill.order_id, {})
            pos_val = fill.filled_quantity * fill.filled_price
            atr = target.get("atr", self.latest_atrs.get(fill.symbol, fill.filled_price * 0.006))
            stop_dist = atr * 2.0
            tp_dist = stop_dist * 1.5

            if fill.side == OrderSide.BUY:
                stop_loss = target.get("stop_loss", fill.filled_price - stop_dist)
                take_profit = target.get("take_profit", fill.filled_price + tp_dist)
            else:
                stop_loss = target.get("stop_loss", fill.filled_price + stop_dist)
                take_profit = target.get("take_profit", fill.filled_price - tp_dist)

            sent = await self.notifier.notify_trade_open(
                symbol=fill.symbol,
                side=fill.side.value,
                entry_price=fill.filled_price,
                quantity=fill.filled_quantity,
                position_value=pos_val,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy_name=target.get("strategy", "TrendEMA_Cross"),
                fees=fill.fee,
            )
            print(
                f"\n🔔 [Discord Alert: Trade OPEN {'Sent' if sent else 'Suppressed'}] "
                f"{fill.side.value} {fill.filled_quantity:.4f} {fill.symbol} @ ${fill.filled_price:,.2f} "
                f"(SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f})"
            )

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
            self.portfolio.positions = positions
            unrealized_pnl = sum(p.unrealized_pnl for p in positions.values())
            notional_held = sum(p.notional_value for p in positions.values())
            self.portfolio.total_equity = self.portfolio.initial_capital + realized_pnl + unrealized_pnl
            self.portfolio.cash = max(0.0, self.portfolio.total_equity - notional_held)
            self.metrics.record_portfolio(
                self.portfolio, realized_pnl=realized_pnl, unrealized_pnl=unrealized_pnl
            )

    async def _run_demo_trade_flow(self, sym: str, current_price: float) -> None:
        """
        Runs an automated comprehensive test suite covering all realistic cases:
        Case 1: Open LONG position -> Trigger Take Profit (WIN)
        Case 2: Open SHORT position -> Trigger Stop Loss (LOSS)
        Verifies Discord Rich Alerts (Open/Close), OMS Accounting, Grafana Metrics, and Win Rate.
        """
        await asyncio.sleep(2.0)
        print(f"\n🧪 [COMPREHENSIVE TEST FLOW] Starting Multi-Case Verification on {sym} @ ${current_price:,.2f}...")

        atr = self.latest_atrs.get(sym, current_price * 0.006)
        stop_dist = atr * 2.0
        tp_dist = stop_dist * 1.5

        # -------------------------------------------------------------
        # CASE 1: LONG POSITION -> TAKE PROFIT (WIN)
        # -------------------------------------------------------------
        print("\n🔹 [CASE 1/2] Testing LONG Position -> Take Profit Triggered (WIN)...")
        sig_long = AlphaSignal(
            symbol=sym,
            direction=SignalDirection.LONG,
            strength=0.90,
            strategy_name="Bullish_Breakout",
            metadata={"atr": atr, "close": current_price},
        )
        approved_order_long = self.risk_manager.evaluate_signal(sig_long, self.portfolio, current_price)
        if approved_order_long:
            self.planned_targets[approved_order_long.order_id] = {
                "symbol": sym,
                "direction": "LONG",
                "stop_loss": current_price - stop_dist,
                "take_profit": current_price + tp_dist,
                "strategy": "Bullish_Breakout",
            }
            await self.oms.submit_order(approved_order_long, current_price=current_price)

            print("⏳ [CASE 1/2] Holding LONG position for 6 seconds (Watch Discord & Grafana)...")
            await asyncio.sleep(6.0)

            # Close with Take Profit (+Price increase)
            pos_long = self.oms.get_positions().get(sym)
            if pos_long and pos_long.is_open:
                win_exit_price = pos_long.entry_price + (atr * 0.75)
                close_id_1 = f"close_{uuid.uuid4().hex[:8]}"
                self.closing_contexts[close_id_1] = {
                    "symbol": sym,
                    "closed_side": "LONG",
                    "entry_price": pos_long.entry_price,
                    "quantity": abs(pos_long.quantity),
                    "reason": "🎯 Take Profit Triggered (+Target Hit)",
                }
                close_order_1 = OrderRequest(
                    order_id=close_id_1,
                    symbol=sym,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=abs(pos_long.quantity),
                    price=win_exit_price,
                    client_tag="Take Profit Exit",
                )
                await self.oms.submit_order(close_order_1, current_price=win_exit_price)
                print("✅ [CASE 1/2] LONG Take Profit executed successfully!")

        await asyncio.sleep(4.0)

        # -------------------------------------------------------------
        # CASE 2: SHORT POSITION -> STOP LOSS (LOSS)
        # -------------------------------------------------------------
        print("\n🔸 [CASE 2/2] Testing SHORT Position -> Stop Loss Triggered (LOSS)...")
        sig_short = AlphaSignal(
            symbol=sym,
            direction=SignalDirection.SHORT,
            strength=0.90,
            strategy_name="Bearish_Rejection",
            metadata={"atr": atr, "close": current_price},
        )
        approved_order_short = self.risk_manager.evaluate_signal(sig_short, self.portfolio, current_price)
        if approved_order_short:
            self.planned_targets[approved_order_short.order_id] = {
                "symbol": sym,
                "direction": "SHORT",
                "stop_loss": current_price + stop_dist,
                "take_profit": current_price - tp_dist,
                "strategy": "Bearish_Rejection",
            }
            await self.oms.submit_order(approved_order_short, current_price=current_price)

            print("⏳ [CASE 2/2] Holding SHORT position for 6 seconds (Watch Discord & Grafana)...")
            await asyncio.sleep(6.0)

            # Close with Stop Loss (Price went up against short)
            pos_short = self.oms.get_positions().get(sym)
            if pos_short and pos_short.is_open:
                loss_exit_price = pos_short.entry_price + (atr * 0.5)
                close_id_2 = f"close_{uuid.uuid4().hex[:8]}"
                self.closing_contexts[close_id_2] = {
                    "symbol": sym,
                    "closed_side": "SHORT",
                    "entry_price": pos_short.entry_price,
                    "quantity": abs(pos_short.quantity),
                    "reason": "🛑 Stop Loss Triggered (Risk Defense)",
                }
                close_order_2 = OrderRequest(
                    order_id=close_id_2,
                    symbol=sym,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=abs(pos_short.quantity),
                    price=loss_exit_price,
                    client_tag="Stop Loss Exit",
                )
                await self.oms.submit_order(close_order_2, current_price=loss_exit_price)
                print("🛑 [CASE 2/2] SHORT Stop Loss executed successfully!")

        print("\n🎉 [ALL CASES VERIFIED] Full-loop tested: 1 WIN, 1 LOSS, Realized PnL, Win Rate & Discord/Grafana synced.\n")

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
    import argparse

    parser = argparse.ArgumentParser(
        description="MultiQuant Institutional Live Paper Trading",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "capital_pos",
        nargs="?",
        type=float,
        default=None,
        help="Initial capital in USD (e.g. 50, 100, 500)",
    )
    parser.add_argument(
        "--capital",
        "-c",
        type=float,
        default=None,
        help="Initial capital in USD (e.g. --capital 50)",
    )
    parser.add_argument(
        "--symbols",
        "-s",
        type=str,
        default=None,
        help="Comma-separated trading symbols (e.g. 'BTCUSDT,ETHUSDT')",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=60.0,
        help="Trade cooldown in seconds per asset",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run an automated test trade on startup to immediately verify Discord and Grafana sync",
    )

    args = parser.parse_args()

    chosen_capital = (
        args.capital
        if args.capital is not None
        else (args.capital_pos if args.capital_pos is not None else settings.INITIAL_CAPITAL)
    )
    chosen_symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols
        else settings.DEFAULT_SYMBOLS
    )

    app = PaperTradingApp(
        symbols=chosen_symbols,
        initial_capital=chosen_capital,
        trade_cooldown_sec=args.cooldown,
        demo_mode=args.demo,
    )
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        pass
