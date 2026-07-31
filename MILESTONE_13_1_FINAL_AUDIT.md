# M13.1 Final Audit — Alpaca broker adapter + paper/sandbox contract tests

**Audit type:** External final audit of the ACTUAL working tree  
**HEAD (M12 closure, unchanged):** `ee0a351f25f57581752681cb66c698c285b61f64`  
**Scope:** M13.1 only — Alpaca Adapter #1 + mocked contract tests; production LIVE unreachable  
**Audit actions:** Code/test inspection + test execution. No source/test modifications. No stage/commit/push/PR. M13.2 not started.

---

## 1. Exact files audited

### M13.1 implementation files (in scope)

| Path | Role |
|---|---|
| `broker_interface/alpaca/adapter.py` | `AlpacaBroker` / `AlpacaBrokerConfig` / paper-host validation / settings helper |
| `broker_interface/alpaca/__init__.py` | Package exports; adapter-boundary docstring |
| `broker_interface/http_transport.py` | Broker-agnostic `HttpTransport` / `UrllibHttpTransport` |
| `broker_interface/orders.py` | Additive optional `client_order_id` on `BrokerOrderRequest` |
| `broker_interface/__init__.py` | Exports + multi-venue boundary note |
| `.env.example` | Alpaca paper credential guidance (commented; not factory-wired) |
| `tests/broker_interface/test_alpaca_broker.py` | Mocked HTTP contract + factory-closed tests |

### Cross-checked for coupling / regression (must remain Alpaca-free / unchanged)

| Area | Result |
|---|---|
| `runtime/` (factory, mode_policy, paper_operator, session, trading_runtime, broker_executor) | **No Alpaca imports**; `git diff HEAD -- runtime/` empty (0 bytes) |
| `strategy_engine/`, `risk_manager/`, `order_manager/`, `portfolio_manager/` | **No Alpaca references** |
| `backtesting/` | Untouched |
| `broker_interface/broker.py` (`PaperBroker`) | Untouched |
| `broker_interface/module.py` | Untouched; still constructs `PaperBroker` only; unknown `broker_name` falls back to paper with warning (does **not** auto-wire Alpaca) |

### Pre-existing untracked audit/design Markdown (NOT M13.1 implementation)

- `MILESTONE_11_*_FINAL_AUDIT.md`
- `MILESTONE_12_*_FINAL_AUDIT.md` / `MILESTONE_12_*_DESIGN_REVIEW.md` / `MILESTONE_12_DESIGN_REVIEW.md`
- `MILESTONE_13_DESIGN_REVIEW.md`
- This file: `MILESTONE_13_1_FINAL_AUDIT.md` (documentation-only audit output)

---

## 2. Broker-agnostic architecture

### Evidence — PASS

1. **Adapter #1 behind `Broker`:** `AlpacaBroker(Broker)` in `broker_interface/alpaca/adapter.py`; package docstring states core must not import Alpaca types; future IBKR/TradeStation/Webull should implement the same port.
2. **Core untouched:** No `alpaca`/`Alpaca`/`APCA` matches under `runtime/`, `strategy_engine/`, `risk_manager/`, `order_manager/`, `portfolio_manager/`.
3. **HTTP transport is venue-neutral:** `broker_interface/http_transport.py` defines `HttpTransport` Protocol + stdlib `UrllibHttpTransport` with no Alpaca headers/URLs/constants. Alpaca-specific auth/URLs live only in the Alpaca adapter.
4. **Additive DTO only:** `BrokerOrderRequest.client_order_id: str | None = None` is broker-agnostic; PaperBroker ignores it (compatible).
5. **Factory not Alpaca-aware:** `create_trading_runtime` still requires `PaperBroker` for `execution="paper"`; injecting `AlpacaBroker` raises `ConfigurationError` (tested).

### Findings

| Severity | Finding |
|---|---|
| MINOR | `BrokerInterfaceModule` still falls back to `PaperBroker` for unknown `broker_name` (including `alpaca_paper`) with a warning. This is fail-safe (no live wire) but could confuse operators who set `BROKER_NAME=alpaca_paper` expecting the Alpaca adapter. Documented as “NOT wired into factory” in `.env.example`; acceptable for M13.1. |

---

## 3. LIVE safety

### Evidence — PASS

1. **Production Alpaca host blocked:** `_validate_paper_base_url` rejects hostname `api.alpaca.markets` and allows only `paper-api.alpaca.markets` (`adapter.py`). Covered by `test_alpaca_rejects_live_api_host`.
2. **`trading_mode=live` rejected:** Factory `_validate_settings_mode` unchanged; `test_factory_still_rejects_trading_mode_live` passes.
3. **Alpaca not factory-wired:** No factory/module path constructs `AlpacaBroker`. Helper `alpaca_paper_broker_from_settings` is explicit/opt-in and still subject to paper-host validation. `test_factory_still_rejects_non_paper_broker` proves Alpaca cannot be injected as the paper executor.
4. **`run-paper-operator` paper-only:** `PaperOperator._validate_paper_settings_mode` still requires `trading_mode='paper'`; no M13.1 changes to `runtime/paper_operator.py`.
5. **No hidden real-money path:** Order HTTP only targets validated paper trading base URL (`…/v2/orders`). Live trading host cannot be constructed. Module registry does not select Alpaca.

### Production LIVE remains unreachable: **YES**

---

## 4. Credential safety

### Evidence — PASS

