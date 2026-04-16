# Execution Truth Model v1 — Audit Closure
## Practice Booking System
**Status: CLOSED**
**Closed: 2026-04-17 | SHA: cbb5697 | Authority: Engineering Lead**

This document formally closes the E2E Coverage Audit Program at the
"Execution Truth Model v1" level and defines the release gate that applies
to all future merges to main.

It also formally separates the future "Assertion Observability Layer" into
a backlog item (`RFC_CI_OBSERVABILITY_GAP.md`) that is entirely independent
of the current release decision.

---

## What "Execution Truth Model v1" Means

The Execution Truth Model v1 answers exactly one question per test:

> **Did this test execute and pass in CI?**

It does NOT answer:

> What were the runtime values of HTTP responses, DB state, or UI output?

That deeper question belongs to the future **Assertion Observability Layer**
(see `RFC_CI_OBSERVABILITY_GAP.md`). The two questions are at different levels
of the verification stack. Conflating them would require infrastructure that
does not yet exist in this pipeline and would delay the current release
without adding risk coverage.

---

## Formal Release Gate Definition

These are the three conditions for a merge to main to be considered
"release gated". They are immutable at this model version.

### Gate 1 — CI Success (REQUIRED)

| Property | Value |
|----------|-------|
| Check | `gh run view {run_id} --json conclusion` returns `"success"` |
| Scope | All 24 jobs in `test-baseline-check.yml` |
| Tolerance | 0 failed jobs |
| Evidence source | GitHub Actions run conclusion (raw API) |
| Status at freeze | ✅ SATISFIED — run `24533537667`, conclusion `success` |

### Gate 2 — Nodeid Execution (REQUIRED)

| Property | Value |
|----------|-------|
| Check | All 59 test_critical_e2e.py nodeids present in CI step log |
| Scope | `Unit Tests` job → `Run unit + integration tests with coverage` step |
| Tolerance | 0 nodeids missing |
| Evidence source | `gh run view {run_id} --log \| grep test_critical_e2e.py:: \| wc -l` = 59 |
| Status at freeze | ✅ SATISFIED — 59/59 nodeids confirmed in run `24533537667` |

### Gate 3 — Failure Absence (REQUIRED)

| Property | Value |
|----------|-------|
| Check | Suite summary line contains `0 failed, 0 errors` |
| Scope | Full test suite (7766 tests) |
| Tolerance | 0 failures, 0 errors (`xfailed` and `xpassed` are tolerated) |
| Evidence source | `gh run view {run_id} --log \| grep "passed.*failed.*error"` |
| Status at freeze | ✅ SATISFIED — `7766 passed, 2 skipped, 21 xfailed, 1 xpassed` |

### Gate 4 — Assertion Runtime Visibility (OPTIONAL — FUTURE LAYER)

| Property | Value |
|----------|-------|
| Check | JUnit XML artifact present AND per-assertion log entries readable |
| Scope | test_critical_e2e.py (62 BF flows) |
| Tolerance | N/A — not a release gate |
| Evidence source | NOT YET AVAILABLE — see `RFC_CI_OBSERVABILITY_GAP.md` |
| Status at freeze | 🔵 NOT GATING — backlog item |

> **Rule:** Gate 4 is explicitly NOT a release gate at v1.
> Absence of per-assertion runtime values does not block release.
> It is a future quality improvement, not a correctness requirement.

---

## Audit Program Document Inventory

All documents produced by the E2E Coverage Audit Program, in reverse
chronological order. Each is frozen at its creation SHA.

