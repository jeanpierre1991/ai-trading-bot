# M11–M14 Formal Design Specification

**Basis:** post-M10 architecture @ `b827f07`, Option A paper/backtest isolation preserved.  
**Status:** design only — no implementation in this document’s approval cycle.  
**Created:** read-only design capture from the approved roadmap audit and design review.

---

## Cross-cutting design principles (all of M11–M14)

1. Reuse `BasicTradingRuntime.run_once` — do not fork a second trading pipeline.
2. Keep M8 mode axes distinct: `Settings.trading_mode` ≠ factory `execution` ≠ `RuntimeContext.mode`.
3. Fail-closed by default on missing/stale/invalid data, risk breach, or kill switch.
4. Paper paths must never silently become live; live requires explicit multi-gate enablement (M13+).
5. Preserve M1–M10 behavior unless a milestone explicitly requires a change; any change needs regression tests.
6. Sub-milestones are review-gated: implement → test → review → approve → then commit.

---

# M11 — Real-market paper fidelity

### 1. Milestone objective

Make supervised paper trading use **real market-data prices** for both decisions and simulated fills, with fail-closed handling for missing/stale/invalid data and an explicit market-hours policy — still `trading_mode=paper` only.

### 2. Why the milestone exists

- **Concrete gap:** Strategy/sizing use bar/`signal.price`, but `--paper` fills via `PaperBroker.get_quote()` hardcoded map (`broker_interface/broker.py`) — paper PnL is not real-market-faithful.
- **Why this milestone (not M12):** Autonomy/persistence is useless if fills are wrong. Fidelity first.
- **Why this milestone (not M13):** No live broker needed; this is paper-only correctness.

### 3. Exact scope

**Likely to change**

- `broker_interface/broker.py` (`PaperBroker` quote source)
- Possibly `runtime/broker_executor.py` / factory wiring of quote source
- `market_data/` (freshness metadata, validation helpers)
- `runtime/trading_runtime.py` or a small pre-cycle gate for stale/hours (preferred: thin gate, not strategy logic)
- `config/settings.py` / `.env.example` (staleness TTL, market-hours mode)
- `runtime/factory.py` only if needed to inject quote provider into `PaperBroker` (minimize; do not broaden allowed modes)
- Tests + README / `MILESTONE_11_SUMMARY.md` at closure

**Must remain unchanged**

- M10 backtest Option A (no `BrokerOrderExecutor` on backtest)
- `mode_policy` live/`backtest` rejection semantics (may gain *additional* checks, not weaken)
- `SessionRunner` contract (M9)
- Booking semantics (`execution_to_fill` / `apply_fill`) unless fee/quote mapping requires a narrow extension
- No live broker adapter

**Possible new interfaces**

- `QuoteSource` / callable: `get_quote(symbol) -> (price, as_of)` injected into `PaperBroker`
- `MarketDataFreshness` / bar `as_of` timestamp validation
- Settings: e.g. `market_data_max_age_seconds`, `market_hours_policy` (`reject` | `allow_extended` | `warn_only` — warn_only discouraged for paper fidelity)

**Integration with M1–M10**

```text
Yahoo/Mock MD → strategy signal.price
              → PaperBroker quote source (same MD last price / last bar close)
              → BrokerOrderExecutor → ExecutionResult → booking (unchanged)
```

### 4. Explicit non-goals

- Daemon / continuous loop / persistence
- Live brokers / enabling `trading_mode=live`
- Partial fills, cancel/amend, reconciliation vs external account
- Streaming websockets
- Changing M10 backtest to use Yahoo by default
- AI/news in the decision path

### 5. Proposed sub-milestones

#### M11.1 — Paper fill price alignment

