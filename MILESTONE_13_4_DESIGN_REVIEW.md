# M13.4 DESIGN REVIEW ONLY — Shadow Mode + Milestone 13 Closure

**Status:** DESIGN ONLY — no implementation in this step  
**Baseline (M13.3 CLOSED, local):** `89998a9e8b3e7c69860a3b8c1dbb028e69345c9d`  
**Inspection:** Actual post-M13.3 repository (factory, mode_policy, live gates, ledger, reconcile, CLI, PaperOperator, SessionRunner, adapter registry)  
**Explicit non-actions:** No source/test changes. No stage/commit/push/PR. No M13.4 implementation. No M14.

---

## 0. Current-state findings (post-M13.3)

| Area | State today |
|---|---|
| `TradingMode` | `PAPER`, `LIVE`, `BACKTEST` only (`core/types.py`) |
| Factory `ExecutionBackend` | `"dry_run" \| "paper" \| "live"` only (`runtime/factory.py`) |
| Live path | G1–G10 → registry sandbox → `LiveCapGuardBroker` → `IdempotentLiveExecutor` + durable ledger + abort-only reconcile |
| CLI execution flags | Mutually exclusive `--dry-run` / `--paper` / `--live` on `run-once` / `run-session`; operator has no `--live`; backtest has no execution flags |
| Shadow implementation | **None.** Only a mode_policy rejection test for `settings_trading_mode="shadow"` and design-doc placeholders |
| LIVE_PRODUCTION | Hard-denied (G9); registry approved set empty |
| PaperOperator | Paper/dry-run only; fail-closed; G8 denies live |
| M13.3 ledger / reconcile | Required only when `execution="live"` |

Parent intent (`MILESTONE_13_DESIGN_REVIEW.md` §M13.4 / Decision L3): shadow validates the live **signal/intent** path without real-money orders; then close Milestone 13.

---

## 1. SHADOW MODE SEMANTICS

### 1.1 Exact definition

**SHADOW** is a supervised validation mode that:

1. Runs the **same upstream decision path** used for gated live validation: market-data gates (M11) → strategy signal → risk gate → `TradeIntent` construction (side, symbol, qty, order type, notional inputs).
2. Records a structured **hypothetical execution decision** for audit/M14 evidence.
3. **MUST NOT** place any real-money order.
4. **MUST NOT** enable `LIVE_PRODUCTION`.
5. **MUST NOT** silently become unattended LIVE or a paper/live hybrid.

SHADOW is **not** PAPER (local `PaperBroker` fills), **not** LIVE sandbox submit (`execution="live"`), and **not** BACKTEST.

### 1.2 Options evaluated

| Option | Meaning | Pros | Cons |
|---|---|---|---|
| **A. No-submit / log-only** | Full signal→risk→intent path; executor never calls `place_order` on any venue | Minimal surface; zero broker order mutation; no live ledger writes; simplest proof tests | Does not exercise adapter submit/idempotency/caps under load; no sandbox fill comparison |
| **B. Sandbox-submit shadow** | Same path then submits to BROKER_SANDBOX via M13.2/M13.3 stack | Stronger end-to-end venue validation | Overlaps heavily with `--live` sandbox; mutates sandbox + ledger; higher blast radius if wiring mistakes blur modes |
| **C. Both, explicit submodes** | `shadow_no_submit` + `shadow_sandbox` | Completeness | Scope creep for M13.4; more CLI/mode_policy matrix; delays M13 closure |

### 1.3 Recommended safest minimal M13.4 design

**Recommend A — no-submit / log-only SHADOW as the only M13.4 shadow semantics.**

Rationale:

- Parent Decision L3 allows “no-submit / sandbox-only”; safest minimal is **no-submit**.
- Supervised sandbox order placement is already covered by M13.2/M13.3 `--live` (BROKER_SANDBOX). Shadow’s unique job is proving the **live-intent path** without venue mutation.
- Avoids durable live-order ledger writes and C1 consumption when nothing is submitted.
- Cleanest proof: shadow executor/broker seam makes **zero** `place_order` calls.

Optional later (post-M13 / M14 prep), not M13.4: a separately gated “sandbox compare” tool. Do **not** ship both in M13.4.

---

## 2. BROKER-AGNOSTIC ARCHITECTURE

### 2.1 Core rule

Shadow core modules must import **no** Alpaca (or future IBKR/TradeStation/Webull) types.

