# M13.2 Design Review — Hard live enablement gates

**Status:** DESIGN / ANALYSIS ONLY — awaiting external approval  
**Mode:** Documentation only. Do **not** implement until this design is approved.  
**Baseline HEAD:** `ac45606c0c07a498364da60fc86fa873a23958de` — `M13.1: add Alpaca paper adapter and contract tests`  
**Branch at design time:** `cursor/m9-paper-session-loop` (ahead of origin; M13.1 committed locally)  
**Parents:** `MILESTONE_13_DESIGN_REVIEW.md` (approved L1–L6 / V1), `MILESTONE_11_14_DESIGN_SPEC.md` § M13.2 / §8 LIVE progression  
**Non-actions:** No source/test changes. No stage/commit/push/PR. Do not start M13.2 implementation from this file alone.

---

## 0. Post-M13.1 repository inspection (actual)

| Area | Current behavior at `ac45606` |
|---|---|
| Defaults | `Settings.trading_mode="paper"`; `BROKER_NAME=paper`; factory `execution` ∈ `{dry_run, paper}` |
| Factory | `_validate_settings_mode` rejects `live` / non-`paper`; `_resolve_paper_broker` requires `isinstance(..., PaperBroker)` |
| Mode policy | `mode_policy_violation` allows only settings `paper` + `TradingMode.PAPER` + `BrokerOrderExecutor(PaperBroker)` / dry-run / None |
| CLI | `run-once` / `run-session` / `run-paper-operator`: `--dry-run` \| `--paper` only; **no `--live`**; contexts hard-code `TradingMode.PAPER` |
| Paper operator | `_validate_paper_settings_mode` requires `trading_mode='paper'`; refuse otherwise |
| Backtest | `run-backtest` requires `trading_mode='paper'`; DryRun path only; freshness/hours enforcement off via context flags |
| Alpaca (M13.1) | Adapter #1 behind `Broker`; paper host only; production `api.alpaca.markets` rejected at construction; **not** factory-wired; helper `alpaca_paper_broker_from_settings` opt-in only |
| Module registry | Unknown `broker_name` → fallback `PaperBroker` + warning (fail-safe, not Alpaca) |
| Caps today | Percent risk only (`max_position_size_pct`, `max_daily_loss_pct`, `max_open_positions`) — **no** absolute live notional / orders-per-day hard caps |
| Shadow | Not implemented (`TradingMode` = paper \| live \| backtest only) |

**Invariant to preserve:** default deny; paper/dry-run/operator/backtest paths unchanged unless every live gate is explicitly satisfied.

---

## 1. Design objective (M13.2 only)

Introduce a **broker-agnostic, conjunctive, fail-closed Live Enablement Authority** so a non-`PaperBroker` adapter may be constructed and used **only** when every required safety condition is explicitly true.

**Critical boundary (must remain true after M13.2):**

> Passing M13.2 gates must **not** authorize a real-money trial.  
> Real-money trial remain **M14**.  
> M13.2 may authorize supervised wiring of an **approved live-capable adapter against a classified broker-sandbox endpoint**.  
> `LIVE_PRODUCTION` endpoint class remains **unreachable** in M13.2 even if other flags are set.

This matches parent LIVE progression: M13.2 = “technically enabled” gated path; M13.3 = reconcile/idempotency; M13.4 = shadow; M14 = tiny real-money trial.

---

## 2. Mode / environment taxonomy (must distinguish)

Core must speak in **broker-agnostic** terms. Venue hosts live only inside adapters (or adapter-supplied classifiers).

| Concept | Meaning | Money risk | Who constructs | M13.2 status |
|---|---|---|---|---|
| **LOCAL_PAPER** | In-process `PaperBroker` | None (simulated) | Factory `execution=paper` (today) | Unchanged default |
| **BROKER_SANDBOX** | Remote broker **paper/sandbox API** (e.g. Alpaca `paper-api.*`) | No real money (venue paper account) | Factory `execution=live` **only if** Live Authority grants + G9 class = sandbox | **Allowed target of M13.2 enablement** |
| **LIVE_PRODUCTION** | Remote broker **production** trading API | Real money | Would require M14 trial authority + production allowlist | **Hard-denied in M13.2** |
| **SHADOW** | Signal/intent path without real-money submit (M13.4) | None for production | Future shadow executor | **Out of scope for M13.2** (design hooks only) |

