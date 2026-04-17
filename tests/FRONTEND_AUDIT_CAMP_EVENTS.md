# Frontend Audit — CAMP / MINI_SEASON / ACADEMY_SEASON Events
## Practice Booking System
**Version: 1.0 | Created: 2026-04-17 | SHA: main**
**Authority: Engineering Lead**

---

## Executive Summary

The CAMP, MINI_SEASON, and ACADEMY_SEASON event types exist in the data model and have partial admin management interfaces. **Student-facing routes for these event types are entirely absent.** There are 8 confirmed bugs and design gaps — including a silent data integrity bug in the ACADEMY_SEASON generator — and zero E2E or Cypress test coverage for any non-tournament event type.

| Event Type | Admin CRUD | Student Browse | Student Enroll | Session Generation | Instructor Assignment | Test Coverage |
|------------|-----------|---------------|---------------|-------------------|----------------------|--------------|
| TOURNAMENT | ✅ Complete | ✅ Complete | ✅ Complete | ✅ Complete | ✅ Complete | ✅ 62/62 flows |
| CAMP | ✅ Partial (admin only) | ❌ Missing | ❌ Missing | ❌ Missing | ❌ Missing | ❌ None |
| ACADEMY_SEASON | ⚠️ API only (no web form) | ❌ Missing | ❌ Missing | ❌ Missing | ⚠️ Parameter exists | ❌ None |
| MINI_SEASON | ❌ No create route | ❌ Missing | ❌ Missing | ❌ Missing | ❌ Missing | ❌ None |

---

## §1 — Model Layer

### 1.1 SemesterCategory Enum
**File**: `app/models/semester.py:21–26`

```python
class SemesterCategory(str, enum.Enum):
    ACADEMY_SEASON = "ACADEMY_SEASON"  # Jul-Jun multi-month program
    MINI_SEASON = "MINI_SEASON"        # Short academy season (4-8 weeks)
    TOURNAMENT = "TOURNAMENT"          # Competitive tournament
    CAMP = "CAMP"                      # Short-term intensive camp
```

### 1.2 Semester Model Fields (relevant to non-tournament types)
**File**: `app/models/semester.py:38–150`

| Field | Type | Nullable | Default | Notes |
|-------|------|----------|---------|-------|
| `code` | String | ❌ NOT NULL | — | CAMP prefix: "CAMP-{hex6}" |
| `name` | String | ❌ NOT NULL | — | |
| `start_date` | Date | ❌ NOT NULL | — | |
| `end_date` | Date | ❌ NOT NULL | — | |
| `status` | SemesterStatus | ❌ NOT NULL | DRAFT | |
| `semester_category` | SemesterCategory | ✅ nullable | NULL | Nullable — legacy rows have NULL |
| `enrollment_cost` | Integer | ❌ NOT NULL | 500 | Credit cost; applies to CAMP/ACADEMY |
| `campus_id` | FK→Campus | ✅ nullable | NULL | Venue assignment |
| `location_id` | FK→Location | ✅ nullable | NULL | Parent location |
| `specialization_type` | String(50) | ✅ nullable | "LFA_FOOTBALL_PLAYER" | |
| `age_group` | String(20) | ✅ nullable | NULL | PRE / YOUTH / AMATEUR / PRO |
| `theme` | String(200) | ✅ nullable | NULL | Camp/mini-season topic |
| `focus_description` | String(500) | ✅ nullable | NULL | |
| `master_instructor_id` | FK→User | ✅ nullable | NULL | |

### 1.3 Configuration gap vs. TOURNAMENT

TOURNAMENT has a dedicated `TournamentConfiguration` table (participant_type, scoring_type, sessions, enrollment cost, meeting_link, etc.).

**CAMP / MINI_SEASON / ACADEMY_SEASON have NO equivalent configuration table.** All configuration is stored as raw nullable fields directly on the `Semester` row. There is no:
- Session schedule template
- Number of sessions per week
- Min/max enrollment caps
- Time gap between sessions
- Parallel field count

---

## §2 — Route Layer

### 2.1 Admin Routes — CAMP

**File**: `app/api/web_routes/admin.py:1609–1809`

