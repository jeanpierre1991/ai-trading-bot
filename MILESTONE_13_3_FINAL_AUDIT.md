# M13.3 Final Audit — Idempotency + abort-only reconciliation (post-remediation)

**Audit type:** Independent re-audit after MAJOR remediation of the ACTUAL working tree vs approved `MILESTONE_13_3_DESIGN_REVIEW.md`  
**Baseline HEAD (M13.2 committed):** `c2f2e81468f6667a8ab5d569499817be195f0d31`  
**Scope:** M13.3 only — durable ledger, idempotent submit, abort-only reconcile (BROKER_SANDBOX)  
**Prior verdict:** NOT APPROVED (4 MAJOR). This document verifies those four remediations only.  
**Audit actions:** Code/test inspection + test execution. No stage/commit/push/PR. M13.4 not started.

---

## 1. Exact files audited

### M13.3 implementation (working tree)

| Path | Role |
|---|---|
| `runtime/live_order_ledger.py` | Atomic JSON ledger; `FAILED_ABSENT`; `first_submit_counted` + `first_submit_day`; `prepare_retry` |
| `runtime/idempotent_submit.py` | `IdempotentLiveExecutor` — UUIDv4 mint + same-id FAILED_ABSENT retry |
| `runtime/reconcile.py` | Abort-only OBSERVE → DIFF → POLICY; UNKNOWN/CREATED/SUBMITTING resolution |
| `runtime/live_caps.py` | C1 durable via ledger; `unwrap_broker` |
| `runtime/factory.py` | Live ledger required; guard wired with ledger; `IdempotentLiveExecutor` |
| `runtime/trading_runtime.py` | Live cycle-start reconcile stage |
| `broker_interface/snapshots.py` | Agnostic order/position snapshots |
| `broker_interface/reconcile_port.py` | `ReconcileCapableBroker` Protocol |
| `broker_interface/alpaca/adapter.py` | Snapshot/reconcile mapping (adapter only) |
| `config/settings.py` / `.env.example` | `LIVE_ORDER_LEDGER_PATH`, entry epsilon |
| `tests/runtime/test_m13_3_reconcile_idempotency.py` | M13.3 + remediation tests |
| `tests/runtime/test_m13_2_live_enablement.py` | Ledger path for live factory tests |
| `MILESTONE_13_3_DESIGN_REVIEW.md` | Approved design baseline |

### Cross-checks

| Area | Result |
|---|---|
| `runtime/idempotent_submit.py` / `reconcile.py` / `live_order_ledger.py` / `live_caps.py` | **No** Alpaca imports |
| Factory `broker=` injection | Still rejected for `execution="live"` |
| Auto-repair / cancel / compensate | **Not** present (ledger-only FAILED_ABSENT / pre-ack adopt; no portfolio mutation) |
| Dead `LiveOrderPropsal` | **Removed** |
| Paper OM / backtest / paper-operator | Unchanged by inspection + suite |

---

## 2. Remediation verification (prior 4 MAJORs)

### MAJOR 1 — Same-id retry through `IdempotentLiveExecutor` — REMEDIATED / PASS

**Evidence**

- New logical orders: `create_order(..., client_order_id=str(uuid.uuid4()))` then `SUBMITTING`.
- Existing logical order in `FAILED_ABSENT`: `retryable_order_for_symbol` → `prepare_retry` reuses the **exact** `client_order_id` (no new UUID).
- UNKNOWN / open rows still block; never blind-retry of ambiguous state.
- Test: `test_executor_same_id_retry_reuses_client_order_id` goes through `IdempotentLiveExecutor.execute` and asserts identical `client_order_id` on both `place_order` calls.

### MAJOR 2 — UNKNOWN → confirmed absent → `FAILED_ABSENT` — REMEDIATED / PASS

**Evidence**

- `reconcile_live` → `_resolve_unknown_and_pre_ack`:
  - `get_order_by_client_id` → `None` (broker-agnostic confirmed absence) → persist `FAILED_ABSENT`, continue.
  - OPEN / PARTIAL / FILLED / other positive observation / malformed / unavailable → **ABORT**; no portfolio repair; no invent.
- Supervised retry after `FAILED_ABSENT` reuses same id (MAJOR 1 path).
- Tests: `test_executor_same_id_retry_reuses_client_order_id` (UNKNOWN→FAILED_ABSENT→retry); `test_unknown_ambiguous_broker_result_aborts`.

### MAJOR 3 — C1 durable across process restart — REMEDIATED / PASS

**Evidence**

- `LiveCapGuardBroker` accepts `ledger=`; seeds in-memory counter from `first_submit_counted` + `first_submit_day` for the current UTC day.
- First attempt: `record_submit` + `ledger.mark_first_submit_counted(cid, day=...)`.
- Same `client_order_id` retry (incl. after restart) does not consume another slot.
- New logical order after restart consumes a new slot.
- Inconsistent durable state (`first_submit_counted` without day, missing ledger row when ledger attached, day mismatch on re-mark) → fail closed.
- M13.2 hard caps not weakened (notional + daily still enforced before inner `place_order`).
- Tests: `test_c1_same_id_retry_across_restart_no_double_count`; `test_c1_new_logical_order_after_restart_consumes_slot`.

### MAJOR 4 — CREATED / SUBMITTING crash recovery — REMEDIATED / PASS

**Evidence**

