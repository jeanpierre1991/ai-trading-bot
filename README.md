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

# Run one decision cycle (dry-run by default; requires TRADING_MODE=paper)
python main.py run-once --symbol AAPL

# Run a bounded multi-cycle paper/dry-run session
python main.py run-session --cycles 3 --symbol AAPL
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
| `TRADING_MODE` | `paper` | Must be `paper` for M8/M9 cycles (`live` / `backtest` are rejected) |
| `MARKET_DATA_PROVIDER` | `mock` | Prefer `mock` for local/CI; `yahoo` hits the network |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `DEFAULT_SYMBOL` | `AAPL` | Default trading symbol |
| `MAX_POSITION_SIZE_PCT` | `0.05` | Max position as % of portfolio |
| `MAX_DAILY_LOSS_PCT` | `0.02` | Daily/session loss limit (fraction). `run-once` uses `--daily-pnl-pct`; `run-session` computes session PnL automatically |
| `AI_PROVIDER` | `mock` | AI analysis provider |
| `BROKER_NAME` | `paper` | Broker adapter (`paper` only in M8/M9) |

## Run one cycle (`run-once`)

Milestone 8 exposes a single decision cycle over the existing runtime factory.

**Mode contract (do not confuse these):**

| Axis | Value |
|---|---|
| `Settings.trading_mode` / `TRADING_MODE` | `paper` (required for both dry-run and paper execution) |
| CLI / factory `execution` | `--dry-run` (default) or `--paper` (explicit) |
| `RuntimeContext.mode` | always `PAPER` for `run-once` |

```bash
# Safe default: DryRunExecutor (no PaperBroker orders)
python main.py run-once --symbol AAPL

# Explicit paper path: BrokerOrderExecutor + PaperBroker (local only)
python main.py run-once --paper --symbol AAPL --strategy ema_crossover

# Operational risk input (default 0)
python main.py run-once --daily-pnl-pct 0 --bar-limit 100
```

Exit codes: `0` cycle finished (including controlled `success=False`), `1` config/mode/startup failure, `2` unexpected error, `130` interrupted.

See `MILESTONE_8_SUMMARY.md` for the formal Milestone 8 closure.

## Run a bounded session (`run-session`)

Milestone 9 adds a **bounded Paper Session Loop**: N cycles on one shared Runtime / portfolio / OrderManager via `SessionRunner`.

**Same mode axes as `run-once`**, plus:

| Axis | Value |
|---|---|
| `--cycles` | Required; integer in `1..100` (`MAX_SESSION_CYCLES`) |
| Session PnL | Computed as `(current_equity - session_start_equity) / session_start_equity` and passed as `RuntimeContext.daily_pnl_pct` each cycle (no `--daily-pnl-pct` on this command) |
| Fail-closed | First cycle with `PipelineResult.success=False` stops the session (`stopped_early=True`) |

```bash
# Safe default: dry-run session
python main.py run-session --cycles 3 --symbol AAPL

# Explicit paper session
python main.py run-session --cycles 5 --paper --symbol AAPL --strategy ema_crossover --bar-limit 100
```

Notes:

- Prefer `MARKET_DATA_PROVIDER=mock` for local/CI (no Yahoo/network in the M9 test suite).
- `run-session` does **not** expose `--daily-pnl-pct`, live, backtest, or infinite/daemon loops.
- Exit codes match `run-once`: `0` includes controlled `stopped_early`; `1` config/startup/factory/invalid cycles; `2` unexpected; `130` interrupted.

See `MILESTONE_9_SUMMARY.md` for the formal Milestone 9 closure.

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

**Milestone 9 (Complete):** Bounded Paper Session Loop.

- `SessionRunner` multi-cycle orchestration over shared Runtime/portfolio/OrderManager
- Session equity PnL mapped to `RuntimeContext.daily_pnl_pct` (M8.2 fail-closed preserved)
- Fail-closed stop on first controlled cycle abort
- Integration coverage for dry-run/paper paths without network
- CLI `run-session` (dry-run default, `--paper` explicit, `--cycles` required 1..100)

Earlier foundations: modular startup (M1), runtime pipeline and paper booking (M5–M7), operational hardening and `run-once` (M8). Details: `MILESTONE_7_SUMMARY.md`, `MILESTONE_8_SUMMARY.md`, `MILESTONE_9_SUMMARY.md`.

## License

Private — All rights reserved.
