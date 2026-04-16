# Execution Truth Model v1 — Audit Closure
## Practice Booking System
**Status: CLOSED — AMENDED 2026-04-17**
**Closed: 2026-04-17 | SHA: cbb5697 → amended SHA: see §Gate C | Authority: Engineering Lead**

> **AMENDMENT (2026-04-17):** Cypress E2E gate added as Gate C (required).
> Prior v1 was incomplete: `cypress-web-integration` was PR-only (skipped on push to main).
> Fix: `cypress-web-e2e.yml` line 138 — condition extended to include `push` events.
> The amendment does not invalidate Gates 1–3; it adds a third parallel required gate.

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

These are the **four** conditions for a merge to main to be considered
"release gated". Gates A/B/C are required. Gate D is optional (future layer).
All are independently verifiable from GitHub Actions artifacts.

### Gate A — pytest CI Success (REQUIRED)

| Property | Value |
|----------|-------|
| Check | `gh run view {run_id} --json conclusion` returns `"success"` |
| Scope | All 24 jobs in `test-baseline-check.yml` |
| Tolerance | 0 failed jobs |
| Evidence source | GitHub Actions run conclusion (raw API) |
| Status at freeze | ✅ SATISFIED — run `24533537667`, conclusion `success` |

### Gate B — Nodeid Execution + Failure Absence (REQUIRED)

| Property | Value |
|----------|-------|
| Check | 59 test_critical_e2e.py nodeids in step log AND `0 failed, 0 errors` |
| Scope | `Unit Tests` job → `Run unit + integration tests with coverage` step |
| Tolerance | 0 nodeids missing; 0 failures; 0 errors |
| Evidence source | `gh run view {run_id} --log \| grep test_critical_e2e.py:: \| wc -l` = 59; summary = `7766 passed` |
| Status at freeze | ✅ SATISFIED — 59/59 nodeids, `7766 passed, 2 skipped, 21 xfailed, 1 xpassed` |

### Gate C — Cypress E2E Suite (REQUIRED) ← ADDED BY AMENDMENT

| Property | Value |
|----------|-------|
| Check | `cypress-web-e2e.yml` workflow conclusion = `success`; ALL 5 jobs pass (`skipped` not acceptable for `cypress-web-integration`) |
| Scope | `cypress-web-by-role` ×4 + `cypress-web-integration` ×1 = 5 jobs |
| Tolerance | 0 failed jobs; 0 skipped jobs |
| Evidence source | `gh run list --workflow=cypress-web-e2e.yml --branch=main --limit=1 --json databaseId,conclusion` |
| Status at freeze (pre-fix) | ⚠️ PARTIAL — 4/5 jobs passed; `cypress-web-integration` was SKIPPED (PR-only condition) |
| Status after fix | ✅ REQUIRED — `cypress-web-e2e.yml` line 138 amended; `push` events now trigger integration job |
| Fix SHA | see commit after this amendment |

**Gap that was present:** `cypress-web-integration` had `if: github.event_name == 'pull_request'` — the cross-role full student lifecycle Cypress test was never executed on push to main. Fixed by extending the condition to `if: github.event_name == 'pull_request' || github.event_name == 'push'`.

**What Cypress covers (13 specs):**
- `cypress/e2e/web/admin/**` — admin CRUD flows
- `cypress/e2e/web/instructor/**` + `instructor_session_lifecycle.cy.js` — instructor flows
- `cypress/e2e/web/auth/**` + `student/**` + `student_booking.cy.js` — student flows
- `cypress/e2e/web/business_workflows/**` — business lifecycle flows
- `cypress/e2e/web/cross_role/full_student_lifecycle.cy.js` — cross-role integration (was PR-only)

**Gate C verification command:**
```bash
gh run list --workflow=cypress-web-e2e.yml --branch=main --limit=1 \
  --json databaseId,conclusion,headSha \
  | python3 -c "
import sys,json; runs=json.load(sys.stdin)
r=runs[0]; assert r['conclusion']=='success', f'FAIL: {r}'
print(f'Gate C PASS — run {r[\"databaseId\"]} SHA {r[\"headSha\"][:7]}')
"
```

**Gate C job-level verification:**
```bash
gh run view $(gh run list --workflow=cypress-web-e2e.yml --branch=main --limit=1 --json databaseId --jq '.[0].databaseId') \
  --json jobs \
  | python3 -c "
import sys,json
jobs=json.load(sys.stdin)['jobs']
skipped=[j['name'] for j in jobs if j['conclusion']=='skipped']
failed=[j['name'] for j in jobs if j['conclusion'] in ('failure','cancelled')]
assert not skipped, f'SKIPPED jobs (not allowed): {skipped}'
assert not failed, f'FAILED jobs: {failed}'
print(f'Gate C PASS — {len(jobs)}/5 jobs success, 0 skipped, 0 failed')
"
```

### Gate D — Assertion Runtime Visibility (OPTIONAL — FUTURE LAYER)

| Property | Value |
|----------|-------|
| Check | JUnit XML artifact present AND per-assertion log entries readable |
| Scope | test_critical_e2e.py (62 BF flows) |
| Tolerance | N/A — not a release gate |
| Evidence source | NOT YET AVAILABLE — see `RFC_CI_OBSERVABILITY_GAP.md` |
| Status at freeze | 🔵 NOT GATING — backlog item |

> **Rule:** Gate D is explicitly NOT a release gate at v1.
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
# Gate A: CI success
gh run view 24533537667 --json conclusion \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['conclusion']=='success', d['conclusion']; print('Gate A PASS')"

# Gate B: 59 nodeids executed
gh run view 24533537667 --log \
  | grep "Unit Tests.*Run unit" \
  | grep "test_critical_e2e.py::" \
  | grep -v "warning\b" \
  | wc -l
# Expected: 59 → Gate B PASS

# Gate B: 0 failures
gh run view 24533537667 --log \
  | grep "Unit Tests.*Run unit" \
  | grep -E "[0-9]+ passed.*[0-9]+ (failed|skipped)" \
  | tail -1
# Expected: "7766 passed ... 0 failed" → Gate B PASS
```

---

## Layer Separation

```
┌─────────────────────────────────────────────────────────┐
│  EXECUTION TRUTH MODEL v1  (CLOSED — this document)     │
│                                                          │
│  Gates A+B+C: CI success, nodeid execution, Cypress E2E │
│  Evidence: raw GitHub Actions logs                       │
│  Status: SATISFIED at SHA cbb5697 (amended)              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  ASSERTION OBSERVABILITY LAYER  (BACKLOG)                │
│                                                          │
│  Gate D: JUnit XML + per-assert runtime values           │
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