### Settings vs runtime vs endpoint (three axes)

1. **`settings.trading_mode`** — operator intent: `paper` (default) \| `live` \| `backtest` (backtest remains CLI-isolated).  
2. **Factory `execution`** — wiring: `dry_run` \| `paper` \| **`live`** (new in M13.2).  
3. **`broker_endpoint_class`** (new) — classified endpoint: `local_paper` \| `broker_sandbox` \| `live_production`.

M13.2 rule: `execution=live` ⇒ `trading_mode=live` **and** `broker_endpoint_class=broker_sandbox` (only).  
`live_production` ⇒ always `ConfigurationError` in M13.2 with an explicit “requires M14” reason.

Alpaca-specific host checks remain **inside** `broker_interface/alpaca/` (already rejects production host). Core G9 consumes a **classified** result / allowlist id, not Alpaca constants.

---

## 3. Exact gates and semantics (conjunctive AND)

All gates are **fail-closed**. Missing, empty, unknown, or mismatched values ⇒ **deny**. No partial enablement.

| ID | Gate | Pass condition | Fail behavior |
|---|---|---|---|
| **G1** | Trading mode | `settings.trading_mode == "live"` | `ConfigurationError` / mode abort; no non-paper broker |
| **G2** | Explicit enable | `LIVE_TRADING_ENABLED=true` (strict boolean; default `false`) | Deny |
| **G3** | Confirmation token | `LIVE_CONFIRM_TOKEN` equals exact configured expected phrase (recommend env-only; default empty ⇒ always fail when gates evaluated) | Deny |
| **G4** | Approved adapter | `BROKER_NAME` ∈ **approved live-capable adapter registry** for the requested endpoint class (M13.2: e.g. `alpaca_paper` / sandbox adapter id only) | Deny; unknown names must **not** silently fall back to PaperBroker when `execution=live` |
| **G5** | Credentials / config | Non-empty API key + secret; required base URL present; adapter construction succeeds | Deny before any order |
| **G6** | Execution context allows LIVE | Call path ∈ `{run-once, run-session}` **and** factory `execution="live"` **and** `RuntimeContext.mode == TradingMode.LIVE` | Deny |
| **G7** | Backtest prohibited | Not `run-backtest`; context must not disable live via backtest isolation flags as a live bypass; `trading_mode=backtest` never builds live executor | Deny |
| **G8** | Paper operator prohibited | `run-paper-operator` / `PaperOperator` **never** requests or accepts live | Deny at operator entry (keep `trading_mode=paper` requirement) |
| **G9** | Endpoint classification | Explicit `BROKER_ENDPOINT_CLASS` (or equivalent) must be `broker_sandbox` for M13.2; adapter validates URL ∈ sandbox allowlist for that adapter; `live_production` **always denied in M13.2** | Deny |
| **G10** | Minimal hard caps configured | Absolute live caps present and sane (see §8): max order notional **and** max orders per UTC day (and optionally max gross notional) — must be set and `> 0` when live path authorized | Deny |

**Conjunctive:** Live Authority returns `Authorized` iff **G1 ∧ G2 ∧ G3 ∧ G4 ∧ G5 ∧ G6 ∧ G7 ∧ G8 ∧ G9 ∧ G10**.

**Non-implication:** `Authorized` in M13.2 means “may wire approved **sandbox** adapter under supervision.” It does **not** mean M14 trial checklist, cancel-all, production host, or unattended live.

---

## 4. Where each gate is evaluated

