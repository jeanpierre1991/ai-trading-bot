# M11.3 DESIGN ONLY — Market Hours / Session Awareness

**Read-only.** No implementation files modified. No commit/push/PR.  
**Do not** modify or commit `MILESTONE_11_2_FINAL_AUDIT.md` (external-review artifact).

---

## Current-state findings (inspection)

### What exists today (post-M11.2)
| Area | State |
|---|---|
| **Freshness** | `market_data/freshness.py` — wall-clock TTL vs injectable UTC `now`; fail-closed for missing/invalid/stale/future-anomaly |
| **Runtime gate** | `BasicTradingRuntime.run_once` after non-empty bars, **before** strategy |
| **Quote defense** | `ClosedBarQuoteSource` reuses same freshness helpers before fill price |
| **M10 isolation** | `BacktestRunner` sets `enforce_market_data_freshness=False` |
| **Hours / calendar** | **None** — M11.2 explicitly deferred weekend/RTH false-stale |
| **Defaults** | Bot is US-equity oriented (`DEFAULT_SYMBOL=AAPL`, Yahoo/mock); paper-only factory / mode_policy |
| **Deps** | No calendar library; Python stdlib `zoneinfo` available (3.9+) |
| **LIVE** | Still rejected by factory + mode_policy |

### Approved parent spec (`MILESTONE_11_14_DESIGN_SPEC.md`)
- **M11.3 objective:** Explicit market-hours policy for paper; start simple (US equity RTH or configurable window); settings-driven.
- **Decision 3:** Hours/stale gates inside `run_once` (not CLI-only).
- **Decision 4:** Default hours policy **permissive for M11 supervised** (`allow` / off); stricter `reject` intended for M12 unattended operator.
- **Safety:** Outside hours + `reject` → no new orders; fail-closed.

### Problem M11.3 must solve (beyond “reject outside hours”)
Wall-clock M11.2 TTL treats **legitimate closed-market age** (overnight, weekend, holiday, early close → reopen gap) as **stale**, even when the last bar is the correct last regular-session close. M11.3 must make the system **session-aware** so freshness and trading permission are consistent with real US equity sessions — without weakening fail-closed behavior when data is truly bad.

---

## 1. Exact objective and acceptance criteria

### Objective
Make paper decision cycles **aware of US equity market sessions** so that:
1. **Freshness** does not falsely reject the last valid regular-session bar merely because the exchange is closed.
2. An explicit **market-hours policy** can allow or reject trading outside regular hours (supervised paper vs future unattended operator).
3. M10 / M11.1 / M11.2 contracts remain intact; LIVE stays disabled.

### Acceptance criteria
1. During **RTH**, M11.2 wall-clock freshness semantics remain in force (stale during an open session still fails closed).
2. During **overnight / weekend / holiday / after early close**, a last bar that is valid for the **most recent completed regular session** is **not** rejected as wall-clock-stale solely due to closure duration.
3. Truly missing / invalid / future-anomalous timestamps still fail closed (session awareness must not invent timestamps or skip structural checks).
4. Bars that are stale **relative to the last completed session** (e.g. Thursday bar when Friday has already closed) still fail closed.
5. With `market_hours_policy=reject`, outside allowed session → cycle abort (or equivalent) with **no new FILLED paper order**.
6. With supervised default policy (`allow` / hours gate off — see Decision A below), off-hours cycles may proceed **only if** session-aware freshness still passes.
7. M10 backtests remain deterministic and **do not** consult live wall-clock session calendars for gates.
8. M11.1 closed-bar pricing unchanged when data is session-fresh and trading is allowed.
9. No LIVE enablement; factory/mode_policy live rejects unchanged.
10. Focused + full suites green; deterministic frozen-clock tests cover the matrix in §9.

---

## 2. Current components/files M11.3 would interact with

