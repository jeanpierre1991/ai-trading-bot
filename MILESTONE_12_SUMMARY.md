# Milestone 12 Summary — Autonomous bounded paper operator

**Status:** Complete (M12.1–M12.4)  
**Mode:** `TRADING_MODE=paper` only — LIVE remains disabled by factory and mode_policy.

Milestone 12 adds an **unattended but hard-bounded** paper/dry-run operator with kill switch, durable portfolio/order state, interval scheduling, CLI, CI soak evidence, and operational runbooks — still paper-only.

---

## 1. Sub-milestones

| ID | Objective | Outcome |
|---|---|---|
| **M12.1** | Kill switch + hard bounds + operator skeleton | `PaperOperator` + `FileEnvKillSwitch`; bounds validated; A2/B1 |
| **M12.2** | Atomic persistence + safe resume | JSON state store; D1 equity baseline; corrected E1 idempotency |
| **M12.3** | Interval loop + CLI | Injectable sleeper; `run-paper-operator`; Decision H exits |
| **M12.4** | Soak + FS preflight + closure docs | CI mock soak; start-time state writability; Checkpoint C/D docs |

---

## 2. Architecture

```text
CLI run-paper-operator
  → factory (dry_run | paper only; LIVE rejected)
  → PaperOperator
       kill check (file / PAPER_OPERATOR_KILL)
       → state parent writability preflight (M12.4)
       → load/resume atomic JSON (or fresh start)
       → loop:
            kill / wall-time bounds
            → RuntimeContext (PAPER, D1 daily_pnl_pct, E1 cursor)
            → TradingRuntime.run_once (M11 hours/freshness/risk)
            → persist OperatorState
            → sleep(interval) if continuing
```

`SessionRunner` / `run-session` remains the supervised fail-closed baseline (stops on any `success=False`) and is **not** replaced by the operator.

---

## 3. Frozen decisions

| ID | Choice |
|---|---|
| **A2** | Continue-on only `stage_reached=market_hours` |
| **B1** | Operator requires hours enabled + `reject` |
| **C1** | Atomic JSON state |
| **D1** | Persist `operator_start_equity` across restarts |
| **E1** | Last actionable bar timestamp; post-risk side-effect skip using authoritative `market_bar_timestamp` |
| **F1** | CLI `run-paper-operator` |
| **G1** | CLI-required bounds / state / kill (no unattended `.env` auto-start) |
| **H** | Exit `0` controlled; `1` refuse/config/corrupt/FS; `2` unexpected; `130` SIGINT |
| **S1b** | Start-time state-path parent/writability preflight before first `run_once` |
| **S2a** | Library-level deterministic CI soak (mock MD, injected sleeper/clock) |
| **S3b** | Yahoo overnight soak is runbook-only (not CI) |
| **S4a** | Closure docs: this summary + README + `.env.example` |

---

## 4. Key CLI

```bash
python main.py run-paper-operator \
  --max-cycles N \
  --max-wall-time-seconds SEC \
  --interval-seconds SEC \
  --state-path PATH \
  --kill-file PATH \
  [--symbol SYM] [--strategy NAME] [--bar-limit N] \
  [--dry-run | --paper]
```

Default execution is **dry-run**. All five bound/path flags are required.

---

## 5. Checkpoint B — Multi-session supervised PAPER (strengthened)

- M11 allowed multiple supervised sessions with in-memory state only.
- **M12.2+** durable resume via `--state-path` for operator runs.
- Humans may still use `run-session` without persistence.

---

## 6. Checkpoint C — Unattended bounded PAPER (checklist)

1. Confirm `TRADING_MODE=paper` (never `live`).
2. Set `MARKET_HOURS_ENABLED=true` and `MARKET_HOURS_POLICY=reject`.
3. Prefer `MARKET_DATA_PROVIDER=mock` until intentionally supervising Yahoo.
4. Choose explicit `--state-path` and `--kill-file` (no defaults).
5. Choose explicit `--max-cycles`, `--max-wall-time-seconds`, `--interval-seconds`.
6. Start dry-run first (CLI default).
7. Only then consider `--paper`.
8. Verify kill file / `PAPER_OPERATOR_KILL` stops new cycles.
9. Verify process restart resumes from state (D1 baseline; E1 no duplicate actionable bar).
10. Do **not** use `run-session` as an unattended overnight loop.

---

## 7. Checkpoint D — Long-duration PAPER soak

### Automated (CI)

- `tests/runtime/test_m12_soak.py` — mock MD, injectable clock/sleeper, no network, no real sleeps.
- Proves max-cycles completion, mid-soak kill, wall-time bound, and A2 hours continue-on.
- State-path preflight/FS fail-closed: `tests/runtime/test_paper_state_store_fs.py`.

### Manual overnight runbook (optional; not CI)

1. Same Checkpoint C profile; set `MARKET_DATA_PROVIDER=yahoo` only on a supervised machine.
2. Keep finite `--max-cycles` **and** `--max-wall-time-seconds`.
3. Place `--kill-file` where you can create it quickly in an emergency (or export `PAPER_OPERATOR_KILL=1`).
4. Ensure `--state-path` parent is writable (operator preflights at start).
5. Run `run-paper-operator` (prefer dry-run first, then `--paper` if intentional).
6. Confirm kill works; confirm restart resume; note outcomes for later M14 evidence.
7. Never enable LIVE. Do not rely on CI for Yahoo overnight.

---

## 8. Explicit non-goals (M12)

- LIVE trading / live broker enablement (M13)
- Changing `SessionRunner` to A2 continue-on
- Changing global default `MARKET_HOURS_POLICY` from `allow` to `reject` (operator enforces B1 itself)
- Yahoo/network overnight inside CI
- Strategy performance certification for real-money trial (pre-M14 pack)
- Broker cancel-all / reconciliation (M14)

---

## 9. Safety invariants preserved

- M8 factory + mode_policy reject `live`
- M9 `SessionRunner` fail-closed on any unsuccessful cycle
- M10 historical backtest: freshness/hours enforcement off via context
- M11 freshness + XNYS hours inherited every operator cycle
- M12 kill, corrupt-state refuse, D1/E1, Decision H exits

---

## 10. Tests (CI)

- `tests/runtime/test_kill_switch.py`
- `tests/runtime/test_paper_operator_bounds.py`
- `tests/runtime/test_paper_operator_persistence.py`
- `tests/runtime/test_paper_operator_integration.py`
- `tests/runtime/test_paper_operator_interval.py`
- `tests/runtime/test_paper_state_store.py`
- `tests/runtime/test_paper_state_store_fs.py`
- `tests/runtime/test_m12_soak.py`
- `tests/test_main_run_paper_operator.py`

---

## 11. Related docs

- Design: `MILESTONE_11_14_DESIGN_SPEC.md` § M12, `MILESTONE_12_DESIGN_REVIEW.md`, `MILESTONE_12_4_DESIGN_REVIEW.md` (review artifacts; may remain untracked)
- Prior closures: `MILESTONE_8_SUMMARY.md` … `MILESTONE_11_SUMMARY.md`
- Operator ops: README section **Unattended paper operator (Milestone 12)**