| Route | Method | Auth | Status |
|-------|--------|------|--------|
| `GET /admin/camps` | GET | admin | ✅ Implemented — list with status/age_group/name filters |
| `POST /admin/camps` | POST | admin | ✅ Implemented — create new CAMP semester |
| `GET /admin/camps/{id}/edit` | GET | admin | ✅ Implemented — edit form |
| `POST /admin/camps/{id}/edit` | POST | admin | ✅ Implemented — update camp |
| `GET /admin/events/locations/{id}` | GET | admin | ✅ Implemented — unified location hub showing CAMP + TOURNAMENT + seasons |

**Camp creation schema** (admin.py:1668–1715):
- `name`, `code` (auto-generated "CAMP-{hex6}" if missing), `start_date`, `end_date`
- `age_group` (PRE/YOUTH/AMATEUR/PRO), `location_id`, `campus_id`, `enrollment_cost`
- Creates: `semester_category=SemesterCategory.CAMP`, `status=DRAFT`

### 2.2 Admin Routes — ACADEMY_SEASON

**File**: `app/api/api_v1/endpoints/semesters/academy_generator.py`

| Route | Method | Auth | Status |
|-------|--------|------|--------|
| `POST /api/v1/semesters/generate-academy-season` | POST | admin (Bearer) | ✅ API endpoint only |
| `GET /api/v1/semesters/academy-seasons/available-years` | GET | admin (Bearer) | ✅ API endpoint only |

There is **no web admin form** (`GET /admin/academy-seasons` does not exist). Academy seasons can only be created via direct API call, not through the admin UI. The API requires: `specialization_type`, `location_id`, `campus_id`, `year`, optional `master_instructor_id`.

**Location constraint**: ACADEMY_SEASON requires `location.location_type == LocationType.CENTER` (enforced at line 96). CAMP does NOT have this validation (BUG-01 below).

### 2.3 Admin Routes — MINI_SEASON

**No admin create route for MINI_SEASON exists anywhere in the codebase.**

MINI_SEASON appears only in:
- `SemesterCategory` enum definition
- `admin.py:1862` — location hub query that aggregates seasons at a location
- No create, no edit, no list route

### 2.4 Student-Facing Routes

| Route | Expected | Status |
|-------|----------|--------|
| `GET /events/camps` | Browse available camps | ❌ NOT IMPLEMENTED |
| `GET /events/camps/{id}` | Camp detail page | ❌ NOT IMPLEMENTED |
| `POST /events/camps/{id}/enroll` | Enroll in camp | ❌ NOT IMPLEMENTED |
| `POST /events/camps/{id}/unenroll` | Unenroll (50% refund) | ❌ NOT IMPLEMENTED |
| `GET /events/academy-seasons` | Browse academy seasons | ❌ NOT IMPLEMENTED |
| `GET /events/mini-seasons` | Browse mini-seasons | ❌ NOT IMPLEMENTED |
| `GET /student/my-camps` | Student's enrolled camps | ❌ NOT IMPLEMENTED |

The enrollment route for tournaments (`POST /tournaments/{id}/enroll`) in `tournaments.py` explicitly filters `semester_category == SemesterCategory.TOURNAMENT` at line 443. Camp/season enrollment via this route would fail silently or 404.

### 2.5 Enrollment Auto-Approval Logic

From MEMORY.md note: "CEE — Camp Enrollment: auto-approved (no admin step); same 50% refund logic as tournaments."

The auto-approval logic referenced in MEMORY exists for CAMP in the web route layer (`tournaments.py:230` comment "auto-approved") but this applies to TOURNAMENT routes only. The camp enrollment API endpoint does not exist, so the auto-approval behavior cannot be triggered by a student via any UI route.

**Conclusion**: Auto-approval for CAMP is a design intent that has never been implemented end-to-end. The `SemesterEnrollment` model supports it (status=PENDING→APPROVED), but no student route exists to initiate the enrollment, and no auto-approval code runs on CAMP category objects.

---

## §3 — Template Layer

### 3.1 Admin Templates — CAMP

