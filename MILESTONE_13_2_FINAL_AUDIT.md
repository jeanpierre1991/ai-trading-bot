# M13.2 Final Audit — Hard LIVE enablement gates (BROKER_SANDBOX only)

**Audit type:** External final audit of the ACTUAL working tree (+ MAJOR remediation follow-up)  
**Baseline HEAD (M13.1, committed):** `ac45606c0c07a498364da60fc86fa873a23958de`  
**Scope:** M13.2 only — broker-agnostic conjunctive live gates; supervised `execution="live"` → **BROKER_SANDBOX** only  
**Remediation:** Live factory `broker=` injection seam closed (registry-only sandbox construction).  
**Audit actions:** Code/test inspection + test execution. Follow-up modified only `runtime/factory.py`, M13.2 tests, and this audit file. No stage/commit/push/PR. M13.3 not started.

---

## 1. Exact files audited

### M13.2 implementation files (in scope)

| Path | Role |
|---|---|
| `runtime/live_enablement.py` | Pure G1–G10 `evaluate_live_enablement` / `LiveExecutionContext` / expected confirm phrase |
| `runtime/live_caps.py` | `LiveOrderCounter` + `LiveCapGuardBroker` (pre-`place_order` caps) |
| `broker_interface/adapter_registry.py` | Broker-agnostic sandbox/production allowlists + `construct_sandbox_broker` |
| `runtime/mode_policy.py` | Paper path + live path that **re-calls** Authority (D-A) |
| `runtime/factory.py` | `execution="live"` composition; registry-only sandbox broker; rejects all `broker=` injection |
| `runtime/trading_runtime.py` | Stores `execution`/`command`; mode_policy at cycle start |
| `runtime/session.py` | Optional `SessionConfig.mode` for supervised live sessions |
| `config/settings.py` | Live gate settings defaults (deny) |
| `main.py` | `--live` on run-once/session only; operator/backtest closed |
| `.env.example` | Documented deny-by-default live vars |
| `broker_interface/alpaca/adapter.py` | Docstring-only touch; production host reject unchanged |
| `tests/runtime/test_m13_2_live_enablement.py` | Gate matrix + factory/caps/CLI/security tests |
| `tests/runtime/test_runtime_factory.py` | Updated unsupported-execution case |
| `tests/runtime/test_trading_runtime_mode_guards.py` | Message/assert compatibility |
| `MILESTONE_13_2_DESIGN_REVIEW.md` | Approved design (documentation) |

### Cross-checked (must remain safe / unchanged in behavior)

| Area | Result |
|---|---|
| `runtime/paper_operator.py` | Still `ExecutionBackend = dry_run\|paper`; requires `trading_mode=paper` |
| `backtesting/` | No live factory wiring |
| `strategy_engine/`, `risk_manager/`, `order_manager/`, `portfolio_manager/` | No Alpaca / live-gate coupling |
| `runtime/live_enablement.py` | **Zero** `alpaca`/`Alpaca`/`APCA` references |
| M13.3 reconcile / M13.4 shadow modules | **Absent** |

### Pre-existing untracked Markdown (NOT M13.2 implementation)

- `MILESTONE_11_*_FINAL_AUDIT.md`
- `MILESTONE_12_*_FINAL_AUDIT.md` / `MILESTONE_12_*_DESIGN_REVIEW.md` / `MILESTONE_12_DESIGN_REVIEW.md`
- `MILESTONE_13_1_FINAL_AUDIT.md` (committed with M13.1)
- This file: `MILESTONE_13_2_FINAL_AUDIT.md` (documentation-only audit output)

---

## 2. LIVE Enablement Authority

### Evidence — PASS

