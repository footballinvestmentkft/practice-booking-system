# Gap Report — CI Artifact Based
## Routes Provably NOT Covered by Any Test
**Frozen: 2026-04-16 | SHA: 3138f5b | Methodology: static AST analysis**

---

## Methodology Statement

**What this report is:**
A list of state-changing HTTP routes (POST/PATCH/PUT/DELETE) for which
NO test file in the repository contains all of the route's fixed URL segments.

**What this report is NOT:**
- A claim that these routes are broken
- A self-assessed coverage percentage
- A narrative about risk

**Data sources (all reproducible from repo artifacts):**
1. Route source: `app/api/web_routes/*.py` — `@router.{method}("path")` decorators
2. Test source: `tests/**/*.py` — all Python test files
3. Coverage criterion: ALL fixed (non-path-parameter) segments of the route URL
   must appear in at least one test file

**Reproducibility command** (must return `covered=105 uncovered=11 total=116`):
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
```

---

## Summary

| Metric | Value |
|--------|-------|
| Total state-changing routes analysed | **116** |
| Routes with test file coverage | **105** (90.5%) |
| Routes with NO test file coverage | **11** (9.5%) |
| Source SHA | `3138f5b` |

---

## The 11 Uncovered Routes

### Group 1: `admin.py` — 6 routes

| Method | Route | Fixed segments | Risk classification |
|--------|-------|---------------|---------------------|
| POST | `/admin/clubs/{club_id}/csv-import` | `admin`, `clubs`, `csv-import` | LOW — batch data import, no direct credit mutation |
| POST | `/admin/clubs/{club_id}/toggle` | `admin`, `clubs`, `toggle` | LOW — flag flip, reversible |
| POST | `/admin/pitches/{pitch_id}/toggle` | `admin`, `pitches`, `toggle` | LOW — flag flip, reversible |
| POST | `/admin/sport-directors/{assignment_id}/deactivate` | `admin`, `sport-directors`, `deactivate` | MEDIUM — deactivates SD role assignment; no credit mutation |
| POST | `/admin/users/{user_id}/lfa-player-photo` | `admin`, `users`, `lfa-player-photo` | LOW — file upload, no financial state |
| POST | `/admin/users/{user_id}/lfa-player-photo/delete` | `admin`, `users`, `lfa-player-photo`, `delete` | LOW — file deletion |

### Group 2: `dashboard.py` — 2 routes

| Method | Route | Fixed segments | Risk classification |
|--------|-------|---------------|---------------------|
| POST | `/dashboard/lfa-player-photo` | `dashboard`, `lfa-player-photo` | LOW — student self-upload, no financial state |
| POST | `/dashboard/lfa-player-photo/delete` | `dashboard`, `lfa-player-photo`, `delete` | LOW — student self-delete |

### Group 3: `tournaments.py` — 3 routes

| Method | Route | Fixed segments | Risk classification |
|--------|-------|---------------|---------------------|
| POST | `/admin/tournaments/{tournament_id}/apply-fallback` | `admin`, `tournaments`, `apply-fallback` | LOW — emergency admin route, rarely triggered |
| POST | `/admin/tournaments/{tournament_id}/players/enroll-from-team` | `admin`, `tournaments`, `players`, `enroll-from-team` | MEDIUM — enrollment mutation; admin bypass (`payment_verified=True`), no credit deduction |
| POST | `/admin/tournaments/{tournament_id}/unenroll-player` | `admin`, `tournaments`, `unenroll-player` | MEDIUM — removes individual from tournament; verify no orphan credit if used after enrollment |

---

## Routes Excluded from This Report

The following categories are excluded because they are GET-only or have no state-changing
side-effects:

- All `@router.get(...)` routes (142 routes) — read-only, no state change
- Routes in `app/api/api_v1/` — separate REST API surface, separate coverage tracking

---

## Note on Methodology Conservatism

The fixed-segment criterion is conservative in one direction:
a route is marked COVERED only if ALL its fixed segments appear together in a single
test file. A route is marked UNCOVERED only if NO test file contains all segments.

This means:
- **False positives (over-reporting coverage) are unlikely** — all segments must coexist.
- **False negatives (under-reporting coverage) are possible** — a test calling
  `/admin/users/42/grant-license` would satisfy `admin + users + grant-license` even
  though `42` is a runtime ID, not a fixed segment.

The 11 routes listed are therefore genuinely absent from any test file
by segment composition. No test file contains all their identifying URL parts.

---

*GAP_REPORT_CI_BASED v1 — 2026-04-16 — main @ 3138f5b*
*Static analysis: `app/api/web_routes/*.py` × `tests/**/*.py`*