| Template | Status | Notes |
|----------|--------|-------|
| `app/templates/admin/camps.html` | ✅ Implemented | KPI dashboard, filter bar, create modal, table |
| `app/templates/admin/camp_edit.html` | ✅ Implemented | Edit form with all Semester fields |
| `app/templates/admin/location_events.html` | ✅ Partial | Shows CAMP + TOURNAMENT + seasons in unified hub |

### 3.2 Student Templates — ALL NON-TOURNAMENT TYPES

| Template | Status |
|----------|--------|
| Student camp browse page | ❌ Not implemented |
| Student camp detail page | ❌ Not implemented |
| Student academy season browse | ❌ Not implemented |
| Student mini-season browse | ❌ Not implemented |
| Dashboard "My Camps" widget | ❌ Not implemented |

The student dashboard (`app/api/web_routes/dashboard.py`) filters events as `!= TOURNAMENT` (line 264) but does not specifically surface CAMP/MINI_SEASON/ACADEMY_SEASON events to students — they are invisible in all student-facing views.

---

## §4 — Scheduling and Timing Analysis

### 4.1 Session Generation — TOURNAMENT vs. CAMP

TOURNAMENT has a full session generation pipeline:
- `app/services/tournament/session_generation/` — 5 format generators (IR, League, Knockout, GK, Swiss)
- `get_campus_schedule()` in `utils.py:150` — resolves timing parameters per campus
- `pick_campus()` in `utils.py:13` — round-robin multi-campus distribution
- `pick_pitch()` — field assignment within campus
- `generate_sessions` API endpoint validates campus_id, parallel_fields, sessions config

**CAMP has none of this.** There is no:
- Session generator for CAMP
- `CampSessionGenerator` or equivalent service
- API endpoint to generate sessions for a camp
- Campus schedule template for camps

Sessions for a camp must be created manually via the generic session create route (if it exists for non-tournament contexts). There is no admin UI for bulk session scheduling under a camp.

### 4.2 Campus-Level Breakdown

**For TOURNAMENT:**
- Multi-campus session distribution: round-robin via `pick_campus()`
- Per-campus pitch assignment: `pick_pitch()` based on `parallel_fields`
- Campus schedule parameters: `session_start_time`, `session_end_time`, `break_duration_minutes` (from `CampusScheduleConfig` if present, else tournament defaults)
- Schedule gap enforcement: `break_duration_minutes` between sessions

**For CAMP:**
- `campus_id` stored on Semester row — single campus association only
- No per-campus schedule parameters
- No break-duration or time-gap enforcement
- No multi-campus distribution mechanism
- No field/pitch assignment logic

### 4.3 Time Gap Between Sessions — Gap Analysis

The tournament session generator enforces gaps via `break_duration_minutes` in `TournamentConfiguration`. **No equivalent field exists for CAMP, MINI_SEASON, or ACADEMY_SEASON.** If sessions were manually added to a camp, there is:
- No minimum break enforcement between consecutive sessions
- No instructor availability check across sessions
- No capacity-overlap detection between campuses

### 4.4 Instructor Responsibility and Timing

**For TOURNAMENT:**
- `TournamentInstructorSlot` table: `instructor_id`, `slot_role` (MASTER/FIELD), `campus_id`, `pitch_id`, `session_id`, `status` (PLANNED/CHECKED_IN)
- Admin can assign instructors per-session, per-pitch, per-campus
- Check-in flow: `POST /admin/tournaments/{id}/players/{pid}/checkin` verifies slot status
- F-61 (instructor slot create) + F-62 (player checkin) are tested in E2E

**For CAMP/MINI_SEASON/ACADEMY_SEASON:**
- No `CampInstructorSlot` table
- No instructor assignment routes
- `master_instructor_id` on `Semester` is the only instructor reference (single FK, no slot model)
- No per-session instructor assignment
- No instructor timing validation

ACADEMY_SEASON generator accepts `master_instructor_id` as an optional parameter but sets status to `DRAFT` (not `SEEKING_INSTRUCTOR` as documented in the code, line 185 of `academy_generator.py` actually sets `DRAFT` when master_instructor_id IS provided — this is a logic inversion bug, BUG-05 below).

---

## §5 — Bug Register

### BUG-01 — CRITICAL: LocationType not enforced for CAMP

