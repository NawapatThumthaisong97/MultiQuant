# MultiQuant: Institutional-Grade Quantitative Trading Framework

<p align="center">
  <a href="https://github.com/Xplosion97/MultiQuant/actions/workflows/ci.yml">
    <img src="https://github.com/Xplosion97/MultiQuant/actions/workflows/ci.yml/badge.svg" alt="CI Pipeline Status" />
  </a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB.svg?logo=python&logoColor=white" alt="Python 3.11 | 3.12" />
  <img src="https://img.shields.io/badge/code%20style-ruff-000000.svg?logo=ruff&logoColor=white" alt="Code Style: Ruff" />
  <img src="https://img.shields.io/badge/type%20checked-mypy-blue.svg?logo=python&logoColor=white" alt="Type Checked: MyPy" />
  <img src="https://img.shields.io/badge/tests-33%2F33%20passed-success.svg?logo=pytest&logoColor=white" alt="Tests 100% Passed" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT" />
</p>

<p align="center">
  <strong>An event-driven, decoupled 6-layer quantitative trading framework engineered for high-throughput crypto market data ingestion, SIMD-vectorized signal generation, mathematical risk management, and real-time observability.</strong>
</p>

---

## 🌟 Key Highlights

- **🏛️ Decoupled 6-Layer Architecture**: Strict unidirectional dependencies from Market Connectivity to Execution and State Persistence.
- **⚡ High-Performance Vectorization**: Polars-powered technical indicators (EMA, ATR, Bollinger, SMC Swings) utilizing multi-threaded SIMD acceleration.
- **🛡️ Institutional Risk Controls**: 5 Pre-Trade Risk Gates, Dynamic Volatility/ATR Position Sizing, and Graduated Circuit Breakers (Drawdown Guard).
- **🛒 Order Management System (OMS)**: Realistic slippage modeling (3 bps), tiered exchange fee simulation, and real-time FIFO/weighted-average PnL accounting.
- **🗄️ Dual-Tier Persistence**: SQLite in WAL (Write-Ahead Logging) mode via `aiosqlite` with Redis hot-caching and graceful in-memory fallback.
- **🚨 Full-Stack Observability**: Native Prometheus HTTP metrics exporter (`:8000/metrics`), a 14-panel Grafana cockpit, and instant color-coded Discord Webhook alerts for trade entries and exits.

---

## 🏛️ System Architecture

```mermaid
graph TD
    subgraph L1 ["📡 Layer 1: Market Connectivity"]
        WS[Resilient WebSocket Feed]
        Parser[Tick / Bar Normalizer]
    end

    subgraph L2 ["🧠 Layer 2: Alpha Engine"]
        Buffer[Rolling Bar Buffer]
        PolarsPipe[Polars High-Speed Pipeline]
        Signals[Alpha Strategies & Signals]
    end

    subgraph L3 ["🛡️ Layer 3: Risk & Position Sizing"]
        RiskGates[5 Pre-Trade Risk Gates]
        ATRSizer[Volatility-Based ATR Sizer]
        CircuitBreaker[Drawdown Circuit Breakers]
    end

    subgraph L4 ["🛒 Layer 4: Order Management System (OMS)"]
        OMSEngine[Simulated OMS & State Machine]
        ExecSim[Slippage & Fee Simulator]
        Tracker[Position & PnL Accounting]
    end

    subgraph L5 ["🗄️ Layer 5: State & Persistence"]
        SQLiteDB[(SQLite WAL Mode)]
        RedisCache[(Redis Cache + RAM Fallback)]
    end

    subgraph L6 ["🚨 Layer 6: Observability & Monitoring"]
        Discord[Discord Webhook Alerts]
        Prometheus[Prometheus Exporter :8000]
        Grafana[Grafana 14-Panel Cockpit]
    end

    WS --> Parser
    Parser -->|Normalized Data| Buffer
    Buffer --> PolarsPipe
    PolarsPipe --> Signals
    Signals -->|AlphaSignal| RiskGates
    RiskGates --> ATRSizer
    CircuitBreaker -.->|Enforces Status| RiskGates
    ATRSizer -->|Approved OrderRequest| OMSEngine
    OMSEngine --> ExecSim
    ExecSim --> Tracker
    Tracker -->|Fills & Snapshots| SQLiteDB
    Tracker -->|Hot State| RedisCache
    ExecSim -->|Rich Open/Close Embeds| Discord
    OMSEngine -->|Real-time Metrics| Prometheus
    Prometheus --> Grafana
```

