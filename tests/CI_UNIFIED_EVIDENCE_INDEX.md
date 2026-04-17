# CI Unified Evidence Index
## Practice Booking System
**Version: 1.0 | Created: 2026-04-17 | SHA: e556158**
**Authority: Engineering Lead**

> All CI claims in this document are independently verifiable via GitHub Actions API.
> Run IDs are permanent identifiers. SHA pinned at document creation.

---

## §1 — Active CI Suite Inventory

| Suite | Workflow File | Run ID (latest) | SHA | Conclusion | Triggered by |
|-------|--------------|-----------------|-----|-----------|-------------|
| **Backend E2E (pytest)** | `test-baseline-check.yml` | `24538319971` | `e556158` | ✅ success | push to main |
| **Cypress Web E2E** | `cypress-web-e2e.yml` | `24538319993` | `e556158` | ✅ success | push to main |
| Playwright | — | — | — | 🔵 not implemented | — |

**Authoritative release-gate runs** (ETM v1, Gates A/B/C):

| Gate | Run ID | SHA | Timestamp |
|------|--------|-----|-----------|
| Gate A + B (pytest) | `24533537667` | `3138f5b` | 2026-04-16T20:56Z |
| Gate C (Cypress) | `24538022311` | `0d74407` | 2026-04-16T22:49Z |

The above two runs satisfy all required gates per `EXECUTION_TRUTH_MODEL_v1.md`.
Post-amendment runs (`24538319971` / `24538319993` on `e556158`) confirm green state is maintained.

---

## §2 — Backend E2E (pytest) — Run `24538319971`

### Verification Commands

```bash
# Suite conclusion
gh run view 24538319971 --json conclusion
# → {"conclusion":"success"}

# 59 test_critical_e2e.py nodeids executed
gh run view 24538319971 --log \
  | grep "Unit Tests.*Run unit" \
  | grep "test_critical_e2e.py::" \
  | grep -v "warning\b" \
  | wc -l
# → 59

# 0 failures
gh run view 24538319971 --log \
  | grep "Unit Tests.*Run unit" \
  | grep -E "[0-9]+ passed"
# → "7766 passed, 2 skipped, 21 xfailed, 1 xpassed"
```

### Scope

| Metric | Value |
|--------|-------|
| Total pytest tests | 7,766 passed + 2 skipped + 21 xfailed + 1 xpassed |
| test_critical_e2e.py nodeids | 59 (covering 62 BF flows) |
| CI jobs | 24 / 24 success |
| Coverage (BFC) | 62 / 62 = 100% |
| Coverage (ERC) | 105 / 116 = 90.5% (informational) |

---

## §3 — Cypress Web E2E — Run `24538319993`

### Verification Commands

```bash
# Suite conclusion
gh run list --workflow=cypress-web-e2e.yml --branch=main --limit=1 \
  --json databaseId,conclusion,headSha \
  | python3 -c "
import sys,json; r=json.load(sys.stdin)[0]
assert r['conclusion']=='success', r
print(f'Gate C PASS — run {r[\"databaseId\"]} SHA {r[\"headSha\"][:7]}')
"

# Job-level: 0 skipped, 0 failed
gh run view 24538319993 --json jobs \
  | python3 -c "
import sys,json
jobs=json.load(sys.stdin)['jobs']
skipped=[j['name'] for j in jobs if j['conclusion']=='skipped']
failed=[j['name'] for j in jobs if j['conclusion'] in ('failure','cancelled')]
assert not skipped, f'SKIPPED: {skipped}'
assert not failed, f'FAILED: {failed}'
print(f'Gate C PASS — {len(jobs)}/5 success, 0 skipped, 0 failed')
"
```

### Job Summary — Run `24538319993`

| Job | Conclusion | Spec Scope |
|-----|-----------|------------|
| cypress-web-by-role (admin) | ✅ success | `cypress/e2e/web/admin/**` |
| cypress-web-by-role (instructor) | ✅ success | `cypress/e2e/web/instructor/**` + `instructor_session_lifecycle.cy.js` |
| cypress-web-by-role (student) | ✅ success | `cypress/e2e/web/auth/**` + `cypress/e2e/web/student/**` + `student_booking.cy.js` |
| cypress-web-by-role (business-workflow) | ✅ success | `cypress/e2e/web/business_workflows/**` |
| cypress-web-integration | ✅ success | `cypress/e2e/web/cross_role/full_student_lifecycle.cy.js` |

### Scope

| Metric | Value |
|--------|-------|
| Spec files executed | 29 (across 5 CI jobs) |
| Total test cases | 339 |
| Behavioral (action → outcome) | ~205 tests (~61%) |
| Structural (page load / element existence) | ~134 tests (~39%) |
| cypress-web-integration (was skipped pre-fix) | 15 tests, 15/15 passing |

---

## §4 — Future Suites (Placeholder)

| Suite | Status | Trigger | Owner |
|-------|--------|---------|-------|
| Playwright API contract | 🔵 not started | — | backlog |
| Playwright mobile | 🔵 not started | — | backlog |
| Performance / k6 | 🔵 not started | — | backlog |

When a new suite is added, it is registered in this index with its workflow file,
first green run ID, and SHA before being considered a gating artifact.

---

## §5 — SHA Lineage (main branch)

```
3138f5b  fix(mf-02): add enrollment guard to admin_delete_tournament
ccd43b4  fix(mf-02): ...
...
0d74407  fix(ci): add Cypress E2E as required Gate C in release gate
e556158  docs(ci): record verified Gate C run — 24538022311, 5/5 Cypress jobs pass
         ↑ current HEAD (this document pinned at this SHA)
```

---

*CI Unified Evidence Index v1.0 — 2026-04-17 — main @ e556158*
*Practice Booking System — Engineering Lead*