| Gate | Primary evaluation site | Secondary / defense-in-depth |
|---|---|---|
| G1 | `LiveEnablementAuthority.evaluate(settings, …)` called from factory | `mode_policy` still checks settings mode vs context |
| G2 | Authority (settings) | — |
| G3 | Authority (settings) | CLI may re-supply token only if it matches; empty CLI must not weaken env |
| G4 | Authority + factory adapter registry lookup | Adapter construction |
| G5 | Factory/adapter construction after Authority pre-check of non-empty secrets | Adapter `__init__` (existing Alpaca checks) |
| G6 | CLI command handlers set `execution=live` + `RuntimeContext.mode=LIVE`; Authority receives `LiveExecutionContext` | `mode_policy` rejects LIVE context unless Authorized snapshot present / policy expanded |
| G7 | `run-backtest` never calls live factory; Authority rejects `command=backtest` | Existing backtest mode checks remain |
| G8 | `PaperOperator` / `_run_paper_operator_command` refuse `--live` and `trading_mode!=paper` before factory | Authority rejects `command=paper_operator` |
| G9 | Authority checks declared endpoint class + registry allowlist id | **Adapter-local** URL/host validation (Alpaca paper host only today) |
| G10 | Authority checks settings caps present/sane; risk/runtime enforce at order time (M13.2 minimal) | Risk gate or live-cap helper before `place_order` |

**Recommended new pure module:** `runtime/live_enablement.py`  
- No I/O, no broker calls, no Alpaca imports.  
- Inputs: settings slice + `LiveExecutionContext(command, execution, context_mode, endpoint_class)`.  
- Output: `LiveAuthorization(authorized: bool, reason: str | None, endpoint_class, adapter_id)`.

---

## 5. Component that owns final LIVE authorization

| Component | Role |
|---|---|
| **`LiveEnablementAuthority`** (new) | **Sole owner of pass/fail** for enabling non-paper live-capable wiring |
| **`create_trading_runtime` (factory)** | **Sole composition root** that may construct `BrokerOrderExecutor(approved_adapter)` when Authority authorizes; must not construct live adapters otherwise |
| **`mode_policy_violation`** | Defense-in-depth every `run_once`: reject LIVE / non-PaperBroker unless authorization contract satisfied (see Decision D-A) |
| **Adapters** | Venue URL/credential shape only; **cannot** grant core live authorization |
| **CLI (`main.py`)** | Maps `--live` → `execution=live` + `TradingMode.LIVE`; never bypasses Authority |

**Rule:** No module registry path, helper, or test double may place real network orders in production paths without going through factory + Authority. M13.1 helper `alpaca_paper_broker_from_settings` remains usable in **unit tests**; production CLI must not call it behind the Authority’s back.

---

## 6. Failure behavior (every gate)

Uniform policy:

1. **Fail closed** — no order placement, no live executor wiring.  
2. **Explicit reason** — `ConfigurationError` at composition/CLI time, or `PipelineResult(success=False, stage_reached="mode", …)` if a cycle somehow starts with bad wiring.  
3. **No silent downgrade** when `execution=live` was requested — do **not** substitute `PaperBroker` / dry-run (that would mask misconfiguration). Paper fallback remains only for today’s `broker_name` unknown path under **`execution=paper`**.  
4. **No secret leakage** in reasons/logs (reuse M13.1 redaction discipline).  
5. **Defaults** keep today’s deny: unset G2/G3/G10 ⇒ live impossible.

---

## 7. CLI behavior

### 7.1 `run-once` (supervised)

| Today | M13.2 proposal |
|---|---|
| `--dry-run` / `--paper`; context always `PAPER` | Add mutually exclusive **`--live`** |
| Factory `execution` dry_run/paper | `--live` ⇒ `execution="live"`, `RuntimeContext.mode=TradingMode.LIVE` |
| | Requires G1–G10 via Authority inside factory; exit `1` on `ConfigurationError` |

Dry-run/paper defaults and paper behavior unchanged when `--live` absent.

### 7.2 `run-session` (supervised, fail-closed)

Same execution flags as `run-once`, including optional `--live`.  
`SessionRunner` semantics unchanged: **any** `success=False` stops the session.  
Live is allowed here only because a human is supervising a **bounded** cycle count (approved L2).

### 7.3 `run-paper-operator` (unattended paper)

| Requirement | Behavior |
|---|---|
| G8 | **No `--live` flag** on this parser (reject if ever added) |
| Settings | Keep hard require `trading_mode='paper'` |
| Factory | Only `dry_run` / `paper` |
| Rationale | Unattended loops must not become the live trial vehicle; live remains supervised (`run-once` / `run-session`) through M13; trial ops are M14 |

### 7.4 `run-backtest`

Unchanged: paper settings, DryRun/CommissionDryRun only; never `execution=live` (G7).

### 7.5 Why `run-paper-operator` cannot use LIVE