> 📖 **Deep Dive Documentation**: For an in-depth breakdown of every layer's design, mathematical models, and state diagrams, read [docs/MultiQuant.md](docs/MultiQuant.md).

---

## 🖥️ Live Paper Trading Terminal & Monitoring

When executing live paper trading against Binance real-time feeds:

### 1. Terminal Dashboard
```text
================================================================================
📊 MULTIQUANT PAPER TRADING MONITOR — 01:25:30 UTC
================================================================================
💰 Total Equity : $100,631.60 | Cash: $75,631.60
📈 Realized PnL : +$650.00    | Unrealized PnL: +$18.40
🎯 Win Rate     : 80.0% (4 Wins / 1 Losses) | Trades: 5
🛡️ Circuit Breaker: NORMAL (Max Drawdown: 0.24%)
--------------------------------------------------------------------------------
🛒 ACTIVE POSITIONS:
   • BTCUSDT : LONG  0.3080 units | Entry: $81,250.00 | Mark: $81,310.00 | PnL: +$18.48
--------------------------------------------------------------------------------
📡 LIVE MARKET PRICES & 1-MIN ATR VOLATILITY:
   • BTCUSDT : $81,310.00 | 1m ATR: $512.40 | Trend: BULLISH
   • ETHUSDT : $2,185.50  | 1m ATR: $18.20  | Trend: BEARISH
--------------------------------------------------------------------------------
🔔 LAST EXECUTION RECEIPT:
   ⚡ BUY 0.3080 BTCUSDT @ $81,250.00 (Fee: $10.01 | Slippage: $7.51)
================================================================================
```

### 2. Rich Discord Webhook Embeds
- **🟢 Position Open Alerts**: Broadcasts symbol, direction (LONG/SHORT), entry price, position size, Stop Loss (SL), Take Profit (TP), and Risk:Reward ratio.
- **🏆 / 🛑 Position Close Alerts**: Broadcasts realized PnL ($ and %), exit reason, total fees & slippage, current portfolio equity, and win rate track record.

### 3. Grafana 14-Panel Monitoring Cockpit
- Real-time Equity Curve vs. Cash Balance.
- Max Drawdown Gauge with automated warning thresholds (8% warning / 15% halt).
- Win Rate Gauge and Trades Won vs. Lost distribution.
- Real-time BTC & ETH mark price tickers and 1-minute ATR volatility gauges.

---

## 🔬 Institutional Backtesting & Quantitative Benchmark

MultiQuant features a high-performance Polars & NumPy event-driven backtesting engine that simulates realistic exchange execution, including **taker fees (5 bps/leg)**, **adverse slippage (3 bps/leg)**, and dynamic volatility capital sizing.

### 1. Full-Year 2026 5-Minute BTCUSDT Benchmark (89,050 Candles | $50 Initial Capital)

Tested across the entire year (`2025-12-31` to `2026-11-05`, where **BTC Buy & Hold returned `-13.31%`** with a **`-40.97%` Max Drawdown** from `$97,924` to `$57,800`), comparing **6 Orthogonal Strategy Presets**:

| Preset Combo | Core Orthogonal Indicators | Trades | Win Rate | Profit Factor | Net Return | Sharpe | Max DD | Fees+Slip | Final Equity |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1: Retail EMA 9/21** | Raw `EMA(9/21)` Crossover | 1,002 | 34.4% | 0.66 | -61.89% | -8.74 | 62.00% | $23.41 | $19.05 |
| **P2: Minimalist 3-Pillar** | `EMA(576)` + `Donchian(96)` + `RVOL/Taker` | 132 | 23.5% | 0.90 | -9.50% | -0.70 | 22.46% | $4.99 | $45.25 |
| **P3: Volatility Squeeze** | `BB Squeeze` + `Donchian(96)` + `EMA(576)` + `Orderflow` | 114 | 35.1% | 1.48 | +30.15% | 2.22 | 7.59% | $4.90 | $65.07 |
| **P4: Squeeze + ADX Regime** | P3 + `ADX(14) >= 20` Trend Filter | 110 | 33.6% | 1.38 | +23.47% | 1.80 | 9.13% | $4.73 | $61.74 |
| **P5: Institutional Elite** | P4 + `1h Stationary CVD` + `DI+/DI-` | 108 | 34.3% | 1.42 | +25.45% | 1.93 | 9.13% | $4.70 | $62.73 |
| **P6: MTF Squeeze Champion (★ BEST)** | **P3 + `1h/12h EMA Alignment` (`EMA 12 > EMA 144`)** | **113** | **35.4%** | **1.50** | **+31.11%** | **2.28** | **7.59%** | **$4.89** | **$65.56** |

