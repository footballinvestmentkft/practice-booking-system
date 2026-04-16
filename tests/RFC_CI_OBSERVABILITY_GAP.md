# RFC-001: CI Observability Gap — Assertion Observability Layer
## Practice Booking System
**Status: BACKLOG — DRAFT**
**Created: 2026-04-17 | Author: Engineering Lead**
**Does NOT affect current release gate. See `EXECUTION_TRUTH_MODEL_v1.md`.**

---

## Summary

The current CI pipeline (run `24533537667`) produces binary PASS/FAIL evidence
at the nodeid level but does not capture assertion runtime values (HTTP status
codes, DB column values, UI strings) for passing tests.

This RFC proposes the changes needed to produce **per-assertion observable output**
in CI artifacts. Implementation makes the "Assertion Observability Layer" (Gate 4)
possible but does not change the current release gate (Gates 1–3).

This is a quality improvement RFC, not a defect fix.

---

## Problem Statement

### Current state

When `test_admin_invoice_verify_credits_student` passes in CI, the log contains:

```
2026-04-16T20:58:58.8009811Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_invoice_verify_credits_student
2026-04-16T20:58:58.8010398Z  -------------------------------- live log setup --------------------------------
2026-04-16T20:58:58.8010933Z  2026-04-16 20:58:58 [    INFO] 🚀 Application startup initiated
```

An auditor cannot read from this log that:
- `POST /admin/invoices/{id}/verify` returned HTTP `200`
- `InvoiceRequest.status` changed to `"verified"` in the database
- `"credits_added"` appeared in the response body

These values are correct (the test passed), but they are invisible to the artifact layer.

### Desired state

A passing run of the same test produces:

```xml
<!-- test-results/critical_e2e.xml -->
<testcase name="test_admin_invoice_verify_credits_student"
          classname="tests.integration.web_flows.test_critical_e2e"
          time="0.187">
  <properties>
    <property name="http_layer" value="POST /admin/invoices/{id}/verify → 200"/>
    <property name="db_layer"   value="InvoiceRequest.status=verified, verified_at IS NOT NULL"/>
    <property name="ui_layer"   value=credits_added in response.text"/>
  </properties>
</testcase>
```

And the CI step log contains:
```
[ASSERT:HTTP] POST /admin/invoices/42/verify → 200 ✓
[ASSERT:DB]   InvoiceRequest(id=42).status == 'verified' ✓
[ASSERT:UI]   'credits_added' in response.text ✓
```

---

## Scope

This RFC covers changes to:

1. `tests/integration/web_flows/test_critical_e2e.py` — add structured assertion helpers
2. `.github/workflows/test-baseline-check.yml` — add `--junit-xml` + log-cli config
3. `pytest.ini` or `pyproject.toml` — logging configuration
4. `tests/conftest.py` — optional: per-test assertion capture fixture

This RFC does NOT cover:
- Changes to application code
- Changes to any other test file
- Changes to the release gate (Gates 1–3 remain unchanged)
- Any other workflow file

---

## Proposed Changes

### Change 1 — JUnit XML Artifact

**File:** `.github/workflows/test-baseline-check.yml`

**Change** the `Run unit + integration tests with coverage` step:

```yaml
# BEFORE:
run: |
  pytest tests/unit/ tests/integration/tournament/ tests/integration/web_flows/ tests/integration/domain/ \
    -q --tb=line -v \
    --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=85 --durations=20

# AFTER:
run: |
  pytest tests/unit/ tests/integration/tournament/ tests/integration/web_flows/ tests/integration/domain/ \
    -v --tb=short \
    --junit-xml=test-results/unit_integration.xml \
    --junit-prefix=unit \
    --log-cli-level=INFO \
    --log-cli-format="%(asctime)s [%(levelname)8s] %(message)s" \
    --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=85 --durations=20
```

**Add** artifact upload step after coverage upload:

```yaml
- name: Upload test results (JUnit XML)
  uses: actions/upload-artifact@v4
  if: always()
  with:
    name: test-results-unit-integration
    path: test-results/unit_integration.xml
    retention-days: 90
```

**Effect:**
- `test-results/unit_integration.xml` uploaded as artifact on every run
- Contains: per-test `name`, `classname`, `time`, `failure` (if any)
- Provides machine-readable PASS/FAIL with duration, independent of log parsing
- `--tb=short` (was `--tb=line`) shows full assert context on failure

**Breaking change risk:** NONE. Adds output only.

---

### Change 2 — Structured Assertion Logging

**File:** `tests/integration/web_flows/test_critical_e2e.py`

Introduce a thin logging helper at the top of the file:

```python
import logging
_obs = logging.getLogger("assert.observability")

def _http(method: str, path: str, status: int) -> None:
    _obs.info("[ASSERT:HTTP] %s %s → %d ✓", method, path, status)

def _db(model: str, condition: str) -> None:
    _obs.info("[ASSERT:DB]   %s %s ✓", model, condition)

def _ui(fragment: str, present: bool = True) -> None:
    _obs.info("[ASSERT:UI]   '%s' %s ✓", fragment, "in" if present else "absent from")
```

**Usage** — add logging calls alongside existing assertions (do NOT replace them):

```python
# Example: test_admin_invoice_verify_credits_student
r = client.post(f"/admin/invoices/{invoice.id}/verify", ...)
assert r.status_code == 200, f"..."
_http("POST", f"/admin/invoices/{invoice.id}/verify", r.status_code)  # ← ADD

test_db.refresh(inv)
assert inv.status == "verified", f"..."
_db("InvoiceRequest", f"status='verified'")                           # ← ADD

assert "credits_added" in r.text
_ui("credits_added")                                                   # ← ADD
```