1. **L2 / parent non-goal:** unattended live operator deferred.  
2. **Kill/cancel-all** for live is M14 — operator kill today stops local cycles, not venue cancel-all.  
3. **Persistence/E1** designed for paper state, not live reconcile (M13.3).  
4. **G8** is an explicit conjunctive gate so even a mistaken `TRADING_MODE=live` in `.env` cannot start the operator into live wiring.

---

## 8. Minimal M13 live exposure caps (G10) — where they belong

Parent safety table: max notional / max orders per day in M13; fuller trial limits in M14.

| Cap | Recommended M13.2 setting | Owner |
|---|---|---|
| Max order notional (absolute money) | `LIVE_MAX_ORDER_NOTIONAL` (Decimal, required `> 0` when live authorized) | Settings + enforce before/with risk for live path |
| Max orders per UTC day | `LIVE_MAX_ORDERS_PER_DAY` (int, required `≥ 1` when live authorized) | Settings + live order counter (in-memory for M13.2 supervised runs; durable counter can wait for M13.3/M14 if needed — see Decision D-C) |
| Optional gross exposure | `LIVE_MAX_GROSS_NOTIONAL` optional in M13.2; required in M14 trial | Defer require to M14 unless approved now |

**Do not** overload percent-only paper risk (`max_position_size_pct`) as the sole live hard cap — keep percent risk, **add** absolute live caps.

**M14 remains** responsible for trial-tiny defaults, symbol allowlists, signed checklist, cancel-all.

---

## 9. Required settings / environment variables

| Variable | Default | M13.2 role |
|---|---|---|
| `TRADING_MODE` | `paper` | G1 |
| `LIVE_TRADING_ENABLED` | `false` | G2 (new) |
| `LIVE_CONFIRM_TOKEN` | `""` | G3 (new; empty ⇒ deny when evaluated) |
| `LIVE_CONFIRM_TOKEN_EXPECTED` | fixed phrase in settings default **or** require operator to set both sides | See Decision D-B |
| `BROKER_NAME` | `paper` | G4 (approved ids only for live) |
| `BROKER_API_KEY` / `BROKER_API_SECRET` | `""` | G5 |
| `BROKER_BASE_URL` | paper example URL | G5/G9 input to adapter |
| `BROKER_ENDPOINT_CLASS` | unset / `local_paper` | G9 (new); live path requires `broker_sandbox` |
| `LIVE_MAX_ORDER_NOTIONAL` | unset | G10 (new; required when authorizing live) |
| `LIVE_MAX_ORDERS_PER_DAY` | unset | G10 (new) |
| Existing risk / MD / hours settings | unchanged | M11 protections remain |

`.env.example` documents all as **commented, deny-by-default**; never commit real tokens/keys.

**Recommended expected confirm phrase (if single-sided):**  
`I_UNDERSTAND_LIVE_IS_GATED_NOT_A_TRIAL`  
(Must not be the default of `LIVE_CONFIRM_TOKEN` — operator must copy deliberately.)

---

## 10. Future brokers (IBKR / TradeStation / Webull)

Plugin model (no core redesign):

1. Implement `Broker` ABC (+ optional venue URL validator).  
2. Register in a **broker-agnostic adapter registry**:

```text
AdapterRegistration(
  adapter_id="alpaca_paper",          # M13.2
  endpoint_classes={BROKER_SANDBOX},  # production class only when M14+ and adapter supports it
  factory=callable(settings, transport) -> Broker,
)
```

3. Live Authority checks `adapter_id ∈ registry` and `requested_endpoint_class ∈ registration.endpoint_classes`.  
4. Core never imports IBKR/TS/Webull types; only registry + `Broker` port.  
5. HTTP remains `HttpTransport` (already venue-neutral from M13.1).

M13.2 ships registry with **Alpaca sandbox adapter only**. Multi-broker is still a milestone non-goal; the **mechanism** must not be Alpaca-shaped.

---

## 11. Architectural changes required (M13.2 implementation preview)

