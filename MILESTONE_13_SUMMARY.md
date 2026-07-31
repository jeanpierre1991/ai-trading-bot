# Milestone 13 Summary — Live Broker Path (Gated Sandbox + Shadow)

**Closure review date:** 2026-07-31  
**Branch:** `cursor/m9-paper-session-loop`  
**HEAD:** `bcf1189680f2c402ca27d6007ecb92827c53c919` (M13.4)  
**Parent baseline (M12 closed):** `ee0a351f25f57581752681cb66c698c285b61f64`  
**Status:** Milestone 13 sub-milestones M13.1–M13.4 are **committed locally** and each final audit is **APPROVED**. This summary is documentation-only (not yet committed at the time of writing).

---

## 1. M13 objective

Deliver a **broker-agnostic, fail-closed LIVE sandbox path** for supervised validation:

1. Adapter #1 (Alpaca paper/sandbox) behind the existing `Broker` port  
2. Conjunctive hard enablement gates (G1–G10) — BROKER_SANDBOX only  
3. Durable order identity, idempotent submit, abort-only reconciliation  
4. No-submit **shadow** mode for live signal/intent validation without venue orders  

**Explicit non-goals of M13:** real-money trading, `LIVE_PRODUCTION` enablement, unattended LIVE, automatic portfolio repair, M14 cancel-all / trial checklist, multi-broker adapters beyond Alpaca paper, profitability certification.

---

## 2. M13.1 — Alpaca broker adapter (Adapter #1)

| Item | Detail |
|---|---|
| **Commit** | `ac45606c0c07a498364da60fc86fa873a23958de` |
| **Message** | `M13.1: add Alpaca paper adapter and contract tests` |
| **Final audit** | `MILESTONE_13_1_FINAL_AUDIT.md` → **APPROVED** |
| **Delivered** | Alpaca paper/sandbox adapter implementing `Broker`; credentials from settings/env; production host rejected; additive `client_order_id` on `BrokerOrderRequest`; contract tests with mocked transport |
| **Safety** | Not factory-wired for unsupervised live at M13.1 close; paper/operator paths remain paper-only; no real secrets in tree |

---

## 3. M13.2 — Broker-agnostic live sandbox safety gates

| Item | Detail |
|---|---|
| **Commit** | `c2f2e81468f6667a8ab5d569499817be195f0d31` |
| **Message** | `M13.2: add broker-agnostic live sandbox safety gates` |
| **Final audit** | `MILESTONE_13_2_FINAL_AUDIT.md` → **APPROVED** |
| **Delivered** | Conjunctive G1–G10 `evaluate_live_enablement`; `execution="live"` → BROKER_SANDBOX only; registry-only sandbox construction (injected `broker=` rejected); `--live` on `run-once` / `run-session` only; G10 notional + daily order caps via `LiveCapGuardBroker`; mode_policy re-authorization |
| **Safety** | `LIVE_PRODUCTION` hard-denied (G9); `run-paper-operator` / `run-backtest` cannot enable live; fixed confirm token; no silent PaperBroker downgrade |

---

## 4. M13.3 — Idempotent live submission and abort-only reconciliation

| Item | Detail |
|---|---|
| **Commit** | `89998a9e8b3e7c69860a3b8c1dbb028e69345c9d` |
| **Message** | `M13.3: add idempotent live submit and abort-only reconciliation` |
| **Final audit** | `MILESTONE_13_3_FINAL_AUDIT.md` → **APPROVED** (post-remediation) |
| **Delivered** | Atomic JSON live order ledger; UUIDv4 `client_order_id` + same-id retry for `FAILED_ABSENT`; `IdempotentLiveExecutor`; I2 one-open-order-per-symbol; UNKNOWN / CREATED / SUBMITTING crash recovery; abort-only `reconcile_live`; durable C1 cap accounting (`first_submit_counted` / `first_submit_day`); `ReconcileCapableBroker` + agnostic snapshots; Alpaca observe mapping in adapter only |
| **Safety** | No automatic compensating trades / cancels / portfolio repair; broker-agnostic core (`reconcile.py` / `idempotent_submit.py` / ledger / caps have no Alpaca imports) |

---

## 5. M13.4 — No-submit shadow validation mode

| Item | Detail |
|---|---|
| **Commit** | `bcf1189680f2c402ca27d6007ecb92827c53c919` |
| **Message** | `M13.4: add no-submit shadow validation mode` |
| **Final audit** | `MILESTONE_13_4_FINAL_AUDIT.md` → **APPROVED** (external confirmed) |
| **Delivered** | Distinct `execution="shadow"` (no `TradingMode.SHADOW`); `ShadowExecutor`; required append-only JSONL `SHADOW_AUDIT_PATH`; live-gated config + explicit **G6b**; CLI `--shadow` on `run-once` / `run-session` only (mutex with dry-run/paper/live); hypothetical cap evaluation without consumption; market-data quotes only (no broker client) |
| **Safety** | Never `place_order`; never constructs production/sandbox submit path; no live ledger writes; no M13.3 reconcile for shadow; operator/backtest cannot use shadow |

---

## 6. Architecture delivered

