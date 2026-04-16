# External Verification Checklist
## Practice Booking System — CI Artifact-Only Audit
**Frozen: 2026-04-16 | SHA: 3138f5b | No narrative. No interpretation. Commands only.**

---

## Prerequisites

```bash
# Required tools
gh --version          # GitHub CLI ≥ 2.40
python3 --version     # ≥ 3.11
git --version
sha256sum --version   # or shasum -a 256 on macOS

# Required access
gh auth status        # must be authenticated to football-investment org
git clone https://github.com/football-investment/practice-booking-system
cd practice-booking-system
git checkout 3138f5b  # freeze SHA
```

---

## CHECK-01 — HEAD SHA matches freeze record

```bash
git rev-parse HEAD
```

**Expected output (exact):**
```
3138f5b74effbb680b07b702921a658c77f4ca3d
```

**PASS criterion:** output equals above.

---

## CHECK-02 — MF-02 guard present in source code

```bash
grep -n "ind_count\|team_count\|total_enrollments" \
  app/api/web_routes/tournaments.py
```

**Expected output (at least these lines):**
```
762:    ind_count = db.query(SemesterEnrollment).filter(
766:    team_count = db.query(TournamentTeamEnrollment).filter(
770:    total_enrollments = ind_count + team_count
771:    if total_enrollments > 0:
```

**PASS criterion:** all 4 line patterns present; line numbers within ±5 of expected.

---

## CHECK-03 — Test file checksum matches freeze record

```bash
sha256sum tests/integration/web_flows/test_critical_e2e.py
```

**Expected output (exact):**
```
d47d39016ecd2dbc6e5e89c39b98538acda3640229bbe71787f270d92f94f310  tests/integration/web_flows/test_critical_e2e.py
```

**PASS criterion:** checksum matches character-for-character.

```bash
sha256sum tests/COVERAGE_BASELINE.md
```

**Expected:**
```
f4e0c9b26e907cf559e6f2eb80cf101d1a13704f8dbb682dfb85041688e40e6f  tests/COVERAGE_BASELINE.md
```

```bash
sha256sum scripts/verify_coverage_layers.py
```

**Expected:**
```
34cd76b49b776ed14238defda1bdf1e2ffecd365a492675b96e5e85a1a4519e6  scripts/verify_coverage_layers.py
```

---

## CHECK-04 — Test count matches freeze record

```bash
python -m pytest tests/integration/web_flows/test_critical_e2e.py \
  --collect-only -q 2>/dev/null | grep -c "test_"
```

**Expected output (exact):**
```
59
```

**PASS criterion:** integer equals 59.

---

## CHECK-05 — Coverage layer validator passes

```bash
python scripts/verify_coverage_layers.py
```

**Expected output (last 2 lines):**
```
verify_coverage_layers: 59 tests checked, 59 passed, 0 failed
PASS — all tests have HTTP + DB + UI layers.
```

**PASS criterion:** exit code 0; last line equals `PASS — all tests have HTTP + DB + UI layers.`

---

## CHECK-06 — CI run 24529447831 (Sprint 7) result

```bash
gh run view 24529447831 --json conclusion,headSha,name \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['conclusion'], d['headSha'][:10], d['name'])"
```

**Expected output:**
```
success 553e5c3 Test Baseline Check
```

**PASS criterion:** `conclusion` = `success`.

---

## CHECK-07 — CI run 24532953093 (MF-02 guard) result

```bash
gh run view 24532953093 --json conclusion,headSha,name \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['conclusion'], d['headSha'][:10], d['name'])"
```

**Expected output:**
```
success ccd43b4ee2 Test Baseline Check
```

**PASS criterion:** `conclusion` = `success`.

---

## CHECK-08 — CI run 24532953093 job-level detail (all 24 green)

```bash
gh run view 24532953093 --json jobs \
  | python3 -c "
import sys, json
jobs = json.load(sys.stdin)['jobs']
passed = sum(1 for j in jobs if j['conclusion'] == 'success')
total  = len(jobs)
failed = [j['name'] for j in jobs if j['conclusion'] not in ('success','skipped')]
print(f'{passed}/{total} passed')
if failed: print('FAILED:', failed)
"
```

**Expected output:**
```
24/24 passed
```

**PASS criterion:** `24/24 passed`; no `FAILED:` line.

---

## CHECK-09 — CI run 24533537667 (freeze SHA) result

```bash
gh run view 24533537667 --json conclusion,headSha \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['conclusion'], d['headSha'][:7])"
```

**Expected output:**
```
success 3138f5b
```

**PASS criterion:** `conclusion` = `success`.

---

## CHECK-10 — 62 flows documented in baseline

```bash
grep -c "| F-" tests/COVERAGE_BASELINE.md
```

**Expected output:**
```
64
```
*(62 flows F-01..F-64 + 2 NOT_IMPLEMENTED rows F-26/F-27 = 64 pipe-delimited F- rows)*

**PASS criterion:** integer ≥ 64.

```bash
grep "NOT IMPLEMENTED" tests/COVERAGE_BASELINE.md | wc -l
```

**Expected output:** `2` *(F-26, F-27 — camp routes absent from main)*

---

## CHECK-11 — Zero defined flows uncovered

```bash
grep "| \*\*COVERED\*\*" tests/COVERAGE_BASELINE.md | wc -l
```

**Expected output:**
```
62
```

**PASS criterion:** integer equals 62.

---

## CHECK-12 — MF-02 guard blocks delete with enrollments (functional)

*Requires running test suite locally with DB.*

```bash
python -m pytest tests/integration/web_flows/test_critical_e2e.py \
  -k "test_admin" -v --tb=short 2>/dev/null | tail -15
```

**PASS criterion:** all `test_admin_*` tests pass; exit code 0.

---

## CHECK-13 — Static route→test coverage (gap count)

```bash
python3 - <<'EOF'
import re
from pathlib import Path
from collections import defaultdict

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

**Expected output:**
```
covered=105 uncovered=11 total=116
```

**PASS criterion:** `uncovered=11` and `covered=105`. See `GAP_REPORT_CI_BASED.md` for the 11 routes.

---

## Summary Scorecard

| Check | Description | Expected |
|-------|-------------|----------|
| CHECK-01 | HEAD SHA | `3138f5b...` |
| CHECK-02 | MF-02 guard in source | 4 grep matches |
| CHECK-03 | File checksums | 3 SHA-256 matches |
| CHECK-04 | Test count | 59 |
| CHECK-05 | Layer validator | `59/59 PASS` |
| CHECK-06 | CI run 24529447831 | `success` |
| CHECK-07 | CI run 24532953093 | `success` |
| CHECK-08 | CI 24532953093 jobs | `24/24 passed` |
| CHECK-09 | CI run 24533537667 | `success` |
| CHECK-10 | Flow rows in baseline | ≥ 64 |
| CHECK-11 | Covered flows | 62 |
| CHECK-12 | Admin tests pass | all green |
| CHECK-13 | Static gap count | `uncovered=11` |

**All 13 checks must PASS for the freeze state to be independently verified.**

---

*External Verification Checklist — 2026-04-16 — main @ 3138f5b*
*Practice Booking System — E2E Coverage Program*
