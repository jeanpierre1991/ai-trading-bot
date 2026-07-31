# M13 Design Review — Live broker adapter behind hard gates

**Status:** DESIGN/ANALYSIS ONLY — awaiting external approval  
**Date context:** After Milestone 12 closure commit `ee0a351f25f57581752681cb66c698c285b61f64`  
**Branch at design time:** `cursor/m9-paper-session-loop` (ahead of origin by 4: M12.1–M12.4)  
**Canonical parent roadmap:** `MILESTONE_11_14_DESIGN_SPEC.md` § M13 / § M14 / Decision 5 / LIVE progression  
**Mode:** Documentation only. Do not implement until this design and required decisions are approved.

---

## 0. Repository inspection summary (no code changes)

**HEAD:** `ee0a351f25f57581752681cb66c698c285b61f64` — `M12.4: add soak validation and close Milestone 12`  
**Canonical roadmap:** `MILESTONE_11_14_DESIGN_SPEC.md` already defines **M13** and **M14** after M11–M12.  
**Closure docs:** `MILESTONE_12_SUMMARY.md`, README “Unattended paper operator”, soak/preflight tests committed.  
**Broker surface today:** `Broker` ABC + `PaperBroker` only; `BrokerOrderRequest` has no client-order-id; factory/`mode_policy` hard-reject `live` and non-`PaperBroker` executors.

---

## 1. Was Milestone 13 already defined?

**Yes — explicitly.**

| Source | What it says |
|---|---|
| `MILESTONE_11_14_DESIGN_SPEC.md` § M13 | **“Live broker adapter behind hard gates”** with M13.1–M13.4 |
| Same doc §8 LIVE progression | Code path → sandbox → gated enable → shadow → (M14) trial |
| Same doc Decision **5** | Live venue deferred to **pre-M13.1** (Alpaca-like vs other) |
| `MILESTONE_12_SUMMARY.md` | Non-goal: LIVE enablement (M13); handoff after Checkpoint D |
| M12 design/audits | Repeated “do not start M13 until M12 closure” |

M13 is **not** a greenfield invention; this design phase should **refine and approve** the existing M11–M14 plan against the post-M12 codebase.

---

## 2. System capabilities as of approved M12 closure

| Layer | Capability |
|---|---|
| Modes | Default `trading_mode=paper`; **LIVE unreachable** (factory + mode_policy) |
| Execution | `dry_run` / `BrokerOrderExecutor(PaperBroker)` only |
| Pipeline | `run_once`: mode → MD → hours → freshness → strategy → risk → intent → exec → OM → portfolio |
| Paper fidelity (M11) | Closed-bar fills, freshness, XNYS hours |
| Supervised loops (M9) | `run-session` / `SessionRunner`: fail-closed on **any** `success=False` |
| Unattended paper (M12) | `run-paper-operator`: kill, bounds, interval, durable JSON, A2/B1/D1/E1, Decision H, CI soak |
| Backtest (M10) | Isolated; `enforce_market_data_freshness/hours=False`; no live executor |
| Broker | Local `PaperBroker` only; settings hint `BROKER_*` / paper-api URL but no live adapter |

**Missing for live (by design until M13):** real broker adapter, conjunctive live gates, client-order idempotency, broker↔local reconciliation, shadow mode, cancel-all (M14).

---

## 3. Most logical M13 objective (architecture-aligned)

**Make live trading *technically possible* behind fail-closed multi-gates — without enabling a real-money trial.**

That matches the parent spec: M13 = adapter + gates + idempotency/reconcile + shadow; **M14** = emergency cancel-all, alerts, evidence checklist, human-approved tiny trial.

Doing strategy research, profitability gates, or unattended live operator next would skip the documented LIVE progression and weaken the paper/live isolation the repo has enforced through M12.

**Parent milestone objective (verbatim intent):**
Introduce **one** real broker adapter and a **multi-gate** live enablement path without weakening paper defaults; include account sync, idempotent orders, and basic fill reconciliation.