| | |
|---|---|
| **Objective** | Paper MARKET fills use market-data-derived price, not static map. |
| **Implementation scope** | Introduce injectable quote source for `PaperBroker`; wire from factory/runtime MD last price or last bar close; keep DryRun behavior unchanged unless explicitly synced. |
| **Files/modules likely involved** | `broker_interface/broker.py`, `runtime/factory.py` and/or `runtime/broker_executor.py`, tests under `tests/broker_interface/`, `tests/runtime/`. |
| **Dependencies** | M6–M9 complete. |
| **Safety constraints** | Missing quote → REJECTED / cycle abort (fail-closed); never invent price; no live path. |
| **Tests required** | Unit: quote injection; Integration: `--paper` fill ≈ MD price; Regression: DryRun + M10 untouched. |
| **Acceptance criteria** | With mocked MD price P, paper fill price = P (within documented quantization); static map unused when quote source present. |
| **Stop/review point** | Review and approve M11.1 before starting M11.2. |

#### M11.2 — Stale / missing / invalid market-data gate

| | |
|---|---|
| **Objective** | Fail-closed when bars/price are empty, malformed, or older than TTL. |
| **Implementation scope** | Freshness check before strategy or at cycle start; configurable TTL; structured abort reason. |
| **Files/modules likely involved** | `market_data/` helpers and/or `runtime/` gate; `config/settings.py`; runtime tests. |
| **Dependencies** | M11.1. |
| **Safety constraints** | Stale/missing → `success=False` before new exposure; no order placement. |
| **Tests required** | Unit TTL; Integration abort before executor; Failure-injection: empty bars, old timestamp. |
| **Acceptance criteria** | Documented TTL; stale bar never produces FILLED paper order. |
| **Stop/review point** | Review and approve M11.2 before starting M11.3. |

#### M11.3 — Market-hours policy (paper)

| | |
|---|---|
| **Objective** | Explicit policy for trading outside regular hours. |
| **Implementation scope** | Calendar/session check (start simple: US equity RTH or configurable window); settings-driven `reject` default for automated paper later. |
| **Files/modules likely involved** | New small helper (e.g. `market_data/hours.py` or `runtime/market_hours.py`); settings; tests. |
| **Dependencies** | M11.2. |
| **Safety constraints** | Default fail-closed outside hours when policy=`reject`; supervised override only via explicit setting. |
| **Tests required** | Unit clock boundaries; Integration outside-hours abort. |
| **Acceptance criteria** | Outside hours + `reject` → no new orders; policy documented. |
| **Stop/review point** | Review and approve M11.3 before starting M11.4. |

#### M11.4 — Supervised real-market paper validation + docs

| | |
|---|---|
| **Objective** | Prove Yahoo (or configured provider) path end-to-end under supervision; document safe usage. |
| **Implementation scope** | Integration/smoke tests with mocked network; optional manual checklist; README + M11 summary; no daemon. |
| **Files/modules likely involved** | Tests, README, `MILESTONE_11_SUMMARY.md`. |
| **Dependencies** | M11.1–M11.3. |
| **Safety constraints** | Document `MARKET_DATA_PROVIDER=yahoo` risks; prefer mock in CI. |
| **Tests required** | Provider contract with mocks; full paper cycle regression; M8–M10 suites green. |
| **Acceptance criteria** | Checklist for Checkpoint A; suite green; M11 closed in docs. |
| **Stop/review point** | **M11 final audit** before commit/push of M11. |

### 6. Safety architecture (M11 focus)

| Control | M11 definition |
|---|---|
| Fail-closed | Missing/stale/invalid MD or hours reject → abort cycle, no FILLED |
| Paper/live isolation | `trading_mode` remains paper-only; factory still rejects live |
| Stale-data | TTL gate (M11.2) |
| Missing MD | Empty bars / no quote → abort |
| Duplicate orders | Out of scope beyond existing OM (defer stronger idempotency to M12/M13) |
| Reconciliation | N/A (local paper) |
| Crash/restart | N/A (M12) |
| Kill switch | N/A (M12) |
| Secrets | No new live secrets; Yahoo needs no key today |
| Max exposure | Existing `max_position_size_pct` / open / daily loss unchanged |
| Market hours | M11.3 |
| Retry/backoff | Light optional on MD fetch only; never retry order blindly |
| Alerts | Existing console alerts on booking/reject sufficient; optional stale alert |

