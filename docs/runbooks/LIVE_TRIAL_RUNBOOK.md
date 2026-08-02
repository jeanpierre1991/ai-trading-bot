# LIVE Trial Runbook (Milestone 14.3)

This runbook describes the **supervised** process for a future controlled
real-money trial. **M14.3 does not enable `LIVE_PRODUCTION`.** Incomplete
evidence checklists always deny production-trial authorization.

## Purpose

Bind human and technical evidence (M10–M14) into a fail-closed checklist gate
before any production-trial wiring can be considered in M14.4.

## Non-negotiables

1. `BROKER_ENDPOINT_CLASS=broker_sandbox` remains the only live path that can
   authorize submits today (M13 G1–G10).
2. `BROKER_ENDPOINT_CLASS=live_production` is always denied in M14.3, even with
   a complete checklist (T8 empty production allowlist + T9 wiring latch).
3. Missing, invalid, or incomplete checklist evidence **always denies**.
4. There is **no bypass** flag, env var, or code path around the checklist.
5. Secrets (API keys, tokens, passwords) must **never** appear in checklist JSON.

## Preconditions (sandbox first)

Before drafting a production-trial checklist:

1. M10 backtest isolation / strategy validation pack evidence exists.
2. M12 paper soak evidence exists.
3. M13.2/M13.3 sandbox live gated path exercised successfully.
4. M13.4 shadow JSONL samples captured (no `place_order`).
5. M14.1 emergency stop dry-run in sandbox: kill → halt → cancel-all → CRITICAL alert.
6. M14.2 alert channels configured and mocked/tested (webhook/email failures must
   not block halt).

## Checklist file

- Setting: `TRIAL_EVIDENCE_CHECKLIST_PATH`
- Example template: `docs/evidence/live_trial_checklist.example.json`
- Schema version: `1`
- Required item ids (all must be `complete=true` with existing `evidence_path`):
  - `m10_strategy_validation_pack`
  - `m12_paper_soak`
  - `m13_sandbox_live_gated`
  - `m13_shadow_validation`
  - `m14_kill_cancel_dry_run`
- Human sign-off fields (all required):
  - `signed_off_by`
  - `signed_off_at` (UTC ISO-8601)
  - `sign_off_confirmed: true`

Optional `evidence_sha256` (hex) is verified when present.

## Production-trial settings (still denied in M14.3)

```bash
TRADING_MODE=live
LIVE_TRADING_ENABLED=true
BROKER_ENDPOINT_CLASS=live_production
LIVE_TRIAL_CONFIRM_TOKEN=I_UNDERSTAND_THIS_IS_A_CONTROLLED_LIVE_TRIAL
TRIAL_EVIDENCE_CHECKLIST_PATH=./path/to/live_trial_checklist.json
TRIAL_MAX_ORDER_NOTIONAL=<tiny>
TRIAL_MAX_ORDERS_PER_DAY=<tiny>
```

Expected result in M14.3: factory/runtime authorization **denied** (checklist
incomplete → T3; complete → T8/T9). Never assume authorization from a filled
checklist alone.

## Emergency stop during any live work

1. Engage kill file/env (`LIVE_EMERGENCY_KILL_PATH` / `LIVE_EMERGENCY_KILL`).
2. Confirm durable halt latch engaged and Incident ID logged.
3. Confirm best-effort cancel-all + CRITICAL alert + incident snapshot.
4. Do **not** clear the halt until human review + checklist re-validation.

## Resume policy

Resume is supervised and out-of-band. Automatic resume is forbidden. After an
incident, re-validate the evidence checklist before any future trial attempt.

## M14.4 protocol closure

M14.4 closes the controlled-trial **readiness** protocol:

1. Sandbox dry-run: kill → durable halt → best-effort cancel-all → CRITICAL alert
   → incident snapshot → new `place_order` blocked (`runtime/protocol_dry_run.py`).
2. Evidence checklist + trial enablement remain fail-closed.
3. `LIVE_PRODUCTION_TRIAL_WIRING_ENABLED` defaults to **false**.
4. Production adapter allowlist remains **empty** until a separate human-approved
   registration after external sign-off.

Treat any production authorization attempt without that human process as a
failed gate, not a configuration inconvenience.

## Human process gate (outside code)

Real-money trial start additionally requires recorded human approval after the
M14 final audit. Code gates alone are not sufficient.