**Severity**: HIGH
**File**: `app/api/web_routes/admin.py:1668–1715`
**Evidence**: CAMP create route (`POST /admin/camps`) accepts any `location_id` without checking `location.location_type`. ACADEMY_SEASON enforces `LocationType.CENTER` (academy_generator.py:96), but CAMP does not.
**Impact**: Camps can be created at PARTNER locations, violating the intended business rule that PARTNER locations are for tournaments only.
**Fix**: Add `if location.location_type not in [LocationType.CENTER, LocationType.PARTNER]: raise HTTPException(...)` or document explicit policy.

### BUG-02 — CRITICAL: ACADEMY_SEASON generator omits `semester_category` field

**Severity**: CRITICAL
**File**: `app/api/api_v1/endpoints/semesters/academy_generator.py:176–186`
**Evidence**:
```python
new_semester = Semester(
    code=semester_code,
    name=semester_name,
    specialization_type=request.specialization_type.value,
    start_date=start_date,
    end_date=end_date,
    location_id=request.location_id,
    campus_id=request.campus_id,
    master_instructor_id=...,
    status=SemesterStatus.DRAFT
    # ⚠️ semester_category is NOT SET
)
```
**Impact**: Every ACADEMY_SEASON created via the generator has `semester_category = NULL` in the database. The location hub query at `admin.py:1862` filters `semester_category.in_([SemesterCategory.ACADEMY_SEASON, SemesterCategory.MINI_SEASON])` — so these semesters do NOT appear in the location hub. The admin has no UI way to find created academy seasons.
**Fix**: Add `semester_category=SemesterCategory.ACADEMY_SEASON` to the Semester constructor.

### BUG-03 — CRITICAL: MINI_SEASON has no create route

**Severity**: HIGH
**File**: All web_routes
**Evidence**: No `POST /admin/mini-seasons` or equivalent route. MINI_SEASON exists only as an enum value and a filter in the location hub query.
**Impact**: MINI_SEASON semesters cannot be created via any admin UI route. Only via direct DB insert or as raw Semester objects with `semester_category=MINI_SEASON` (no web surface).
**Fix**: Either implement `POST /admin/mini-seasons` (similar to `/admin/camps`) or document that MINI_SEASON is a future feature and remove from the enum until implemented.

### BUG-04 — HIGH: No student-facing enrollment routes for CAMP

**Severity**: HIGH
**File**: All student-facing web_routes
**Evidence**: No `GET /events/camps`, no `POST /events/camps/{id}/enroll`. The test_critical_e2e.py marks "CEE — Camp Enrollment (NOT IMPLEMENTED on main)".
**Impact**: Students cannot browse or enroll in camps through the UI. Credit deduction for camp enrollment cannot occur. The `enrollment_cost` field on CAMP semesters has no functional effect.
**Fix**: Implement `/events/camps` (browse), `/events/camps/{id}` (detail), `POST /events/camps/{id}/enroll` (enrollment with credit deduction + auto-approval).

### BUG-05 — MEDIUM: ACADEMY_SEASON status logic inversion in generator

**Severity**: MEDIUM
**File**: `app/api/api_v1/endpoints/semesters/academy_generator.py:185`
**Evidence**:
```python
status=SemesterStatus.DRAFT if not request.master_instructor_id else SemesterStatus.SEEKING_INSTRUCTOR
```
The logic is inverted: when `master_instructor_id` IS provided (instructor assigned), status is set to `SEEKING_INSTRUCTOR`. When no instructor is provided, status is `DRAFT`. Should be the reverse: assigned → `READY_FOR_ENROLLMENT`, unassigned → `SEEKING_INSTRUCTOR` (or `DRAFT`).
**Fix**: `status=SemesterStatus.SEEKING_INSTRUCTOR if not request.master_instructor_id else SemesterStatus.DRAFT`

### BUG-06 — HIGH: No refund logic for CAMP unenroll

**Severity**: HIGH
**File**: No file — route does not exist
**Evidence**: The MEMORY.md documents "50% refund logic same as tournaments" for CAMP. There is no `POST /events/camps/{id}/unenroll` route. No `CreditTransaction` for CAMP refund.
**Impact**: If a student were enrolled in a camp (via admin-level DB action), there is no way to unenroll and receive a refund through any UI route.

