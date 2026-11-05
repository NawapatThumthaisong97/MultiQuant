"""
CLI Runner for MultiQuant Historical Backtest on Binance BTC Data.
Fetches real historical candlestick data from Binance API and benchmarks:
1. Baseline Raw EMA Crossover (Unfiltered)
2. Quantitative Filtered EMA Strategy (Trend Regime + Volatility Filter)
Outputs institutional performance metrics, Sharpe/Sortino ratios, and Drawdowns.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import polars as pl

from src.layer2_alpha.backtest import BacktestEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("multiquant.run_backtest")


def fetch_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    total_candles: int = 1000,
) -> pl.DataFrame:
    """Fetches real historical klines from Binance Public REST API."""
    logger.info("Fetching %d candles for %s (%s) from Binance API...", total_candles, symbol, interval)
    base_url = "https://api.binance.com/api/v3/klines"
    limit = min(total_candles, 1000)

    url = f"{base_url}?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "MultiQuant/1.0"})

    with urllib.request.urlopen(req, timeout=15) as response:
        raw_data = json.loads(response.read().decode("utf-8"))

    rows = []
    for k in raw_data:
        rows.append({
            "timestamp": datetime.fromtimestamp(k[0] / 1000.0),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    df = pl.DataFrame(rows)
    logger.info("Loaded %d historical bars (From: %s To: %s)", len(df), df["timestamp"][0], df["timestamp"][-1])
    return df


def print_comparison_table(res_raw: Any, res_filtered: Any, symbol: str, interval: str) -> None:
    """Prints a beautiful institutional benchmarking table in terminal."""
    print("\n" + "=" * 88)
    print(f"📊 MULTIQUANT INSTITUTIONAL BACKTEST REPORT: {symbol} [{interval}]")
    print("=" * 88)
    header = f"{'Metric Name':<32} | {'Baseline (No Filter)':<22} | {'MultiQuant Quant-Filtered':<22}"
    print(header)
    print("-" * 88)

    rows = [
        ("Initial Capital", f"${res_raw.initial_capital:.2f}", f"${res_filtered.initial_capital:.2f}"),
        ("Final Equity", f"${res_raw.final_equity:.2f}", f"${res_filtered.final_equity:.2f}"),
        ("Total Return %", f"{res_raw.total_return_pct:+.2f}%", f"{res_filtered.total_return_pct:+.2f}%"),
        ("Annualized Return %", f"{res_raw.annualized_return_pct:+.2f}%", f"{res_filtered.annualized_return_pct:+.2f}%"),
        ("Sharpe Ratio (Annualized)", f"{res_raw.sharpe_ratio:.2f}", f"{res_filtered.sharpe_ratio:.2f}"),
        ("Sortino Ratio", f"{res_raw.sortino_ratio:.2f}", f"{res_filtered.sortino_ratio:.2f}"),
        ("Max Drawdown %", f"{res_raw.max_drawdown_pct:.2f}%", f"{res_filtered.max_drawdown_pct:.2f}%"),
        ("Profit Factor", f"{res_raw.profit_factor:.2f}", f"{res_filtered.profit_factor:.2f}"),
        ("Win Rate %", f"{res_raw.win_rate_pct:.1f}%", f"{res_filtered.win_rate_pct:.1f}%"),
        ("Total Trades Executed", f"{res_raw.total_trades} trades", f"{res_filtered.total_trades} trades"),
        ("Winning vs Losing Trades", f"{res_raw.winning_trades}W / {res_raw.losing_trades}L", f"{res_filtered.winning_trades}W / {res_filtered.losing_trades}L"),
        ("Total Fees & Slippage Paid", f"${res_raw.total_fees_paid + res_raw.total_slippage_paid:.2f}", f"${res_filtered.total_fees_paid + res_filtered.total_slippage_paid:.2f}"),
    ]

    for name, v1, v2 in rows:
        print(f"{name:<32} | {v1:<22} | {v2:<22}")

    print("=" * 88)


def main() -> None:
    parser = argparse.ArgumentParser(description="MultiQuant Institutional Backtesting")
    parser.add_argument("--symbol", default="BTCUSDT", help="Pair to backtest (default: BTCUSDT)")
    parser.add_argument("--interval", default="1h", help="Timeframe (1h, 15m, 4h)")
    parser.add_argument("--candles", type=int, default=1000, help="Number of candles (default: 1000)")
    parser.add_argument("--capital", type=float, default=50.0, help="Initial capital in USD")
    args = parser.parse_args()

    df = fetch_binance_klines(symbol=args.symbol, interval=args.interval, total_candles=args.candles)

    engine = BacktestEngine(initial_capital=args.capital)

    # 1. Run Baseline (No Filters)
    metrics_raw, _ = engine.run(
        df,
        fast_period=9,
        slow_period=21,
        trend_period=200,
        use_trend_filter=False,
        use_volatility_filter=False,
    )

    # 2. Run Quant-Filtered Strategy (Regime Trend + Volatility Scaling)
    metrics_filtered, _ = engine.run(
        df,
        fast_period=9,
        slow_period=21,
        trend_period=200,
        use_trend_filter=True,
        use_volatility_filter=True,
    )

    print_comparison_table(metrics_raw, metrics_filtered, args.symbol, args.interval)


if __name__ == "__main__":
    main()