---

# M12 — Autonomous bounded paper operator

### 1. Milestone objective

Run **unattended but hard-bounded** paper sessions with interval scheduling, kill switch, and durable portfolio/order state across restarts — still paper-only.

### 2. Why the milestone exists

- **Concrete gap:** `SessionRunner` is bounded but synchronous with no sleep; state is in-memory only; no kill switch.
- **Why this milestone (not M11):** Needs fidelity first (M11).
- **Why this milestone (not M13):** Still not live.

### 3. Exact scope

**Likely to change/add**

- New operator module (e.g. `runtime/paper_operator.py`) or CLI `run-paper-operator`
- Persistence layer (portfolio + orders + operator cursor)
- Kill-switch reader (file and/or env)
- Settings: interval, max runtime, max cycles, state path
- Tests + docs

**Must remain unchanged**

- Core `run_once` booking/risk semantics
- M10 backtest path
- Live still blocked
- Prefer composing `SessionRunner` / repeated `run_once` over rewriting them

**Possible new interfaces**

- `PaperStateStore` (load/save)
- `KillSwitch` (`is_engaged() -> bool`)
- `PaperOperatorConfig` (interval, max_cycles, max_wall_time, state_path)

**Integration with M1–M10**

```text
PaperOperator (bounds + kill + interval + persistence)
  → create_trading_runtime / explicit paper wiring (M8.6)
  → SessionRunner or N× run_once (M9/M8)
  → M11 data-quality + hours gates inside run_once
```

### 4. Explicit non-goals

- Infinite unsupervised loops without caps
- Live trading
- Distributed multi-host HA
- Full broker reconciliation

### 5. Proposed sub-milestones

#### M12.1 — Kill switch + hard operator bounds

| | |
|---|---|
| **Objective** | Operator refuses to start/continue if kill engaged; enforce max cycles + max wall time + interval. |
| **Implementation scope** | Kill switch + config validation + operator skeleton calling existing runtime. |
| **Files/modules likely involved** | New operator module, settings, CLI stub, tests. |
| **Dependencies** | M11 complete. |
| **Safety constraints** | Kill → stop before next `run_once`; bounds required (no unbounded default). |
| **Tests required** | Kill mid-run; invalid bounds fail-closed; regression SessionRunner. |
| **Acceptance criteria** | Cannot run without explicit bounds; kill stops new cycles. |
| **Stop/review point** | Review and approve M12.1 before starting M12.2. |

#### M12.2 — State persistence & restart resume

| | |
|---|---|
| **Objective** | Persist portfolio (+ OM) and restore on restart without incorrectly duplicating open exposure. |
| **Implementation scope** | Atomic save; load on start; define resume semantics (no replay of filled cycles). |
| **Files/modules likely involved** | Persistence module, portfolio/OM serialization, tests. |
| **Dependencies** | M12.1. |
| **Safety constraints** | Corrupt state → refuse start; atomic write; never invent positions. |
| **Tests required** | Round-trip; crash between cycles; corrupt file abort. |
| **Acceptance criteria** | Restart continues from saved equity/positions; documented semantics. |
| **Stop/review point** | Review and approve M12.2 before starting M12.3. |

#### M12.3 — Interval operator + supervised multi-session CLI

| | |
|---|---|
| **Objective** | Sleep/interval between cycles; CLI for unattended bounded paper. |
| **Implementation scope** | Full operator loop; logging; integrate M11 gates. |
| **Files/modules likely involved** | `main.py`, operator, docs. |
| **Dependencies** | M12.1–M12.2, M11. |
| **Safety constraints** | Still paper-only; MD stale/hours apply each cycle. |
| **Tests required** | Fake clock/interval; integration with DryRun/Paper; M8–M11 regressions. |
| **Acceptance criteria** | Checkpoint C unlocked; docs for unattended paper. |
| **Stop/review point** | Review and approve M12.3 before starting M12.4. |

