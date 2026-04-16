# COVERAGE FREEZE — v1
## Practice Booking System · Immutable Audit Snapshot
**Frozen: 2026-04-16T22:54:59+02:00 | DO NOT MODIFY**

This file is an immutable audit record. It must not be updated in place.
If the coverage state changes, a new version (v2, v3, …) must be created.

---

## 1. Repository State at Freeze

| Field | Value |
|-------|-------|
| Repository | `football-investment/practice-booking-system` |
| Branch | `main` |
| HEAD SHA (full) | `3138f5b74effbb680b07b702921a658c77f4ca3d` |
| HEAD SHA (short) | `3138f5b` |
| Commit message | `docs(baseline): update CI SHA — Test Baseline Check ccd43b4 (24/24 green, MF-02 resolved)` |
| Committed at | `2026-04-16T22:54:59+02:00` |
| Author | Claude Sonnet 4.6 |

### Commit chain (freeze point → program start)

| Short SHA | Timestamp | Message |
|-----------|-----------|---------|
| `3138f5b` | 2026-04-16 22:54 | docs(baseline): CI SHA update — 24/24 green |
| `ccd43b4` | 2026-04-16 22:41 | **fix(mf-02): enrollment guard → UNCONDITIONAL GO** |
| `4ebf4cc` | 2026-04-16 22:24 | docs(release): Release Decision — CONDITIONAL GO |
| `df543fa` | 2026-04-16 22:06 | docs(coverage): Coverage Acceptance & Risk Sign-off |
| `6439aad` | 2026-04-16 21:47 | docs(coverage): Final Coverage Consolidation Report |
| `805d87c` | 2026-04-16 21:41 | Merge PR #51 (Sprint 7 → main) |
| `553e5c3` | (on branch) | test(coverage): Sprint 7 — F-57..F-64 |

---

## 2. CI Artifact References

All runs verifiable via: `gh run view {RUN_ID}`

| Run ID | Workflow | SHA | Result | Jobs |
|--------|----------|-----|--------|------|
| **24533537667** | Test Baseline Check | `3138f5b` | ✅ success | 24/24 |
| **24532953093** | Test Baseline Check | `ccd43b4` | ✅ success | 24/24 |
| 24532946385 | Test Baseline Check | `ccd43b4` | ✗ cancelled (infra) | — |
| 24532202904 | Test Baseline Check | `4ebf4cc` | ✅ success | 24/24 |
| 24531417476 | Test Baseline Check | `df543fa` | ✅ success | 24/24 |
| **24529447831** | Test Baseline Check | `553e5c3` (Sprint 7) | ✅ success | 24/24 |

**Authoritative run for MF-02 guard verification: `24532953093`**
Verification command: `gh run view 24532953093 --json jobs`

---

## 3. Test Artifact Checksums (SHA-256)

Computed at freeze point from main @ `3138f5b`.

| File | SHA-256 |
|------|---------|
| `tests/integration/web_flows/test_critical_e2e.py` | `d47d39016ecd2dbc6e5e89c39b98538acda3640229bbe71787f270d92f94f310` |
| `tests/COVERAGE_BASELINE.md` | `f4e0c9b26e907cf559e6f2eb80cf101d1a13704f8dbb682dfb85041688e40e6f` |
| `scripts/verify_coverage_layers.py` | `34cd76b49b776ed14238defda1bdf1e2ffecd365a492675b96e5e85a1a4519e6` |

Verification command:
```bash
sha256sum tests/integration/web_flows/test_critical_e2e.py \
          tests/COVERAGE_BASELINE.md \
          scripts/verify_coverage_layers.py
```

---

## 4. Coverage Metrics at Freeze

| Metric | Value | Source |
|--------|-------|--------|
| Defined business flows | 62 | `tests/COVERAGE_BASELINE.md` §0 |
| E2E-covered flows (3-layer) | 62 / 62 | `tests/COVERAGE_BASELINE.md` §0 |
| `test_critical_e2e.py` test count | 59 | `pytest --collect-only -q` |
| `verify_coverage_layers.py` result | `59 tests checked, 59 passed, 0 failed` | local + CI run 24529447831 |
| Web routes (total, all methods) | 264 | static analysis `app/api/web_routes/` |
| State-changing routes (POST/PATCH/PUT/DELETE) | 116 | static analysis |
| Routes with all fixed segments in test corpus | 105 | `GAP_REPORT_CI_BASED.md` analysis |
| Routes provably NOT in any test | 11 | `GAP_REPORT_CI_BASED.md` |

---

## 5. Key Document Inventory

| Document | Path | Purpose | Mutable? |
|----------|------|---------|---------|
| Coverage Baseline | `tests/COVERAGE_BASELINE.md` | Living reference — updated per sprint | Yes |
| Closure Report | `tests/COVERAGE_CLOSURE_REPORT.md` | Program sign-off, gap map, residual risk | No |
| Acceptance Sign-off | `tests/COVERAGE_ACCEPTANCE_SIGNOFF.md` | MUST FIX / ACCEPTED / BACKLOG | No |
| Release Decision | `tests/RELEASE_DECISION.md` | GO/NO-GO with code evidence | No |
| **This file** | `tests/COVERAGE_FREEZE_v1.md` | **Immutable audit snapshot** | **No** |
| Gap Report | `tests/GAP_REPORT_CI_BASED.md` | 11 uncovered routes, CI-artifact based | No |
| Ext. Verification | `tests/EXTERNAL_VERIFICATION_CHECKLIST.md` | Independent audit steps (no narrative) | No |

---

## 6. Freeze Attestation

This snapshot was generated programmatically from:
- `git log` output (SHA, timestamps)
- `gh run list` output (CI run IDs, results)
- `sha256sum` of key artifact files
- `pytest --collect-only` (test count)
- `python scripts/verify_coverage_layers.py` (layer validation)
- Static AST analysis of `app/api/web_routes/*.py` vs `tests/**/*.py`

No manual interpretation was applied to derive any metric in this document.
All values are reproducible by re-running the commands above on the same SHA.

---

*COVERAGE FREEZE v1 — 2026-04-16 — main @ 3138f5b*
*Practice Booking System — E2E Coverage Program*
