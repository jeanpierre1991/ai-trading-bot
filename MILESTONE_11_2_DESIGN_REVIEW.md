# M11.2 DESIGN ONLY — Stale-market-data protection

**Read-only.** No files modified. No implementation/commit/push/PR.

---

## Current-state findings (inspection)

### 1. How bars represent timestamps
- `MarketBar` (`core/types.py`) is a `dict` with a `timestamp` property returning `self["timestamp"]`.
- Type is **not enforced** at the model layer (any object can be stored).
- Providers conventionally store a `datetime`.

### 2. Timezone awareness
| Source | Behavior |
|---|---|
| **Yahoo** | `_ensure_utc()`: naive → `tzinfo=UTC`; aware → `astimezone(UTC)`. Bars are **UTC-aware**. |
| **Mock** | `datetime.now(timezone.utc)` — **UTC-aware**. |
| **Historical tests / M10** | Explicit `tzinfo=timezone.utc` — **UTC-aware**. |
| **Contract** | No global invariant; Yahoo tests prove UTC normalization. |

### 3. YahooFinanceProvider bar construction
- `history.iterrows()` → `_ensure_utc(timestamp)` → `_row_to_market_bar(...)` with OHLCV Decimals, `symbol`, `timeframe.value`.
- Empty history → `ValueError` (runtime already maps `ValueError` at market_data stage).

### 4. ClosedBarQuoteSource (M11.1) price selection
- `get_bars(symbol=, limit=)` → **`bars[-1]`** → validate symbol/close → return close.
- **No timestamp / freshness check today.**

### 5. Safest location for freshness (aligns with approved Decision 3)
- **Primary:** inside `BasicTradingRuntime.run_once` after bars are fetched and non-empty, **before** strategy — so `run-once`, `run-session`, and any future operator share one gate.
- **Defense in depth:** shared pure validator also used by `ClosedBarQuoteSource` before returning a fill price (broker can re-fetch bars independently of the cycle’s bar list).

### 6–10. (Addressed in proposed architecture below)

---

## Critical interaction: wall-clock freshness vs M10 / weekends

**Wall-clock TTL against historical bars will always fail** (M10 timestamps are in the past).  
**Weekend/RTH gaps:** last Friday bar on Sunday can look “stale” even when it is the correct last closed bar.

| Concern | Boundary |
|---|---|
| **M11.2 (this milestone)** | Is the last bar’s timestamp **present, UTC-normalizable, not absurdly in the future, and not older than an allowed age vs injectable “now”?** |
| **M11.3 (later)** | Are we **allowed to trade in this wall-clock session** (RTH / extended / reject)? Session calendar may later **relax or reinterpret** age when the market is closed — **not in M11.2**. |

M11.2 must **not** invent market-hours logic; it must document that long calendar gaps can trip age checks until M11.3.

---

## Proposed architecture

```text
get_bars(...)
  → [run_once] validate last bar freshness (if enabled)
       fail → PipelineResult(success=False, stage=market_data|data_quality)
  → strategy → risk → intent
  → PaperBroker + ClosedBarQuoteSource
       → get_bars again → validate same freshness rules on bars[-1]
       → else QuoteUnavailableError → REJECTED (no FILLED)
```

**New pure module (recommended):** `market_data/freshness.py` (or `runtime/data_quality.py` if preferred — **recommend `market_data/freshness.py`** so providers/tests can unit-test without runtime).

```text
evaluate_bar_freshness(bar, *, now_utc, max_age, future_skew) -> FreshnessResult
normalize_bar_timestamp(ts) -> datetime UTC | error
default_max_age_for_timeframe(timeframe) -> timedelta
```

**No MarketBar schema change required** for M11.2 (read existing `timestamp` only).

### Enablement (mandatory for M10 safety)

Freshness **must be skippable** for historical replay:

| Path | Freshness |
|---|---|
| Factory paper / `run-once` / `run-session` (real/mock MD) | **ON** (settings) |
| M10 `BacktestRunner` / historical provider | **OFF** (explicit) |

**Recommended mechanism (pick one in implementation approval):**

- **Preferred:** `RuntimeContext.enforce_market_data_freshness: bool | None = None`  
  - `None` → use `settings.market_data_freshness_enabled`  
  - `BacktestRunner` sets `False` on every context  
- **Alternative:** detect `HistoricalRuntimeMarketData` and skip — more brittle; prefer explicit context/settings.

DryRun: same gate (no trading on unverifiable data) when freshness enabled — consistent with Decision 3.

---

## Exact files expected to change (when implementing)

| File | Role |
|---|---|
| `market_data/freshness.py` | **New** — pure validation + defaults |
| `market_data/__init__.py` | Export helpers |
| `config/settings.py`, `.env.example` | Freshness settings |
| `runtime/context.py` | Optional enforce flag |
| `runtime/trading_runtime.py` | Gate after bars, before strategy |
| `broker_interface/quotes.py` | Reuse validator in `ClosedBarQuoteSource` |
| `runtime/factory.py` | Pass settings/clock into quote source if needed (narrow) |
| `backtesting/runner.py` | Set freshness **off** on context |
| Tests: `tests/market_data/test_freshness.py`, runtime + quote-source integration | Deterministic `now` injection |
| Docs: brief README note only if needed for settings — **no** M11 summary yet |

**Unchanged:** `mode_policy` live rejects, M10 Option A (no BrokerOrderExecutor on backtest), DryRun fill math, PaperBroker quote-source contract from M11.1.

---

## Proposed configuration (no unsafe hardcoding)