#### M12.4 — Soak harness + M12 closure

| | |
|---|---|
| **Objective** | Deterministic soak test (short wall time in CI) + long-soak runbook. |
| **Implementation scope** | Soak test with mock MD; checklist for overnight Yahoo soak; summary docs. |
| **Files/modules likely involved** | Tests, README, `MILESTONE_12_SUMMARY.md`. |
| **Dependencies** | M12.3. |
| **Safety constraints** | Soak cannot enable live; kill switch verified in soak. |
| **Tests required** | CI soak (minutes); optional manual overnight. |
| **Acceptance criteria** | Checkpoint D criteria documented; suite green. |
| **Stop/review point** | **M12 final audit** before commit/push of M12. |

### 6. Safety architecture (M12 adds)

| Control | M12 definition |
|---|---|
| Kill switch | Checked before each cycle; engaged → stop new orders |
| Persistence | Corrupt/missing state → refuse start (fail-closed) |
| Crash/restart | Resume from last saved snapshot; no silent position invention |
| Duplicate-order | Prefer client cycle idempotency / one actionable intent per symbol per bar timestamp (define in M12.2/M12.3) |
| Paper/live isolation | Operator paper-only; live still blocked by M8 policy/factory |
| Stale-data / hours | Inherit M11 gates every cycle |
| Retry/backoff | MD only with backoff; never blind order retry |
| Alerts | On kill, state load failure, repeated MD failures |
| Max exposure | Existing risk limits + operator max cycles / wall time |

---

# M13 — Live broker adapter behind hard gates

### 1. Milestone objective

Introduce **one** real broker adapter and a **multi-gate** live enablement path without weakening paper defaults; include account sync, idempotent orders, and basic fill reconciliation.

### 2. Why the milestone exists

- **Concrete gap:** No live broker; live explicitly rejected by factory/`mode_policy`.
- **Why this milestone (not earlier):** Paper fidelity + autonomous paper must be proven first (M11–M12).
- **Why this milestone (not M14):** M13 makes live *technically possible*; M14 makes a *trial* operationally acceptable.

### 3. Exact scope

**Likely to change/add**

- New broker implementation (concrete venue TBD — see Decision 5)
- `runtime/mode_policy.py` / `runtime/factory.py` — carefully expand allowed live wiring
- Reconciliation service (broker positions/orders vs local portfolio)
- Client order id / idempotency on `BrokerOrderRequest`
- Settings for live flags, base URL, keys
- Contract tests against broker **sandbox** if available

**Must remain unchanged unless gated**

- Default `trading_mode=paper`
- Paper and backtest paths behavior
- M10 Option A (backtest still no live executor)

**Integration with M1–M10**

```text
Live gates (M13.2) → factory may build live BrokerOrderExecutor
  → run_once (unchanged booking mapper)
  → reconcile before new exposure (M13.3)
Shadow mode (M13.4) must not place real-money orders
```

### 4. Explicit non-goals

- Multi-broker
- Production money trial (M14)
- Full advanced order types beyond MARKET (unless sandbox requires)
- Removing paper mode

### 5. Proposed sub-milestones

#### M13.1 — Broker adapter + sandbox contract tests

| | |
|---|---|
| **Objective** | Implement adapter against sandbox/paper-account API; no production enable. |
| **Implementation scope** | Adapter, auth, place/get status; secrets via env; live not reachable without later gates. |
| **Files/modules likely involved** | New broker module under `broker_interface/`, settings, tests. |
| **Dependencies** | M11–M12 complete; Decision 5 (venue) resolved. |
| **Safety constraints** | Credentials never logged; sandbox URL default. |
| **Tests required** | Contract tests mocked + optional sandbox integration marked. |
| **Acceptance criteria** | Adapter places/rejects in sandbox harness; unit suite green. |
| **Stop/review point** | Review and approve M13.1 before starting M13.2. |

#### M13.2 — Hard live enablement gates

