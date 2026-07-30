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

# Run a bounded historical paper backtest (network-free bars source required)
python main.py run-backtest --synthetic-bars 50 --max-cycles 20 --symbol AAPL
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
| `TRADING_MODE` | `paper` | Must be `paper` for M8–M11 cycles (`live` / `backtest` are rejected) |
| `MARKET_DATA_PROVIDER` | `mock` | Prefer `mock` for local/CI; `yahoo` hits the network (supervised paper only; not used by `run-backtest`) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `DEFAULT_SYMBOL` | `AAPL` | Default trading symbol |
| `DEFAULT_TIMEFRAME` | `1h` | Bar timeframe (also used for freshness max-age derivation) |
| `MAX_POSITION_SIZE_PCT` | `0.05` | Max position as % of portfolio |
| `MAX_DAILY_LOSS_PCT` | `0.02` | Daily/session loss limit (fraction). `run-once` uses `--daily-pnl-pct`; `run-session` computes session PnL automatically |
| `MARKET_DATA_FRESHNESS_ENABLED` | `true` | M11.2 fail-closed freshness gate |
| `MARKET_DATA_FRESHNESS_BAR_PERIODS` | `2` | Periods × timeframe + slack when max age unset |
| `MARKET_HOURS_ENABLED` | `true` | M11.3 hours policy gate |
| `MARKET_HOURS_POLICY` | `allow` | `allow` (supervised default) or `reject` (RTH-only) |
| `MARKET_HOURS_CALENDAR` | `xnys` | US equity XNYS calendar (coverage 2024–2027) |
| `AI_PROVIDER` | `mock` | AI analysis provider |
| `BROKER_NAME` | `paper` | Broker adapter (`paper` only in M8–M11; unused by M10 `run-backtest`) |
| `BACKTEST_INITIAL_CAPITAL` | `100000` | Starting cash for historical paper backtests |
| `BACKTEST_COMMISSION_PCT` | `0.001` | Commission fraction of notional for `run-backtest` (override with `--commission-pct`) |

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

## Run a bounded historical backtest (`run-backtest`)

Milestone 10 adds a **Historical Paper Backtest Loop** (Option A): replay local/synthetic bars through the existing `run_once` pipeline under `RuntimeContext.mode=PAPER`.

**Mode contract (do not confuse with live/backtest settings modes):**

| Axis | Value |
|---|---|
| `Settings.trading_mode` / `TRADING_MODE` | must remain `paper` |
| Executor | `DryRunExecutor` (`--commission-pct 0`) or `CommissionDryRunExecutor` (default settings / positive pct) |
| `RuntimeContext.mode` | always `PAPER` (no `TradingMode.BACKTEST`) |
| Bars source | required `--bars-file` **or** `--synthetic-bars` (network-free; no Yahoo on this path) |
| Bounds | series ≤ `MAX_HISTORICAL_BARS` (10_000); `--max-cycles` ≤ `MAX_BACKTEST_CYCLES` (10_000) |

```bash
# Deterministic synthetic series (CI / local, no network)
python main.py run-backtest --synthetic-bars 50 --max-cycles 20 --symbol AAPL

# Local CSV (columns: timestamp,open,high,low,close,volume)
python main.py run-backtest --bars-file ./bars.csv --warmup-bars 30 --max-cycles 100

# Commission override (fraction of notional)
python main.py run-backtest --synthetic-bars 40 --commission-pct 0.001 --max-cycles 10
```

Notes:

- `run-backtest` does **not** expose `--paper` / `--dry-run` and never wires `BrokerOrderExecutor` or a live broker.
- It does **not** use `create_trading_runtime` factory execution backends; wiring is explicit DryRun/Commission only.
- Printed metrics: initial capital, ending equity, return_pct, trades, wins, losses, win_rate, realized PnL, commissions paid, cycles executed.
- Exit codes match other CLI commands: `0` success, `1` config/startup/invalid bounds, `2` unexpected, `130` interrupted.

See `MILESTONE_10_SUMMARY.md` for the formal Milestone 10 closure.

## Milestone 11 — Market-data paper fidelity

Milestone 11 aligns **paper fills** with market-data closed-bar prices and adds fail-closed **freshness** plus **XNYS market-hours** awareness. LIVE trading remains disabled.

| Sub-milestone | What it does |
|---|---|
| **M11.1** | `PaperBroker` fills via `ClosedBarQuoteSource` (last bar close); no static fallback when a quote source is set |
| **M11.2** | Freshness gate after bars, before strategy; missing/invalid/stale/future-anomaly fail closed |
| **M11.3** | XNYS session calendar; when not RTH-open, freshness ages vs last regular close; `MARKET_HOURS_POLICY=allow\|reject` |
| **M11.4** | Supervised validation docs + mocked Yahoo E2E tests (CI stays network-free on `mock`) |

### Freshness (M11.2)

- Enabled by default (`MARKET_DATA_FRESHNESS_ENABLED=true`).
- Default max age: `bar_periods × timeframe_seconds + slack` (override with `MARKET_DATA_MAX_AGE_SECONDS`).
- During XNYS RTH, age uses wall-clock UTC; outside RTH, age uses the last completed regular-session close (M11.3).

### Market hours (M11.3)

- Calendar: XNYS / `America/New_York`, coverage **2024–2027** (outside → fail closed).
- Default policy **`allow`** for supervised paper; set **`reject`** to abort entire cycles outside RTH (`stage_reached=market_hours`) before strategy.
- Historical `run-backtest` disables hours and freshness enforcement via context flags (M10 isolation).

### Supervised Yahoo paper (Checkpoint A)

**Prefer `MARKET_DATA_PROVIDER=mock` for CI and routine local work.**  
`yahoo` uses the network (yfinance): rate limits, gaps, and non-guaranteed quality apply. This is **not** LIVE trading.

```bash
# Still paper-only settings; provider is the only change
# TRADING_MODE=paper
# MARKET_DATA_PROVIDER=yahoo

python main.py run-once --symbol AAPL
python main.py run-once --paper --symbol AAPL --strategy ema_crossover
python main.py run-session --cycles 2 --paper --symbol AAPL
```

Operator checklist and M11 closure details: **`MILESTONE_11_SUMMARY.md`**.

Automated proof in CI uses a **mocked** Yahoo ticker factory (see `tests/runtime/test_m11_supervised_paper_path.py`) — no live Yahoo calls in the test suite.

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

**Milestone 11 (Complete):** Market-data paper fidelity (closed-bar fills, freshness, XNYS hours, supervised Yahoo docs).

- M11.1–M11.3: quote-source paper pricing, freshness gate, session-aware hours
- M11.4: Checkpoint A checklist + mocked Yahoo E2E tests; CI remains on `mock`
- LIVE still rejected by factory / mode_policy

Earlier foundations: modular startup (M1), runtime pipeline and paper booking (M5–M7), operational hardening and `run-once` (M8), bounded `run-session` (M9), historical `run-backtest` (M10). Details: `MILESTONE_7_SUMMARY.md` … `MILESTONE_11_SUMMARY.md`.

## License

Private — All rights reserved.