| Setting | Type | Proposed default | Notes |
|---|---|---|---|
| `market_data_freshness_enabled` | `bool` | `True` | Master switch; backtest forces off via context |
| `market_data_max_age_seconds` | `int \| None` | `None` | `None` → derive from timeframe |
| `market_data_freshness_bar_periods` | `int` | `2` | Used when max_age is None: `periods × timeframe_seconds + slack` |
| `market_data_freshness_slack_seconds` | `int` | `120` | Clock/provider slack |
| `market_data_future_skew_seconds` | `int` | `60` | Allow small clock skew; beyond → anomaly |

**Derived max age when `max_age_seconds is None`:**

```text
timeframe_seconds(settings.default_timeframe)  # e.g. 1h → 3600
max_age = periods * timeframe_seconds + slack
```

Explicit `market_data_max_age_seconds` overrides derivation (ops can tighten/loosen without code change).

---

## Timestamp / timezone policy

1. **Canonical clock:** UTC.  
2. **Normalization:** naive → treat as UTC (same as Yahoo `_ensure_utc`); aware → convert to UTC.  
3. **Missing timestamp:** fail-closed.  
4. **Non-datetime timestamp:** fail-closed.  
5. **Future:** if `bar_ts > now_utc + future_skew` → fail-closed (clock anomaly).  
6. **Age:** `age = now_utc - bar_ts`; if `age > max_age` → **stale** → fail-closed.  
7. **Injectable `now`:** `Callable[[], datetime]` defaulting to `datetime.now(timezone.utc)` for tests.

---

## Freshness classifications

| Class | Condition | Action |
|---|---|---|
| **Fresh** | Valid UTC ts; not future-beyond-skew; `age ≤ max_age` | Continue |
| **Stale** | Valid ts; `age > max_age` | Abort / reject fill |
| **Missing timestamp** | Key absent / `None` | Abort / reject |
| **Invalid timestamp** | Wrong type / unnormalizable | Abort / reject |
| **Future / clock anomaly** | `bar_ts > now + skew` | Abort / reject |
| **Unverifiable** | Empty bars (already handled); get_bars errors | Abort / reject |

---

## Fail-closed rules

1. Stale/invalid/missing/future → **no strategy-driven new exposure** when gated in `run_once` (`success=False`, clear `aborted_reason`).  
2. Same classes in `ClosedBarQuoteSource` → `QuoteUnavailableError` → PaperBroker **REJECTED**, **no FILLED**, buying power unchanged (M11.1).  
3. Never invent timestamps or prices.  
4. Never fall back to legacy static quotes because data is stale.  
5. Backtest path must not wall-clock-reject historical bars (context/settings off).

---

## Weekend / market-closure boundary (no M11.3 logic)

- M11.2 **does not** ask “is the exchange open?”  
- Long gaps (weekend, holiday) may mark Friday’s last bar **stale** under a short H1-derived TTL.  
- **Mitigations without hours logic:** timeframe-scaled default age (`periods≥2`); allow explicit larger `market_data_max_age_seconds` for supervised paper; document limitation.  
- **M11.3** owns session calendar and may later define “outside hours → don’t trade” and/or “outside hours → don’t treat last RTH bar as operationally stale for *trading permission*” — separate concern.

---

## Deterministic testing strategy

- Inject fixed `now=` into freshness helpers and into quote source / runtime (test seam).  
- Cases: fresh boundary (`age == max_age` OK), stale (`age == max_age + 1` fail), missing ts, naive→UTC, aware non-UTC, future beyond skew, future within skew OK, NaN/invalid N/A for ts.  
- Integration: paper path with mocked bars + frozen now → no FILLED when stale.  
- Regression: DryRun unchanged when fresh; M10 backtest full path with freshness forced off; M11.1 price alignment still holds when fresh.  
- No network in unit tests.

---

## Compatibility / migration risks

| Risk | Mitigation |
|---|---|
| M10 false stale | BacktestRunner disables enforcement |
| Weekend false stale | Timeframe-scaled defaults + config override; defer hours to M11.3 |
| Double `get_bars` (runtime vs quote source) | Shared validator; same rules both places |
| Naive timestamps in ad-hoc tests | Normalize-as-UTC policy (document) |
| Breaking Bar API | **Avoid** — no required model change |
| Default `enabled=True` surprises mock tests with old fixtures | Prefer real UTC “now-relative” bars in mocks (session tests already use dated bars — may need `now` injection or fresh timestamps in a few tests) |

**Bar model/API change:** **Not required.** Optional later: document that `timestamp` should be UTC-aware `datetime`.

---

## Acceptance criteria (for future M11.2 implementation)

1. Fresh last bar → paper/dry-run cycle can proceed (subject to existing risk).  
2. Stale / missing / invalid / future-anomaly → no FILLED paper order; cycle aborts or execution REJECTED.  
3. Configurable max age; default derived from timeframe + periods + slack when unset.  
4. Deterministic tests with injected clock.  
5. M10 backtests still pass (freshness off on that path).  
6. M11.1 closed-bar pricing unchanged when data is fresh.  
7. No LIVE enablement; mode_policy/factory live rejects intact.  
8. No market-hours calendar implementation.  
9. Full suite green.

---

## Recommendation

# APPROVE DESIGN

Approve with these explicit implementation constraints when M11.2 coding starts:

1. Gate in `run_once` **and** reuse in `ClosedBarQuoteSource`.  
2. **Explicit freshness disable** on M10/historical contexts.  
3. **No MarketBar breaking change.**  
4. **Timeframe-derived default max age** + optional absolute override.  
5. **Document** weekend/RTH false-positive as out of scope until M11.3.  
6. **Injectable UTC clock** for tests.

Optional pre-implementation confirmations (non-blocking for “APPROVE DESIGN”):

- Default `market_data_freshness_bar_periods=2` vs `3`  
- Whether `RuntimeContext` flag is preferred over historical-type detection (**prefer context flag**)

---

**STOP.** Awaiting approval to implement M11.2.