| | |
|---|---|
| **Objective** | Define conjunctive gates for live (e.g. `trading_mode=live` AND `LIVE_TRADING_ENABLED=true` AND confirm token AND live adapter AND not backtest). |
| **Implementation scope** | mode_policy + factory + CLI refusal by default. |
| **Files/modules likely involved** | `runtime/mode_policy.py`, `runtime/factory.py`, `main.py`, tests. |
| **Dependencies** | M13.1. |
| **Safety constraints** | Any missing gate → fail-closed; paper unchanged. |
| **Tests required** | Matrix of gate combinations; regression paper/backtest. |
| **Acceptance criteria** | Live unreachable unless all gates set; documented. |
| **Stop/review point** | Review and approve M13.2 before starting M13.3. |

#### M13.3 — Idempotency + reconciliation basics

| | |
|---|---|
| **Objective** | Prevent duplicate live submits; reconcile broker vs local portfolio on cycle start. |
| **Implementation scope** | Client order ids; reconcile positions/open orders; abort on mismatch. |
| **Files/modules likely involved** | `broker_interface/orders.py`, reconciler module, runtime wiring, tests. |
| **Dependencies** | M13.2. |
| **Safety constraints** | Mismatch → kill new orders; alert. |
| **Tests required** | Duplicate submit; mismatch abort; recovery paths. |
| **Acceptance criteria** | Documented reconcile rules; tests prove fail-closed. |
| **Stop/review point** | Review and approve M13.3 before starting M13.4. |

#### M13.4 — Live shadow mode

| | |
|---|---|
| **Objective** | Run live signal path without sending live orders (or send to sandbox only) while paper executes. |
| **Implementation scope** | Shadow executor / compare logs. |
| **Files/modules likely involved** | Runtime/executor wiring, tests, docs. |
| **Dependencies** | M13.3. |
| **Safety constraints** | Shadow cannot place real money orders. |
| **Tests required** | Shadow never calls live `place_order` for real money. |
| **Acceptance criteria** | Shadow checkpoint unlocked. |
| **Stop/review point** | **M13 final audit** (live code path exists + gated). |

### 6. Safety architecture (M13 adds)

| Control | M13 definition |
|---|---|
| Paper/live isolation | Multi-gate conjunctive enablement; default deny |
| Secrets/API keys | Env-only; redacted logs; no commit of secrets |
| Max exposure/notional | New hard live caps (max notional / max orders per day) |
| Duplicate-order | Client order idempotency keys |
| Reconciliation | Broker vs local mismatch → abort new orders |
| Retry/backoff | Transient broker errors only with idempotent keys |
| Alerts | CRITICAL on gate bypass attempts, reconcile fail |
| Kill switch | Inherit M12; wire toward cancel-all in M14 |
| Stale-data / hours | Inherit M11 |

---

# M14 — Controlled live trial readiness

### 1. Milestone objective

Operationalize a **small, human-approved** real-money trial: emergency stop, alert channels, runbooks, evidence pack from paper soak + sandbox, tiny limits.

### 2. Why the milestone exists

- **Concrete gap:** Technical live ≠ validated trial.
- **Why this milestone (not M13):** Ops/process/limits/evidence belong here.

### 3. Exact scope

**Likely to change/add**

- Kill switch → cancel-all (broker) + local halt
- Email/webhook notifier implementations (settings fields already exist)
- Trial config: max notional, max loss, max trades, symbol allowlist
- Runbooks + `MILESTONE_14_SUMMARY.md`
- Evidence checklist binding M10 backtest + M12 soak + M13 sandbox

**Must remain unchanged**

- Paper/backtest default safety
- Requirement that all live gates remain conjunctive

### 4. Explicit non-goals

- Scaling capital
- Strategy research platform
- Unattended large live

### 5. Proposed sub-milestones

#### M14.1 — Emergency stop + live hard limits

