# M11.4 DESIGN ONLY — Supervised real-market paper validation + docs

**Read-only.** No production/implementation files modified. No commit/push/PR.  
Awaiting approval before any M11.4 coding.

---

## Current-state findings (inspection)

### What M11.1–M11.3 already delivered
| Capability | Status |
|---|---|
| **M11.1** | Paper fills from closed-bar close via `QuoteSource` / `ClosedBarQuoteSource` |
| **M11.2** | Wall-clock freshness gate in `run_once` + quote defense; injectable UTC clock |
| **M11.3** | XNYS session calendar; session-aware freshness `T_ref`; hours policy `allow`/`reject` |
| **M10** | Historical backtest isolation (`enforce_*=False`); DryRun-only |
| **LIVE** | Factory + mode_policy still reject live |

### What already exists for Yahoo / docs
| Asset | State |
|---|---|
| `YahooFinanceProvider` | Implemented; injectable `ticker_factory` (tests already mock network) |
| `MarketDataModule` | Selects `mock` / `yahoo`/`yfinance` from settings |
| `tests/market_data/test_yahoo_provider.py` | Provider-level unit tests with mocks |
| CLI `run-once` / `run-session --paper` | Operational; default dry-run; paper explicit |
| README | Documents through **M10**; missing M11 freshness/hours/Yahoo supervised path |
| `MILESTONE_11_SUMMARY.md` | **Does not exist yet** |

### Parent-spec M11.4 (from `MILESTONE_11_14_DESIGN_SPEC.md`)
- **Objective:** Prove Yahoo (or configured provider) path end-to-end under supervision; document safe usage.
- **Scope:** Integration/smoke tests with **mocked network**; optional manual checklist; README + M11 summary; **no daemon**.
- **Safety:** Document `MARKET_DATA_PROVIDER=yahoo` risks; prefer `mock` in CI.
- **Acceptance:** Checklist for **Checkpoint A**; suite green; M11 closed in docs.
- **Stop:** M11 final audit before commit/push of M11 closure.

### Checkpoint mapping
| Checkpoint | M11.4 role |
|---|---|
| **A — First supervised real-market PAPER** | Primary deliverable (docs + mocked E2E proof + manual checklist) |
| **B — Multi-session supervised PAPER** | **Partial** — document ops discipline only; no persistence (M12.2) |

---

## 1. Exact objective

Close Milestone 11 by:
1. Proving (in CI, **without live network**) that a Yahoo-shaped market-data provider can drive a full paper cycle through M11.1–M11.3 gates with closed-bar fill alignment.
2. Publishing a **supervised operator checklist** for humans who optionally set `MARKET_DATA_PROVIDER=yahoo` and run `run-once` / `run-session --paper`.
3. Updating README + creating `MILESTONE_11_SUMMARY.md` so M11 is formally closed in docs.
4. **Not** enabling LIVE, daemons, unattended operators, or new trading strategies.

---

## 2. Proposed architecture (minimal)

M11.4 is primarily a **validation + documentation** milestone. Prefer composing existing pieces over new runtime architecture.

```text
[CI / automated]
  MockTicker / fake history DataFrame
    → YahooFinanceProvider(ticker_factory=...)
    → MarketDataModule-compatible get_bars
    → BasicTradingRuntime (freshness + hours + ClosedBarQuoteSource)
    → DryRun or PaperBroker fill
    → assertions: success/abort paths, fill == last close, no LIVE

[Human supervised — optional, not CI]
  .env: MARKET_DATA_PROVIDER=yahoo, TRADING_MODE=paper
    → main.py run-once|--paper / run-session --paper
    → operator follows Checkpoint A checklist
```

### Design principles
- **No network in CI** — all automated Yahoo path tests use `ticker_factory` mocks (existing pattern).
- **No new executor / broker / mode** — reuse paper factory path.
- **No daemon / interval operator** — deferred to M12.
- **Docs are first-class deliverables** — Checkpoint A is incomplete without them.
- **Fail-closed behavior is already implemented** — M11.4 documents and regression-tests it; does not re-architect gates.

---

## 3. Affected modules / files

### Expected to create
| File | Role |
|---|---|
| `tests/runtime/test_m11_supervised_paper_path.py` (name flexible) | End-to-end mocked Yahoo → paper/dry-run cycle with M11 gates |
| `MILESTONE_11_SUMMARY.md` | Formal M11 closure summary (Checkpoint A checklist included or linked) |
| `MILESTONE_11_4_DESIGN_REVIEW.md` | This design document (untracked until commit policy decided) |

### Expected to modify
| File | Role |
|---|---|
| `README.md` | M11 section: freshness, hours, Yahoo supervised usage, risks; update “Current Milestone” |
| `.env.example` | Brief comments pointing to M11 supervised settings (if not already sufficient) |