| Change | Purpose |
|---|---|
| Add `runtime/live_enablement.py` | Pure conjunctive Authority |
| Extend `config/settings.py` + `.env.example` | G2/G3/G9/G10 fields |
| Extend `runtime/factory.py` | `execution="live"`; call Authority; construct approved adapter; refuse production class |
| Update `runtime/mode_policy.py` | Allow LIVE + non-PaperBroker **only** when consistent with Authorized live sandbox wiring (Decision D-A) |
| Extend `main.py` | `--live` on run-once/session; forbid on paper-operator; set `TradingMode.LIVE` |
| Adapter registry (small) | G4/G9 without Alpaca imports in runtime |
| Live cap enforcement helper | G10 at intent/execution boundary |
| Tests | Gate matrix + regressions |
| Docs | README safety notes (implementation phase) |

**Must not change for M13.2:** M10 backtest isolation, M11 freshness/hours defaults, M12 operator paper-only semantics, SessionRunner fail-closed, strategies/risk/OM/portfolio becoming venue-specific, Alpaca production host enablement.

**Alpaca adapter in M13.2:** Keep rejecting `api.alpaca.markets`. Factory wires paper/sandbox Alpaca under live gates — not production.

---

## 12. Required tests (including gate matrix)

### 12.1 Unit — Authority

- Each gate alone false ⇒ deny with distinct reason substring.  
- All gates true + sandbox class ⇒ authorize.  
- All gates true + `live_production` ⇒ **deny** (M14 boundary).  
- Defaults (empty env) ⇒ deny.

### 12.2 Full gate-combination matrix (minimum)

For boolean-ish gates `{G1,G2,G3,G4,G5,G9_sandbox,G10}` evaluate:

| Case | Expect |
|---|---|
| All pass (sandbox) | Factory builds live sandbox executor |
| Flip each one off (7+ cases) | Factory/`ConfigurationError` or Authority deny |
| G9=`live_production` with all else pass | Deny (M13.2) |
| G6 wrong command (`paper_operator` / `backtest`) | Deny |
| G8 operator + `trading_mode=live` | Operator refuses before live factory |
| `execution=paper` + live env flags set | Remains PaperBroker path; flags ignored for paper |
| `execution=live` + `trading_mode=paper` | Deny (G1) |

Use parametrize; no network.

### 12.3 Factory / mode_policy / CLI

- `--live` on run-once/session happy path with fakes/mocks.  
- `--live` absent ⇒ unchanged paper/dry-run.  
- `run-paper-operator` rejects live settings / has no `--live`.  
- `run-backtest` cannot obtain live executor.  
- Injecting non-approved broker still fails.

### 12.4 Caps (G10)

- Missing/zero/negative notional or orders/day ⇒ deny.  
- Order above max notional ⇒ reject/fail-closed (no place_order).  
- Orders/day exceeded ⇒ reject.

### 12.5 Regression (must stay green)

- M10 backtest isolation  
- M11 freshness/hours  
- M12 operator (A2/B1/D1/E1, soak helpers)  
- SessionRunner fail-closed  
- M13.1 Alpaca paper contract tests + production host still rejected  
- Full suite

### 12.6 Security / bypass tests

- Module registry cannot smuggle Alpaca into `execution=paper`.  
- Direct `BasicTradingRuntime` with non-paper broker + PAPER mode still aborted by mode_policy.  
- Confirm token mismatch; substring/partial match fails.  
- Secrets absent from deny messages/logs.

---

## 13. Security risks / bypass paths

| Risk | Mitigation |
|---|---|
| Partial flags (`trading_mode=live` only) | Conjunctive Authority; matrix tests |
| Silent PaperBroker fallback on live request | Forbidden for `execution=live` |
| Production URL via `BROKER_BASE_URL` | G9 class deny + adapter host deny (defense in depth) |
| Operator unattended live | G8; no `--live` on operator |
| Backtest → live executor | G7; separate CLI |
| Test helper used in CLI | CLI/factory only path for app wiring |
| Token in argv visible in process list | Prefer env-only token (Decision D-B); docs warn |
| Caps unset but gates otherwise open | G10 required |
| Assuming M13.2 = real-money OK | Docs + hard deny `live_production`; audit language |

---

## 14. Explicit M13.2 non-goals