```text
CLI: run-once | run-session
     [--dry-run | --paper | --live | --shadow]

Factory execution:
  dry_run  → DryRunExecutor
  paper    → BrokerOrderExecutor(PaperBroker)
  live     → G1–G10 → registry BROKER_SANDBOX → LiveCapGuardBroker
             → IdempotentLiveExecutor + JsonLiveOrderLedger
             → cycle-start abort-only reconcile
  shadow   → G1–G5/G7–G10 + G6b → ShadowExecutor + ShadowAuditLog
             (no broker construct, no ledger, no reconcile, no place_order)

Ports:
  Broker / BrokerOrderRequest(+client_order_id)
  ReconcileCapableBroker + BrokerOrderSnapshot / BrokerPositionSnapshot
  Adapter registry: broker_sandbox includes {alpaca_paper}; live_production = empty

Preserved prior milestones:
  M10 backtest isolation · M11 freshness/hours · M12 PaperOperator paper-only
```

---

## 7. Safety invariants (M13 closed state)

| Invariant | Status |
|---|---|
| Default `trading_mode=paper` | Intact |
| `LIVE_PRODUCTION` unreachable (G9 + empty production registry) | **Confirmed** |
| Live submit only via supervised `--live` + all gates + registry sandbox | Intact |
| Registry-only sandbox construction (no live `broker=` injection) | Intact |
| Abort-only reconcile; no auto-repair / cancel / compensate | Intact |
| I2 one-open-order-per-symbol + UNKNOWN fail-closed | Intact |
| C1 same-id retry does not double-count daily slots (durable) | Intact |
| Shadow never submits; never enables production | Intact |
| `run-paper-operator` paper-only (no live/shadow) | Intact |
| `run-backtest` isolated from live/shadow | Intact |
| SessionRunner fail-closed | Intact |
| Broker-agnostic core for gates / ledger / reconcile / shadow | Intact |
| M14 (real-money trial / cancel-all checklist) | **Not started** |

---

## 8. Exact final test results (closure review)

Re-run at closure review (HEAD `bcf1189`):

```bash
.venv/bin/python -m pytest tests/broker_interface/test_alpaca_broker.py -q --tb=line
# 17 passed

.venv/bin/python -m pytest tests/runtime/test_m13_2_live_enablement.py -q --tb=line
# 45 passed

.venv/bin/python -m pytest tests/runtime/test_m13_3_reconcile_idempotency.py -q --tb=line
# 23 passed

.venv/bin/python -m pytest tests/runtime/test_m13_4_shadow_mode.py -q --tb=line
# 15 passed

.venv/bin/python -m pytest tests/broker_interface/test_alpaca_broker.py \
  tests/runtime/test_m13_2_live_enablement.py \
  tests/runtime/test_m13_3_reconcile_idempotency.py \
  tests/runtime/test_m13_4_shadow_mode.py -q --tb=line
# 100 passed

.venv/bin/python -m pytest -q --tb=line
# 688 passed
```

---

## 9. Commit hashes (M13.1–M13.4)

| Sub-milestone | Full hash | Subject |
|---|---|---|
| **M13.1** | `ac45606c0c07a498364da60fc86fa873a23958de` | M13.1: add Alpaca paper adapter and contract tests |
| **M13.2** | `c2f2e81468f6667a8ab5d569499817be195f0d31` | M13.2: add broker-agnostic live sandbox safety gates |
| **M13.3** | `89998a9e8b3e7c69860a3b8c1dbb028e69345c9d` | M13.3: add idempotent live submit and abort-only reconciliation |
| **M13.4** | `bcf1189680f2c402ca27d6007ecb92827c53c919` | M13.4: add no-submit shadow validation mode |

Linear order on `cursor/m9-paper-session-loop`: M13.1 → M13.2 → M13.3 → M13.4.

---

## 10. Git status (at closure review)

```text
Branch: cursor/m9-paper-session-loop
HEAD:   bcf1189680f2c402ca27d6007ecb92827c53c919
Ahead of origin/cursor/m9-paper-session-loop: 8 commits

Working tree:
  - No modified tracked files from M13 implementation
  - Untracked (unrelated to this summary commit decision):
      MILESTONE_11_* / MILESTONE_12_* audit/design Markdown leftovers
  - This file MILESTONE_13_SUMMARY.md is newly created and untracked
```

**Commits ahead of origin:** **8**

---

## 11. Confirmations

| Claim | Confirmation |
|---|---|
| LIVE_PRODUCTION remains unreachable | **YES** — G9 hard-deny; production registry empty; shadow GS deny; Alpaca live host rejected; tests green |
| M14 has not started | **YES** — no M14 implementation files; no cancel-all/trial checklist; audits explicitly defer M14 |
| Implementation code modified for this summary | **NO** — documentation-only |
| Staged / committed / pushed / PR opened | **NO** (for this summary step) |

---

## 12. Closure review verdict

Milestone 13 is **ready for closure** from an engineering evidence standpoint:

- All four sub-milestones are locally committed with APPROVED final audits  
- Full repository suite is green (**688 passed**)  
- Production money path remains closed; shadow is no-submit  
- M14 remains the exclusive future real-money trial boundary  

**Next process step (external):** authorize any closure commit that should include this summary (and related docs), then decide push/PR separately. Do **not** start M14 until that process completes.