Venue-specific behavior stays inside adapters — same boundary as M13.1–M13.3 (`ReconcileCapableBroker`, snapshots, registry builders).

### 2.2 Recommended module sketch (implementation later)

```text
runtime/shadow_executor.py     # OrderExecutor: records hypothetical decision; never place_order
runtime/shadow_record.py       # Typed shadow artifact / JSONL row schema (broker-agnostic)
runtime/shadow_compare.py      # Optional pure helpers: notional, slippage_bps, spread math
# NO shadow logic inside broker_interface/alpaca/*
```

### 2.3 Future brokers

The same `execution="shadow"` factory path + `ShadowExecutor` works for any adapter id. M13.4 does **not** implement IBKR/TradeStation/Webull; it only preserves the seam so those adapters plug into LIVE/sandbox later without rewriting shadow.

If shadow optionally *reads* quotes for `arrival_price` / `spread_at_decision`, use the existing market-data / quote abstractions — not Alpaca REST — unless an explicit broker-agnostic quote port already exists for the chosen source.

---

## 3. EXECUTION PATH

### 3.1 Recommendation: separate factory execution backend (not a TradingMode)

| Mechanism | Recommendation | Why |
|---|---|---|
| New `TradingMode.SHADOW` | **No for M13.4** | Settings `trading_mode` today drives G1 (`== "live"`) and paper isolation (`== "paper"`). Adding SHADOW to the enum risks ambiguous settings/context coupling and expands pydantic `Literal` surface. |
| New factory `execution="shadow"` | **Yes** | Mirrors existing `dry_run` / `paper` / `live` composition root; CLI maps `--shadow` → `"shadow"` cleanly. |
| RuntimeContext.mode | Keep **`TradingMode.LIVE`** when shadowing the live-intent path, **or** introduce context-only distinction without settings enum — see Decision D-S1 | Shadow validates the **live signal/intention** path; context should not pretend to be PAPER. |
| mode_policy | New branch for `execution=="shadow"` | Must not fall through to paper or live branches ambiguously. |

### 3.2 Proposed wiring matrix

| Surface | `execution` | `settings.trading_mode` | `RuntimeContext.mode` | Executor | Broker submit |
|---|---|---|---|---|---|
| dry-run | `dry_run` | `paper` | `PAPER` | `DryRunExecutor` | none (local sim) |
| paper | `paper` | `paper` | `PAPER` | `BrokerOrderExecutor(PaperBroker)` | local paper only |
| live sandbox | `live` | `live` | `LIVE` | `IdempotentLiveExecutor` + caps + ledger | BROKER_SANDBOX only |
| **shadow (M13.4)** | **`shadow`** | **`live`** (see D-S1) | **`LIVE`** | **`ShadowExecutor`** | **never** |
| backtest | N/A (BacktestRunner) | `paper` | backtest isolation | DryRun/Commission | none |
| paper-operator | `dry_run`/`paper` only | `paper` | `PAPER` | paper path | paper only |

### 3.3 Interaction with live gates

SHADOW must **not** be a bypass around G1–G10.

Recommended (Decision D-S2):

- Factory construction of `execution="shadow"` requires the **same conjunctive live enablement** as live sandbox **except** it must refuse any path that would construct a submitting sandbox executor.
- Explicit shadow-specific asserts after Authority:
  - `broker_endpoint_class != live_production` (already G9)
  - Selected executor is `ShadowExecutor` (type check in mode_policy)
  - No `IdempotentLiveExecutor` / no `LiveCapGuardBroker.place_order` on the hot path
- Command allow-list: `run-once` / `run-session` only (same G6/G8 spirit as live).

Alternative considered: weaker gates for shadow (only `trading_mode=paper`). **Rejected** — that would mix shadow into paper semantics and fail requirement “same strategy/risk/order-intent path relevant to LIVE validation.”

### 3.4 Interaction with reconcile + ledger

| Component | Shadow behavior |
|---|---|
| Abort-only reconcile | **Do not require** for no-submit shadow (no local live ledger orders; no sandbox submits). See §6. |
| Live order ledger | **Do not require / do not write** for no-submit shadow. |
| Hard caps | **Evaluate hypothetically**; do not increment daily counter. See §7. |

### 3.5 mode_policy sketch