- Real-money / production endpoint enablement (M14)  
- M14 trial checklist, cancel-all, email/webhook channels  
- Reconciliation / durable idempotency completeness (M13.3) — note: `client_order_id` already additive from M13.1; M13.2 need not finish reconcile  
- Shadow mode (M13.4)  
- Unattended live operator  
- Multi-broker adapters beyond registry hook + Alpaca sandbox  
- Weakening paper defaults, M10/M11/M12, SessionRunner fail-closed  
- Strategy profitability gates  
- Making core import Alpaca types/constants for authorization

---

## 15. Mapping to approved parent decisions

| ID | Approved direction | M13.2 application |
|---|---|---|
| **V1** | Alpaca Adapter #1 | Registry entry for sandbox only; core agnostic |
| **L1** | Richer conjunctive gates | G1–G10 as specified |
| **L2** | Supervised live paths; operator paper-only | `--live` on run-once/session; G8 |
| **L3** | Shadow never production money | Deferred to M13.4; taxonomy reserved |
| **L4** | Reconcile abort-only | Deferred to M13.3 |
| **L5** | Mock HTTP in CI | Gate tests use fakes; no live network required |
| **L6** | Minimal caps in M13 | G10 absolute notional + orders/day |

---

## 16. Unresolved decisions

| ID | Question | Options | Recommendation |
|---|---|---|---|
| **D-A** | How mode_policy learns authorization | (a) Pass `LiveAuthorization` into policy; (b) policy re-evaluates Authority from settings+context; (c) policy only checks types and trusts factory | **(b)** re-evaluate pure Authority in mode_policy (no ambient mutable “armed” flag) |
| **D-B** | Confirm token shape | (a) Single env `LIVE_CONFIRM_TOKEN` must equal code constant; (b) two env vars token==expected; (c) CLI `--i-confirm-live=...` only | **(a)** constant expected phrase + env token (simplest, auditable); document process-list risk |
| **D-C** | Orders-per-day counter durability | (a) In-process only for M13.2 supervised; (b) file-backed | **(a)** for M13.2; durable in M13.3/M14 as needed |
| **D-D** | Naming: factory `execution="live"` vs `"broker"` | (a) `live`; (b) `broker_sandbox` | **(a) `live`** with G9 forcing sandbox class in M13.2 (matches parent language); document clearly that M13.2 live ≠ production money |
| **D-E** | Should G10 also require `LIVE_MAX_GROSS_NOTIONAL` now? | (a) optional; (b) required | **(a)** optional in M13.2; required in M14 |
| **D-F** | Approved adapter id string | Keep `alpaca_paper` vs introduce `alpaca_sandbox` | **Keep `alpaca_paper`** (M13.1 name) as registry id for sandbox class |

None of D-A–D-F block writing this design; they should be confirmed before implementation.

---

## 17. Recommended design (summary)

1. Add broker-agnostic **`LiveEnablementAuthority`** implementing **G1–G10** conjunctively.  
2. Expand factory with **`execution="live"`** that builds `BrokerOrderExecutor(approved_adapter)` **only** when Authorized **and** endpoint class is **`broker_sandbox`**.  
3. **Deny `live_production` in M13.2** (M14 trial boundary).  
4. CLI: **`--live`** for `run-once` / `run-session` only; set `TradingMode.LIVE`; **never** for `run-paper-operator`.  
5. Registry plugs future brokers; Alpaca remains Adapter #1 with adapter-local host allowlist.  
6. Absolute **G10** caps required for authorization; percent risk unchanged.  
7. Defense-in-depth via mode_policy re-check; no silent fallbacks; full gate matrix tests.  
8. Preserve paper default, PaperBroker, M10/M11/M12, SessionRunner fail-closed.

---

## 18. DESIGN VERDICT

# M13.2 DESIGN READY FOR EXTERNAL APPROVAL

The post-M13.1 tree still hard-rejects live at factory/mode_policy/CLI/operator. This design specifies a broker-agnostic conjunctive gate system (G1–G10), clear PAPER / BROKER_SANDBOX / LIVE_PRODUCTION / SHADOW taxonomy, supervised CLI surfaces, operator/backtest exclusion, minimal absolute caps, and an explicit M14 boundary so gate passage ≠ real-money trial.

Unresolved items D-A–D-F should be confirmed by external review before implementation starts.

---

## WAITING FOR EXTERNAL APPROVAL

**STOP.** Do not implement M13.2. Do not modify source/tests for this design. Do not stage, commit, push, or open a PR from this documentation phase.