| Component | Interaction |
|---|---|
| `market_data/freshness.py` | Extend or wrap: session-aware `effective_now` / max-age reference; keep pure helpers |
| `runtime/trading_runtime.py` | Add market-hours gate; pass session snapshot into freshness evaluation |
| `broker_interface/quotes.py` (`ClosedBarQuoteSource`) | Reuse same session calendar + session-aware freshness (defense in depth) |
| `runtime/factory.py` | Wire calendar + hours settings into runtime/quote source |
| `runtime/context.py` | Optional `enforce_market_hours` (mirror freshness override pattern) |
| `backtesting/runner.py` | Explicitly disable hours enforcement (and keep freshness off) |
| `config/settings.py`, `.env.example` | Hours policy, calendar id, enable flags |
| `runtime/session.py` / CLI | No rewrite required if gates live in `run_once` |
| Tests | New session/hours/freshness-integration tests; fixture adaptations |

**Unchanged by design:** `mode_policy`, LIVE paths, `MarketBar` schema, M10 DryRun-only architecture, PaperBroker static-fallback rules when quote source unset.

---

## 3. Proposed market-session abstraction

### Responsibilities
A pure, injectable **session calendar** that answers, for a given UTC instant:
- What is the exchange session state?
- What are today’s (or the relevant day’s) regular open/close (including early close)?
- What is the **last completed regular-session close** (for freshness reference)?
- Is **RTH trading** currently allowed?
- Is the instant in pre-market / after-hours (informational; optional policy later)?

It does **not**: fetch market data, place orders, invent bar timestamps, or enable LIVE.

### Interface (proposed)

```text
SessionState =
  OPEN_RTH
  | PRE_MARKET
  | AFTER_HOURS
  | CLOSED_OVERNIGHT
  | CLOSED_WEEKEND
  | CLOSED_HOLIDAY
  | UNKNOWN_UNAVAILABLE   # fail-closed sentinel

SessionSnapshot (frozen):
  state: SessionState
  exchange_id: str                 # e.g. "XNYS"
  tz_name: str                     # e.g. "America/New_York"
  as_of_utc: datetime
  session_date: date | None        # trading date if defined
  regular_open_utc: datetime | None
  regular_close_utc: datetime | None   # early-close aware when that day
  last_completed_regular_close_utc: datetime | None
  is_rth_open: bool
  is_early_close_day: bool
  reason: str | None               # when unavailable / closed

SessionCalendar (Protocol):
  resolve(now_utc: datetime) -> SessionSnapshot
```

Optional thin helpers (same module):
- `freshness_reference_now(snapshot, wall_now_utc) -> datetime`
- `is_trading_permitted(snapshot, policy) -> bool`

### Ownership
**Recommended:** `market_data/session_calendar.py` (+ optional `market_data/calendars/us_equity_xnys.py` or static data beside it).

Rationale: calendar is market-domain knowledge (like freshness), reusable by runtime and quote source without importing CLI/session-runner concepts. Matches M11.2 placement of `freshness.py` under `market_data/`.

Alternative `runtime/market_hours.py` couples calendar to runtime; weaker for quote-source reuse.

### Dependency injection
- Construct calendar from settings in **factory** (composition root).
- Inject into `BasicTradingRuntime` and `ClosedBarQuoteSource` (same instance preferred).
- Injectable UTC **clock** remains the source of `now` (M11.2 seam); calendar is a pure function of `now_utc` + static rules/data.
- Tests inject a **FakeSessionCalendar** or frozen clock + real US calendar.

### Timezone handling
- Canonical evaluation clock: **UTC** (existing bot convention).
- Exchange local zone: **`America/New_York`** via stdlib `zoneinfo.ZoneInfo` (no pytz required).
- Convert `now_utc` → exchange local for weekday/holiday/open-close comparisons; convert session bounds back to UTC for comparisons with bar timestamps.
- Naive datetimes in calendar APIs: **reject / fail-closed** (callers must pass UTC-aware), consistent with freshness’s explicit normalize policy for **bars** only.

---

## 4. How to determine session facets (US equity / XNYS scope)