1. **Conjunctive G1–G10:** `evaluate_live_enablement` returns `authorized=True` only after all checks; each failure returns immediately via `deny(...)`.
2. **Fail-closed defaults:** `live_trading_enabled=False`, `live_confirm_token=""`, `broker_endpoint_class=local_paper`, caps `None` → cannot authorize.
3. **No armed flag:** No module-level mutable enable latch; authorization is a pure function of settings + `LiveExecutionContext`.
4. **mode_policy re-evaluation (D-A):** `_live_mode_policy_violation` calls `evaluate_live_enablement(...)` independently on every `run_once` when `execution=="live"`.
5. **No Alpaca types in core Authority:** `runtime/live_enablement.py` imports only endpoint helpers / `is_approved_adapter` from the agnostic registry + `TradingMode`.

### Gate-by-gate verification

| Gate | Implementation evidence | Fail-closed |
|---|---|---|
| **G1** | `trading_mode != "live"` → deny | Yes |
| **G2** | `live_trading_enabled is not True` → deny | Yes |
| **G3** | Empty token deny; `provided != EXPECTED_LIVE_CONFIRM_TOKEN` (exact, case-sensitive) | Yes |
| **G4** | `is_approved_adapter(adapter_id, endpoint_class)`; empty name deny | Yes |
| **G5** | Non-empty key/secret/base_url; values not echoed in reasons | Yes |
| **G6** | `execution=="live"` ∧ command ∈ {run-once, run-session} ∧ `TradingMode.LIVE` | Yes |
| **G7** | Rejects `run-backtest` command and `TradingMode.BACKTEST` | Yes |
| **G8** | Rejects `run-paper-operator` command | Yes |
| **G9** | `live_production` hard-deny (M14); else requires `broker_sandbox` | Yes |
| **G10** | Notional and orders/day required and `> 0`; optional gross if set must be `> 0` | Yes |

### Findings

| Severity | Finding |
|---|---|
| MINOR | G7 branch `trading_mode == "backtest"` is unreachable after G1; harmless dead branch. |

---

## 3. Production money safety

### Evidence — PASS

1. **LIVE_PRODUCTION hard-denied:** Authority G9 returns M14 message even when all other settings would pass; registry `_PRODUCTION_ADAPTERS = frozenset()`.
2. **Alpaca production host:** `_validate_paper_base_url` still rejects hostname `api.alpaca.markets` (only `paper-api.alpaca.markets`); covered by M13.1 + M13.2 tests.
3. **Factory live path:** After Authority, **always** builds via `construct_sandbox_broker` → registry sandbox builder; wraps `LiveCapGuardBroker`. Any non-`None` `broker=` argument raises `ConfigurationError` (registry boundary; no Alpaca-specific type checks in factory).
4. **CLI:** `--live` maps to `execution="live"` + `TradingMode.LIVE` only for run-once/session; help text states not a real-money trial.
5. **No silent PaperBroker fallback** when live requested: unknown broker → `ConfigurationError` from registry/Authority (tested).

### MAJOR remediation (resolved)

| Item | Status |
|---|---|
| Prior MAJOR: live `broker=` injection bypassed registry | **FIXED** in `runtime/factory.py` `_build_live_sandbox_executor` |
| `execution="live"` + arbitrary injected broker | Fail-closed (`test_factory_rejects_arbitrary_injected_broker_on_live`) |
| `execution="live"` + PaperBroker injection | Fail-closed (`test_factory_rejects_paperbroker_injection_on_live`) |
| Registry-built BROKER_SANDBOX path | Still works (`test_factory_live_sandbox_wires_cap_guarded_alpaca`) |
| `execution="paper"` broker injection | Unchanged (`test_execution_paper_still_accepts_explicit_paperbroker`) |

### Production-money reachability verdict

**UNREACHABLE via settings, registry, CLI, factory composition, and Alpaca adapter construction.**  
LIVE_PRODUCTION class cannot authorize. Alpaca `api.alpaca.markets` cannot be constructed.  
Live factory no longer accepts injected brokers of any kind.

---

## 4. Supervised execution

### Evidence — PASS

