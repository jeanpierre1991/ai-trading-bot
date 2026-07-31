# M13.3 Design Review — Idempotency + abort-only reconciliation (BROKER_SANDBOX)

**Status:** DESIGN / ANALYSIS ONLY — awaiting external approval  
**Mode:** Documentation only. Do **not** implement until this design is approved.  
**Baseline HEAD:** `c2f2e81468f6667a8ab5d569499817be195f0d31` — `M13.2: add broker-agnostic live sandbox safety gates`  
**Branch at design time:** `cursor/m9-paper-session-loop` (ahead of origin; M13.1–M13.2 committed locally)  
**Parents:** `MILESTONE_13_DESIGN_REVIEW.md` § M13.3 / Decision **L4** (abort-only), `MILESTONE_13_2_DESIGN_REVIEW.md`, `MILESTONE_11_14_DESIGN_SPEC.md`  
**Non-actions:** No source/test changes. No stage/commit/push/PR. Do not start implementation from this file alone.

---

## 0. Post-M13.2 repository inspection (actual)

| Area | Current behavior at `c2f2e81` |
|---|---|
| Live gates | Conjunctive G1–G10; registry-only sandbox construction; `broker=` injection rejected; LIVE_PRODUCTION hard-denied |
| Caps | `LiveCapGuardBroker` + in-process `LiveOrderCounter`; counts **submission attempts** after pre-submit checks |
| Order DTO | `BrokerOrderRequest.client_order_id: str \| None` (M13.1 additive); Alpaca forwards when set |
| Executor | `BrokerOrderExecutor` **does not** generate or attach `client_order_id` today |
| OM states | `PENDING` → `SUBMITTED` → `FILLED` \| `REJECTED` \| `CANCELLED` — **no** `UNKNOWN` / open / partial |
| ExecutionStatus | `FILLED` \| `REJECTED` only; Alpaca maps accepted/open → reject (no invented fills) |
| Broker ABC | `connect` / `disconnect` / `get_status` / `get_quote` / `place_order` — **no** list-open-orders / positions / get-by-client-id |
| Alpaca adapter | Extra `get_order(order_id)` / account helpers exist on the concrete class, not on the port |
| Persistence | M12 `OperatorState` JSON for **paper** operator only; live path has **no** durable order ledger |
| Paper operator | Paper-only; no `--live` |
| Backtest | Isolated; no live executor |
| Reconcile / shadow | **Absent** (correct for M13.2 closure) |

**Gap M13.3 must close:** supervised sandbox can place orders without a durable idempotency ledger or broker↔local abort-only reconcile — unsafe under timeout/crash/retry.

---

## 1. Design objective (M13.3 only)

Add **broker-agnostic order idempotency** and **abort-only reconciliation** for supervised `execution="live"` + `BROKER_SANDBOX` only, such that:

1. The same logical order cannot create uncontrolled duplicate venue exposure.  
2. Ambiguous submission outcomes become **UNKNOWN** and block further potentially duplicative submits until reconcile clears them.  
3. Material local↔broker mismatches **abort** (fail closed) without automatic repair.  
4. M13.2 gates, registry-only construction, hard caps, LIVE_PRODUCTION denial, paper/operator/backtest invariants remain intact.

**Non-implication:** M13.3 does **not** authorize LIVE_PRODUCTION, real-money trial (M14), shadow (M13.4), cancel-all, or unattended live.

---

## 2. Layered model (must keep separate)

| Layer | Meaning in M13.3 | May mutate local portfolio/OM? |
|---|---|---|
| **OBSERVATION** | Fetch broker facts (orders/positions) via agnostic port | No |
| **DIFF** | Compare observations to local ledger/portfolio; classify mismatches | No |
| **POLICY DECISION** | `PROCEED` \| `ABORT` \| `BLOCK_NEW_ORDERS` (UNKNOWN pending) | No state repair |
| **STATE MUTATION** | Only: (a) advance **local ledger** lifecycle for *known* outcomes from *this* process’s submits; (b) persist ledger; (c) normal booking of **confirmed** fills from the current cycle’s `ExecutionResult` as today | **No** overwrite-from-broker repair |

Broker is **external execution truth for observation**. Local portfolio remains the bot’s accounting book. Divergence ⇒ abort, not silent sync.

---

## 3. Idempotency design

### 3.1 Ownership and generation of `client_order_id`

