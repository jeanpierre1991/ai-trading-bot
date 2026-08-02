# M14 DESIGN REVIEW ONLY — Controlled Live Trial Readiness

**Status:** DESIGN ONLY — no implementation in this step  
**Baseline:** Milestone 13 CLOSED (`M13: close milestone 13` / HEAD at push: `4702ab212b5a6101ef01b4a3c1f04714fc5df20f`)  
**Canonical parent roadmap:** `MILESTONE_11_14_DESIGN_SPEC.md` § M14  
**Post-M13 invariants preserved:** G1–G10, registry-only sandbox construction, abort-only reconcile, no-submit shadow, paper/operator/backtest isolation, SessionRunner fail-closed  
**Explicit non-actions for this step:** No source/test changes. No stage/commit/push/PR. No M14 implementation until this design is externally approved.

---

## 1. Objective of Milestone 14

Operationalize a **small, human-approved, fail-closed real-money trial** on top of the M13 live technical path.

M13 made supervised LIVE *technically possible* (sandbox adapter, gates, idempotency, reconcile, shadow).  
M14 makes a *trial operationally acceptable*:

1. **Emergency stop** that halts new exposure and best-effort cancels open broker orders  
2. **Trial-specific hard limits** (tiny notional / loss / trade / symbol constraints)  
3. **Reliable CRITICAL alert channels** (webhook and/or email) without crashing the cycle  
4. **Evidence + checklist gate** binding M10/M12/M13 proof before production trial wiring can open  
5. **Controlled protocol closure** with documentation, sandbox dry-run of the trial procedure, and a final audit before any human-approved real-money start  

**Outcome:** unlock the LIVE progression checkpoint “small controlled real-money trial” by **process + code gates**, not by silently enabling `LIVE_PRODUCTION`.

---

## 2. Functional requirements

### 2.1 Must deliver

| ID | Requirement |
|---|---|
| FR-1 | Kill / emergency stop halts **new** live exposure immediately (local fail-closed). |
| FR-2 | Kill triggers **best-effort broker cancel-all** (or cancel-open-orders) via a broker-agnostic port; partial cancel failure must **alert** and still keep local halt engaged. |
| FR-3 | Trial hard limits enforce max order notional, max gross/day exposure, max trades, max daily loss, and optional symbol allowlist — stricter than or additive to M13.2 G10. |
| FR-4 | `LIVE_PRODUCTION` remains **default-denied**; enabling it requires M13 gates **plus** M14 trial gates (checklist, confirm tokens, endpoint allowlist, tiny limits). |
| FR-5 | CRITICAL/ERROR live events emit through webhook and/or email notifiers using existing settings fields where possible; notifier failure must not crash the trading cycle. |
| FR-6 | Trial start is refused if the evidence/checklist gate is incomplete (M12 soak, M13 sandbox/shadow evidence, strategy validation pack, human sign-off fields). |
| FR-7 | Sandbox protocol dry-run exercises kill → cancel-all → halt without requiring production money. |
| FR-8 | Paper, dry-run, backtest, paper-operator, and shadow behaviors remain unchanged unless explicitly extended for alert/kill wiring in a non-production-safe way. |
| FR-9 | Secrets remain env-only; never logged; trial credentials preferably segregated from paper keys. |
| FR-10 | Full repository suite remains green; M13.1–M13.4 regressions remain green. |

### 2.2 Explicit non-goals