**Placement rule:** one `_http()` / `_db()` / `_ui()` call per assertion line,
immediately after the `assert`. Never replace the `assert`.

**Effect:** When `--log-cli-level=INFO` is set (Change 1), these lines appear
in the step log and in the JUnit XML `<system-out>` element.

**Implementation cost:** ~3 lines per test function × 59 functions = ~177 lines.
Can be done incrementally (one sprint) without affecting test correctness.

**Breaking change risk:** NONE. Logging calls do not affect test outcome.

---

### Change 3 — pytest.ini Logging Configuration

**File:** `pytest.ini` (or `[tool.pytest.ini_options]` in `pyproject.toml`)

```ini
[pytest]
log_cli = true
log_cli_level = INFO
log_cli_format = %(asctime)s [%(levelname)8s] %(name)s: %(message)s
log_cli_date_format = %H:%M:%S

# Suppress noisy loggers in CI output
log_level = WARNING
filterwarnings =
    ignore::DeprecationWarning
    ignore::sqlalchemy.exc.SAWarning
```

**Effect:** Separates the `assert.observability` logger output (INFO) from
SQLAlchemy noise (WARNING+). The structured `[ASSERT:*]` lines are readable
in the CI log without being buried in framework warnings.

**Breaking change risk:** LOW. Changes log verbosity only. Tests are unaffected.

---

### Change 4 — Optional: Per-Test Assertion Capture Fixture

**File:** `tests/conftest.py`

This is optional (implement after Changes 1–3 are stable):

```python
import pytest
import json
from pathlib import Path

class AssertionCapture:
    """Collects structured assertion events for JUnit XML properties."""
    def __init__(self):
        self.events: list[dict] = []

    def http(self, method, path, status):
        self.events.append({"layer": "HTTP", "method": method, "path": path, "status": status})

    def db(self, model, condition):
        self.events.append({"layer": "DB", "model": model, "condition": condition})

    def ui(self, fragment, present=True):
        self.events.append({"layer": "UI", "fragment": fragment, "present": present})

@pytest.fixture
def capture():
    return AssertionCapture()

@pytest.fixture(autouse=True)
def _write_assertion_capture(request, tmp_path):
    """Write assertion events to JUnit-compatible properties after each test."""
    yield
    cap = request.node.funcargs.get("capture")
    if cap and cap.events:
        out = tmp_path / f"{request.node.name}_assertions.json"
        out.write_text(json.dumps(cap.events, indent=2))
```

When this fixture is active, each test that uses `capture` gets a per-test
JSON file with all assertion events, uploadable as an artifact.

**Breaking change risk:** NONE. `autouse` fixture is no-op when `capture` is unused.

---

## Implementation Plan

| Phase | Changes | Effort | Prerequisite |
|-------|---------|--------|-------------|
| Phase 1 | Change 1 (JUnit XML) | 1 hour | None |
| Phase 2 | Change 3 (pytest.ini) | 30 min | Phase 1 |
| Phase 3 | Change 2 (logging helpers) | 1 sprint | Phase 2 |
| Phase 4 | Change 4 (capture fixture) | 1 sprint | Phase 3 stable |

Phase 1 alone already satisfies the minimum "Assertion Observability Layer" requirement
for machine-readable PASS/FAIL with duration. Phases 2–4 add depth.

---

## Acceptance Criteria

The Assertion Observability Layer (Gate 4) is satisfied when ALL of the following
are true in a CI run:

| Criterion | Verification command |
|-----------|---------------------|
| JUnit XML artifact present | `gh run download {run_id} -n test-results-unit-integration` |
| 59 `<testcase>` elements in XML | `xmllint --xpath "count(//testcase[contains(@classname,'test_critical_e2e')])" test-results-unit-integration/unit_integration.xml` = 59 |
| 0 `<failure>` elements | `xmllint --xpath "count(//failure)" ...xml` = 0 |
| `[ASSERT:HTTP]` lines in step log | `gh run view {run_id} --log \| grep "ASSERT:HTTP" \| wc -l` ≥ 59 |
| `[ASSERT:DB]` lines in step log | `gh run view {run_id} --log \| grep "ASSERT:DB" \| wc -l` ≥ 59 |
| `[ASSERT:UI]` lines in step log | `gh run view {run_id} --log \| grep "ASSERT:UI" \| wc -l` ≥ 59 |

When these criteria are met, a `CI_GROUND_TRUTH_VERIFICATION_v2.md` is produced
that supersedes the v1 document for future runs. The v1 document is retained as
historical record.

---

## What This RFC Does NOT Change

- The release gate (Gates 1–3 in `EXECUTION_TRUTH_MODEL_v1.md`)
- The `RELEASE_DECISION.md` verdict (UNCONDITIONAL GO)
- The `COVERAGE_BASELINE.md` baseline
- Any application code
- Any test assertion logic (only adds logging calls alongside existing asserts)

---

## Out of Scope

- Mutation testing integration
- Property-based testing
- Load/performance observability
- Database query logging (N+1 detection)
- Network traffic capture (HTTP proxy)

These are separate concerns and should be tracked as separate RFCs if needed.

---

## Backlog Classification

| Field | Value |
|-------|-------|
| RFC ID | RFC-001 |
| Priority | LOW (quality improvement, not defect) |
| Blocking | Nothing — this RFC has zero blockers |
| Sprint target | Sprint 8 or later |
| Owner | To be assigned |
| Related documents | `EXECUTION_TRUTH_MODEL_v1.md`, `CI_GROUND_TRUTH_VERIFICATION.md` |

---

*RFC-001: CI Observability Gap — 2026-04-17 — BACKLOG/DRAFT*
*Practice Booking System — E2E Coverage Audit Program*
*This RFC does not affect the current release gate or release decision.*