| Surface | Evidence |
|---|---|
| `run-once` / `run-session` | `_add_execution_flags(..., allow_live=True)`; `live_command` set accordingly |
| `run-paper-operator` | `allow_live` default false (no `--live`); CLI refuses `trading_mode!=paper` and `execution==live`; PaperOperator still paper-only |
| `run-backtest` | Requires `trading_mode=paper`; does not call live factory |
| SessionRunner | Still stops on `not result.success` (fail-closed); `SessionConfig.mode` defaults `PAPER` |
| Direct invalid wiring | mode_policy rejects live+DryRunExecutor, paper+non-PaperBroker, ungated live settings (tested) |

---

## 5. Broker-agnostic architecture

### Evidence — PASS

1. Alpaca remains Adapter #1 via registry id `alpaca_paper` + lazy builder only.
2. Authority depends on adapter **ids** and endpoint **classes**, not Alpaca HTTP/hosts.
3. Strategies / risk / OM / portfolio / SessionRunner orchestration have no Alpaca imports.
4. Future venues: add registry id + sandbox builder; same G1–G10 / factory / mode_policy path.

---

## 6. G10 hard caps

### Evidence — PASS (semantics documented)

1. Authority requires `LIVE_MAX_ORDER_NOTIONAL > 0` and `LIVE_MAX_ORDERS_PER_DAY > 0` (≥ 1 for ints).
2. `LiveCapGuardBroker.place_order`:
   - Order-count check **before** quote/notional/`place_order`
   - Notional check **before** `place_order`
   - Pre-submit rejects (count/notional/quote failure) **do not** call `record_submit` → **do not** consume capacity
3. **Off-by-one:** `current_count() >= max` blocks; with `max=1`, first submit allowed (0→1), second blocked. Correct.
4. **UTC-day:** `_utc_day` normalizes to UTC date ISO; day rollover yields `current_count()==0` until next `record_submit` resets day. Deterministic and fail-closed.
5. **Post-cap venue attempts:** `record_submit()` runs after cap checks and **before** inner `place_order`. Venue-level failures/rejects after that point **do** consume daily capacity. This is **explicitly designed** (comment in `live_caps.py`) as submission-attempt accounting for M13.2 in-process counter (D-C); durable success-only accounting deferred.

### Findings

| Severity | Finding |
|---|---|
| MINOR | Venue `place_order` failures still consume the daily counter (by design). Pre-submit cap rejects do not. Documented here; ensure operators understand before M14. |

---

## 7. Credential / confirmation security

### Evidence — PASS

1. Expected phrase constant `I_UNDERSTAND_LIVE_IS_GATED_NOT_A_TRIAL`; default token `""` denies.
2. Exact match only; substring/partial tested; **case mismatch covered** by `test_g3_case_mismatch_denies_without_changing_semantics` (semantics unchanged).
3. Deny reasons reference gate ids, not secret/token values; tests assert `SECRET` / token absent from reasons.
4. Factory connect/construction errors use exception **class names**, not secret payloads.

---

## 8. Regression safety

| Invariant | Evidence |
|---|---|
| M10 backtest isolation | `test_m10_3_integration_safety` + full suite green; backtest still paper + no live executor |
| M11 freshness/hours | Dedicated tests green; settings defaults unchanged |
| M12 PaperOperator | Integration/bounds tests green; paper-only + no `--live` |
| M13.1 Alpaca contract | 17/17 green; production host still rejected |
| PaperBroker default | `execution=paper` still PaperBroker even if live env vars present (tested) |
| SessionRunner fail-closed | Unchanged early-stop on `success=False` |

---

## 9. Test quality

### Commands and results

```bash
.venv/bin/python -m pytest tests/runtime/test_m13_2_live_enablement.py -q --tb=line
# 45 passed  (post-remediation; was 42)

.venv/bin/python -m pytest tests/broker_interface/test_alpaca_broker.py -q --tb=line
# 17 passed

.venv/bin/python -m pytest -q --tb=line
# 650 passed  (post-remediation; was 647)
```