| Facet | Determination (M11.3 proposed) |
|---|---|
| **Regular trading hours** | Mon–Fri **09:30–16:00** America/New_York on a scheduled trading day (inclusive open, exclusive close — exact boundary rule to lock in implementation approval). |
| **Weekends** | Local Saturday/Sunday → `CLOSED_WEEKEND` (unless ever listed as special session — out of scope). |
| **Exchange holidays** | Date in curated **full-day holiday** set for XNYS → `CLOSED_HOLIDAY`. |
| **Early-close sessions** | Date in curated **early-close** map (e.g. 13:00 ET) → RTH ends at early close; after that → `AFTER_HOURS` / overnight closed semantics with `is_early_close_day=True`. |
| **Pre-market** | Trading day, local time before regular open (e.g. before 09:30) → `PRE_MARKET`. |
| **After-hours** | Trading day, local time after regular/early close → `AFTER_HOURS`. |
| **Overnight** | After prior close until next pre-market (non-weekend/holiday) → `CLOSED_OVERNIGHT` or fold into after-hours + pre-market; either is fine if `last_completed_regular_close_utc` is correct. |

**Not in M11.3:** auction quirks, halt calendars, LULD, symbol-specific trading status, options/futures product hours, multi-exchange routing by symbol.

---

## 5. Single calendar vs multi-calendar architecture

### Recommendation: **single US equity (XNYS) calendar behind a Protocol**

| Approach | Verdict |
|---|---|
| **Multi-exchange registry now** | Overkill — bot default symbol is AAPL; paper path is US equity; adds unused complexity |
| **Hard-code only, no Protocol** | Acceptable short-term, harder to fake in tests / extend later |
| **Protocol + one `UsEquityXnysCalendar` implementation** | **Preferred** — minimal scope, clean tests, future EUR/crypto without rewriting gates |

Do **not** build a multi-calendar selection UI or per-symbol MIC router in M11.3. Settings may carry `market_hours_calendar=xnys` as a closed enum with a single allowed value for now (fail-closed on unknown).

---

## 6. Exact interaction: session awareness × M11.2 freshness

### Shared rule (proposed)
Structural freshness checks **always** run when freshness is enforced:
- missing / invalid timestamp → fail
- future beyond skew → fail

**Age check** uses a **session-aware reference time** `T_ref`:

```text
if snapshot.is_rth_open:
    T_ref = wall_now_utc          # current M11.2 behavior
else:
    T_ref = snapshot.last_completed_regular_close_utc
    if T_ref is None:
        fail-closed (calendar unavailable)
    # age = T_ref - bar_ts   (same compare as today: age > max_age → stale)
```

Interpretation: when the market is closed, “how old is this bar?” means “how late was it relative to the last time the market finished a regular session?”, **not** “how long has the wall clock been sitting still since Friday.”

### Scenario matrix

| Situation | Hours policy `allow` (M11 supervised default) | Hours policy `reject` | Freshness age vs |
|---|---|---|---|
| **RTH open, bar recent** | Proceed | Proceed | `wall_now` |
| **RTH open, bar stale vs wall** | Abort freshness | Abort freshness | `wall_now` (true stale) |
| **After normal close (same day)** | May proceed if bar OK vs today’s close | Abort hours (no new orders) | last completed close (= today’s close) |
| **Overnight** | May proceed if bar OK vs prior close | Abort hours | prior regular close |
| **Weekend** | May proceed if Friday close bar OK | Abort hours | Friday (last) regular close |
| **Holiday** | May proceed if prior session bar OK | Abort hours | last completed regular close before holiday |
| **Early-close day after 13:00 ET** | May proceed if bar OK vs early close | Abort hours | early close as last completed close |
| **Reopen (Mon pre-market / open)** | Pre-market: allow policy may proceed; at open: wall_now again | reject until RTH | pre/closed → last Friday close; once RTH open → wall_now |
| **Thursday bar after Friday already closed** | Stale vs Friday close | Stale (and/or hours) | last completed close |

### Gate ordering in `run_once` (proposed)

```text
mode policy
  → get_bars
  → empty bars abort
  → resolve SessionSnapshot(now)
  → [optional] market_hours policy gate   # abort stage=market_hours if reject & !RTH
  → freshness (session-aware T_ref)       # abort stage=market_data if fail
  → strategy → risk → …
```