| Document | SHA | Purpose | Status |
|----------|-----|---------|--------|
| `EXECUTION_TRUTH_MODEL_v1.md` | cbb5697 | Audit closure + release gate | **CLOSED** |
| `CI_GROUND_TRUTH_VERIFICATION.md` | cbb5697 | Raw CI artifact audit | FROZEN |
| `ZERO_RISK_ACCEPTANCE_LOG.md` | 69e3a5b | 11 ZERO routes × decision | FROZEN |
| `COVERAGE_EVIDENCE_INDEX.md` | 69e3a5b | 62 flows × source assertions | FROZEN (index only) |
| `METRIC_RECONCILIATION.md` | cd21b84 | 116-route tier mapping | FROZEN |
| `METRIC_CONTRACT.md` | cd21b84 | BFC/ERC/CEC definitions | FROZEN |
| `GAP_REPORT_CI_BASED.md` | d7dc5dc | 11 ZERO routes technical list | FROZEN |
| `EXTERNAL_VERIFICATION_CHECKLIST.md` | d7dc5dc | 13 independent checks | FROZEN |
| `COVERAGE_FREEZE_v1.md` | d7dc5dc | SHA + artifact checksums | FROZEN |
| `RELEASE_DECISION.md` | 3138f5b | UNCONDITIONAL GO decision | FROZEN |
| `COVERAGE_ACCEPTANCE_SIGNOFF.md` | 3138f5b | MF-01..MF-03 disposition | FROZEN |
| `COVERAGE_CLOSURE_REPORT.md` | 3138f5b | Gap → flow closure map | FROZEN |
| `COVERAGE_BASELINE.md` | 3138f5b | 62/62 flow baseline | FROZEN |

---

## Verification Chain

A third-party auditor can independently verify the release gate using
only these commands (no repo checkout required):

```bash
# Gate 1: CI success
gh run view 24533537667 --json conclusion \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['conclusion']=='success', d['conclusion']; print('Gate 1 PASS')"

# Gate 2: 59 nodeids executed
gh run view 24533537667 --log \
  | grep "Unit Tests.*Run unit" \
  | grep "test_critical_e2e.py::" \
  | grep -v "warning\b" \
  | wc -l
# Expected: 59 → Gate 2 PASS

# Gate 3: 0 failures
gh run view 24533537667 --log \
  | grep "Unit Tests.*Run unit" \
  | grep -E "[0-9]+ passed.*[0-9]+ (failed|skipped)" \
  | tail -1
# Expected: "7766 passed ... 0 failed" → Gate 3 PASS
```

---

## Layer Separation

```
┌─────────────────────────────────────────────────────────┐
│  EXECUTION TRUTH MODEL v1  (CLOSED — this document)     │
│                                                          │
│  Gates 1+2+3: CI success, nodeid execution, 0 failures  │
│  Evidence: raw GitHub Actions logs                       │
│  Status: SATISFIED at SHA cbb5697                        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  ASSERTION OBSERVABILITY LAYER  (BACKLOG)                │
│                                                          │
│  Gate 4: JUnit XML + per-assert runtime values           │
│  Evidence: structured CI artifacts (not yet produced)    │
│  Status: RFC drafted in RFC_CI_OBSERVABILITY_GAP.md      │
│  Release impact: NONE — optional quality improvement     │
└─────────────────────────────────────────────────────────┘
```

These two layers are **architecturally separate**. Implementing the
Assertion Observability Layer does not change, supersede, or invalidate
the Execution Truth Model v1 gate definitions. It adds depth, not
correction.

---

## What Happens Next

1. **Merges to main**: Gates 1+2+3 apply immediately. No action needed.
2. **RFC implementation**: When `RFC_CI_OBSERVABILITY_GAP.md` is
   approved and implemented, a new `CI_GROUND_TRUTH_VERIFICATION_v2.md`
   will be produced with runtime assertion values. The v1 model remains
   valid historical record.
3. **Release decision**: Remains `UNCONDITIONAL GO` per `RELEASE_DECISION.md`.
   The Execution Truth Model v1 closure does not change the release decision;
   it formalizes the evidence standard under which it was made.

---

*Execution Truth Model v1 — CLOSED — 2026-04-17 — main @ cbb5697*
*Practice Booking System — E2E Coverage Audit Program*