| Role | Owner |
|---|---|
| Generate | Core runtime / live order service (**not** the adapter) |
| Attach | `BrokerOrderExecutor` (or thin live submit wrapper) onto `BrokerOrderRequest` |
| Persist | Live order ledger (see §10) **before** network submit |
| Forward | Venue adapter if supported (Alpaca already does) |

**Recommended ID form (Decision I1):**  
`{session_or_run_id}.{symbol}.{side}.{logical_seq}` with a short random suffix **or** UUIDv4 — prefer:

- **Unique per logical intent** (UUIDv4 or ULID), generated once when the ledger row is created in `CREATED`  
- **Stable across retries** of that same ledger row  
- **Not** regenerated on network retry  

Deterministic-from-signal-only IDs are **rejected** for M13.3: same bar/signal replay after a true fill would collide incorrectly; M12 E1 already handles paper bar idempotency separately.

### 3.2 Retry behavior

| Situation | Behavior |
|---|---|
| Pre-submit validation fail (caps, risk, quote) | No ledger `SUBMITTING`; no cap consume beyond today’s pre-submit rules; may create new logical order later |
| Transport error **before** bytes likely sent | Stay/retry same `client_order_id` only after observe-by-client-id shows **absent**; else UNKNOWN |
| Timeout / connection drop **after** submit started | Mark **UNKNOWN**; **do not** place a new order with a new id; reconcile first |
| Explicit venue reject with clear response | `REJECTED`; new logical order may be created later if strategy still wants exposure |
| Duplicate `client_order_id` accepted by venue as same order | Treat as success path for that ledger row (idempotent accept) |

**Hard rule:** Never blindly resubmit when it is unknown whether the broker accepted the original request.

### 3.3 Process restart

On restart of a supervised live session:

1. Load durable live ledger.  
2. If any row in `SUBMITTING` / `UNKNOWN` / open-like states → **mandatory reconcile** before any new `place_order`.  
3. If reconcile cannot clear UNKNOWN → abort session (fail closed).  

Paper operator state schema remains untouched.

### 3.4 Duplicate signal vs duplicate submit

| Concern | Mechanism |
|---|---|
| Duplicate strategy signal same bar | Existing paper E1 is paper-operator-specific; for live M13.3, ledger + optional “one open order per symbol” policy (Decision I2) |
| Duplicate broker submission | Same `client_order_id` retained; submit path refuses second `place_order` while row ∈ {SUBMITTING, SUBMITTED, ACCEPTED, PARTIAL, UNKNOWN} |

### 3.5 Interaction with `LIVE_MAX_ORDERS_PER_DAY`

| Event | Count? |
|---|---|
| First network submit attempt of a logical order | Yes (retain M13.2 attempt semantics) |
| Retry of the **same** logical order / same `client_order_id` after UNKNOWN cleared as “not on broker” | **No second count** (Decision C1 — recommended) |
| New logical order (new id) | Yes |
| Cap/pre-submit reject | No (unchanged) |

Implement via ledger-linked cap accounting (counter key = `client_order_id` first-attempt), not a raw increment on every socket call.

---

## 4. Reconciliation design (abort-only)

### 4.1 Compared dimensions (minimum)

| Dimension | Local source | Broker observation |
|---|---|---|
| Submitted / open / filled / rejected / canceled orders | Live ledger (+ OM if wired) | `list_open_orders` / `get_order` / `get_order_by_client_id` |
| Positions (symbol → qty) | `Portfolio.positions` | `list_positions` |
| Quantity | Local qty | Broker qty |
| Avg entry (where applicable) | `Position.entry_price` | Broker avg entry / cost basis if provided |

Cash/buying-power mismatch: **recommended material abort** in M13.3 if both sides available (Decision R1); otherwise defer cash to M14 with orders+positions mandatory.

### 4.2 Mismatch classification (explicit)

| Code | Meaning | Policy |
|---|---|---|
| `MATCH` | Within tolerance | Proceed |
| `LOCAL_OPEN_MISSING_AT_BROKER` | Local open/submitted not found remotely | **ABORT** |
| `BROKER_OPEN_UNEXPECTED` | Broker open order not in local ledger | **ABORT** |
| `ORDER_STATUS_MISMATCH` | Same id, incompatible status | **ABORT** |
| `ORDER_QTY_MISMATCH` | Qty/side/symbol disagree | **ABORT** |
| `POSITION_QTY_MISMATCH` | Symbol qty disagree | **ABORT** |
| `POSITION_ENTRY_MISMATCH` | Avg entry outside tolerance | **ABORT** (if compared) |
| `UNKNOWN_LOCAL_ORDER` | Local UNKNOWN unresolved | **BLOCK** new orders / abort cycle |
| `BROKER_UNAVAILABLE` | Reconcile fetch failed | **ABORT** |
| `MALFORMED_BROKER_PAYLOAD` | Parse/schema failure | **ABORT** |

