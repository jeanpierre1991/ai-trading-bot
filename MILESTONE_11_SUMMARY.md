# Milestone 11 Summary — Market-data paper fidelity

**Status:** Complete (M11.1–M11.4)  
**Mode:** `TRADING_MODE=paper` only — LIVE remains disabled by factory and mode_policy.

Milestone 11 made supervised paper trading use **real market-data prices** for decisions and simulated fills, with fail-closed freshness and XNYS session awareness.

---

## 1. Sub-milestones

| ID | Objective | Outcome |
|---|---|---|
| **M11.1** | Paper fills from last closed-bar close | `QuoteSource` / `ClosedBarQuoteSource` on `PaperBroker`; no static fallback when source set |
| **M11.2** | Stale / missing / invalid / future-anomaly gate | `market_data/freshness.py`; gate in `run_once` before strategy; quote defense |
| **M11.3** | Market hours / session awareness | XNYS calendar (2024–2027); session-aware freshness `T_ref`; hours `allow`/`reject` |
| **M11.4** | Supervised validation + docs | Mocked Yahoo E2E tests (no CI network); README; this summary; Checkpoint A checklist |

---

## 2. Paper path (architecture)

```text
get_bars (mock | yahoo)
  → mode policy (paper only)
  → empty bars abort
  → market hours gate (M11.3; policy=reject → stage=market_hours)
  → freshness gate (M11.2; session-aware T_ref when calendar present)
  → strategy → risk → intent
  → DryRunExecutor  OR  BrokerOrderExecutor(PaperBroker + ClosedBarQuoteSource)
       → fill price = last closed bar close (M11.1)
```

Backtests (`run-backtest` / `BacktestRunner`) set `enforce_market_data_freshness=False` and `enforce_market_hours=False` so historical replay stays deterministic (M10 isolation).

---

## 3. Key settings

| Setting | Default | Notes |
|---|---|---|
| `MARKET_DATA_PROVIDER` | `mock` | Prefer for CI/local; `yahoo` for supervised humans only |
| `MARKET_DATA_FRESHNESS_ENABLED` | `true` | Master freshness switch |
| `MARKET_DATA_FRESHNESS_BAR_PERIODS` | `2` | Used when max age unset |
| `MARKET_HOURS_ENABLED` | `true` | Hours policy gate master switch |
| `MARKET_HOURS_POLICY` | `allow` | Supervised M11 default; use `reject` for RTH-only / future unattended |
| `MARKET_HOURS_CALENDAR` | `xnys` | Only XNYS supported in M11.3 |
| `TRADING_MODE` | `paper` | `live` rejected |

---

## 4. Checkpoint A — First supervised real-market PAPER (checklist)

**Automated CI does not hit live Yahoo.** Use this checklist only on a supervised machine when you intentionally set `MARKET_DATA_PROVIDER=yahoo`.

1. Confirm `TRADING_MODE=paper` (never `live`).
2. Prefer starting with `MARKET_DATA_PROVIDER=mock` dry-run, then switch to `yahoo` for the supervised run.
3. Set `MARKET_DATA_FRESHNESS_ENABLED=true`.
4. Choose hours policy intentionally:
   - `allow` — may run off-hours (supervised experimentation)
   - `reject` — abort outside XNYS RTH (`stage_reached=market_hours`)
5. Run dry-run first: `python main.py run-once --symbol AAPL`
6. Run explicit paper: `python main.py run-once --paper --symbol AAPL --strategy ema_crossover`
7. Verify fill (if any) matches the last closed-bar close from market data — **not** a legacy static quote.
8. On empty/bad symbol/stale data: expect controlled abort / REJECTED; buying power unchanged on reject; no silent static fallback.
9. Optional: `python main.py run-session --cycles 2 --paper --symbol AAPL` (still supervised; stop on first failed cycle).
10. Do **not** leave an unattended loop running; M12 owns autonomy / kill / persistence.

---

## 5. Checkpoint B — Multi-session supervised PAPER (partial)

- Same day, multiple supervised `run-session` / `run-once` invocations are allowed **with a human between them**.
- Portfolio/order state is **in-memory** for a process: restarting the process resets state.
- Durable resume is **M12.2**, not M11.4.

---

## 6. Explicit non-goals (M11)

- LIVE trading / live broker enablement
- Daemon / interval unattended operator (M12)
- Exchange calendars beyond XNYS
- Strategy performance certification for real-money trial

---

## 7. Safety invariants preserved

- M8 factory + mode_policy reject `live`
- M9 bounded sessions (no infinite loops)
- M10 historical backtest: DryRun/Commission only; freshness/hours enforcement off via context
- M11.1–M11.3 fail-closed gates remain the source of truth; M11.4 validates and documents them

---

## 8. Tests (CI)

- `tests/runtime/test_m11_supervised_paper_path.py` — mocked Yahoo → dry-run success, paper fill==last close, empty/stale fail-closed
- Existing M11.2 / M11.3 / Yahoo provider unit tests remain network-free

---

## 9. Related docs

- Design: `MILESTONE_11_14_DESIGN_SPEC.md`, `MILESTONE_11_2_DESIGN_REVIEW.md`, `MILESTONE_11_3_DESIGN_REVIEW.md`, `MILESTONE_11_4_DESIGN_REVIEW.md`
- Prior closures: `MILESTONE_8_SUMMARY.md`, `MILESTONE_9_SUMMARY.md`, `MILESTONE_10_SUMMARY.md`
