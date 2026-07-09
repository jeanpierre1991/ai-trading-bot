# AI Trading Bot

A production-ready, modular AI-powered quantitative trading platform built with Python 3.13.

## Architecture

The project follows a **plugin-based modular architecture**. Every domain module implements the `BaseModule` contract and registers itself through the `ModuleRegistry`. New modules can be added without modifying core application code.

```
├── main.py                  # Application entry point & startup verifier
├── config/                  # Settings & environment configuration
├── core/                    # Framework: registry, events, application lifecycle
├── utilities/               # Logging, helpers
├── market_data/             # Price feeds & OHLCV data
├── technical_analysis/      # Indicators (SMA, EMA, RSI)
├── ai_engine/               # AI-driven market analysis
├── news_engine/             # News aggregation & sentiment
├── risk_manager/            # Position sizing & risk limits
├── strategy_engine/         # Strategy orchestration
├── order_manager/           # Order lifecycle management
├── portfolio_manager/       # Portfolio & position tracking
├── broker_interface/        # Broker connectivity
├── backtesting/             # Historical simulation
└── alerts/                  # Notification system
```

### Design Principles

- **Modular**: Each package is self-contained with its own `MODULE_CLASS` and `register_modules()` hook
- **Extensible**: Add a new package, implement `BaseModule`, and append it to `DEFAULT_MODULE_PACKAGES`
- **Type-safe**: Full type hints across all modules
- **Configurable**: Centralized settings via Pydantic + `.env`
- **Observable**: Structured logging with rotation

## Requirements

- Python 3.13+
- pip

## Quick Start

```bash
# Create and activate virtual environment
python3.13 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Run startup verification
python main.py
```

Expected output:

```
============================================================
AI TRADING BOT — STARTUP VERIFICATION
============================================================
Settings loaded: YES
Modules discovered: 11
Modules loaded: 11

Module Health:
  ✓ market_data               [ready         ] Market data provider operational
  ✓ technical_analysis        [ready         ] Indicators computed successfully
  ...
Overall status: READY
============================================================
```

## Configuration

All settings are loaded from environment variables or a `.env` file. See `.env.example` for available options.

| Variable | Default | Description |
|---|---|---|
| `TRADING_MODE` | `paper` | `paper`, `live`, or `backtest` |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `DEFAULT_SYMBOL` | `AAPL` | Default trading symbol |
| `MAX_POSITION_SIZE_PCT` | `0.05` | Max position as % of portfolio |
| `AI_PROVIDER` | `mock` | AI analysis provider |
| `BROKER_NAME` | `paper` | Broker adapter |

## Adding a New Module

1. Create a package directory (e.g., `sentiment_engine/`)
2. Implement a class extending `BaseModule`
3. Export `MODULE_CLASS` and `register_modules()` in `__init__.py`
4. Add the package name to `DEFAULT_MODULE_PACKAGES` in `core/module_registry.py`

```python
# sentiment_engine/module.py
class SentimentEngineModule(BaseModule):
    @property
    def name(self) -> str:
        return "sentiment_engine"

    def health_check(self) -> ModuleHealth:
        return self._healthy(message="OK")
```

No changes to `main.py` or `core/application.py` are required.

## Current Milestone

**Milestone 1 (Complete):** Full architecture with startup verification.

- All 11 domain modules load and pass health checks
- Mock providers for market data, AI, news, and broker
- Real indicator calculations (SMA, EMA, RSI)
- Risk rules engine with configurable limits
- Order and portfolio management scaffolding

**Milestone 2 (Pending):** Trading logic implementation — awaiting confirmation.

## License

Private — All rights reserved.