Tolerances (Decision R2): qty exact; entry price small epsilon (e.g. `0.0001` relative or venue tick) — document constants in code.

### 4.3 Abort-only policy (L4 — binding)

On material mismatch M13.3 **MUST NOT**:

- overwrite local portfolio from broker  
- invent fills to “catch up”  
- place compensating trades  
- cancel broker orders  
- assume either side is correct and continue trading  

Allowed actions: log + alert (console pattern) + return `PipelineResult(success=False, …)` / session stop / `ConfigurationError` at startup — **fail closed**.

Human/operator repair is outside M13.3 (runbooks may say “inspect and restart after manual fix”; code does not auto-fix).

---

## 5. When reconciliation runs (mandatory vs optional)

| Checkpoint | M13.3 requirement |
|---|---|
| Supervised live session / run-once **startup** (factory or first cycle before orders) | **Mandatory** full reconcile |
| Before **first** sandbox `place_order` in a process | **Mandatory** (may coalesce with startup) |
| After ambiguous submit (timeout → UNKNOWN) | **Mandatory** before any further submit |
| After reconnect / `connect()` success on live path | **Mandatory** |
| After a clear fill in the same cycle | **Optional** lightweight order refresh (recommended, not required for acceptance) |
| Paper / dry-run / backtest / paper-operator | **Must not** engage live reconcile |

SessionRunner fail-closed: reconcile abort ⇒ `success=False` ⇒ session stops (preserved).

---

## 6. Order submission state machine

Extend live ledger states (OM may mirror a subset; avoid breaking paper OM enum without care — prefer a **live-specific** state enum or additive OM values).

Proposed states:

```text
CREATED
  → SUBMITTING
      → SUBMITTED          # venue ack with broker order id
          → ACCEPTED_OPEN
              → PARTIALLY_FILLED
                  → FILLED
              → FILLED
              → CANCELED
          → REJECTED
          → CANCELED
      → REJECTED           # clear venue reject
      → UNKNOWN            # ambiguous timeout / crash window
UNKNOWN
  → SUBMITTED | ACCEPTED_OPEN | FILLED | REJECTED | CANCELED   # via reconcile observe only
  → CREATED-equivalent “confirmed absent” → allow new attempt with SAME client_order_id OR close row as FAILED_ABSENT
```

**Rules:**

- `UNKNOWN` ⇒ fail closed: **no new logical order** that could duplicate exposure for that symbol/side until reconcile resolves.  
- Transitions into terminal success/fail from UNKNOWN only via **OBSERVATION**, not guesswork.  
- Paper path keeps existing OM states unchanged if live ledger is separate (recommended).

---

## 7. Crash / network failure scenarios

| Scenario | Designed behavior |
|---|---|
| Timeout before broker receives request | Reconcile by `client_order_id`; if absent → safe retry **same id**; if present → adopt broker state |
| Timeout after broker receives request | UNKNOWN → reconcile; never new id |
| Response lost after accept | UNKNOWN → reconcile finds open/filled → bind broker id; book only if policy allows confirmed fill observation **without** inventing — prefer abort if local portfolio would need repair (Decision R3) |
| Crash after submission | Durable ledger shows SUBMITTING/UNKNOWN → startup reconcile |
| Crash after fill booked | Ledger FILLED + portfolio updated; startup match → proceed |
| Reconnect with existing open broker order | Unexpected if not in ledger → ABORT; expected → continue / block new until flat (Decision I2) |
| Duplicate `client_order_id` | Adapter/venue idempotent accept → single logical order |
| Broker reports order unknown | If local OPEN/SUBMITTED → `LOCAL_OPEN_MISSING_AT_BROKER` → ABORT |
| Local order unknown to broker while UNKNOWN | After sufficient observe → mark failed-absent; allow retry same id once (documented) |

---

## 8. M13.2 interaction (must not weaken)