**Why M13 (not earlier / not M14):**
- Concrete gap: No live broker; live explicitly rejected by factory/`mode_policy`.
- Paper fidelity + autonomous paper proven first (M11–M12) — now complete.
- M13 makes live *technically possible*; M14 makes a *trial* operationally acceptable.

---

## 4. Dependencies, risks, invariants M13 must not break

### 4.1 Dependencies (prerequisites)

| Prerequisite | Status |
|---|---|
| M11 paper fidelity | Satisfied |
| M12 autonomous paper + Checkpoint D soak evidence | Satisfied (`ee0a351`) |
| Decision **5** venue choice | **Still required before M13.1** |

### 4.2 Invariants (must not break)

1. Default remains `trading_mode=paper` / `MARKET_DATA_PROVIDER=mock` for CI  
2. Paper path (`PaperBroker`, closed-bar fills, hours/freshness) behavior unchanged when live gates closed  
3. `SessionRunner` fail-closed-on-any-abort unchanged  
4. `PaperOperator` stays **paper-only** (must not become unattended LIVE in M13)  
5. M10 backtest isolation (`enforce_*=False`, no live executor)  
6. Secrets never logged or committed  
7. Full suite stays green; CI does not require live broker network by default  

### 4.3 Risks

| Risk | Mitigation in design |
|---|---|
| Accidental live enable via partial flags | Conjunctive gates; default deny; gate-matrix tests |
| Duplicate live submits on retry/crash | Client order id + reconcile before new exposure |
| Local portfolio drift vs broker | Fail-closed mismatch abort (M13.3) |
| Venue API complexity / geo | Resolve Decision 5; sandbox-first; mocked HTTP in CI |
| Shadow mode secretly ordering | Hard proof shadow never hits real-money `place_order` |
| Scope creep into M14 trial ops | Explicit non-goals: no cancel-all trial protocol in M13 |

---

## 5. Parent LIVE progression (context)

From `MILESTONE_11_14_DESIGN_SPEC.md` §8:

| Stage | Meaning | Prerequisites / gates |
|---|---|---|
| LIVE **code path exists** | Adapter merged, not enabled | **M13.1** |
| LIVE **sandbox/test env** | Sandbox orders work in tests/harness | **M13.1** (+ optional CI secret) |
| LIVE **technically enabled** | All conjunctive gates can open | **M13.2** + **M13.3** |
| LIVE **shadow validation** | Signals compared without real money (or sandbox only) | **M13.4** |
| **Small controlled real-money trial** | Tiny limits + kill + alerts + signed checklist | **M14.4** after M12.4 soak evidence + strategy validation pack |

Parent integration sketch:

```text
Live gates (M13.2) → factory may build live BrokerOrderExecutor
  → run_once (unchanged booking mapper)
  → reconcile before new exposure (M13.3)
Shadow mode (M13.4) must not place real-money orders
```

---

## 6. Proposed M13 design overview

**Milestone title:** Live broker adapter behind hard gates  
**Outcome:** LIVE *code path* and *sandbox/gated* enablement exist; real-money trial remains **M14**.  
**Workflow:** Design approve → implement M13.1 → STOP audit → commit → … → M13.4 final audit → closure commit discipline as prior milestones. No push/PR unless requested.

**Likely to change/add (parent + post-M12 refinement):**
- New broker implementation (concrete venue TBD — Decision 5 / V1)
- `runtime/mode_policy.py` / `runtime/factory.py` — carefully expand allowed live wiring
- Reconciliation service (broker positions/orders vs local portfolio)
- Client order id / idempotency on `BrokerOrderRequest`
- Settings for live flags, base URL, keys
- Contract tests against broker **sandbox** if available
- Shadow executor / compare logs
- README + `MILESTONE_13_SUMMARY.md` at closure

**Must remain unchanged unless gated:**
- Default `trading_mode=paper`
- Paper and backtest path behavior
- M10 Option A (backtest still no live executor)
- `SessionRunner` fail-closed semantics
- `PaperOperator` paper-only requirement