### Assessment

- Parametrized settings-gate matrix flips **one field at a time** against a shared happy baseline — genuine independent denials, not a single smoke path.
- Separate tests for G6/G7/G8 context axes, LIVE_PRODUCTION, factory wiring, PaperBroker non-fallback, caps `place_calls==0`, operator/CLI, secret non-leakage, M13.1 host reject.
- Caps tests use a recording fake broker — assert no `place_order` on cap breach.
- Post-remediation: arbitrary/`PaperBroker` live injection fail-closed; registry sandbox path and paper `broker=` path still proven.

---

## 10. Working tree / scope hygiene

### M13.2 source/test/config (authorized implementation set)

**Created**
- `runtime/live_enablement.py`
- `runtime/live_caps.py`
- `broker_interface/adapter_registry.py`
- `tests/runtime/test_m13_2_live_enablement.py`

**Modified**
- `config/settings.py`
- `.env.example`
- `runtime/factory.py`
- `runtime/mode_policy.py`
- `runtime/trading_runtime.py`
- `runtime/session.py`
- `main.py`
- `broker_interface/alpaca/adapter.py` (**docstring only**)
- `tests/runtime/test_runtime_factory.py`
- `tests/runtime/test_trading_runtime_mode_guards.py`

**Documentation (design; untracked until commit process)**
- `MILESTONE_13_2_DESIGN_REVIEW.md`
- `MILESTONE_13_2_FINAL_AUDIT.md` (this file)

### Separated: pre-existing untracked M11/M12 Markdown

Listed in §1 — **not** M13.2 implementation.

### Accidental unrelated source changes

**None observed** beyond the docstring update on the Alpaca helper (in-scope documentation of factory wiring).

---

## 11. M14 boundary

| Claim | Verdict |
|---|---|
| Passing G1–G10 authorizes real-money trading | **NO** — only `broker_sandbox`; help/docs/Authority state M14 for production |
| LIVE_PRODUCTION hard-denied | **YES** |
| M14 trial protections prematurely implemented | **NO** — no trial checklist, cancel-all, production allowlist, or required gross notional |
| M13.3 started | **NO** — no reconciler module / durable idempotency enforcement |
| M13.4 started | **NO** — no shadow executor/mode |

### M14-boundary verdict

**INTACT.** M13.2 opens supervised **sandbox** wiring only. Real-money trial remains M14.

---

## 12. Findings summary (post-remediation)

| Severity | Count | Items |
|---|---|---|
| **BLOCKER** | 0 | — |
| **MAJOR** | 0 | Prior live `broker=` registry bypass — **RESOLVED** |
| **MINOR** | 3 | Dead G7 branch; counter consumes post-cap venue attempts by design; Alpaca error text still says “M13.1” (unrelated; not addressed in follow-up) |

---

## 13. Final verdict

# APPROVED

M13.2 meets the approved design after MAJOR remediation: conjunctive broker-agnostic G1–G10 Authority, mode_policy re-evaluation, supervised `--live` only on run-once/session, operator/backtest closed, BROKER_SANDBOX-only enablement via **registry-only** factory construction (injected `broker=` rejected), G10 pre-submit caps, and production money remaining unreachable. Remaining items are MINOR only.

**Do not commit until external process authorizes an M13.2 commit file list.**  
**Do not start M13.3 until that commit (if any) and next design gate are approved.**

---

## 14. Audit / remediation process confirmations

- Follow-up code changes limited to: `runtime/factory.py`, `tests/runtime/test_m13_2_live_enablement.py`, and this audit file  
- Unrelated MINOR findings: **not** addressed (per instructions)  
- Staging / commit / push / PR: **not performed**  
- M13.3: **not started**  
- STOP after updating this file