### Explicitly out of scope / do not change for M11.4 coding
| Area | Why |
|---|---|
| `runtime/mode_policy.py` / LIVE enablement | LIVE remains disabled |
| `YahooFinanceProvider` core fetch logic | Already sufficient; only test/docs unless a blocking bug is found |
| `BacktestRunner` architecture | M10 isolation already correct |
| Persistence / kill switch / interval operator | M12 |
| Strategy quality thresholds | Pre-LIVE (spec §9), not M11.4 |
| Audit artifacts `MILESTONE_11_*_FINAL_AUDIT.md` | External review; remain untracked unless separately approved |

---

## 4. Configuration changes

### Automated / defaults (no unsafe change)
| Setting | M11.4 stance |
|---|---|
| `MARKET_DATA_PROVIDER` | Default remains **`mock`** for CI/local safety |
| `TRADING_MODE` | Remains **`paper`** |
| `market_data_freshness_*` | Document; defaults unchanged (M11.2) |
| `market_hours_*` | Document; default `policy=allow` (M11.3 Decision A) for supervised experimentation |

### Supervised Yahoo profile (documentation only — operator sets manually)
```text
TRADING_MODE=paper
MARKET_DATA_PROVIDER=yahoo
# Recommended for first supervised runs:
MARKET_HOURS_POLICY=allow          # or reject if forcing RTH-only
MARKET_DATA_FRESHNESS_ENABLED=true
DEFAULT_SYMBOL=AAPL
DEFAULT_TIMEFRAME=1h
```

**No new required env vars** for M11.4 unless a thin optional flag is approved (not recommended). Prefer documenting existing knobs.

---

## 5. Runtime behavior (proposed validation scenarios)

### Automated (mocked Yahoo history)
1. **Happy path (fresh RTH-equivalent bars):** mocked bars with timestamps accepted under frozen clock + calendar → cycle can succeed; if `--paper` / paper executor, **fill price == last bar close**.
2. **Empty / provider error:** Yahoo mock returns empty → `ValueError` / market_data abort → no FILLED.
3. **Stale during RTH:** bar older than max age vs wall `T_ref` → freshness abort → no FILLED.
4. **Weekend + Friday bar + `policy=allow`:** session-aware freshness accepts last-session bar (M11.3 regression).
5. **`policy=reject` off-hours:** `stage_reached=market_hours` before strategy.
6. **LIVE still rejected:** factory/mode_policy regression (existing tests sufficient; cite in summary).

### Human supervised (manual checklist — not automated against live Yahoo)
1. Confirm `TRADING_MODE=paper`, provider `yahoo`, paper CLI flag explicit.
2. Prefer RTH or understand `allow` off-hours semantics.
3. Run `run-once --paper` (or dry-run first), compare printed/log fill vs last closed bar.
4. Abort paths: disconnect network / bad symbol → controlled failure, no silent static quote.
5. Optional second session same day (Checkpoint B partial): human between sessions; note in-memory portfolio reset on process restart.

---

## 6. Safety / fail-closed behavior

| Control | M11.4 expectation |
|---|---|
| Missing / empty Yahoo data | Abort; no FILLED; no static fallback when quote source wired |
| Stale / invalid / future-anomaly timestamps | M11.2 fail-closed preserved |
| Hours `reject` outside RTH | M11.3 abort at `market_hours` |
| CI network | **Forbidden** for M11.4 automated tests |
| LIVE | Must remain impossible via factory/mode_policy |
| Secrets | Yahoo needs no API key today; document rate-limit / ToS / non-guaranteed data quality risks |
| Daemon / infinite loop | Not introduced |

M11.4 should **not** weaken defaults (e.g. must not flip CI default provider to yahoo).

---

## 7. Testing strategy

| Layer | Content |
|---|---|
| **New integration test(s)** | Mocked `YahooFinanceProvider` → runtime paper/dry-run cycle; fill alignment; key abort paths |
| **Existing yahoo unit tests** | Remain; no network |
| **Existing M11.2 / M11.3 tests** | Remain green (regression) |
| **Full suite** | Must stay green; prefer `mock` provider in any app-level fixtures |
| **Manual checklist** | Documented in summary/README; **not** a CI gate requiring live Yahoo |

### Determinism
- Frozen clock + XNYS calendar for hours/freshness cases.
- Fixed OHLCV DataFrame in mock ticker history.
- Never call real `yf.Ticker` in CI.

---

## 8. Backward compatibility

