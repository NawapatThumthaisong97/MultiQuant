"""
Interactive Live Demo: MultiQuant Layer 1 -> Layer 2 -> Layer 3.
Connects to live Binance WebSockets, computes Polars indicators in real-time,
and dynamically sizes positions using live ATR volatility.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.core.models import AlphaSignal, BarData, PortfolioState, SignalDirection, TickData
from src.layer1_connectivity.websocket_client import BinanceWebSocketFeed
from src.layer2_alpha.buffer import RollingBarBuffer
from src.layer2_alpha.indicators import compute_atr, compute_ema
from src.layer3_risk.risk_manager import PreTradeRiskManager

# Configure clean console logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


async def run_live_risk_demo(duration_seconds: int = 15) -> None:
    print("=" * 75)
    print("🚀 MultiQuant Live Pipeline Demo (Layers 1 + 2 + 3)")
    print("   Connecting to Live Binance WebSocket Stream...")
    print("   Portfolio Capital: $100,000.00 USD | Risk Fraction: 1.5% per trade")
    print("=" * 75)

    portfolio = PortfolioState(
        total_equity=100_000.0,
        cash=100_000.0,
        initial_capital=100_000.0,
        peak_equity=100_000.0,
    )
    risk_manager = PreTradeRiskManager()
    buffers: dict[str, RollingBarBuffer] = {
        "BTCUSDT": RollingBarBuffer(maxlen=50),
        "ETHUSDT": RollingBarBuffer(maxlen=50),
    }

    tick_count = 0

    async def on_live_tick(tick: TickData) -> None:
        nonlocal tick_count
        tick_count += 1
        sym = tick.symbol
        buf = buffers.get(sym)
        if not buf:
            return

        # Synthesize real-time micro-bar for dynamic ATR demonstration
        current_time = datetime.now(UTC)
        synthetic_bar = BarData(
            symbol=sym,
            timestamp=current_time,
            open=tick.bid_price - 0.2,
            high=max(tick.ask_price, tick.last_price + 0.5),
            low=min(tick.bid_price, tick.last_price - 0.5),
            close=tick.last_price,
            volume=tick.volume if tick.volume > 0 else 1.0,
            timeframe="1s",
        )
        buf.add_bar(synthetic_bar)

        # Once we have at least 15 bars, compute live ATR with Polars
        if buf.count(sym) >= 15:
            df = buf.to_polars(sym)
            if df is not None:
                df_atr = compute_atr(df, period=14)
                df_calc = compute_ema(df_atr, period=5, column="close")
                latest_atr = float(df_calc["atr_14"][-1])
                current_price = tick.last_price

                # Create test Alpha Signal
                test_signal = AlphaSignal(
                    symbol=sym,
                    direction=SignalDirection.LONG,
                    strength=0.90,
                    strategy_name="LiveDemoStrategy",
                    metadata={"atr": latest_atr, "close": current_price},
                )

                # Evaluate through Layer 3 Pre-Trade Risk Manager
                approved_order = risk_manager.evaluate_signal(test_signal, portfolio, current_price)

                if approved_order:
                    print(
                        f"\n[LIVE TICK #{tick_count}] {sym} Price: ${current_price:,.2f} | Live ATR: ${latest_atr:.2f}\n"
                        f"   🛡️ Risk Check: APPROVED\n"
                        f"   📦 Calculated Sizing: {approved_order.quantity:.4f} {sym} (Notional: ${approved_order.quantity * current_price:,.2f})\n"
                        f"   🛑 Dynamic Stop Loss: ${approved_order.stop_price:,.2f} (-2x ATR)\n"
                        f"   🎯 Order Request ID: {approved_order.order_id}"
                    )
        else:
            print(
                f". Buffering real ticks for {sym}: {buf.count(sym)}/15 bars...",
                end="\r",
                flush=True,
            )

    feed = BinanceWebSocketFeed(
        symbols=["BTCUSDT", "ETHUSDT"],
        stream_type="trade",
        on_tick=on_live_tick,
    )

    await feed.connect()
    try:
        await asyncio.sleep(duration_seconds)
    finally:
        print("\n\n⏹️ Stopping Live WebSocket Feed...")
        await feed.disconnect()
        print("✅ Live Demo complete.")


if __name__ == "__main__":
    asyncio.run(run_live_risk_demo(duration_seconds=15))