1. Credentials required at construction from `AlpacaBrokerConfig` / settings (`broker_api_key` / `broker_api_secret`); empty keys → `ConfigurationError`.
2. Defaults in `Settings` remain empty strings; `.env.example` uses placeholders only (`<alpaca paper key id>`).
3. Construction log redacts key id via `_redact`; does not log secret (`test_auth_headers_use_apca_keys_without_logging_secret`).
4. Error paths use `_safe_exc` / `_safe_response_detail`; HTTP 422 test asserts secret fragment not present in `result.message`.
5. Test credentials are synthetic (`PKTESTKEY…`, `SECRETKEYVALUE…`), not real keys.

### Findings

| Severity | Finding |
|---|---|
| MINOR | `auth_headers()` returns the raw secret (required for HTTP). There is no custom `__repr__`/`__str__` on `AlpacaBroker`. Default object repr does not dump attributes, but callers must not log `auth_headers()` or `vars(broker)`. Acceptable if disciplined; consider redacting repr in a later hardening slice. |
| MINOR | `_safe_exc` redacts by suspicious *token names* in exception text, not by scrubbing the secret *value*. Low practical risk with urllib errors. |

---

## 5. Order contract

### Evidence — PASS

1. **`client_order_id` additive:** Optional on `BrokerOrderRequest`; forwarded only when set (`place_order` body). `test_client_order_id_optional_on_request_dto` + happy-path body assertion.
2. **PaperBroker compatibility:** `broker.py` untouched; `tests/broker_interface/test_paper_broker_orders.py` still green within full suite.
3. **Accepted/open ≠ fill:** `_map_order_payload` fail-closes for `accepted`/`new`/etc. without inventing fills (`test_open_accepted_order_fail_closed_not_invented_fill`). Only `filled` (or partial/done_for_day with positive `filled_qty` **and** positive `filled_avg_price`) maps to `FILLED`.
4. **Reject / HTTP / network fail-closed:** Venue `rejected` → `REJECTED`; HTTP ≥400 → reject via exception path; `TimeoutError` → `REJECTED`; disconnected → `REJECTED` with no HTTP call. Covered by dedicated tests.

### Findings

| Severity | Finding |
|---|---|
| MINOR | No dedicated mocked test for `get_quote` (data API latest trade). Not required to block M13.1 (order/status/failure paths covered), but coverage gap for quote parsing. |

---

## 6. Regression safety

### Evidence — PASS

| Invariant | Evidence |
|---|---|
| M10 backtest isolation | `git diff HEAD -- backtesting/` empty; full suite includes M10 tests — green |
| M11 hours/freshness | No runtime/market_data changes; suite green |
| M12 PaperOperator | `runtime/paper_operator.py` untouched; suite green |
| SessionRunner fail-closed | `runtime/session.py` untouched; suite green |
| Core/runtime diff | `git diff HEAD -- runtime/ …` size **0** |

---

## 7. Test quality

### M13.1-specific suite

**Command:**
```bash
.venv/bin/python -m pytest tests/broker_interface/test_alpaca_broker.py -q --tb=line
```

**Result:** **17 passed** in 0.23s

### Assessment

Tests are **behavioral**, not smoke-only:
- Assert HTTP method/URL/JSON body and auth header names
- Assert fill vs reject status and quantities/prices
- Assert live host construction failure
- Assert factory refuses Alpaca injection and `trading_mode=live`
- Assert secret absent from log text / error messages
- Use injectable fake transport — **no network**

Gaps (MINOR): `get_quote` untested; log-capture test depends on caplog receiving the adapter logger (asserts absence of secret; still valid for construction path when logs are captured).

### Complete repository suite

**Command:**
```bash
.venv/bin/python -m pytest -q --tb=line
```

**Result:** **605 passed** in 0.87s

---

## 8. Working tree / scope hygiene

### M13.1 source/test/config changes (authorized implementation set)

**Modified**
- `.env.example`
- `broker_interface/__init__.py`
- `broker_interface/orders.py`

**Created**
- `broker_interface/http_transport.py`
- `broker_interface/alpaca/__init__.py`
- `broker_interface/alpaca/adapter.py`
- `tests/broker_interface/test_alpaca_broker.py`

### Unrelated accidental source changes

**None observed.** No diffs under `runtime/`, strategies, risk, OM, portfolio, backtesting, or `main.py`.

### Separated: pre-existing untracked Markdown

Design/audit Markdown listed in §1 remain untracked and are **not** part of the M13.1 implementation change set (except this new audit file, also documentation-only / untracked).

---

## 9. Findings summary

| Severity | Count | Items |
|---|---|---|
| **BLOCKER** | 0 | — |
| **MAJOR** | 0 | — |
| **MINOR** | 4 | Module `broker_name=alpaca_paper` still falls back to PaperBroker; public `auth_headers()` secret surface; `_safe_exc` name-based redaction; no `get_quote` unit test |

None of the MINOR items enable production LIVE or invent fills.

---

## 10. Production LIVE unreachable?

**YES — production LIVE remains unreachable.**

- Live Alpaca trading host cannot be constructed.
- Factory rejects `trading_mode=live`.
- Factory rejects non-`PaperBroker` injection (including `AlpacaBroker`).
- Module init does not construct Alpaca.
- Paper operator remains paper-only.

---

## 11. Final verdict

# APPROVED

M13.1 meets the approved scope: Alpaca paper/sandbox Adapter #1 behind the broker-agnostic `Broker` port, mocked contract tests, credentials from config/env with no real secrets in tree, fail-closed order mapping, and production/live execution paths still closed. MINOR findings do not block approval.

**Do not commit until external process authorizes an M13.1 commit file list.**  
**Do not start M13.2 until that commit (if any) and gate design are approved.**

---

## 12. Audit process confirmations

- Source code / tests: **not modified** during this audit  
- Staging / commit / push / PR: **not performed**  
- M13.2: **not started**  
- STOP after writing this file