> 💡 **Key Quantitative Finding ("Less is More" / Fractal Orthogonality)**:
> On 5-minute BTC bars, stacking collinear oscillators causes severe lag, while raw crossovers suffer fatal fee bleed (`$23.41` in fees across 1,002 trades). Combining **4 strictly orthogonal dimensions** in **Preset 6 (`P6_MTF_SQUEEZE_CHAMPION` / `MTFVolatilitySqueezeAlpha`)** — *Fractal Multi-Timeframe Trend (`1h EMA 12 > 12h EMA 144` & `Close > 48h EMA 576`) + Volatility Compression (`BB Squeeze`) + 8h Structural Breakout (`Donchian 96`) + Aggressive Taker Orderflow (`RVOL >= 2.0`, `Taker Buy >= 58%`)* with asymmetric $1:3.0$ Risk:Reward — generates **+31.11% Net Return (+44.42% Alpha over BTC)** with a **2.28 Sharpe Ratio** and **7.59% Max Drawdown**.

```bash
# Run the Full-Year 5-Minute 5-Preset Benchmark (89,050+ candles)
python scripts/run_backtest.py --mode 5m-year

# Run the 1-Hour 3-Tier Microstructure Benchmark (1,000 candles)
python scripts/run_backtest.py --mode 3tier
```

---

## 🚀 Quickstart

### 1. Clone & Install
```bash
git clone https://github.com/Xplosion97/MultiQuant.git
cd MultiQuant

# Create and activate Python virtual environment
python -m venv venv
.\venv\Scripts\activate   # Windows PowerShell
# source venv/bin/activate  # macOS / Linux

# Install dependencies and editable package
pip install -r requirements.txt
pip install -e .
```

### 2. Configure Environment
```bash
# Copy example configuration
cp .env.example .env
```
*(Optionally add your Discord Webhook URL inside `.env` to receive real-time execution alerts)*

### 3. Start Infrastructure Stack (Docker)
```bash
docker compose up -d
```
This spins up:
- **Redis 7** on port `6379`
- **Prometheus** on port `9090` (configured to scrape MultiQuant at `:8000/metrics`)
- **Grafana** on port `3000` (pre-configured with the MultiQuant Dashboard)

### 4. Run Live Paper Trading
```bash
python scripts/run_paper_trading.py
```
View the live dashboard at: **[http://localhost:3000](http://localhost:3000)** *(User: `admin` / Password: `admin`)*

---

## 🧪 Testing & Code Quality

MultiQuant enforces strict code health checked by automated CI pipelines on every pull request and push:

```bash
# 1. Run full unit and integration test suite with coverage
pytest -v --cov=src

# 2. Run Ruff linter and formatter
ruff check .
ruff format --check .

# 3. Run MyPy static type checker
mypy src --ignore-missing-imports
```

---

## 📁 Repository Structure

```text
MultiQuant/
├── .github/workflows/ci.yml       # GitHub Actions CI matrix testing (Python 3.11/3.12)
├── config/                        # Pydantic Settings & Prometheus configurations
│   ├── prometheus.yml
│   └── settings.py
├── docs/                          # Comprehensive architectural documentation
│   └── MultiQuant.md
├── scripts/                       # Executable runners and live demo scripts
│   ├── demo_live_risk.py
│   ├── run_backtest.py            # 3-Tier quant backtest benchmark runner
│   └── run_paper_trading.py       # Live visual paper trading runner
├── src/
│   ├── core/                      # Immutable domain models and async EventBus
│   ├── layer1_connectivity/       # Resilient WebSocket and market data normalizer
│   ├── layer2_alpha/              # Polars-based vectorized indicators, backtest & alpha strategies
│   ├── layer3_risk/               # Pre-trade risk gates, ATR sizer, & drawdown circuit breaker
│   ├── layer4_oms/                # Order state machine, slippage simulator, & position tracker
│   ├── layer5_state/              # SQLite WAL mode & Redis cache with memory fallback
│   └── layer6_monitoring/         # Discord notifier, Prometheus exporter, & Grafana dashboard
├── tests/                         # Full test suite (33/33 tests pass, 100% coverage)
├── docker-compose.yml             # Orchestration for Redis, Prometheus, and Grafana
├── pyproject.toml                 # Ruff, MyPy, and Pytest configuration
├── requirements.txt               # Production and development dependencies
└── README.md                      # Project landing page
```

---

## 📜 License

Distributed under the **MIT License**. See `LICENSE` for more information.