```text
if execution == "shadow":
  - re-evaluate live enablement (G1–G10) with shadow-aware LiveExecutionContext
    where execution field is "shadow" (extend G6 to allow execution in {live, shadow}
    for run-once/run-session + context LIVE — OR add G6b)
  - require isinstance(executor, ShadowExecutor)
  - deny PaperBroker / IdempotentLiveExecutor / any Broker.place_order wiring
  - deny command run-paper-operator / run-backtest
if execution == "live": unchanged M13.2/M13.3
if execution in {dry_run, paper}: unchanged paper policy
```

**Unresolved detail:** exact G6 extension vs new gate id — see §Unresolved.

---

## 4. REAL-MONEY SAFETY

M13.4 must prove (design → tests → audit):

| Proof | Mechanism |
|---|---|
| SHADOW cannot call production `place_order` | `ShadowExecutor` never holds a production broker; G9 + empty `LIVE_PRODUCTION` registry; unit test with hostile mock asserting zero `place_order` |
| SHADOW cannot enable LIVE_PRODUCTION | Authority still denies `broker_endpoint_class=live_production`; factory refuses construction |
| SHADOW cannot bypass G1–G10 | Factory + mode_policy both require Authority; tests flip each gate |
| SHADOW cannot convert to unattended LIVE | No auto-upgrade of `execution`; SessionRunner still fail-closed per cycle; no background daemon mode in M13.4 |
| `run-paper-operator` cannot become shadow/live hybrid | CLI: no `--shadow` / no `--live` on operator; operator type rejects `execution in {live, shadow}` |
| M14 remains only real-money trial boundary | Docs + G9 message unchanged; no trial checklist code in M13.4 |

**Hard invariant:** There is no code path where `execution="shadow"` constructs `construct_sandbox_broker` *for submit*, nor any production builder.

---

## 5. SIGNAL / INTENT COMPARISON + OBSERVABILITY

### 5.1 Structured shadow record (one cycle row)

Broker-agnostic JSON object (also JSONL line). Minimum fields:

| Field | Source |
|---|---|
| `schema_version` | constant `1` |
| `ts_utc` | cycle timestamp |
| `execution_mode` | `"shadow"` |
| `broker_adapter_id` | settings `broker_name` (informational; may be unused for submit) |
| `broker_endpoint_class` | settings (must be non-production) |
| `command` | `run-once` / `run-session` |
| `symbol` | context |
| `strategy_name` | signal / intent |
| `signal_action` | strategy output |
| `signal_confidence` | strategy output |
| `signal_price` | price used at signal time (bar close / quote policy — define in impl) |
| `risk_decision` | `allow` / `reject` + reason code/message |
| `requested_side` | intent side if formed |
| `order_type` | intent order type if formed |
| `intended_quantity` | intent qty if formed |
| `intended_notional` | qty × reference price if computable |
| `arrival_price` | reference/arrival price at intent time |
| `spread_at_decision` | optional if bid/ask available; else `null` |
| `hypothetical_execution_decision` | `would_submit` / `blocked_risk` / `blocked_cap` / `blocked_hours` / `blocked_freshness` / `hold` / `no_intent` |
| `cap_evaluation` | `{ "order_notional_ok": bool, "daily_count_ok": bool, "would_consume_slot": false }` |
| `latency_ms` | cycle stage timings (at least decision latency) |
| `submitted_price` | **always null** in M13.4 no-submit |
| `fill_price` | **always null** in M13.4 no-submit |
| `slippage_bps` | null unless both signal/arrival and a compare fill exist (paper compare optional) |
| `paper_comparison` | optional nested object if dual-path enabled (default **off** in M13.4) |
| `client_order_id_hypothetical` | optional UUIDv4 generated for audit correlation only — **not** written to live ledger |

### 5.2 Metrics reserved for future broker comparisons

Design the schema now so M14 / multi-broker trials can fill:

`signal_price`, `arrival_price`, `submitted_price`, `fill_price`, `spread_at_decision`, `latency_ms`, `slippage_bps`, `broker_adapter_id`, `execution_mode`.

M13.4 no-submit leaves submit/fill/slippage null unless an **optional** paper-side comparison is explicitly enabled (default off to avoid hybrid complexity).

### 5.3 Optional paper comparison (non-default)

Parent text mentioned “paper/dry-run can still execute for comparison.” For M13.4 safety:

- **Default:** single-path shadow (intent only, no paper fill in the same cycle).
- **Optional flag (Decision D-S3):** `--shadow-compare-paper` only if external approval wants it; runs a **separate** paper executor after shadow record is frozen — never feeds back into live ledger. Prefer deferring to keep M13.4 minimal.

**Recommendation:** defer dual-path compare; record enough fields that offline comparison against a separate paper run is possible.

---

## 6. SHADOW + RECONCILIATION

| Question | Recommendation |
|---|---|
| Run M13.3 broker reconciliation? | **No** for no-submit shadow |
| Read broker positions/open orders? | **No** by default (avoids coupling and false aborts) |
| Require empty/clean sandbox state? | **No** for no-submit (shadow does not use sandbox orders) |
| Write durable live order ledger? | **No** — no actual submit ⇒ no ledger identity required |

**Safety preference honored:** no production broker state mutation; no durable live-order state without submission.

If a future sandbox-submit shadow submode is approved later, it would inherit full M13.3 reconcile + ledger requirements and must be a distinct `execution` value — out of M13.4 scope.

---

## 7. SHADOW + CAPS

| Behavior | Recommendation |
|---|---|
| Evaluate hard notional / daily caps | **Yes** — pure function / dry check against current counter **snapshot** + settings |
| Record hypothetical cap violations | **Yes** — in shadow artifact (`blocked_cap`) |
| Increment `LIVE_MAX_ORDERS_PER_DAY` | **Never** for no-submit shadow |
| Touch `first_submit_counted` ledger fields | **Never** |
| Fail closed on missing cap settings when `trading_mode=live` | **Yes** (G10 still applies via Authority) |

Implementation sketch: extract cap **evaluation** helpers from `LiveCapGuardBroker` (read-only) without calling `record_submit`. Do not wrap a real broker for shadow.

---

## 8. CLI

### 8.1 Surfaces

| Command | `--shadow` | Notes |
|---|---|---|
| `run-once` | **Allowed** | Mutually exclusive with `--dry-run` / `--paper` / `--live` |
| `run-session` | **Allowed** | Same exclusion group; SessionRunner fail-closed unchanged |
| `run-paper-operator` | **Forbidden** | Do not add flag; reject if somehow set |
| `run-backtest` | **Forbidden** | No execution flags; reject any shadow wiring |

### 8.2 Mutual exclusion

Extend `_add_execution_flags(..., allow_live=True, allow_shadow=True)`:

```text
[--dry-run | --paper | --live | --shadow]   # mutually exclusive
default (no flag) → dry_run   # unchanged
```

Mapping:

| Flags | `execution` | Context mode |
|---|---|---|
| (none) / `--dry-run` | `dry_run` | `PAPER` |
| `--paper` | `paper` | `PAPER` |
| `--live` | `live` | `LIVE` |
| `--shadow` | `shadow` | `LIVE` |

### 8.3 Settings expectation for `--shadow`

Operator must configure live-gated settings (`TRADING_MODE=live`, confirm token, sandbox endpoint class, caps, credentials as required by G1–G10) even though no order is submitted. This is intentional: shadow validates the **live-configured** path.

---

## 9. FAILURE BEHAVIOR

### 9.1 Fail closed (infrastructure / wiring) — abort cycle, non-zero semantics as today

| Failure | Treatment |
|---|---|
| Malformed / stale market data (M11) | Abort — infrastructure/validation gate |
| Market hours reject policy | Abort — expected gate when enabled |
| Invalid mode wiring / wrong executor type | Abort — `mode_policy` |
| Production endpoint / LIVE_PRODUCTION | Deny at Authority / factory — hard fail |
| Missing live confirm / caps / credentials when shadow requires Authority | Deny — hard fail |
| Unknown execution state / corrupt settings | Fail closed |
| Configuration mismatch (e.g. shadow + paper-operator) | Fail closed at CLI/factory |

### 9.2 Expected validation outcomes (recorded in shadow artifact; may still end cycle “successfully” as observational)

| Outcome | Treatment |
|---|---|
| Strategy HOLD / no intent | Record `hypothetical_execution_decision=hold` / `no_intent` |
| Risk rejection | Record `risk_decision=reject`; **no submit**; cycle may be success-with-record or aborted per existing risk_abort pattern — **prefer record + existing risk_abort consistency** (Decision D-S4) |
| Hypothetical cap breach | Record `blocked_cap`; **do not** call broker; **do not** increment caps |
| Shadow path completes with `would_submit` | Record only; zero `place_order` |