| Control | M13.3 rule |
|---|---|
| G1–G10 | Unchanged; reconcile runs only after live authorized |
| Registry-only sandbox construction | Unchanged; no `broker=` injection |
| Hard notional / order-count caps | Retained; retries of same logical order do not double-count (C1) |
| LIVE_PRODUCTION denial | Unchanged |
| Cap guard before `place_order` | Remains; idempotency layer wraps **inside/before** executor, still behind guard |

---

## 9. Multi-broker / Broker port (additive)

Core reconcile/idempotency modules must import **no** Alpaca types/constants.

### 9.1 Minimum additive Broker capabilities (recommended Protocol or ABC optional methods)

Prefer a separate Protocol `ReconcileCapableBroker` implemented by sandbox adapters, so `PaperBroker` need not grow heavy APIs:

| Capability | Purpose |
|---|---|
| `get_order_by_client_id(client_order_id) -> BrokerOrderSnapshot \| None` | Idempotent observe |
| `get_order(broker_order_id) -> BrokerOrderSnapshot \| None` | Status refresh |
| `list_open_orders() -> list[BrokerOrderSnapshot]` | Unexpected open detection |
| `list_positions() -> list[BrokerPositionSnapshot]` | Position reconcile |

Agnostic snapshots (venue-neutral DTOs):

```text
BrokerOrderSnapshot:
  broker_order_id, client_order_id, symbol, side, qty, filled_qty,
  status (normalized enum), avg_fill_price | None, raw_status | None

BrokerPositionSnapshot:
  symbol, quantity, avg_entry_price | None
```

Alpaca maps its REST into these DTOs inside the adapter. IBKR / TradeStation / Webull do the same later.

`place_order` remains; `client_order_id` already on the request DTO.

---

## 10. Persistence (smallest safe mechanism)

**Need that must survive restart:** live order ledger rows with at least:

- `client_order_id`  
- symbol / side / qty / order_type  
- state  
- broker_order_id (nullable)  
- timestamps  
- optional intent fingerprint / run id  
- first_submit_counted (for cap accounting)

**Recommendation (Decision P1):**  
JSON file ledger (atomic write, schema versioned), **separate** from M12 `OperatorState` — e.g. path from settings/CLI `LIVE_ORDER_LEDGER_PATH` required when `execution=live`.

Rationale: matches existing JsonPaperStateStore patterns; no DB; enough for supervised sandbox; evolvable to SQLite later.

**Not required in M13.3:** full portfolio durability for live (abort-only means we refuse to trade when diverged rather than rebuild books).

---

## 11. Proposed module / wiring sketch

```text
runtime/live_order_ledger.py     # durable rows + state machine
runtime/idempotent_submit.py     # create row → persist → submit → interpret
runtime/reconcile.py             # OBSERVE → DIFF → POLICY (abort-only)
broker_interface/snapshots.py    # BrokerOrderSnapshot / BrokerPositionSnapshot
broker_interface/reconcile_port.py  # Protocol
Alpaca adapter implements Protocol (mapping only)

BasicTradingRuntime.run_once (live only):
  mode gates → reconcile mandatory gates → ... → idempotent execute → book confirmed fills
```

Paper/dry-run/backtest/operator: **no** live ledger/reconcile engagement.

---

## 12. Test plan (design)

| # | Test intent |
|---|---|
| 1 | Same logical order cannot be submitted twice while open/unknown |
| 2 | Retry uses same `client_order_id` |
| 3 | Ambiguous timeout ⇒ UNKNOWN + fail closed (no second id) |
| 4 | Reconcile exact match ⇒ proceed |
| 5 | Order mismatch ⇒ abort |
| 6 | Position mismatch ⇒ abort |
| 7 | Unexpected broker open order ⇒ abort |
| 8 | Local open missing at broker ⇒ abort |
| 9 | Process restart loads ledger; prevents duplicate submit |
| 10 | Malformed broker reconcile payload ⇒ abort |
| 11 | Broker unavailable during reconcile ⇒ abort |
| 12 | Paper / backtest / operator unchanged |
| 13 | M13.2 gate matrix still green; LIVE_PRODUCTION unreachable |
| 14 | Same-id retry does not double-count `LIVE_MAX_ORDERS_PER_DAY` |
| 15 | Injected live `broker=` still rejected; registry-only construction |

Prefer mocked HTTP / fake `ReconcileCapableBroker` — no network in default CI (L5).

---

## 13. Explicit non-goals (M13.3)

