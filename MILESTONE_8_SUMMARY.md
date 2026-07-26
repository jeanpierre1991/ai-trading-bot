# Milestone 8 Summary

## 1. Objetivo del milestone

Milestone 8 endureció operativamente el loop paper/dry-run cerrado en Milestone 7: guardas de modo, riesgo operacional efectivo, observabilidad, OrderManager y alerts opcionales, composition root, y CLI `run-once`.

Tema: *Operational Hardening of the Paper Decision Loop*.

No se abrieron brokers live ni se cambió el modelo de booking de M7.

## 2. Bloques completados

| Bloque | Objetivo | Commit (referencia) |
|---|---|---|
| **M8.1** | Guardas paper/dry-run (`mode_policy`) | `7cdca6c` |
| **M8.2** | Límites `max_open_positions` / `max_daily_loss_pct` en path Runtime | `9705396` |
| **M8.3** | Logging estructurado de ciclo | `27f0d08` |
| **M8.4** | `OrderManager` opcional → `PipelineResult.order` | `5de5011` |
| **M8.5** | `AlertNotifier` opcional → `alerts_sent` | `24bd85e` |
| **M8.6** | Factory / composition root | `e34470c` |
| **M8.7** | CLI `run-once` + docs de cierre | (este cierre) |

## 3. Flujo operacional final

```text
main.py [startup | run-once]
  -> load_settings / setup_logging
  -> TradingBotApplication.startup()          # run-once
  -> create_trading_runtime_from_app(...)     # M8.6
  -> RuntimeContext(mode=PAPER, ...)
  -> BasicTradingRuntime.run_once(...)
       -> mode_policy (M8.1)
       -> market_data → strategy → risk (M8.2)
       -> TradeIntent → executor (dry-run | paper)
       -> booking M7 (execution_to_fill → apply_fill)
       -> OrderManager opcional (M8.4)
       -> alerts opcionales (M8.5)
       -> PipelineResult (+ logs M8.3)
```

## 4. Contratos de modo (M8.7 / M8.1 / M8.6)

Tres ejes distintos:

| Eje | Valor permitido en M8 |
|---|---|
| `Settings.trading_mode` | `"paper"` (también para dry-run) |
| Factory `execution` | `"dry_run"` \| `"paper"` |
| `RuntimeContext.mode` | `TradingMode.PAPER` |

- `--dry-run` es el default seguro del CLI.
- `--paper` debe pedirse explícitamente.
- `live` / `backtest` fallan en factory (y `mode_policy` en Runtime) antes de un ciclo útil.

## 5. CLI `run-once` (M8.7)

```bash
# Startup verifier (comportamiento previo)
python main.py

# Un ciclo dry-run (default)
python main.py run-once --symbol AAPL

# Un ciclo paper (explícito)
python main.py run-once --paper --symbol AAPL
```

### Exit codes

| Código | Significado |
|---|---|
| `0` | `run_once` completó sin excepción (incluye `success=False` controlado) |
| `1` | Config / modo no soportado / startup fallido |
| `2` | Excepción inesperada |
| `130` | Interrupción (Ctrl+C) |

## 6. Fuera de alcance de M8

- Brokers live / APIs reales de trading
- Ejecución continua / daemon
- Partial / cancelled fills
- Reconciliation broker ↔ portfolio
- Email / webhook alerts (solo `ConsoleNotifier` / abstracción)
- Cancel/amend avanzado
- Backtest loop CLI

## 7. Definition of Done (M8)

- [x] Runtime paper/dry-run invocable desde CLI vía factory
- [x] `trading_mode=live` y brokers no-paper no ejecutan órdenes reales
- [x] `max_open_positions` y `max_daily_loss_pct` efectivos en el path de decisión
- [x] Logs de ciclo en Runtime
- [x] OrderManager opcional coherente con execution
- [x] Alerts opcionales + `alerts_enabled` respetado
- [x] Contratos M7 de booking sin regresiones intencionales
- [x] Docs: README operacional + este summary de cierre

## 8. Estado final

**Milestone 8 is complete:** el loop paper/dry-run de M7 es operable, seguro y observable desde la aplicación, con composition root y CLI `run-once`.