**Reconciliation failures:** N/A for no-submit shadow (reconcile not required). If mistakenly enabled, must abort (fail closed) rather than ignore.

---

## 10. OBSERVABILITY / AUDIT LOG

### 10.1 Format recommendation

**Append-only JSONL file** + mirror key fields to existing `trading_bot.runtime` key=value logs.

| Item | Choice |
|---|---|
| Primary artifact | `SHADOW_AUDIT_PATH` (settings/CLI), JSONL, one record per cycle |
| Schema | versioned (`schema_version: 1`), fields in §5 |
| Database | **Not required** for M13.4 |
| Secrets | Never log API keys/tokens; reuse redaction habits from live gates |
| Determinism | Stable field names/order; UUIDs only in optional hypothetical id; timestamps ISO-UTC; decimals as strings |

### 10.2 Why JSONL

Sufficient for M14 evidence packs and multi-broker offline joins; matches existing JSON ledger philosophy without inventing a DB; easy to `diff` / `jq`.

---

## 11. TEST PLAN

Minimum proof tests (new `tests/runtime/test_m13_4_shadow_mode.py` + CLI/mode_policy updates):

| # | Proof |
|---|---|
| 1 | Shadow never calls production `place_order` (hostile mock / production builder unreachable) |
| 2 | No-submit shadow makes **zero** broker `place_order` calls (sandbox mock also zero) |
| 3 | Shadow cannot authorize `LIVE_PRODUCTION` |
| 4 | Paper behavior unchanged (existing paper tests green) |
| 5 | Live sandbox behavior unchanged (M13.2/M13.3 suites green) |
| 6 | `run-paper-operator` cannot use `--shadow` |
| 7 | `run-backtest` cannot use shadow |
| 8 | `mode_policy` rejects invalid combinations (shadow+PaperBroker, shadow+IdempotentLiveExecutor, shadow+PAPER context, etc.) |
| 9 | Risk rejection recorded in artifact; zero submit |
| 10 | Cap breach recorded; `LIVE_MAX_ORDERS_PER_DAY` counter unchanged |
| 11 | Structured shadow JSONL schema stable / auditable (golden fields) |
| 12 | Shadow path depends only on agnostic executor interface (fake adapter id string ok) |
| 13 | Full regression: M13.1 Alpaca + M13.2 gates + M13.3 reconcile/idempotency + full suite |

Also preserve: SessionRunner fail-closed; registry-only live construction; no ledger file required for shadow.

---

## 12. M13 CLOSURE (after M13.4 implementation + audit)

Milestone 13 is complete only when **all** of the following hold:

1. **M13.4 final audit** document exists and verdict is **APPROVED** (independent of this design review).
2. **Full repository test suite** green (including M13.1 / M13.2 / M13.3 / M13.4 proofs).
3. **Production-money unreachable confirmation** in audit: `LIVE_PRODUCTION` denied; shadow never submits; live remains sandbox-gated.
4. **Broker-agnostic architecture confirmation**: no Alpaca types in shadow core; registry/port boundaries intact.
5. **Shadow safety proof**: tests in §11 pass; audit cites them.
6. **`MILESTONE_13_SUMMARY.md`** created at closure, covering:
   - M13.1 adapter #1
   - M13.2 G1–G10
   - M13.3 idempotency + abort-only reconcile
   - M13.4 shadow no-submit validation
   - Explicit statement: M14 not started; real-money trial still forbidden
7. **Closure commit discipline:** one local commit (or explicitly approved set) including only M13.4 + summary/docs; no unrelated M11/M12 audit noise; no push/PR unless requested.
8. **No M14 implementation before M13 closure** (cancel-all, trial checklist, production enablement remain out of tree).

Parent “shadow checkpoint” in the LIVE progression table unlocks only after the above.

---

## 13. NON-GOALS (explicit exclusions)

- Real-money trading / production endpoint enablement  
- M14 cancel-all / trial checklist / email-webhook alert channels  
- Unattended LIVE  
- Automatic portfolio repair / compensating trades / broker cancels  
- Sandbox-submit shadow submode (deferred)  
- Multi-broker adapter implementations beyond current Alpaca paper adapter  
- Profitability / strategy certification  
- Database-backed shadow store  
- Changing PaperOperator into a live/shadow hybrid  
- Weakening M13.2 caps or M13.3 ledger semantics for `--live`