---

## 7. Sub-milestones

### M13.1 — Broker adapter + sandbox contract tests

**Objective:** Implement **one** live-capable broker adapter against **sandbox/paper-account** API; production/live money still unreachable.

**Expected files/components:**
- New module under `broker_interface/` (e.g. venue-specific adapter implementing `Broker`)
- Possibly extend `Broker` / `BrokerOrderRequest` minimally if sandbox needs order-id/status APIs (prefer additive)
- `config/settings.py`, `.env.example` — sandbox URL, keys (env-only)
- `broker_interface/module.py` registration only if needed without enabling live factory wiring
- Tests: mocked HTTP contract tests; optional marked sandbox integration (not required in default CI)

**Dependencies:** M11–M12 complete; Decision 5 / V1 (venue) resolved.

**Safety constraints:** Credentials never logged; sandbox URL default; live factory path still closed.

**Tests required:**
- Auth/header construction without logging secrets  
- `place_order` / status parse happy + reject paths (mocked)  
- Connection/status mapping to `BrokerStatus` / `ExecutionResult`  
- Regression: paper factory still PaperBroker-only  

**Acceptance criteria:**
- Adapter works in mocked sandbox harness  
- Default app still cannot build live executor  
- Credentials redacted; sandbox URL is the documented default  
- Full suite green  

**Explicit non-goals:**
- Opening factory/`mode_policy` live path (M13.2)  
- Reconciliation / idempotency completeness (M13.3)  
- Shadow mode (M13.4)  
- Real-money production URL as default  
- Multi-broker  

**Stop/review point:** External review and approve M13.1 before starting M13.2.

---

### M13.2 — Hard live enablement gates

**Objective:** Conjunctive fail-closed gates so live wiring is possible **only** when every gate is explicitly set.

**Proposed gate set (subject to Decision L1 approval):**
1. `trading_mode=live`  
2. Explicit enable flag (e.g. `LIVE_TRADING_ENABLED=true`)  
3. Confirm token / typed confirmation (settings or CLI)  
4. Live adapter selected and constructed  
5. Not backtest context / not `run-backtest`  
6. (Recommended) Explicit sandbox vs production URL allowlist check  

**Expected files/components:**
- `runtime/mode_policy.py`, `runtime/factory.py`  
- `config/settings.py`, `.env.example`, README safety docs  
- `main.py` — CLI refusal by default; no silent live  
- Tests: full gate combination matrix  

**Dependencies:** M13.1.

**Safety constraints:** Any missing gate → fail-closed; paper unchanged.

**Tests required:**
- Each missing gate → refuse; paper/dry-run unchanged  
- Live context + PaperBroker mismatch still fail-closed  
- Backtest path cannot obtain live executor  
- `run-paper-operator` still refuses non-paper settings  

**Acceptance criteria:**
- Live unreachable unless **all** gates true  
- Defaults keep today’s deny behavior  
- Documented gate matrix  

**Explicit non-goals:**
- Reconciliation (M13.3)  
- Shadow (M13.4)  
- Unattended live operator  
- Weakening paper defaults  

**Stop/review point:** External review and approve M13.2 before starting M13.3.

---

### M13.3 — Idempotency + reconciliation basics

**Objective:** Prevent duplicate live submits; reconcile broker vs local portfolio/orders before new exposure; abort on mismatch.

**Expected files/components:**
- `broker_interface/orders.py` — client order id / idempotency field(s)  
- `runtime/broker_executor.py` — propagate stable client ids  
- New reconciler module (e.g. `runtime/reconcile.py` or `broker_interface/reconcile.py`)  
- Wire reconcile at cycle start for live-gated path only  
- Alerts/logging on mismatch (console pattern; no new channels required)  
- Tests: duplicate submit, mismatch abort, recovery/no silent overwrite  

**Dependencies:** M13.2.

**Safety constraints:** Mismatch → kill new orders; alert; no silent portfolio invention.