| | |
|---|---|
| **Objective** | Stop before next order; cancel-all best-effort; enforce max notional/loss/trades. |
| **Implementation scope** | Wire kill switch to broker cancel-all; trial hard limits in settings/risk. |
| **Files/modules likely involved** | Kill switch, live broker adapter, risk/settings, tests. |
| **Dependencies** | M13 complete. |
| **Safety constraints** | Kill always halts new exposure even if cancel-all partially fails (alert). |
| **Tests required** | Kill engages; limits reject oversized orders; failure-injection on cancel-all. |
| **Acceptance criteria** | Documented emergency stop behavior; tests green. |
| **Stop/review point** | Review before M14.2. |

#### M14.2 — Alert channels for live events

| | |
|---|---|
| **Objective** | Webhook and/or email for CRITICAL/ERROR live events. |
| **Implementation scope** | Implement notifiers using existing `alert_email` / `alert_webhook_url` settings; preserve M8.5 “alert must not crash cycle” pattern. |
| **Files/modules likely involved** | `alerts/`, settings, tests. |
| **Dependencies** | M14.1. |
| **Safety constraints** | Alert failure logged; trading halt still controlled by kill/limits, not notifier success. |
| **Tests required** | Unit notifiers (mocked network); integration emit on kill/reconcile fail. |
| **Acceptance criteria** | CRITICAL path documented and tested with mocks. |
| **Stop/review point** | Review before M14.3. |

#### M14.3 — Trial runbook + evidence gate

| | |
|---|---|
| **Objective** | Written prerequisites; refuse trial start if checklist incomplete. |
| **Implementation scope** | Runbook doc; checklist config flag or script; bind strategy validation pack. |
| **Files/modules likely involved** | Docs, optional checklist helper, settings. |
| **Dependencies** | M14.2; M12.4 soak evidence; strategy validation pack. |
| **Safety constraints** | Missing checklist → cannot open trial gates. |
| **Tests required** | Checklist gate unit/integration. |
| **Acceptance criteria** | Incomplete evidence blocks trial start. |
| **Stop/review point** | Review before M14.4. |

#### M14.4 — Controlled trial protocol closure

| | |
|---|---|
| **Objective** | Dry-run of trial protocol in sandbox; final audit; docs. |
| **Implementation scope** | Protocol dry-run; `MILESTONE_14_SUMMARY.md`; final safety review. |
| **Files/modules likely involved** | Docs, optional sandbox protocol tests. |
| **Dependencies** | M14.1–M14.3. |
| **Safety constraints** | Real-money start requires human approval after audit. |
| **Tests required** | Sandbox protocol dry-run; full suite green. |
| **Acceptance criteria** | Small controlled real-money trial checkpoint unlocked by process (not by code alone). |
| **Stop/review point** | **M14 final audit**. |

### 6. Safety architecture (M14 adds)

| Control | M14 definition |
|---|---|
| Emergency kill | Local halt + best-effort broker cancel-all |
| Max exposure | Trial-specific tiny notional / loss / trade caps |
| Alerting | Webhook/email for CRITICAL live events |
| Secrets | Same as M13; trial credentials segregated if possible |
| Paper/live isolation | Trial cannot run without M13 gates + M14 checklist |
| Evidence gate | Strategy + soak + sandbox evidence required |

---

## 7. PAPER progression checkpoints

| Checkpoint | Meaning | Safe after |
|---|---|---|
| **A. First supervised real-market PAPER** | Human runs `run-once`/`run-session --paper` with Yahoo; watches fills vs MD | **M11.4** |
| **B. Multi-session supervised PAPER** | Multiple sessions same day; human between sessions; state may still be in-memory | **M11.4** (+ ops discipline); **stronger with M12.2** |
| **C. Unattended bounded PAPER** | Operator with interval + kill + bounds | **M12.3** |
| **D. Long-duration PAPER soak** | Overnight/multi-day with persistence + kill proven | **M12.4** |

---

## 8. LIVE progression

