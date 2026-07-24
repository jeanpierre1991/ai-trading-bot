# Milestone 7 Summary

## 1. Objetivo del milestone

Milestone 7 cerró el loop de ejecución paper/dry-run desde un `TradeIntent` aprobado hasta la actualización real del `Portfolio`.

Antes de M7, el Runtime llegaba a `ExecutionResult` (DryRun o Paper) pero no convertía un fill bookable en estado de cartera. Después de M7, un `FILLED` válido se mapea a `Fill`, se aplica con `Portfolio.apply_fill`, y el `PipelineResult` refleja el snapshot post-fill con `stage_reached="portfolio"`.

El alcance se limitó al booking contractual del Runtime. `OrderManager`, alerts, CLI y brokers live quedaron fuera a propósito.

## 2. Flujo final implementado

```text
market_data
  -> strategy
  -> risk
  -> TradeIntent
  -> OrderExecutor
  -> ExecutionResult
  -> execution_to_fill()
  -> Portfolio.apply_fill()
  -> snapshot post-fill
  -> PipelineResult
```

Booking solo ocurre cuando `is_bookable(execution)` es verdadero, actualmente para `ExecutionStatus.FILLED`. En cualquier otro caso, el Runtime no convierte la ejecución en `Fill` ni modifica el `Portfolio`.

Detalle del tramo de booking:

1. `OrderExecutor.execute(intent)` produce un `ExecutionResult`.
2. Si `is_bookable(execution)` es verdadero (hoy: `FILLED`), se llama `execution_to_fill(execution)`.
3. Si el mapper devuelve un `Fill`, `_book_execution(fill)` llama `Portfolio.apply_fill(fill)`.
4. Tras un booking exitoso se toma un snapshot post-fill y se devuelve `PipelineResult` con `stage_reached="portfolio"`.
5. `REJECTED` y caminos sin executor no mutan el portfolio.

## 3. Componentes involucrados

| Componente | Rol en M7 |
|---|---|
| **BasicTradingRuntime** | Orquestador de un ciclo `run_once`: market data → strategy → risk → intent → executor opcional → booking opcional → `PipelineResult`. |
| **DryRunExecutor** | `OrderExecutor` local que simula fills sin broker. |
| **BrokerOrderExecutor** | Adaptador `TradeIntent` → `Broker.place_order` → `ExecutionResult`. |
| **PaperBroker** | Broker paper con `place_order` MARKET y fills a cotización. |
| **ExecutionResult** | Contrato de resultado de ejecución (`FILLED` / `REJECTED`). |
| **Fill** | Payload de cartera (`symbol`, `side`, `quantity`, `price`, `fee`). |
| **execution_to_fill()** | Mapper puro `ExecutionResult` → `Fill \| None` (o `ValueError` si `FILLED` es inválido). |
| **Portfolio.apply_fill()** | Único punto de mutación de cash/posiciones tras un fill bookable. |
| **PipelineResult** | Resultado del ciclo: éxito/aborto, stage, signal, risk, intent, execution, snapshot. |

## 4. Contratos de comportamiento

### FILLED válido

- Booking exitoso.
- Portfolio mutado.
- Snapshot post-fill presente y alineado con `portfolio.summary()`.
- `stage_reached="portfolio"`.
- `success=True`.

### REJECTED

- Sin booking.
- `execution` preservado.
- `stage_reached="execution"`.
- `success=False` (razón desde el mensaje del executor/broker).

### HOLD

- Sin ejecución.
- Sin booking.
- Comportamiento previo intacto (`stage_reached="risk"`).

### `executor=None`

- Ciclo intent-only.
- Sin booking.
- `execution is None`.
- `stage_reached="portfolio"` (snapshot sin mutación por fill).

### `execution_to_fill` ValueError

- `success=False`.
- `execution` preservado.
- `stage_reached="execution"`.
- Portfolio intacto.
- `aborted_reason` incluye `execution_to_fill failed: …`.

### `apply_fill` ValueError

- `success=False`.
- `execution` preservado.
- `stage_reached="portfolio"`.
- Portfolio intacto (validación antes de mutar; sin rollback manual).
- `aborted_reason` incluye `apply_fill failed: …`.

### Violación de contrato

- `is_bookable=True` + `execution_to_fill(...) is None`.
- Abort controlado (`success=False`, `stage_reached="execution"`).
- Sin fallthrough silencioso a éxito sin booking.
- Portfolio intacto.

## 5. Decisiones arquitectónicas

- **Runtime es el orquestador.** Coordina etapas; no implementa lógica de broker ni de cartera.
- **Broker no conoce Portfolio.** `PaperBroker` solo produce `ExecutionResult`.
- **`execution_to_fill` es puro.** Sin I/O, sin mutación, sin llamadas a broker.
- **`Portfolio.apply_fill` es el único punto de mutación** tras un fill bookable.
- **No se usa `getattr` para `apply_fill`.** Se llama el método directamente en el path de booking.
- **No se usa `except Exception`.** Solo se capturan `ValueError` esperados del mapper y de `apply_fill`.
- **No hay rollback manual** porque `Portfolio` valida antes de mutar.
- **`OrderManager` queda fuera de M7.** `PipelineResult.order` no se cablea en este milestone.

## 6. Pruebas y cobertura

Cobertura relevante de booking/ejecución:

- Booking BUY exitoso (contrato stage + snapshot).
- Booking SELL.
- DryRun end-to-end.
- Paper (`BrokerOrderExecutor` + `PaperBroker`) end-to-end.
- Paridad DryRun/Paper (mismo contrato Runtime, sin exigir precio de fill idéntico).
- REJECTED.
- HOLD.
- `executor=None`.
- `execution_to_fill` ValueError.
- `apply_fill` ValueError.
- Contract violation `fill=None` tras `is_bookable=True`.

Último estado conocido de suites:

- **95** tests runtime passing.
- **267** tests total passing.

## 7. Commits principales de M7

- `4c0494e` — Book FILLED executions into the portfolio
- `888af02` — Handle apply_fill ValueError during booking
- `3f421b3` — Handle execution_to_fill ValueError in run_once
- `f2a5cb3` — Align Runtime docs and booking test names
- `a2fe080` — Add DryRun/Paper booking runtime parity test
- `07a87c5` — Reject bookable executions that map to no fill

(Además de commits previos de M7.1 mapper y hardening de docs/tests del camino feliz.)

## 8. Fuera de alcance de M7

Quedan para Milestone 8 o posteriores:

- OrderManager
- `PipelineResult.order`
- Alerts
- CLI / composition root
- Live brokers
- Partial fills
- Cancelled orders
- Reconciliation
- Continuous execution

## 9. Definition of Done

Milestone 7 se considera completado cuando:

- [x] Runtime booking está cableado.
- [x] DryRun y Paper comparten contrato.
- [x] Errores de mapper y portfolio están controlados.
- [x] No hay fallthrough silencioso.
- [x] Tests runtime y suite completa están verdes.
- [x] Branch está sincronizada con origin.

## 10. Estado final

**Milestone 7 is complete and ready to serve as the execution and booking foundation for Milestone 8.**