**ClosedBarQuoteSource:** apply the **same** `T_ref` freshness rules (and optionally refuse quotes when hours policy would reject — recommended for defense in depth when policy=`reject`, so a stray executor path cannot fill off-hours).

### What M11.3 must **not** do
- Disable freshness entirely whenever the market is closed.
- Treat “market closed” as a reason to invent prices or fall back to static quotes.
- Use historical-provider type detection to skip hours (use explicit context/settings, like M11.2).

---

## 7. Fail-closed behavior

| Failure | Behavior |
|---|---|
| Session/calendar state cannot be determined | `SessionState.CALENDAR_UNAVAILABLE` → abort cycle / quote unavailable; **no FILLED** |
| Invalid / missing timezone (`ZoneInfo` failure) | Fail closed at calendar construction or resolve |
| Calendar data unavailable (date outside curated coverage window) | Fail closed (do not silently assume Mon–Fri RTH) |
| Holiday/early-close tables missing required year | Fail closed for that date |
| Timestamps conflict with session state (e.g. bar far in future; bar after claimed last close beyond skew in ways that imply clock anomaly) | Existing future-anomaly / stale rules; do not “repair” bar timestamps |
| Hours policy=`reject` and not RTH | Abort; no strategy-driven new exposure / no FILLED |
| Freshness structural failure while closed | Still abort (session awareness does not waive missing/invalid/future) |

**Never:** invent session closes, invent bar timestamps, or fall back to legacy static quotes because the calendar failed.

---

## 8. External dependency / calendar library

### Is a library necessary?
**No — not for M11.3 minimal safe scope.**

US equity RTH + weekends are trivial; holidays and early closes are a **finite curated table** for a bounded coverage window (recommend shipping **2024–2027** XNYS holidays/early closes in-repo, extend later).

### Comparison (do **not** install yet)

| Option | Pros | Cons | M11.3 fit |
|---|---|---|---|
| **In-repo static XNYS tables + `zoneinfo`** | Zero new deps; deterministic; auditable; matches AAPL/US paper scope | Must maintain holiday/early-close lists yearly | **Recommended** |
| **`exchange_calendars`** | Accurate XNYS holidays/early closes; MIC-based | New dependency (numpy/pandas stack typical); version drift; heavier for this bot | Optional later adapter |
| **`pandas_market_calendars`** | Convenient pandas schedules | Pulls `exchange_calendars` + pandas; heaviest | **Not recommended** for M11.3 |

### Justification
Current `requirements.txt` is intentionally small. M11.3’s need is **correct gated behavior for US equities**, not a global exchange platform. An interface leaves room to swap in `exchange_calendars` behind `SessionCalendar` in a later milestone if maintenance cost of static tables becomes the bottleneck.

---

## 9. Testing strategy (deterministic / frozen clock)

All tests: inject frozen `now_utc` + real or fake calendar; **no network**.

| Case | Expectation (policy=`allow`, freshness on) | Expectation (policy=`reject`) |
|---|---|---|
| Normal market day mid-RTH, fresh bar | success path possible | success path possible |
| Before open (pre-market), last session bar OK | freshness OK | hours abort |
| During session, fresh | OK | OK |
| After close, last RTH bar OK | freshness OK | hours abort |
| Weekend, Friday close bar | freshness OK | hours abort |
| Holiday, prior close bar | freshness OK | hours abort |
| Early close afternoon | freshness vs early close | hours abort after early close |
| Transition into open (09:30) | switch `T_ref` to wall_now | trading becomes allowed |
| Transition out of open (16:00 / early) | switch `T_ref` to that close | trading becomes rejected |
| Stale data during active RTH | freshness abort | freshness abort |
| Calendar date outside coverage | fail closed | fail closed |
| M10 backtest with hours+freshness settings ON | still runs (context disables both) | same |

Also: quote-source REJECTED + buying power unchanged; no static `190.25` fallback when quote source configured.

---

## 10. M10 backtesting isolation