**Tests required:**
- Same client id → no double exposure  
- Broker/local cash/position/open-order mismatch → fail-closed (no new orders)  
- Paper path unaffected when reconcile not engaged  
- Persistence/E1 paper operator regressions still green  

**Acceptance criteria:**
- Documented reconcile rules  
- Tests prove fail-closed mismatch + idempotent submit behavior  
- LIVE still gated by M13.2  

**Explicit non-goals:**
- Full cancel-all emergency wiring (M14.1)  
- Perfect multi-leg / advanced order types  
- Shadow mode (M13.4)  
- Changing M12 paper state schema unless strictly required (prefer live-specific keys)  

**Stop/review point:** External review and approve M13.3 before starting M13.4.

---

### M13.4 — Live shadow mode + M13 closure

**Objective:** Run live **signal/intent path** without placing real-money orders (sandbox-only or no-submit shadow), while paper/dry-run can still execute for comparison; close Milestone 13.

**Expected files/components:**
- Shadow executor or factory mode (`shadow` / flag)  
- Runtime/CLI wiring + structured compare logs  
- README + `MILESTONE_13_SUMMARY.md`  
- Tests proving shadow never calls real-money `place_order`  
- M13 final audit (untracked) then authorized commit of summary/docs/code  

**Dependencies:** M13.3.

**Safety constraints:** Shadow cannot place real-money orders.

**Tests required:**
- Shadow never hits production live `place_order`  
- Optional: intents logged/compared vs paper execution  
- Full regression: paper, operator, backtest, gate matrix  

**Acceptance criteria:**
- Shadow checkpoint unlocked (LIVE progression table)  
- M13 summary documents gates, adapter, reconcile, shadow  
- Final audit APPROVED before closure commit  
- Still **no** M14 trial enablement  

**Explicit non-goals:**
- Real-money trial / checklist enforcement (M14)  
- Email/webhook alert channels (M14.2)  
- Kill→broker cancel-all (M14.1)  
- Strategy profitability certification  

**Stop/review point:** **M13 final audit** (live code path exists + gated) before closure commit/push.

---

## 8. Safety architecture M13 adds (parent + refinement)

| Control | M13 definition |
|---|---|
| Paper/live isolation | Multi-gate conjunctive enablement; default deny |
| Secrets/API keys | Env-only; redacted logs; no commit of secrets |
| Max exposure/notional | Minimal hard live caps in M13 (see Decision L6); fuller trial caps in M14 |
| Duplicate-order | Client order idempotency keys |
| Reconciliation | Broker vs local mismatch → abort new orders (abort-only in M13; see L4) |
| Retry/backoff | Transient broker errors only with idempotent keys |
| Alerts | CRITICAL on gate bypass attempts, reconcile fail (console pattern in M13) |
| Kill switch | Inherit M12; wire toward cancel-all in M14 |
| Stale-data / hours | Inherit M11 |
| Unattended operator | Remains paper-only through M13 |

---

## 9. Milestone-level non-goals (all of M13)

- Multi-broker  
- Production real-money trial / M14 runbooks  
- Unattended live via `run-paper-operator`  
- Removing or defaulting away from paper mode  
- Changing SessionRunner A2/fail-closed split  
- Weakening M10/M11/M12 paper safety  
- Strategy research platform / profitability gates as code blockers for M13 start (evidence pack remains M14)  
- Full advanced order types beyond MARKET (unless sandbox requires)  

---