### BUG-07 — MEDIUM: No session generation API for CAMP

**Severity**: MEDIUM
**File**: `app/api/api_v1/endpoints/tournaments/generate_sessions.py` (tournament-only)
**Evidence**: Session generation is gated on `semester_category == SemesterCategory.TOURNAMENT` (or code pattern). No equivalent endpoint for CAMP.
**Impact**: Camp schedules cannot be bulk-generated. All sessions must be manually created (if any session creation route accepts CAMP semester_id — unverified).

### BUG-08 — LOW: ACADEMY_SEASON location hub filter excludes NULL semester_category

**Severity**: LOW
**File**: `app/api/web_routes/admin.py:1861`
**Evidence**: Location hub query:
```python
Semester.semester_category.in_([SemesterCategory.ACADEMY_SEASON, SemesterCategory.MINI_SEASON])
```
Combined with BUG-02, all academy seasons created via the generator have `semester_category=NULL` and will never appear in this query.
**Impact**: Admin-created academy seasons are invisible in the location hub.

---

## §6 — Timing and Campus Scheduling — Detailed Analysis

### 6.1 Scheduling Depth by Event Type

| Feature | TOURNAMENT | CAMP | ACADEMY_SEASON | MINI_SEASON |
|---------|-----------|------|---------------|------------|
| Session generation (bulk) | ✅ 5 format generators | ❌ | ❌ | ❌ |
| Multi-campus round-robin | ✅ `pick_campus()` | ❌ | ❌ | ❌ |
| Per-campus pitch assignment | ✅ `pick_pitch()` | ❌ | ❌ | ❌ |
| Break duration between sessions | ✅ `break_duration_minutes` | ❌ | ❌ | ❌ |
| Campus schedule config | ✅ `CampusScheduleConfig` | ❌ | ❌ | ❌ |
| Parallel field support | ✅ `parallel_fields` | ❌ | ❌ | ❌ |
| Instructor slot model | ✅ `TournamentInstructorSlot` | ❌ | ❌ | ❌ |
| Instructor check-in flow | ✅ F-62 implemented | ❌ | ❌ | ❌ |
| Session type (virtual/hybrid/on_site) | ✅ `session_type_config` | ❌ | ❌ | ❌ |

### 6.2 Campus-Level Differentiation

For TOURNAMENT, campus-level scheduling uses `get_campus_schedule()` (`utils.py:150`) which resolves:
- `session_start_time`, `session_end_time`, `session_duration`
- `break_duration_minutes` (minimum gap between consecutive sessions)
- Pitch selection within campus

For CAMP, `campus_id` is a single FK on the Semester. There is no mechanism to:
- Assign different sessions to different campuses under the same camp
- Enforce minimum time gaps between back-to-back sessions
- Track which instructor covers which campus on which day

### 6.3 Sub-Event Timing (Rounds, Matches)

TOURNAMENT has full round/phase tracking:
- `TournamentPhase` (GROUP_STAGE, KNOCKOUT, FINALS, PLACEMENT, SWISS, IR)
- `game_results`, `rounds_data` JSON fields for match sequencing
- Phase ordering via `sql_case()` in route layer

CAMP/MINI_SEASON/ACADEMY_SEASON have **no sub-event model**:
- No round tracking
- No match sequencing
- No phase ordering
- Sessions (if they exist) are flat and unordered within the semester

### 6.4 Inter-Session Time Gap Enforcement

There is no server-side validation preventing two sessions at the same campus being scheduled with zero-minute gaps for CAMP. For TOURNAMENT, `break_duration_minutes` in `TournamentConfiguration` provides this guard. CAMP has no equivalent, and even if sessions were manually created, the system would not detect or reject overlapping schedules.

---

## §7 — Test Coverage

### 7.1 Existing Coverage

| Test type | Coverage for CAMP/MINI/ACADEMY |
|-----------|-------------------------------|
| pytest E2E (test_critical_e2e.py) | ❌ None (CEE marked "NOT IMPLEMENTED on main") |
| pytest web_flows | ❌ None |
| Cypress E2E | ❌ None |
| Unit (admin route) | Unknown — not verified |

