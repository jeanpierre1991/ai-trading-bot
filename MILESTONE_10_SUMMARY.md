# Milestone 10 Summary

## 1. Objetivo del milestone

Milestone 10 añadió un **Historical Paper Backtest Loop** acotado: replay de barras históricas/sintéticas locales a través del pipeline existente `run_once`, sin habilitar live trading ni `trading_mode=backtest`.

Tema: *Historical Paper Backtest Loop*.

**Arquitectura: Option A (autoritativa)**

- `Settings.trading_mode` permanece `"paper"`
- `RuntimeContext.mode` permanece `TradingMode.PAPER`
- **No** se introduce uso operacional de `TradingMode.BACKTEST`
- **No** se habilita `trading_mode=backtest`
- `runtime/mode_policy.py` y `runtime/factory.py` **sin cambios**
- Backtest = camino separado: `HistoricalMarketDataProvider` + `BacktestRunner` + `DryRunExecutor` / `CommissionDryRunExecutor`

## 2. Bloques completados

| Bloque | Objetivo | Notas |
|---|---|---|
| **M10.1** | Historical bar source | `HistoricalMarketDataProvider`, `HistoricalRuntimeMarketData`, hard cap `MAX_HISTORICAL_BARS=10000` |
| **M10.2** | `BacktestRunner` + métricas reales | Reusa `run_once`; `BacktestResult` + `CommissionDryRunExecutor` |
| **M10.3** | Safe integration / wiring | Tests-only bajo Option A; sin cambios de producción en policy/factory |
| **M10.4** | CLI + docs + validación final | `run-backtest`, README, este summary |

## 3. Flujo operacional final

```text
main.py run-backtest (--bars-file PATH | --synthetic-bars N) [bounds...]
  -> load_settings / setup_logging
  -> require trading_mode='paper'
  -> load local/synthetic bars (no Yahoo)
  -> HistoricalMarketDataProvider + HistoricalRuntimeMarketData
  -> TradingBotApplication.startup()   # strategy module only
  -> BasicTradingRuntime(
         market_data=historical adapter,
         executor=DryRunExecutor | CommissionDryRunExecutor,  # never BrokerOrderExecutor
         portfolio=Portfolio(backtest_initial_capital),
         ...
     )
  -> BacktestRunner(runtime, historical=...).run(BacktestConfig(...))
       for each bounded cycle:
         RuntimeContext(mode=PAPER, daily_pnl_pct=session-like equity pct, ...)
         runtime.run_once(context)     # M7–M8 pipeline unchanged
         advance historical cursor (hard-capped)
  -> print BacktestResult metrics
  -> app.shutdown()
```

`run-once` / `run-session` siguen usando `create_trading_runtime_from_app` (dry_run|paper). **`run-backtest` no usa ese camino de execution del factory.**

## 4. Contratos clave

### Option A — modo

| Eje | Valor |
|---|---|
| `Settings.trading_mode` | `"paper"` (requerido por CLI backtest) |
| Executor backtest | `DryRunExecutor` o `CommissionDryRunExecutor` |
| `RuntimeContext.mode` | `TradingMode.PAPER` |
| Factory / mode_policy | intactos; siguen rechazando `live` y `backtest` |

### Métricas `BacktestResult` (M10.2)

- `initial_capital`
- `final_capital` / `ending_equity`
- `total_return_pct` / `return_pct`
- `total_trades`, `wins`, `losses`, `win_rate`
- `realized_pnl`
- `commissions_paid`
- `cycles_executed`

### Comisiones

- `fee = notional * commission_pct` en fills simulados vía `CommissionDryRunExecutor`
- Impacto en equity **una sola vez** por el path existente `execution_to_fill` → `apply_fill`
- No se altera `PaperBroker` ni la semántica global de booking

### Bounds

- Serie histórica ≤ `MAX_HISTORICAL_BARS` (10_000)
- Ciclos ≤ `MAX_BACKTEST_CYCLES` (10_000)
- CLI exige `--bars-file` o `--synthetic-bars` (sin fuente de red)

## 5. CLI `run-backtest` (M10.4)

```bash
# Startup verifier
python main.py

# Synthetic (deterministic, network-free)
python main.py run-backtest --synthetic-bars 50 --max-cycles 20 --symbol AAPL

# Local CSV
python main.py run-backtest --bars-file ./bars.csv --warmup-bars 30 --max-cycles 100

# Commission override
python main.py run-backtest --synthetic-bars 40 --commission-pct 0.001 --max-cycles 10
```

### Exit codes

| Código | Significado |
|---|---|
| `0` | Backtest completó sin excepción |
| `1` | Config / modo / startup / bounds / bars inválidos |
| `2` | Excepción inesperada |
| `130` | Interrupción (Ctrl+C) |

`run-once` y `run-session` permanecen disponibles e intactos.

## 6. Fuera de alcance de M10 (non-goals)

- Brokers live / APIs reales de trading
- `trading_mode=backtest` / `TradingMode.BACKTEST` operacional
- Debilitar `mode_policy` / `factory`
- Daemon / loop infinito
- Partial / cancelled fills / reconciliation
- Streaming market data
- Yahoo/network como dependencia de tests M10
- Cambiar semántica de booking M7, risk/mode M8, o SessionRunner M9

## 7. Safety guarantees

- Backtest contexts siempre `PAPER`
- Sin path a live broker / `BrokerOrderExecutor` en `BacktestRunner` ni CLI `run-backtest`
- Guards M8 (`live` / `backtest` / non-paper broker) siguen efectivos
- `run-once` / `run-session` behavior preserved
- Ejecución histórica explícitamente acotada

## 8. Definition of Done (M10)

- [x] Historical provider acotado y network-free (M10.1)
- [x] `BacktestRunner` reusa `run_once` con métricas reales + commissions (M10.2)
- [x] Integración segura validada; policy/factory sin cambios (M10.3)
- [x] CLI `run-backtest` + README + este summary (M10.4)
- [x] Tests deterministas sin red; suite completa verde

## 9. Test results (M10.4 closure)

| Suite | Result |
|---|---|
| M10.4 focused (`test_main_run_backtest` + `test_bars_io`) | **21 passed** |
| M10 historical/backtesting | **35 passed** |
| Runtime | **177 passed** |
| CLI (`test_main_run_once` + `run_session` + `run_backtest`) | **44 passed** |
| Full suite | **428 passed** |

## 10. Estado final

**Milestone 10 is complete:** el bot puede ejecutar un backtest paper histórico acotado sobre el pipeline `run_once` existente, con métricas reales, comisiones locales, CLI `run-backtest`, y sin debilitar las garantías M8/M9 de modo.