| Mechanism | Proposed |
|---|---|
| Context | `enforce_market_hours=False` on every `BacktestRunner` context (mirror freshness) |
| Freshness | Keep existing `enforce_market_data_freshness=False` |
| Calendar | Must not be required for backtest success; if injected, must be skipped when enforce flag is False |
| Determinism | No wall-clock session lookups affecting historical replay outcomes |

Do **not** detect `HistoricalRuntimeMarketData` by type to skip hours.

---

## 11. M11.1 PaperBroker pricing intact

- Fill price remains last closed-bar **close** via `QuoteSource`.
- Session/hours logic may **reject** a quote as unavailable; it must **not** change the price formula when allowed.
- No return to legacy static map when a quote source is configured.
- DryRun path unchanged aside from earlier abort reasons.

---

## 12. Risks, edge cases, coupling

| Risk | Mitigation |
|---|---|
| Wrong holiday/early-close table | Bounded coverage + fail-closed outside; unit tests for known dates (e.g. Jul 4, Thanksgiving early close) |
| DST transitions (US) | Use `ZoneInfo`; test a spring/fall boundary instant |
| Dual gate confusion (hours vs freshness abort reasons) | Distinct `aborted_reason` prefixes / `stage_reached` (`market_hours` vs `market_data`) |
| Policy=`allow` still trading off-hours on stale Yahoo | Session-aware freshness still enforces last-session quality |
| Quote source vs runtime clock/calendar divergence | Factory injects **same** calendar (+ prefer documenting shared clock; M11.2 audit already noted shared-clock gap) |
| Over-coupling freshness to calendar | Keep `evaluate_bar_freshness` pure; pass `now_utc=T_ref` from a thin adapter |
| Coverage window expiry (post-2027) | Fail closed loud; ops bump table in a small follow-up |
| Treating Yahoo extended-hours prints as RTH | M11.3 policies speak to **permission** + **last regular close**; extended prints as strategy input remain a data-quality concern (document; do not fake) |
| LIVE creep | No mode_policy/factory changes that allow live |
| Architectural coupling to SessionRunner | Gates in `run_once` only |

---

## 13. Exact proposed files to create/modify

### Create
| File | Role |
|---|---|
| `market_data/session_calendar.py` | Protocol, snapshot, `freshness_reference_now`, policy helper |
| `market_data/calendars/us_equity_xnys.py` (or `.../xnys_data.py`) | RTH rules + holiday/early-close tables + `UsEquityXnysCalendar` |
| `tests/market_data/test_session_calendar.py` | Calendar unit tests |
| `tests/runtime/test_market_hours.py` | Runtime hours + session-aware freshness integration |
| `tests/broker_interface/test_quote_source_session_freshness.py` (or extend existing) | Quote defense |

### Modify
| File | Role |
|---|---|
| `market_data/__init__.py` | Export calendar types |
| `config/settings.py`, `.env.example` | Hours/calendar settings |
| `runtime/context.py` | `enforce_market_hours: bool \| None = None` |
| `runtime/trading_runtime.py` | Hours gate + session-aware freshness `T_ref` |
| `broker_interface/quotes.py` | Inject calendar; session-aware freshness |
| `runtime/factory.py` | Wire calendar + settings |
| `backtesting/runner.py` | `enforce_market_hours=False` |
| Existing tests | Fixture adaptations only as needed |

### Explicitly out of scope / do not touch for M11.3 coding
- `MILESTONE_11_2_FINAL_AUDIT.md`
- LIVE adapters / mode_policy relaxations
- `MarketBar` schema break
- M11.4 docs summary (unless tiny settings README note after approval)

---

## 14. Implementation sequence (small reviewable substeps)