### 7.2 What "NOT IMPLEMENTED" Means in test_critical_e2e.py

The CEE (Camp Enrollment E2E) test comment confirms the design intent exists but the routes are absent. The 50% refund logic, auto-approval, and credit deduction patterns for CAMP are documented in MEMORY but untestable because the student enrollment endpoint does not exist.

### 7.3 Risk Classification

| Gap | Risk if in production | Probability of being hit |
|-----|----------------------|--------------------------|
| No student camp enrollment | CRITICAL — feature non-functional | HIGH (any student tries) |
| BUG-02 (NULL semester_category) | HIGH — admin can't find created seasons | MEDIUM (if anyone uses API) |
| BUG-05 (status logic inversion) | MEDIUM — wrong status, may block workflows | LOW (API rarely used) |
| BUG-01 (LocationType not checked) | MEDIUM — data integrity | LOW (admin creates camps) |
| No session generation | HIGH — camp scheduling impossible at scale | HIGH (production use) |
| No time-gap enforcement | MEDIUM — overlapping sessions undetected | MEDIUM |

---

## §8 — Recommended Implementation Order

### Phase 1 — Critical student-facing routes (blocks any production use)

| Item | Route | Files to create/modify |
|------|-------|----------------------|
| P1-01 | `GET /events/camps` — browse available camps | `student_features.py` or new `events_camp.py` |
| P1-02 | `GET /events/camps/{id}` — camp detail + sessions | Same |
| P1-03 | `POST /events/camps/{id}/enroll` — enroll + credit deduction + auto-approve | Same + `SemesterEnrollment` create |
| P1-04 | `POST /events/camps/{id}/unenroll` — unenroll + 50% refund | Same + `CreditTransaction` create |
| P1-05 | Student templates: browse, detail | `app/templates/student/camps/` |
| P1-06 | Dashboard: "My Camps" widget | `dashboard.py` + `dashboard.html` |

### Phase 2 — Bug fixes (data integrity)

| Item | Fix | File |
|------|-----|------|
| P2-01 | BUG-02: Add `semester_category=SemesterCategory.ACADEMY_SEASON` to generator | `academy_generator.py:176` |
| P2-02 | BUG-05: Fix status logic inversion | `academy_generator.py:185` |
| P2-03 | BUG-01: Enforce LocationType for CAMP | `admin.py:1700` |
| P2-04 | BUG-03: Implement `POST /admin/mini-seasons` or mark as future | `admin.py` |

### Phase 3 — Admin UX completeness

| Item | Route | Notes |
|------|-------|-------|
| P3-01 | Web admin form for ACADEMY_SEASON | `GET/POST /admin/academy-seasons` |
| P3-02 | ACADEMY_SEASON edit form | `GET/POST /admin/academy-seasons/{id}/edit` |
| P3-03 | Camp enrollment approval admin UI | `GET/POST /admin/camps/{id}/enrollments` |

### Phase 4 — Scheduling infrastructure (future sprint)

| Item | Notes |
|------|-------|
| P4-01 | `CampConfiguration` table (sessions_per_week, min_break_minutes, max_students) |
| P4-02 | Camp session generator (flat schedule, no rounds/phases) |
| P4-03 | `CampInstructorSlot` model — per-session instructor assignment for camps |
| P4-04 | Time-gap validation for camp sessions |

---

## §9 — Verdict

The CAMP, MINI_SEASON, and ACADEMY_SEASON event types are in a **skeleton state**:

- The data model exists and is coherent (with BUG-02 as the only structural corruption risk)
- Admin CAMP management is functional (create/edit/list)
- **Zero student-facing functionality exists for any non-tournament event type**
- MINI_SEASON cannot be created through any interface
- ACADEMY_SEASON has a broken generator (BUG-02, BUG-05) and no web UI
- Scheduling, timing, and instructor assignment are tournament-exclusive subsystems — not ported to CAMP

**These event types are not production-ready.** Any student-facing launch of CAMP or academy programs requires Phase 1 implementation as a minimum.

---

*Frontend Audit — CAMP / MINI_SEASON / ACADEMY_SEASON — v1.0 — 2026-04-17 — main*
*Practice Booking System — Engineering Lead*