| Stage | Meaning | Prerequisites / gates |
|---|---|---|
| LIVE **code path exists** | Adapter merged, not enabled | **M13.1** |
| LIVE **sandbox/test env** | Sandbox orders work in tests/harness | **M13.1** (+ optional CI secret) |
| LIVE **technically enabled** | All conjunctive gates can open | **M13.2** + **M13.3** |
| LIVE **shadow validation** | Signals compared without real money (or sandbox only) | **M13.4** |
| **Small controlled real-money trial** | Tiny limits + kill + alerts + signed checklist | **M14.4** after M12.4 soak evidence + strategy validation pack |

---

## 9. Strategy validation (before LIVE trial)

Infrastructure readiness is not the same as trading quality. Proposed **measurable** gates (numeric thresholds to approve later):

| Evidence | Metric ideas (approve numbers later) |
|---|---|
| Backtest (M10) | Fixed period; same fees as `backtest_commission_pct`; report return, max DD, trades, win rate |
| Out-of-sample | Hold out last N% / last M months never used for tuning |
| Paper evidence | M12 soak: trade count, realized vs backtest bias, fill vs signal slippage |
| Drawdown | Session and cumulative DD vs `max_daily_loss_pct` behavior |
| Win/loss | Distribution, not just win rate |
| Sample size | Minimum closed trades before trial (TBD by approval) |
| Costs | Commissions modeled; paper slippage note if fill≠mid |
| Overfitting controls | No parameter search without OOS; freeze params before soak |

**Do not** require profitability to start paper.  
**Do** require a completed evidence pack before M14 trial.

---

## 10. Testing strategy by milestone

| Milestone | Unit | Integration | Regression | Safety | Failure-injection | Network/provider | Broker contract | Soak |
|---|---|---|---|---|---|---|---|---|
| M11 | Quote source, TTL, hours | Paper fill=MD | M8–M10 | Stale abort | Empty/old bars | Mocked Yahoo | PaperBroker only | No |
| M12 | Kill, bounds, store | Operator loop | M8–M11 | Kill/corrupt state | Kill mid-run | Mock MD | Paper | CI short + manual long |
| M13 | Adapter parsing, gates | Sandbox harness | Paper/backtest | Gate matrix | Dup order, mismatch | Mock HTTP | Sandbox | Optional |
| M14 | Limits, notifier | Kill→cancel | All prior | Checklist gate | Alert failure | N/A | Sandbox cancel-all | Protocol dry-run |

Preserve M1–M10: every milestone closure runs the **full suite**.

---

## 11. Git / review workflow

1. Design decisions in section 12 resolved → approve M11 start.  
2. For each `Mxx.y`: implement → focused + regression tests → **STOP for review** → approve → **then** commit (message `Milestone x.y - …`).  
3. No push until milestone-level final audit (same pattern as M10).  
4. No PR unless requested after push approval.  
5. Do not batch multiple sub-milestones in one commit.  
6. Do not amend pushed history.

---

## 12. Architectural decisions that MUST be resolved before M11.1

### Decision 1 — How paper fills obtain real prices

| | |
|---|---|
| **Option A** | Inject `QuoteSource` into `PaperBroker` (factory wires MD `get_price` / last bar). |
| **Option B** | Change `BrokerOrderExecutor` / runtime to override fill price post-broker (or bypass broker quote). |
| **Tradeoffs** | A keeps pricing in broker boundary (clean for M13). B couples runtime to paper pricing. |
| **Recommended** | **Option A** |
| **Reasoning** | Matches `Broker` abstraction; live adapters will also own pricing/acks. |

### Decision 2 — Canonical price for paper fills

| | |
|---|---|
| **Option A** | `market_data.get_price(symbol)` (last trade / fast info). |
| **Option B** | Last closed bar `close` from `get_bars`. |
| **Tradeoffs** | A closer to “live quote”; may disagree with strategy bar. B consistent with signal bar, lagging. |
| **Recommended** | **Option B for v1** (bar close used by strategy), document drift; optional A later. |
| **Reasoning** | Minimizes decision/fill mismatch for bar-based strategies (current EMA/RSI design). |

### Decision 3 — Where stale/hours gates run