---

## Unresolved design decisions

| ID | Question | Options | Notes |
|---|---|---|---|
| **D-S1** | Must `settings.trading_mode` be `live` for shadow? | (a) require `live` + Authority (b) allow `paper` settings with shadow flag | (a) preferred for “live-intent path”; (b) mixes paper config with live validation |
| **D-S2** | How to extend G6 for `execution="shadow"` | (a) widen G6 allow-set (b) add G6b/G11 shadow gate | Prefer explicit G6b text in enablement module for audit clarity |
| **D-S3** | Same-cycle paper compare? | (a) defer (b) optional flag | Recommend (a) for minimal M13.4 |
| **D-S4** | Risk reject: `risk_abort` vs success+artifact | Match existing runtime abort semantics vs observational success | Prefer consistency with today’s `risk_abort` **and** always write artifact |
| **D-S5** | `SHADOW_AUDIT_PATH` required vs optional stdout-only | Required file vs optional | Prefer **required path** when `--shadow` (fail closed if missing) for M14 evidence discipline |
| **D-S6** | May shadow call sandbox `get_quote` for arrival/spread? | (a) market-data only (b) optional broker quote read | Prefer (a) to avoid any broker client construction in shadow factory |

---

## Recommended choices (summary)

1. **Semantics:** No-submit / log-only SHADOW only (Option A).  
2. **Architecture:** New `execution="shadow"` + `ShadowExecutor`; not a `TradingMode` enum value in M13.4.  
3. **Context:** `RuntimeContext.mode = LIVE` with live-gated settings (D-S1a).  
4. **Gates:** Authority still required; add explicit shadow executor type checks; never construct submitting live stack.  
5. **Reconcile / ledger:** Off; no durable live-order writes.  
6. **Caps:** Evaluate + record; never increment daily order counter.  
7. **CLI:** `--shadow` on `run-once` / `run-session` only; mutually exclusive with dry-run/paper/live; forbidden on operator/backtest.  
8. **Artifact:** Required append-only JSONL (`SHADOW_AUDIT_PATH`).  
9. **Compare path:** Defer same-cycle paper compare.  
10. **Closure:** M13.4 audit APPROVED + summary + full suite + no M14.

---

## Security / safety risks

| Risk | Mitigation |
|---|---|
| Shadow factory accidentally wires `IdempotentLiveExecutor` | mode_policy type deny + tests; separate builder function |
| Operator confusion: `--shadow` vs `--live` | CLI help text; mutual exclusion; docs in summary |
| Settings `trading_mode=live` with shadow still feels “armed” | Intentional; confirm token + caps still required; no submit seam |
| Hypothetical `client_order_id` written to live ledger | Forbidden in no-submit design |
| Cap counter incremented by evaluation helper bug | Read-only evaluation API; tests assert counter unchanged |
| Future sandbox-shadow conflated with M13.4 | Non-goal; separate execution value if ever added |
| Secrets in JSONL | Redaction policy; never persist API secrets in artifact |
| SessionRunner + shadow used as pseudo-unattended live | Still fail-closed; no production; human supervised CLI only |

---

## M13 closure checklist (post-implementation)

- [ ] M13.4 implemented per approved design (no-submit shadow)  
- [ ] `MILESTONE_13_4_FINAL_AUDIT.md` → **APPROVED**  
- [ ] Tests in §11 green  
- [ ] M13.1 / M13.2 / M13.3 regression green  
- [ ] Full repository suite green  
- [ ] LIVE_PRODUCTION unreachable (reconfirmed)  
- [ ] Shadow zero-submit proof cited in audit  
- [ ] Broker-agnostic shadow core confirmed  
- [ ] `MILESTONE_13_SUMMARY.md` written  
- [ ] Closure commit contains only authorized M13.4/closure files  
- [ ] M14 **not** started  

---

## DESIGN VERDICT

**READY FOR EXTERNAL APPROVAL** as a design package: safest minimal M13.4 is **broker-agnostic no-submit SHADOW** via a distinct factory `execution="shadow"`, live-gated but non-submitting, JSONL-audited, with reconcile/ledger off and caps evaluated without consumption — then formal Milestone 13 closure per §12.

---

## WAITING FOR EXTERNAL APPROVAL

Do not implement M13.4 until this design review is externally approved.  
Do not start M14.  
Do not weaken M13.1–M13.3 invariants.