| Concern | Stance |
|---|---|
| Default `MARKET_DATA_PROVIDER=mock` | Unchanged |
| CLI contracts `run-once` / `run-session` / `run-backtest` | Unchanged |
| M10 backtest network-free | Unchanged |
| Paper fill semantics (M11.1) | Unchanged |
| Freshness / hours gates | Unchanged behavior; better documented |
| Public APIs | No breaking MarketBar / Settings schema changes required |

---

## 9. Documentation deliverables

### README updates (proposed sections)
1. **Current Milestone → M11 complete** (after implementation).
2. **Market data freshness (M11.2)** — settings table + fail-closed note.
3. **Market hours (M11.3)** — XNYS, `allow`/`reject`, coverage 2024–2027.
4. **Supervised Yahoo paper (M11.4 / Checkpoint A)** — how to run; risks; prefer mock in CI; never confuse with LIVE.
5. Link to `MILESTONE_11_SUMMARY.md`.

### `MILESTONE_11_SUMMARY.md` (proposed contents)
1. M11.1–M11.4 objectives and commit references (filled at close).
2. Architecture diagram of paper path with quote source + freshness + hours.
3. **Checkpoint A checklist** (copy-pasteable for operators).
4. Checkpoint B partial notes (multi-session discipline; persistence → M12).
5. Explicit non-goals: LIVE, daemon, unattended operator.
6. Safety invariants preserved (M8–M10).
7. Pointer to design/audit artifacts (without requiring audit files in git).

---

## 10. Acceptance criteria

1. Automated mocked-Yahoo paper path test(s) prove closed-bar fill alignment and critical fail-closed aborts **without network**.
2. README documents M11 freshness, hours, and supervised Yahoo usage + risks.
3. `MILESTONE_11_SUMMARY.md` exists and includes Checkpoint A checklist.
4. Full test suite green; CI remains on `mock` by default.
5. LIVE still rejected; no daemon/operator introduced.
6. M10 / M11.1 / M11.2 / M11.3 invariants unchanged.
7. M11 final audit (separate step) can be performed before commit/push of M11.4 closure.

---

## 11. Implementation sequence (after design approval)

1. Add mocked Yahoo → runtime integration test(s) for happy path + 1–2 fail-closed paths.  
2. Update README (M11 settings + supervised Yahoo + current milestone).  
3. Write `MILESTONE_11_SUMMARY.md` with Checkpoint A checklist.  
4. Light `.env.example` comment touch-ups if needed.  
5. Run focused + full suites.  
6. Stop for M11.4 / M11 closure review (no commit until approved).

---

## 12. Risks, limitations, decisions needing approval

### Risks / limitations
| Item | Notes |
|---|---|
| Live Yahoo quality / rate limits / gaps | Document only; not “fixed” by M11.4 |
| Supervised off-hours with `policy=allow` | By design; operators must understand |
| Checkpoint B without persistence | Portfolio resets on process restart — document |
| Calendar coverage ends 2027 | Already M11.3 maintenance item |
| Mocked E2E ≠ live Yahoo proof | Checklist required for Checkpoint A human evidence |

### Decisions for approval before implementation

**Decision A — Scope of production code**  
- **Recommended:** Tests + README + `MILESTONE_11_SUMMARY.md` (+ minor `.env.example` comments only). **No** runtime/factory behavior changes unless a blocking bug is discovered.  

**Decision B — Executor under automated E2E**  
- **Recommended:** Cover **both** dry-run success path and paper (`BrokerOrderExecutor` + `ClosedBarQuoteSource`) fill-price assertion with mocks.  

**Decision C — Live network tests**  
- **Recommended:** **None** in CI. Manual checklist only for real Yahoo.  

**Decision D — Checkpoint B depth**  
- **Recommended:** Documentation/ops notes only (partial B); no multi-process state work.  

**Decision E — Commit packaging**  
- **Recommended:** M11.4 commit includes summary + README + tests; keep `*_FINAL_AUDIT.md` files untracked unless explicitly requested later.

---

## 13. Compatibility with prior milestones

| Milestone | Preserved by M11.4 design |
|---|---|
| M8 mode guards / factory paper-only | Yes |
| M9 session bounds | Yes — documented for supervised multi-cycle |
| M10 historical isolation | Yes — untouched |
| M11.1 pricing | Validated, not changed |
| M11.2 freshness | Validated/documented |
| M11.3 hours | Validated/documented |
| LIVE disabled | Explicit acceptance criterion |

---

## Recommendation

# APPROVE DESIGN (with Decisions A–E)

M11.4 should be a **thin validation + documentation closure** of Milestone 11, not a new trading subsystem. Primary engineering work is mocked end-to-end proof + operator checklist + README/summary.

---

**STOP.** Awaiting approval to implement M11.4.