| | |
|---|---|
| **Option A** | Inside `BasicTradingRuntime.run_once` early stage (e.g. `market_data` / new `data_quality` stage). |
| **Option B** | Only in CLI/operator wrappers. |
| **Tradeoffs** | A protects all entry points; touches runtime. B can miss programmatic use. |
| **Recommended** | **Option A** |
| **Reasoning** | Fail-closed for factory, session, and future operator uniformly. |

### Decision 4 — Market-hours default for M11 supervised paper

| | |
|---|---|
| **Option A** | Default `allow` / off for M11 supervised; enforce `reject` default in M12 operator. |
| **Option B** | Default `reject` immediately in M11. |
| **Tradeoffs** | A easier supervised Yahoo tests anytime; B safer but blocks off-hours experimentation. |
| **Recommended** | **Option A** with explicit setting |
| **Reasoning** | Matches Checkpoint A (supervised); M12 unattended should default reject. |

### Decision 5 — Live broker venue (inform for M13; not blocking M11)

| | |
|---|---|
| **Option A** | Alpaca (or similar) paper/live API — settings already hint `paper-api.example.com`. |
| **Option B** | Another broker (IBKR, etc.). |
| **Tradeoffs** | API complexity, sandbox quality, geo/regulation. |
| **Recommended** | **Defer choice to pre-M13.1**; not required for M11. |
| **Reasoning** | Does not affect paper fidelity work. |

---

## 13. Final roadmap table

| Sub-milestone | Objective | PAPER capability unlocked | LIVE relevance | Main safety gate |
|---|---|---|---|---|
| M11.1 | Align paper fills to MD | Faithful paper fills | Prerequisite | No fill without quote |
| M11.2 | Stale/missing MD gate | Safe real-data cycles | Prerequisite | TTL fail-closed |
| M11.3 | Market-hours policy | Hours-aware paper | Prerequisite | Hours reject policy |
| M11.4 | Supervised validation + docs | **Checkpoint A (B partial)** | Evidence | Documented supervised path |
| M12.1 | Kill + bounds | Safe autonomy foundation | Ops pattern | Kill + hard caps |
| M12.2 | Persistence/resume | Multi-session durability | Ops pattern | Corrupt state abort |
| M12.3 | Interval operator CLI | **Checkpoint C** | Ops pattern | Paper-only operator |
| M12.4 | Soak + closure | **Checkpoint D** | Evidence for trial | Soak + kill proven |
| M13.1 | Live adapter + sandbox | — | Code path / sandbox | Sandbox-only default |
| M13.2 | Multi-gate live enable | — | Technically enabled | Conjunctive gates |
| M13.3 | Idempotency + reconcile | — | Safe live ops | Mismatch abort |
| M13.4 | Shadow mode | — | Shadow validation | No real orders in shadow |
| M14.1 | Emergency stop + limits | — | Trial safety | Cancel-all + caps |
| M14.2 | Live alert channels | — | Trial safety | CRITICAL alerts |
| M14.3 | Runbook + evidence gate | — | Trial gate | Checklist required |
| M14.4 | Trial protocol closure | — | **Small real-money trial** | Human-approved start |

---

## 14. DESIGN READINESS VERDICT

# M11–M14 DESIGN NEEDS DECISIONS

**Must resolve before starting M11.1:**

1. **Decision 1** — Paper fill quote injection (**recommend A**: `QuoteSource` on `PaperBroker`)
2. **Decision 2** — Canonical price (**recommend B**: last bar close aligned with strategy)
3. **Decision 3** — Gate placement (**recommend A**: inside `run_once`)
4. **Decision 4** — Hours default for M11 (**recommend A**: permissive supervised default; strict for M12 operator)

Decision 5 (live venue) may wait until pre-M13.1.

---

## Document control

- This file is the formal M11–M14 design specification.
- Implementation must not begin until Decisions 1–4 are explicitly approved.
- Sub-milestone review/stop points in this document are authoritative for the M11–M14 workflow.