- Pre-ack rows are observed by `client_order_id` on reconcile:
  - confirmed absent → `FAILED_ABSENT` (unblocks I2; enables same-id retry)
  - existing OPEN/PARTIAL/REJECTED/CANCELED → ledger adopted to matching non-fill state (no duplicate submit; I2 still blocks)
  - FILLED → ABORT (`BROKER_FILLED_LOCAL_NOT_BOOKED`, R3)
  - unavailable / malformed / ambiguous status → ABORT
- Never silently `PROCEED` while leaving unresolved CREATED/SUBMITTING blockers.
- Tests: `test_created_crash_broker_absent_recoverable_same_id_retry`; `test_submitting_crash_broker_absent_recoverable_same_id_retry`; `test_created_submitting_broker_unavailable_aborts`; `test_created_submitting_existing_broker_order_no_duplicate_submit`.

---

## 3. Requirement-by-requirement evidence (full matrix)

| Requirement | Verdict |
|---|---|
| Durable atomic JSON ledger | PASS |
| UUIDv4 for new logical orders; same id on supervised retry | PASS |
| I2 one-open-order-per-symbol | PASS |
| UNKNOWN fail-closed; confirmed absence → FAILED_ABSENT | PASS |
| Abort-only reconcile (no cancel / compensate / portfolio mutation) | PASS |
| Order + position mismatch detection | PASS |
| C1 same-id non-recount incl. restart | PASS |
| Broker-agnostic core (`ReconcileCapableBroker`) | PASS |
| Alpaca mapping in adapter only | PASS |
| CREATED/SUBMITTING crash resolution | PASS |
| Malformed / unavailable → ABORT | PASS |
| G1–G10 preserved | PASS |
| Registry-only sandbox construction | PASS |
| LIVE_PRODUCTION unreachable | PASS (`test_live_production_still_unreachable`) |
| Paper / backtest / paper-operator unchanged | PASS (`test_paper_path_unchanged_without_ledger` + full suite) |

---

## 4. Test commands and results

```bash
.venv/bin/python -m pytest tests/runtime/test_m13_3_reconcile_idempotency.py -q --tb=line
# 23 passed

.venv/bin/python -m pytest tests/runtime/test_m13_2_live_enablement.py -q --tb=line
# 45 passed

.venv/bin/python -m pytest tests/broker_interface/test_alpaca_broker.py -q --tb=line
# 17 passed

.venv/bin/python -m pytest tests/runtime/test_m13_2_live_enablement.py tests/broker_interface/test_alpaca_broker.py -q --tb=line
# 62 passed

.venv/bin/python -m pytest -q --tb=line
# 673 passed
```

### Required remediation scenarios covered

| # | Scenario | Test |
|---|---|---|
| 1 | Executor-level same-id retry | `test_executor_same_id_retry_reuses_client_order_id` |
| 2 | UNKNOWN → confirmed absent → FAILED_ABSENT | same |
| 3 | FAILED_ABSENT retry reuses exact `client_order_id` | same |
| 4 | UNKNOWN + ambiguous broker → ABORT | `test_unknown_ambiguous_broker_result_aborts` |
| 5 | C1 same-id across restart no double-count | `test_c1_same_id_retry_across_restart_no_double_count` |
| 6 | New logical order after restart consumes slot | `test_c1_new_logical_order_after_restart_consumes_slot` |
| 7 | CREATED crash + absent → same-id retry | `test_created_crash_broker_absent_recoverable_same_id_retry` |
| 8 | SUBMITTING crash + absent → same-id retry | `test_submitting_crash_broker_absent_recoverable_same_id_retry` |
| 9 | CREATED/SUBMITTING + unavailable → ABORT | `test_created_submitting_broker_unavailable_aborts` |
| 10 | CREATED/SUBMITTING + existing order → no duplicate | `test_created_submitting_existing_broker_order_no_duplicate_submit` |
| 11 | LIVE_PRODUCTION unreachable | `test_live_production_still_unreachable` |
| 12 | Paper path unchanged | `test_paper_path_unchanged_without_ledger` |

---

## 5. Findings summary

| Severity | Count | Items |
|---|---|---|
| **BLOCKER** | 0 | — |
| **MAJOR** | 0 | Prior four MAJORs independently re-verified as remediated. |
| **MINOR** | 1 | Timeout / open detection in `IdempotentLiveExecutor._interpret_result` still uses brittle message substrings (pre-existing; out of MAJOR remediation scope). |

---

## 6. Production-money / M14 / M13.4 boundary

| Claim | Verdict |
|---|---|
| LIVE_PRODUCTION reachable | **NO** |
| Real-money path enabled | **NO** |
| Auto-repair / cancel-all / trial ops | **NO** |
| M13.4 shadow started | **NO** |

---

## 7. Final verdict

**APPROVED**

M13.3 now meets the approved design on same-id executor retry, UNKNOWN confirmed-absence recovery, durable C1 across restart, and CREATED/SUBMITTING crash resolution, while preserving abort-only reconciliation, I2, G1–G10, registry-only sandbox construction, LIVE_PRODUCTION denial, and paper/backtest/operator behavior. Suite green (673 passed).

**Do not start M13.4 in this remediation step.**  
Commit remains an explicit operator decision (not performed here).

---

## 8. Audit process confirmations

- Remediation source/tests/audit file: updated as required for the four MAJORs  
- Staging / commit / push / PR: **not performed**  
- M13.4: **not started**  
- STOP after writing this file