- Scaling capital / multi-strategy live portfolio management  
- Unattended large LIVE or autonomous overnight production trading  
- Automatic portfolio repair / compensating trades (M13 abort-only remains default)  
- Strategy profitability certification as a code blocker beyond the evidence checklist  
- Implementing IBKR / TradeStation / Webull adapters (ports must stay ready; Alpaca remains Adapter #1)  
- Replacing M13 shadow with a new execution mode  
- Weakening G1–G10 or registry-only construction  

---

## 3. Technical architecture

### 3.1 Layering on M13

```text
M13 (intact)
  G1–G10 LiveEnablementAuthority
  registry BROKER_SANDBOX / empty LIVE_PRODUCTION
  IdempotentLiveExecutor + ledger + abort-only reconcile
  ShadowExecutor (no-submit)

M14 (additive)
  TrialEnablementAuthority (conjunctive; includes M13 + checklist + trial caps)
  EmergencyStopController
    → local halt latch (durable)
    → CancelCapableBroker.cancel_all_open_orders() best-effort
    → CRITICAL alert
  TrialLimitGuard (stricter caps; symbol allowlist; max loss)
  Alert channels: WebhookNotifier / EmailNotifier (mocked in tests)
  EvidenceChecklistGate (fail closed if incomplete)
  CLI / factory: production path only when TrialEnablement authorizes
```

### 3.2 Broker-agnostic ports (new / additive)

| Port | Purpose |
|---|---|
| `CancelCapableBroker` | `cancel_all_open_orders() -> CancelAllResult` (canceled ids, failures, partial flag) |
| Existing `ReconcileCapableBroker` | Remains for observe/diff; M14 does **not** switch to silent overwrite reconcile by default |

Venue-specific cancel mapping lives **only** in adapters (Alpaca first). Core emergency-stop code must not import Alpaca types.

### 3.3 Enablement model

Do **not** weaken G9’s current hard-deny without a new conjunctive layer:

| Layer | Role |
|---|---|
| M13 `evaluate_live_enablement` | Still required for any live submit path |
| M14 `evaluate_trial_enablement` | Additional gates for `broker_endpoint_class=live_production` only |
| Checklist gate | Blocks trial authorization if evidence incomplete |
| Human confirm tokens | Distinct from M13 sandbox confirm phrase |

Recommended production confirmation phrase (exact string TBD in implementation approval): a dedicated `LIVE_TRIAL_CONFIRM_TOKEN` separate from `LIVE_CONFIRM_TOKEN`.

### 3.4 Emergency stop semantics

```text
kill engaged
  → durable local HALT (no new place_order / no new intents that open exposure)
  → best-effort cancel_all_open_orders()
  → if cancel partial/fails → CRITICAL alert + remain halted
  → never auto-resume without explicit human clear + checklist re-check
```

Kill always wins over strategy signals. Resume is out-of-band / supervised CLI, not automatic.

### 3.5 Alerts

Reuse M8.5 pattern: alert send failures are logged; they must not abort an already-halted emergency path incorrectly, and must not prevent halt engagement.

Minimum CRITICAL events:

- Kill engaged  
- Cancel-all partial/total failure  
- Trial limit breach attempt  
- Reconcile ABORT during trial  
- Checklist/gate bypass attempt  
- Production authorization denied / suspicious config  

### 3.6 Evidence checklist (logical artifacts)

| Evidence | Source |
|---|---|
| Backtest isolation / strategy pack | M10 + strategy validation pack |
| Paper soak | M12.4 |
| Sandbox live gated path | M13.2/M13.3 |
| Shadow intent validation | M13.4 JSONL samples |
| Kill/cancel dry-run in sandbox | M14.1/M14.4 protocol |
| Human sign-off | Checklist fields / signed runbook |

---

## 4. Files that will be created or modified

> Exact paths are design intent; implementation may rename slightly if review requires — core boundaries must hold.

### 4.1 Likely created

| Path | Role |
|---|---|
| `runtime/trial_enablement.py` | Conjunctive M14 trial gates (production path) |
| `runtime/emergency_stop.py` | Halt latch + cancel orchestration |
| `runtime/trial_limits.py` | Trial cap evaluation (stricter than G10) |
| `runtime/evidence_checklist.py` | Fail-closed checklist loader/validator |
| `broker_interface/cancel_port.py` | `CancelCapableBroker` Protocol + result DTOs |
| `alerts/webhook.py` / `alerts/email.py` | Notifier implementations (or under `alerts/`) |
| `docs/runbooks/LIVE_TRIAL_RUNBOOK.md` | Operator runbook |
| `tests/runtime/test_m14_*.py` | Gate, kill, limits, checklist, alert tests |
| `tests/broker_interface/test_alpaca_cancel.py` | Adapter cancel mapping (mocked) |
| `MILESTONE_14_1..4_*` design/audit docs as sub-milestones proceed |
| `MILESTONE_14_SUMMARY.md` | Closure summary (end of M14) |

### 4.2 Likely modified

| Path | Role |
|---|---|
| `broker_interface/alpaca/adapter.py` | Implement cancel-all / open-order cancel mapping |
| `broker_interface/adapter_registry.py` | Cautious production adapter registration **only** behind trial gates |
| `runtime/live_enablement.py` | Keep G9 deny by default; optional hand-off hook to trial authority (no silent open) |
| `runtime/live_caps.py` / factory | Compose trial limits with existing caps |
| `runtime/factory.py` | Wire emergency stop + trial path; still reject injected brokers |
| `runtime/mode_policy.py` | Reject invalid trial/production combinations |
| `runtime/trading_runtime.py` | Honor halt latch; emit CRITICAL alerts on trial aborts |
| `config/settings.py` / `.env.example` | Trial limits, checklist path, trial confirm token, alert channel config |
| `main.py` | Supervised trial CLI surfaces (explicit flags; no operator/backtest production) |
| `alerts/notifier.py` (or registry) | Plumb webhook/email implementations |

### 4.3 Must not change behavior of

- `run-paper-operator` paper-only contract  
- Backtest isolation (M10)  
- Shadow no-submit semantics (M13.4)  
- Abort-only reconcile default (unless a separately approved M14 decision explicitly introduces a narrow, audited exception — **not recommended for M14.1**)  

---

## 5. Step-by-step implementation plan

### Phase 0 — Design approval (this document)

- External approval of M14 design and sub-milestone sequencing  
- Freeze non-goals and LIVE_PRODUCTION policy  

### M14.1 — Emergency stop + live hard limits

1. Add `CancelCapableBroker` + Alpaca mocked cancel tests  
2. Implement durable emergency halt latch  
3. Wire kill switch → halt + best-effort cancel-all + CRITICAL alert hook (console acceptable until M14.2)  
4. Add trial limit settings + enforcement before `place_order`  
5. Sandbox-only dry-run tests (no production enablement yet)  
6. **STOP** for M14.1 audit  

### M14.2 — Alert channels for live events

1. Implement webhook notifier (mocked HTTP)  
2. Implement email notifier (mocked SMTP/API)  
3. Map CRITICAL events from kill/limits/reconcile/gate denials  
4. Preserve “alert failure must not crash cycle”  
5. **STOP** for M14.2 audit  

### M14.3 — Trial runbook + evidence gate

1. Author LIVE trial runbook  
2. Implement checklist schema + fail-closed loader  
3. Bind checklist into trial enablement (blocks `LIVE_PRODUCTION`)  
4. Unit/integration: incomplete checklist ⇒ deny  
5. **STOP** for M14.3 audit  

### M14.4 — Controlled trial protocol closure

1. Sandbox protocol dry-run (kill → cancel → halt → alert)  
2. Optional narrowly gated production registry entry **still requiring** human process after final audit  
3. Full suite + M13 regressions  
4. `MILESTONE_14_SUMMARY.md` + M14 final audit  
5. **Human approval** required before any real-money start (process gate, not code alone)  

**Rule:** Do not open production trading in code before M14.3 checklist exists and M14.4 audit is APPROVED.

---

## 6. Design decisions and rationale

| ID | Decision | Options | Recommendation | Rationale |
|---|---|---|---|---|
| **T1** | When can `LIVE_PRODUCTION` authorize? | (a) never in M14 code (b) only after checklist + trial gates + audit | **(b)** with default deny | Parent roadmap requires a trial checkpoint; code must still fail closed without checklist |
| **T2** | Cancel-all scope | (a) cancel all open orders (b) cancel session-tagged only | **(a)** for emergency; document blast radius | Emergency stop must be simple and aggressive; session tagging can refine later |
| **T3** | Halt durability | (a) in-process only (b) durable file latch | **(b)** | Survive process restart during an incident |
| **T4** | Reconcile policy in trial | (a) keep abort-only (b) broker-wins overwrite | **(a)** for M14 | Overwrite is higher risk; abort-only already proven in M13.3 |
| **T5** | Alert vs halt coupling | (a) halt depends on alert success (b) halt independent | **(b)** | Safety must not depend on email/webhook availability |
| **T6** | Trial limits vs G10 | (a) replace G10 (b) additive stricter envelope | **(b)** | Preserve M13 caps; trial adds tinier ceilings |
| **T7** | CLI surface for production trial | (a) reuse `--live` (b) explicit `--live-trial` / production flag | Prefer **explicit trial flag** or endpoint-class driven path with louder confirmations | Reduce accidental production submits from sandbox muscle memory |
| **T8** | Paper-operator production | Forbid | **Forbid** | G8 spirit extended; unattended paper must never become production |
| **T9** | Multi-broker in M14 | Implement all vs ports only | **Ports + Alpaca only** | Matches Adapter #1 reality; avoids scope explosion |
| **T10** | Sub-milestone sequencing | Big-bang vs 14.1→14.4 | **14.1→14.4 with audits** | Same successful M11–M13 workflow |

---

## 7. Risks and edge cases

| Risk / edge case | Mitigation |
|---|---|
| Cancel-all partially succeeds; residual open orders | Remain halted; CRITICAL alert; reconcile ABORT on next cycle; manual broker check in runbook |
| Cancel-all API timeout / 5xx | Best-effort retries with idempotent semantics where venue allows; still halt locally |
| Kill file/env races with in-flight `place_order` | Pre-submit kill check in cap/trial guard; post-submit UNKNOWN/reconcile path from M13.3 |
| Operator enables production without checklist | Trial enablement fail closed; tests for missing checklist |
| Accidental production using paper keys/host mixups | Endpoint class + host allowlist + segregated credential settings; refuse mismatched host/class |
| Alert channel outage during incident | Halt/cancel do not depend on alerts; log alert failures |
| Trial limits weaker than sandbox G10 by misconfig | Enforce `trial_limit <= live_limit` where both set; fail closed on inversion |
| Shadow/live/trial flag confusion | Mutual exclusion in CLI; mode_policy rejects hybrids |
| Durable halt forgotten after incident | Runbook clear-halt procedure; require checklist re-validation to resume |
| Venue cancel cancels unrelated account orders | Document account isolation requirement; prefer dedicated trial account |
| Secrets in checklist/evidence files | Checklist stores paths/hashes/sign-off, never API secrets |
| Scope creep into auto-repair | Explicit non-goal; abort-only remains |

---

## 8. Test strategy

### 8.1 Unit / contract

- Trial enablement matrix (each missing gate denies)  
- Checklist incomplete ⇒ deny production  
- Trial limits reject oversized / wrong-symbol / max-loss breach **before** broker submit (`place_calls==0`)  
- Emergency halt blocks new submits across restart (durable latch)  
- `CancelCapableBroker` adapter mapping (mocked HTTP)  
- Notifiers: success + network failure does not raise into cycle  
- Secrets never appear in deny reasons / alert payloads / logs  

### 8.2 Integration

- Sandbox dry-run: kill → cancel-all → halt → CRITICAL alert emitted (mocked)  
- Live sandbox path still works under M13 gates (no production)  
- Production path denied without checklist even if M13-like settings present  
- `run-paper-operator` / `run-backtest` cannot authorize trial/production  
- Shadow still zero `place_order`  

### 8.3 Regression

- M13.1 Alpaca contract suite  
- M13.2 live enablement suite  
- M13.3 reconcile/idempotency suite  
- M13.4 shadow suite  
- Full repository suite  

### 8.4 Protocol dry-run (M14.4)

Scripted/supervised sandbox procedure validating runbook steps without real money.

---

## 9. Acceptance criteria

Milestone 14 is complete only when **all** are true:

1. M14.1–M14.4 implemented per approved design (or explicitly waived by external decision)  
2. Each sub-milestone final audit **APPROVED**  
3. Emergency stop: new exposure halted + best-effort cancel-all tested (including partial failure)  
4. Trial hard limits enforced pre-submit  
5. CRITICAL alert channels implemented and tested with mocks  
6. Evidence checklist fail-closed for production/trial authorization  
7. `LIVE_PRODUCTION` cannot authorize without M13 gates **and** M14 trial gates  
8. Paper/backtest/operator/shadow invariants unchanged  
9. Broker-agnostic cancel/trial core (no Alpaca types in core emergency/trial modules)  
10. Full suite green + M13 regressions green  
11. `MILESTONE_14_SUMMARY.md` written  
12. M14 final audit **APPROVED**  
13. Human process approval recorded before any real-money trial start (outside pure code)  

---

## 10. Rollback strategy if needed

| Scenario | Rollback action |
|---|---|
| M14.1 kill/cancel proves unsafe in sandbox | Disable trial wiring via config defaults; keep M13 sandbox `--live` path; revert M14 commits or feature-flag off cancel/halt integration |
| Alert channels unstable | Fall back to console notifier; do not block halt/cancel |
| Checklist/gate too brittle for ops | Tighten docs first; do **not** bypass fail-closed to “keep moving” |
| Production registry mistake | Immediate deny: empty production allowlist + G9/trial deny; rotate keys; halt latch on |
| Partial production incident | Kill → cancel-all → halt; do not auto-resume; restore to last known-good M13-only commit tag if needed |
| Need to abandon M14 mid-stream | M13 remains the supported supervised sandbox/shadow ceiling; production stays unreachable |

**Rollback principles:**

- Prefer **config/default-deny** rollback over complex data migrations  
- Never leave `LIVE_PRODUCTION` authorized in defaults  
- Durable halt should remain engageable even if M14 features are disabled  
- Unrelated M11/M12 leftover Markdown must not be mixed into M14 rollback commits  

---

## Unresolved items requiring external approval

1. Exact production confirm token string and whether CLI uses `--live-trial` vs endpoint-class-only control  
2. Whether M14.4 may register any production adapter in-tree before the first human-approved trial date  
3. Cancel-all blast radius: account-wide vs client-order-id/session filtered (recommendation: account-wide for emergency, dedicated trial account required)  
4. Whether gross notional becomes mandatory in trial (recommended: **yes** for production trial)  
5. Email provider choice (SMTP vs API) for M14.2  

---

## DESIGN VERDICT

**READY FOR EXTERNAL APPROVAL** as the Milestone 14 design package: controlled trial readiness via emergency stop + cancel-all, stricter trial limits, CRITICAL alerts, evidence checklist gating, and protocol closure — layered on intact M13 sandbox/shadow architecture without opening unattended real-money trading by default.

---

## WAITING FOR EXTERNAL APPROVAL

Do not implement M14 until this design review is externally approved.  
Do not weaken M13 invariants.  
Do not enable `LIVE_PRODUCTION` in defaults.
