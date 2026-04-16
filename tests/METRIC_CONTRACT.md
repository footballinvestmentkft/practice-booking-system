# Metric Contract — Coverage Program
## Practice Booking System — Frozen Definitions
**Frozen: 2026-04-16 | SHA: 3138f5b | DO NOT MODIFY**

This document is an immutable metric contract.
It defines exactly what each coverage metric measures, how it is computed, and
what claim it supports. It does **not** add interpretation beyond the formula.

---

## Three Distinct Coverage Metrics

### Metric 1 — Business Flow Coverage (BFC)

**Definition:** The percentage of defined business-critical user flows that have
end-to-end proof via HTTP assertion + DB state assertion + UI HTML assertion.

| Property | Value |
|----------|-------|
| Formula | `(flows with 3-layer proof) / (defined flows)` |
| Unit of measurement | Business user flow |
| Measurement file | `tests/COVERAGE_BASELINE.md` |
| Measurement tool | `scripts/verify_coverage_layers.py` |
| Freeze value | **62 / 62 = 100%** |
| Test file | `tests/integration/web_flows/test_critical_e2e.py` |
| Test count | 59 tests |

**What "3-layer proof" means:**
- **HTTP layer**: the route returns an accepted status code (200/201/303)
- **DB layer**: at least one `db.query(Model).filter(...).first()` confirms state change in database
- **UI layer**: at least one `assert "string" in response.text` confirms HTML output

**What this metric DOES support:**
- Claim: every business-critical financial, enrollment, and authentication action
  has an end-to-end test that proves it works
- Release decision: YES — this is the authoritative release-gating KPI

**What this metric does NOT support:**
- A claim that every HTTP route has a test
- A claim that 100% of code paths are exercised

---

### Metric 2 — Endpoint/Route Coverage (ERC)

**Definition:** The percentage of state-changing HTTP routes (POST/PATCH/PUT/DELETE)
in `app/api/web_routes/` for which ALL fixed (non-path-parameter) URL segments
appear together in at least one test file anywhere in `tests/`.

| Property | Value |
|----------|-------|
| Formula | `(routes with all fixed segments present in ≥1 test file) / (total state-changing routes)` |
| Unit of measurement | HTTP route definition (`@router.{method}("path")`) |
| Measurement scope | `app/api/web_routes/*.py` × `tests/**/*.py` |
| Measurement tool | Static AST scan (reproducible command in `GAP_REPORT_CI_BASED.md`) |
| Freeze value | **105 / 116 = 90.5%** |
| Routes with 0 coverage | **11** (see `GAP_REPORT_CI_BASED.md` and `METRIC_RECONCILIATION.md`) |

**Three sub-tiers within this metric:**
```
BUSINESS_FLOW (BF) — 66 routes:
    Route segments present in test_critical_e2e.py specifically.
    These routes have 3-layer E2E proof.

AST_TOUCHED (AT) — 39 routes:
    Route segments present in other test files only.
    Confirmed present in test corpus but NOT via 3-layer E2E.

ZERO — 11 routes:
    Route segments absent from all test files.
    No test of any kind covers these routes.
```

**What this metric DOES support:**
- A conservative lower bound on route-level test presence
- Identification of the 11 routes with zero test coverage

**What this metric does NOT support:**
- Release decisions — this is NOT a release-gating KPI
- Claim that AT-tier routes are functionally verified (file presence ≠ 3-layer proof)
- A quality claim about the 39 AT-tier routes

**Why 90.5% is not a release KPI:**
The 11 uncovered routes are all classified LOW or MEDIUM risk (see `GAP_REPORT_CI_BASED.md`).
None of them are on the critical financial/enrollment/authentication paths that
the 62 business flows cover. Route coverage percentage says nothing about whether
the uncovered routes are important, tested indirectly, or have compensating controls.

---

### Metric 3 — CI Execution Coverage (CEC)

**Definition:** The percentage of CI workflow jobs that pass on the authoritative
freeze SHA.

| Property | Value |
|----------|-------|
| Formula | `(jobs with conclusion = success) / (total jobs in run)` |
| Unit of measurement | GitHub Actions job |
| Authoritative CI run | `24533537667` (SHA `3138f5b`) |
| Freeze value | **24 / 24 = 100%** |
| Verification command | `gh run view 24533537667 --json jobs` |

**What this metric DOES support:**
- Claim: all test workflows pass on the freeze SHA
- A factual gate: "CI is green"

**What this metric does NOT support:**
- A claim about what is tested in each workflow
- A coverage percentage about routes or code paths

---

## Metric Summary Table

| Metric | Abbreviation | Freeze value | Release KPI? | Primary document |
|--------|-------------|--------------|--------------|------------------|
| Business Flow Coverage | BFC | 62/62 = **100%** | **YES** | `COVERAGE_BASELINE.md` |
| Endpoint/Route Coverage | ERC | 105/116 = **90.5%** | **NO** | `GAP_REPORT_CI_BASED.md` |
| CI Execution Coverage | CEC | 24/24 = **100%** | Yes (gate) | `COVERAGE_FREEZE_v1.md` |

---

## Measurement Reproducibility

All three metrics are reproducible from repo artifacts at SHA `3138f5b`.

**BFC reproducibility:**
```bash
python scripts/verify_coverage_layers.py
# Expected: 59 tests checked, 59 passed, 0 failed
```

**ERC reproducibility:**
```bash
python3 - <<'EOF'
import re
from pathlib import Path
route_re = re.compile(r'@router\.(post|patch|put|delete)\("([^"]+)"', re.IGNORECASE)
test_contents = [(f, f.read_text(errors="ignore")) for f in Path("tests").rglob("*.py")]
def fixed_segs(p): return [s for s in p.split("/") if s and not s.startswith("{")]
def covered(p):
    segs = fixed_segs(p)
    return any(all(s in c for s in segs) for _, c in test_contents) if segs else False
results = []
for rf in sorted(Path("app/api/web_routes").glob("*.py")):
    for m in route_re.finditer(rf.read_text(errors="ignore")):
        results.append(covered(m.group(2)))
print(f"covered={sum(results)} uncovered={len(results)-sum(results)} total={len(results)}")
EOF
# Expected: covered=105 uncovered=11 total=116
```

**CEC reproducibility:**
```bash
gh run view 24533537667 --json jobs \
  | python3 -c "
import sys, json
jobs = json.load(sys.stdin)['jobs']
passed = sum(1 for j in jobs if j['conclusion'] == 'success')
print(f'{passed}/{len(jobs)} passed')
"
# Expected: 24/24 passed
```

---

## Relationship Between Metrics

```
BFC (62 flows) ──┐
                 ├──► All 3 = RELEASE-READY
CEC (24 jobs)  ──┘

ERC (105/116)  ──► Informational only; gap map in METRIC_RECONCILIATION.md
```

The release decision is based on BFC = 100% AND CEC = 100%.
ERC = 90.5% is documented for transparency. The 11-route gap is fully classified
in `GAP_REPORT_CI_BASED.md` and all 11 are LOW or MEDIUM risk.

---

*Metric Contract — 2026-04-16 — main @ 3138f5b*
*Practice Booking System — E2E Coverage Program*