1. **Calendar core** — `SessionSnapshot` + `UsEquityXnysCalendar.resolve` (RTH, weekend) with frozen-clock tests.  
2. **Holiday + early-close tables** — curated data + fail-closed outside coverage.  
3. **`freshness_reference_now`** — unit tests for open vs closed `T_ref` without runtime.  
4. **Settings + context flags** — defaults per Decision A; `.env.example`.  
5. **Runtime hours gate** — `reject` vs `allow` / off; stage `market_hours`.  
6. **Runtime freshness uses `T_ref`** — preserve M11.2 open-session behavior.  
7. **ClosedBarQuoteSource parity** — same calendar + `T_ref`; reject path unchanged otherwise.  
8. **Factory wiring** — single calendar instance.  
9. **BacktestRunner** — `enforce_market_hours=False`.  
10. **Integration tests** — matrix §9; full suite; stop for M11.3 audit before commit.

---

## 15. Design decisions requiring approval BEFORE implementation

### Decision A — Default market-hours **trading** policy for M11.3
Align with parent **Decision 4**:
- **Recommended:** `market_hours_policy=allow` (or `off`) by default for supervised M11 paper.  
- Session-aware freshness still active whenever freshness is enforced.  
- Document that M12 unattended operator should default to `reject`.

**Alternative:** Default `reject` immediately (safer unattended, harsher supervised Yahoo anytime).

### Decision B — Session-aware freshness when closed (core of this milestone)
- **Recommended:** Age against `last_completed_regular_close_utc` when not RTH-open; structural checks unchanged.  
- **Reject:** “Disable age check entirely when closed” (too weak — old bars could pass).

### Decision C — Calendar data source
- **Recommended:** In-repo XNYS static tables + `zoneinfo` (no new pip dependency).  
- **Alternative:** Add `exchange_calendars` now (heavier; more complete).

### Decision D — Hours gate severity
- **Recommended:** When `reject` and not RTH → abort entire cycle before strategy (`stage_reached=market_hours`).  
- **Alternative:** Allow HOLD path / only block actionable intents (more complex, easier to miss a fill path).

### Decision E — Quote source off-hours under `reject`
- **Recommended:** Quote source also refuses (defense in depth).  
- **Alternative:** Rely only on runtime hours gate.

### Decision F — Coverage window
- **Recommended:** Ship holidays/early closes for **2024–2027** inclusive; outside → fail closed.  
- Confirm years with approver if different.

### Decision G — Exact RTH boundary semantics
- **Recommended:** Open inclusive / close exclusive in exchange local time (09:30 ≤ t < 16:00), early-close analogous.  
- Lock this before coding boundary tests.

---

## Proposed settings (draft — finalize with Decisions A/C/F)

| Setting | Type | Proposed default | Notes |
|---|---|---|---|
| `market_hours_enabled` | bool | `True` | Master switch for hours **policy gate** |
| `market_hours_policy` | `allow` \| `reject` | `allow` | Per Decision A / parent Decision 4 |
| `market_hours_calendar` | str | `xnys` | Only `xnys` supported in M11.3 |
| `market_hours_timezone` | str | `America/New_York` | Must match calendar |
| *(existing)* freshness settings | — | unchanged | Session-aware `T_ref` when freshness enforced |

Context:
- `enforce_market_hours: bool | None = None` — `None` → settings; `False` for M10.

---

## Compatibility / preservation checklist

| Constraint | Design stance |
|---|---|
| M10 isolation | Explicit context disable hours + existing freshness disable |
| M11.1 pricing | Unchanged close price when permitted |
| M11.2 freshness | Preserved in RTH; reinterpreted reference time when closed |
| Fail-closed | Calendar/hours/freshness failures → no FILLED |
| PAPER/LIVE separation | No LIVE changes |
| LIVE disabled | Factory/mode_policy untouched for enablement |

---

## Recommendation

# APPROVE DESIGN (with confirmations)

Approve M11.3 design to proceed to implementation **after** explicit confirmation of Decisions **A–G** (especially A, B, C, D).

Minimal safe scope:
1. **XNYS-only** calendar behind a Protocol.  
2. **No new pip dependency** (static tables + `zoneinfo`).  
3. **Session-aware freshness `T_ref`** when not RTH-open.  
4. **Hours policy gate** in `run_once` (+ quote defense), default **allow** for supervised M11.  
5. **Explicit backtest disable** of hours enforcement.

---

**STOP.** Awaiting approval to implement M11.3.
