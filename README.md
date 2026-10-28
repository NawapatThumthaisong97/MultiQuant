# MultiQuant: Institutional-Grade Quantitative Trading System

[![MultiQuant CI Pipeline](https://github.com/Xplosion97/MultiQuant/actions/workflows/ci.yml/badge.svg)](https://github.com/Xplosion97/MultiQuant/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checked: MyPy](https://img.shields.io/badge/type%20checked-mypy-blue.svg)](https://mypy-lang.org/)

MultiQuant เป็นระบบ Quantitative Trading แบบแยกส่วน (Decoupled Modular Architecture) 6 ชั้น รองรับการประมวลผลความเร็วสูง (High-throughput), ระบบบริหารความเสี่ยงระดับสถาบัน (Risk Management), และการทดสอบอัตโนมัติ 100% ผ่าน GitHub Actions CI

---

## 🏛️ สถาปัตยกรรมระบบ 6 ชั้น (The 6-Layer Architecture)

```mermaid
graph TD
    subgraph L1 ["📡 Layer 1: Market Connectivity"]
        WS[Resilient WebSocket Feed]
        Parser[Market Data Normalizer]
    end

    subgraph L2 ["🧠 Layer 2: Alpha Engine"]
        PolarsPipe[Polars High-Speed Pipeline]
        Signals[Alpha Strategy & Signal Generator]
    end

    subgraph L3 ["🛡️ Layer 3: Risk & Position Sizing"]
        RiskManager[Pre-Trade Risk Checks]
        ATRSizer[Volatility-Based ATR Sizing]
        DDGuard[Drawdown Circuit Breakers]
    end

    subgraph L4 ["🛒 Layer 4: Order Management System (OMS)"]
        OMSEngine[Custom Simulated OMS]
        SlippageModel[Fill & Slippage Simulator]
    end

    subgraph L5 ["🗄️ Layer 5: State & Persistence"]
        SQLiteDB[(SQLite WAL Persistence)]
        RedisCache[(Redis In-Memory Cache)]
    end

    subgraph L6 ["🚨 Layer 6: Monitoring & Alerting"]
        Discord[Discord Webhook Alerts]
        Grafana[Prometheus & Grafana Metrics]
    end

    WS --> Parser
    Parser -->|Tick / Bar Events| PolarsPipe
    PolarsPipe --> Signals
    Signals -->|AlphaSignal| RiskManager
    RiskManager --> ATRSizer
    ATRSizer -->|Approved OrderRequest| OMSEngine
    OMSEngine --> SlippageModel
    SlippageModel -->|FillEvent| SQLiteDB
    SlippageModel -->|FillEvent| RedisCache
    SlippageModel -->|Alerts| Discord
    OMSEngine -->|Metrics| Grafana
```

---

## 📁 โครงสร้างโปรเจกต์ (Repository Directory)

```text
MultiQuant/
├── .github/workflows/ci.yml       # GitHub Actions CI (Ruff, MyPy, Matrix Pytest, Redis)
├── config/                        # การตั้งค่าระบบ (Pydantic Settings & YAML)
│   ├── settings.py
├── src/
│   ├── core/                      # Data models & EventBus (TickData, BarData, AlphaSignal, etc.)
│   ├── layer1_connectivity/       # 📡 Layer 1: Resilient WebSocket, Feed Normalizer
│   ├── layer2_alpha/              # 🧠 Layer 2: Polars-based Alpha Engine (Next)
│   ├── layer3_risk/               # 🛡️ Layer 3: Volatility & ATR Position Sizer (Next)
│   ├── layer4_oms/                # 🛒 Layer 4: Simulated Order Management System (Next)
│   ├── layer5_state/              # 🗄️ Layer 5: SQLite WAL & Redis Cache (Next)
│   └── layer6_monitoring/         # 🚨 Layer 6: Discord & Prometheus/Grafana (Next)
├── research/                      # สคริปต์ Research เดิม (ML, SMC, Compare assets)
├── tests/                         # Unit & Integration Tests ทุก Layer
├── pyproject.toml                 # Ruff, MyPy, Pytest configurations
└── requirements.txt               # Dependencies
```

---

## 🚀 การติดตั้งและทดสอบ (Getting Started)

### 1. ติดตั้ง Dependencies
```bash
python -m venv venv
# บน Windows
.\venv\Scripts\activate
# ติดตั้ง Library
pip install -r requirements.txt
```

### 2. รัน Unit Tests และตรวจคุณภาพโค้ด
```bash
# รัน Unit Tests พร้อมเช็ค Coverage
pytest -v --cov=src

# รัน Linter
ruff check .

# รัน Type Checker
mypy src --ignore-missing-imports
```