## 10. Architectural decisions requiring approval before implementation

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **D5 / V1** | Live broker venue | Alpaca (settings already hint paper-api) vs IBKR/other | **Must choose before M13.1**; recommend Alpaca paper/sandbox if geo/API acceptable |
| **L1** | Exact conjunctive gate list | Minimal (`mode`+flag) vs richer (+confirm token + URL allowlist + not-backtest) | **Richer set** as sketched in M13.2 |
| **L2** | Where live is allowed in CLI | `run-once` only vs also `run-session` vs never operator | **`run-once` (+ optional supervised `run-session`) only; `run-paper-operator` remains paper-only through M13** |
| **L3** | Shadow semantics | No-submit log-only vs sandbox-submit-only | **No-submit / sandbox-only**; never production money in shadow |
| **L4** | Reconcile authority | Broker wins (overwrite local) vs abort-only | **Abort-only in M13** (safer; overwrite is M14+ if ever) |
| **L5** | CI network policy | Mock HTTP only vs optional marked sandbox job | **Mock HTTP default**; optional sandbox tests marked/skipped without secrets |
| **L6** | Live hard caps in M13 vs M14 | Notional/day caps in M13.2 vs defer to M14.1 | Parent puts max exposure in M13 safety table — **recommend minimal live caps in M13.2/M13.3**, full trial limits in M14 |

Parent Decision 5 (from M11–M14 spec) for reference:

| | |
|---|---|
| **Option A** | Alpaca (or similar) paper/live API — settings already hint `paper-api.example.com`. |
| **Option B** | Another broker (IBKR, etc.). |
| **Recommended (parent)** | **Defer choice to pre-M13.1**; not required for M11. |
| **This review** | Choice is now **blocking** for M13.1 start (V1). |

---

## 11. Testing strategy (parent + M13)

| Layer | M13 expectation |
|---|---|
| Unit | Adapter parsing, gates matrix, idempotency keys |
| Integration | Sandbox harness (mocked HTTP primary) |
| Regression | Paper / operator / backtest / SessionRunner / M10–M12 |
| Safety | Gate matrix; shadow never real-money `place_order` |
| Failure-injection | Dup order; reconcile mismatch |
| Network/provider | Mock HTTP in default CI |
| Broker contract | Sandbox (optional marked) |
| Soak | Optional; not required like M12.4 paper soak |

Preserve M1–M12: every milestone closure runs the **full suite**.

---

## 12. Proposed post-approval sequence

1. Approve this M13 design + decisions **V1, L1–L6**.  
2. Keep this file as the design-review artifact (commit eligibility TBD by external process; do not implement yet).  
3. Implement **M13.1 only** → audit → local commit.  
4. Repeat for M13.2 → M13.3 → M13.4 closure.  
5. Do **not** start M14 until M13 final audit passes.  
6. No push / PR unless explicitly requested.  
7. Do not batch multiple sub-milestones in one commit.

---

## 13. Roadmap table (M13 rows)

| Sub-milestone | Objective | PAPER capability unlocked | LIVE relevance | Main safety gate |
|---|---|---|---|---|
| M13.1 | Live adapter + sandbox | — | Code path / sandbox | Sandbox-only default |
| M13.2 | Multi-gate live enable | — | Technically enabled | Conjunctive gates |
| M13.3 | Idempotency + reconcile | — | Safe live ops | Mismatch abort |
| M13.4 | Shadow mode + closure | — | Shadow validation | No real orders in shadow |

---

## 14. DESIGN READINESS VERDICT

# M13 DESIGN NEEDS DECISIONS / REVIEW

**Must resolve before starting M13.1 implementation:**

1. **V1 / Decision 5** — Live broker venue  
2. **L1** — Exact conjunctive gate list  
3. **L2** — Which CLIs may use live (`run-paper-operator` must stay paper-only)  
4. **L3** — Shadow semantics  
5. **L4** — Reconcile abort-only vs overwrite  
6. **L5** — CI mock vs optional sandbox job  
7. **L6** — Minimal live caps in M13 vs defer entirely to M14  

**Inspection conclusion:** Parent `MILESTONE_11_14_DESIGN_SPEC.md` already owns M13. Post-M12 closure satisfies paper prerequisites. This review restates and refines M13.1–M13.4 for the current architecture without changing M12 closure.

**STOP.** Awaiting external review/approval. Do not begin implementation. Do not modify source/tests for M13 until instructed. Do not commit/push/create a PR for implementation from this design phase.
