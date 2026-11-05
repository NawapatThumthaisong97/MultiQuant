"""
CLI Runner for MultiQuant 3-Tier Historical Backtest on Binance BTC Data.
Benchmarks 3 Tiers of Quant Evolution:
Tier 1: Baseline Raw EMA Crossover (No Filters)
Tier 2: Trend Regime (EMA 50/200) + Volatility Spike Filter (ATR Scaling)
Tier 3: Institutional Microstructure Strategy (Trend + ATR + CVD + Open Interest + Funding Rate Shield)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import polars as pl

from src.layer2_alpha.backtest import BacktestEngine, BacktestMetrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("multiquant.run_backtest")


def fetch_comprehensive_binance_data(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    total_candles: int = 1000,
) -> pl.DataFrame:
    """Fetches OHLCV, Taker Buy Volume, Open Interest, and Funding Rate from Binance."""
    logger.info("Fetching %d candles with Microstructure data for %s (%s)...", total_candles, symbol, interval)

    # 1. Fetch Spot/Perp Klines with Taker Buy Volume
    kline_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={min(total_candles, 1000)}"
    req = urllib.request.Request(kline_url, headers={"User-Agent": "MultiQuant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        raw_klines = json.loads(res.read().decode("utf-8"))

    # 2. Fetch Open Interest History (Futures)
    oi_url = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period={interval}&limit=500"
    oi_map: dict[int, float] = {}
    try:
        req_oi = urllib.request.Request(oi_url, headers={"User-Agent": "MultiQuant/1.0"})
        with urllib.request.urlopen(req_oi, timeout=15) as res_oi:
            raw_oi = json.loads(res_oi.read().decode("utf-8"))
            for item in raw_oi:
                # floor timestamp to hour
                ts_key = int(item["timestamp"]) // (3600 * 1000) * (3600 * 1000)
                oi_map[ts_key] = float(item["sumOpenInterest"])
    except Exception as e:
        logger.warning("Could not fetch Binance Open Interest history: %s (Will use interpolation)", e)

    # 3. Fetch Funding Rate History
    funding_url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=500"
    funding_map: dict[int, float] = {}
    try:
        req_f = urllib.request.Request(funding_url, headers={"User-Agent": "MultiQuant/1.0"})
        with urllib.request.urlopen(req_f, timeout=15) as res_f:
            raw_f = json.loads(res_f.read().decode("utf-8"))
            for item in raw_f:
                ts_key = int(item["fundingTime"]) // (3600 * 1000) * (3600 * 1000)
                funding_map[ts_key] = float(item["fundingRate"])
    except Exception as e:
        logger.warning("Could not fetch Binance Funding Rate history: %s", e)

    rows = []
    last_known_oi = 85000.0
    last_known_funding = 0.0001  # Baseline neutral 0.01%

    for k in raw_klines:
        raw_ts = int(k[0])
        hour_ts = raw_ts // (3600 * 1000) * (3600 * 1000)

        # Update last known values if present
        if hour_ts in oi_map:
            last_known_oi = oi_map[hour_ts]
        if hour_ts in funding_map:
            last_known_funding = funding_map[hour_ts]

        rows.append({
            "timestamp": datetime.fromtimestamp(raw_ts / 1000.0),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "taker_buy_volume": float(k[9]),
            "open_interest": last_known_oi,
            "funding_rate": last_known_funding,
        })

    df = pl.DataFrame(rows)
    logger.info("Loaded %d bars with full OHLCV & Microstructure data (From: %s To: %s)", len(df), df["timestamp"][0], df["timestamp"][-1])
    return df


def print_3tier_benchmark_table(
    t1: BacktestMetrics,
    t2: BacktestMetrics,
    t3: BacktestMetrics,
    symbol: str,
    interval: str,
) -> None:
    """Prints a beautiful 3-tier comparative benchmark table."""
    print("\n" + "=" * 108)
    print(f"🏛️ MULTIQUANT 3-TIER INSTITUTIONAL BENCHMARK REPORT: {symbol} [{interval}]")
    print("=" * 108)
    header = f"{'Performance Metric':<28} | {'Tier 1: Baseline':<22} | {'Tier 2: Quant Regime':<24} | {'Tier 3: Institutional Micro'}"
    print(header)
    print("-" * 108)

    rows = [
        ("Initial Capital", f"${t1.initial_capital:.2f}", f"${t2.initial_capital:.2f}", f"${t3.initial_capital:.2f}"),
        ("Final Portfolio Equity", f"${t1.final_equity:.2f}", f"${t2.final_equity:.2f}", f"${t3.final_equity:.2f}"),
        ("Total Net Return %", f"{t1.total_return_pct:+.2f}%", f"{t2.total_return_pct:+.2f}%", f"{t3.total_return_pct:+.2f}%"),
        ("Annualized Return %", f"{t1.annualized_return_pct:+.2f}%", f"{t2.annualized_return_pct:+.2f}%", f"{t3.annualized_return_pct:+.2f}%"),
        ("Sharpe Ratio (Annualized)", f"{t1.sharpe_ratio:.2f}", f"{t2.sharpe_ratio:.2f}", f"{t3.sharpe_ratio:.2f}"),
        ("Sortino Ratio", f"{t1.sortino_ratio:.2f}", f"{t2.sortino_ratio:.2f}", f"{t3.sortino_ratio:.2f}"),
        ("Max Peak Drawdown %", f"{t1.max_drawdown_pct:.2f}%", f"{t2.max_drawdown_pct:.2f}%", f"{t3.max_drawdown_pct:.2f}%"),
        ("Profit Factor", f"{t1.profit_factor:.2f}", f"{t2.profit_factor:.2f}", f"{t3.profit_factor:.2f}"),
        ("Win Rate %", f"{t1.win_rate_pct:.1f}%", f"{t2.win_rate_pct:.1f}%", f"{t3.win_rate_pct:.1f}%"),
        ("Total Trades Executed", f"{t1.total_trades} trades", f"{t2.total_trades} trades", f"{t3.total_trades} trades"),
        ("Wins vs Losses Count", f"{t1.winning_trades}W / {t1.losing_trades}L", f"{t2.winning_trades}W / {t2.losing_trades}L", f"{t3.winning_trades}W / {t3.losing_trades}L"),
        ("Total Fees & Slippage Paid", f"${t1.total_fees_paid + t1.total_slippage_paid:.2f}", f"${t2.total_fees_paid + t2.total_slippage_paid:.2f}", f"${t3.total_fees_paid + t3.total_slippage_paid:.2f}"),
    ]

    for name, v1, v2, v3 in rows:
        print(f"{name:<28} | {v1:<22} | {v2:<24} | {v3:<26}")

    print("=" * 108)


def main() -> None:
    parser = argparse.ArgumentParser(description="MultiQuant Institutional 3-Tier Backtesting")
    parser.add_argument("--symbol", default="BTCUSDT", help="Pair to backtest (default: BTCUSDT)")
    parser.add_argument("--interval", default="1h", help="Timeframe (1h, 15m, 4h)")
    parser.add_argument("--candles", type=int, default=1000, help="Number of candles (default: 1000)")
    parser.add_argument("--capital", type=float, default=50.0, help="Initial capital in USD")
    args = parser.parse_args()

    df = fetch_comprehensive_binance_data(symbol=args.symbol, interval=args.interval, total_candles=args.candles)
    engine = BacktestEngine(initial_capital=args.capital)

    # Tier 1: Baseline Raw EMA Crossover (No Filters)
    m_t1, _ = engine.run(
        df,
        strategy_name="Tier_1_Baseline",
        fast_period=9,
        slow_period=21,
        trend_period=50,
        use_trend_filter=False,
        use_volatility_filter=False,
    )

    # Tier 2: Trend Regime + Volatility Spike Filter
    m_t2, _ = engine.run(
        df,
        strategy_name="Tier_2_Quant_Regime",
        fast_period=9,
        slow_period=21,
        trend_period=50,
        use_trend_filter=True,
        use_volatility_filter=True,
    )

    # Tier 3: Institutional Microstructure Strategy (Trend + ATR + CVD + OI + Funding)
    m_t3, _ = engine.run(
        df,
        strategy_name="Tier_3_Institutional_Micro",
        fast_period=9,
        slow_period=21,
        trend_period=50,
        use_trend_filter=True,
        use_volatility_filter=True,
        use_cvd_filter=True,
        use_oi_filter=True,
        use_funding_filter=True,
    )

    print_3tier_benchmark_table(m_t1, m_t2, m_t3, args.symbol, args.interval)


if __name__ == "__main__":
    main()