- Automatic state repair / broker→local overwrite  
- Automatic compensating orders or cancel-all  
- Unattended production trading / `run-paper-operator` live  
- Real-money trial / LIVE_PRODUCTION enablement (M14)  
- Shadow mode (M13.4)  
- Multi-broker adapters beyond Alpaca sandbox mapping  
- Changing M12 paper state schema  
- Weakening G1–G10, SessionRunner fail-closed, M10/M11/M12  

---

## 14. Security / safety risks

| Risk | Mitigation |
|---|---|
| Silent overwrite of portfolio from broker | Forbidden by abort-only policy + tests |
| Blind resubmit after timeout | UNKNOWN + mandatory reconcile |
| Cap bypass via retry storms | Same-id retry non-counting + UNKNOWN block |
| Ledger tampering / stale file | Schema version + fail closed on corrupt ledger |
| Secret leakage in reconcile logs | Log ids/status/qty only; never API secrets |
| Paper path accidentally reconcile-aborting | Live-execution gating only |
| Treating Alpaca open as fill | Keep M13.1 mapping; snapshots distinguish open vs filled |

---

## 15. Unresolved design decisions

| ID | Question | Options | Recommendation |
|---|---|---|---|
| **I1** | `client_order_id` shape | (a) UUIDv4 per logical order (b) deterministic bar/signal key (c) hybrid | **(a)** unique per logical order; stable on retry |
| **I2** | Max concurrent open sandbox orders | (a) one open per symbol (b) unlimited within caps (c) configurable | **(a)** for M13.3 simplicity |
| **C1** | Retry vs daily order cap | (a) same-id retry does not re-count (b) every socket counts | **(a)** |
| **R1** | Cash/buying-power in reconcile | (a) mandatory abort on mismatch (b) orders+positions only in M13.3 | **(b)** mandatory orders+positions; cash optional/warn or M14 — prefer **abort if both available**, else skip cash |
| **R2** | Entry-price tolerance | (a) exact (b) epsilon | **(b)** small epsilon |
| **R3** | Broker shows FILLED but local not booked after crash | (a) abort always (b) allow one-time book-from-observation | **(a) abort always** in M13.3 (no auto repair); human clears |
| **P1** | Ledger persistence | (a) JSON file (b) SQLite (c) in-memory only | **(a)** JSON atomic file; required path for live |
| **P2** | OM enum vs separate live ledger | (a) extend OrderState (b) separate live ledger | **(b)** separate ledger to avoid paper regressions |
| **B1** | Broker API shape | (a) expand ABC (b) Protocol `ReconcileCapableBroker` | **(b)** Protocol; Alpaca implements |

---

## 16. Recommended design (summary)

1. **Separate live order ledger** (JSON, atomic) with rich state machine including **UNKNOWN**.  
2. Generate **stable unique `client_order_id`** at CREATED; executor attaches it; retries never mint a new id for that row.  
3. Ambiguous outcomes → UNKNOWN → **mandatory reconcile** → abort or resolve; never blind resubmit.  
4. **Abort-only** reconcile: OBSERVE → DIFF → PROCEED/ABORT; no portfolio overwrite, no compensating trades, no cancel-all.  
5. Mandatory reconcile at live startup, before first order, after reconnect, and whenever UNKNOWN exists.  
6. Additive **ReconcileCapableBroker** Protocol + neutral snapshots; Alpaca maps inside adapter.  
7. Same-id retries do not consume an extra `LIVE_MAX_ORDERS_PER_DAY` slot.  
8. Preserve all M10–M13.2 invariants; paper/operator/backtest untouched; LIVE_PRODUCTION remains unreachable.

---

## 17. DESIGN VERDICT

# M13.3 DESIGN READY FOR EXTERNAL APPROVAL

The post-M13.2 tree has additive `client_order_id` and gated sandbox execution but lacks durable idempotency, UNKNOWN handling, and abort-only broker↔local reconciliation. This design specifies broker-agnostic mechanisms that close those gaps without automatic repair, without weakening M13.2 gates, and without opening LIVE_PRODUCTION or M14 trial scope.

Unresolved items **I1–I2, C1, R1–R3, P1–P2, B1** should be confirmed by external review before implementation.

---

## WAITING FOR EXTERNAL APPROVAL

**STOP.** Do not implement M13.3. Do not modify source/tests for this design. Do not stage, commit, push, or open a PR from this documentation phase.
