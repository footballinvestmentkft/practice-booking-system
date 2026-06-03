# Practice Booking System

LFA Education Center - Session menedzsment, foglalás, jelenlét és gamification rendszer.

---

## 🚀 Gyors Indítás

### Option A — Docker Compose (ajánlott)

Az összes service (web + mood worker + Redis + PostgreSQL) egy paranccsal indul:

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Web / API | http://localhost:8000 |
| API Docs  | http://localhost:8000/docs |

### Option B — Manuális indítás (két terminál)

**Terminal 1 — Web server:**
```bash
make web
# vagy:
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 — Mood photo background removal worker:**
```bash
make worker-mood
# vagy:
celery -A app.celery_app worker -Q mood_photos --pool=solo --loglevel=info
```

> ⚠️ **Fontos:** A `worker-mood` process **kötelező** ha `BG_REMOVAL_PROCESSOR=rembg` van
> beállítva a `.env`-ben. Nélküle a feltöltött mood fotók `Processing...` állapotban
> maradnak, és soha nem kerülnek feldolgozásra.

### Mood Worker Environment (.env)

```env
BG_REMOVAL_PROCESSOR=rembg   # "null" = no real removal, "rembg" = real processing
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

### Processing stuck fotók helyreállítása

Ha a worker leállt és képek `Processing...` állapotban ragadtak:

```bash
make recover-mood          # dry-run: megmutatja mi lenne érintett
make recover-mood-execute  # tényleges visszaállítás 'uploaded' állapotba
```

### Minden make parancs

```bash
make help            # összes parancs listázása
make web             # web server
make worker-mood     # mood photo worker
make worker-all      # minden queue worker
make recover-mood    # stuck fotók dry-run ellenőrzése
make migrate         # Alembic migrációk
make test-unit       # unit tesztek
make docker-up       # docker compose up
```

### Hálózaton való elérés (WiFi)

A szerver `0.0.0.0`-n hallgat, ezért ugyanazon a WiFi hálózaton:
```bash
# Mac IP lekérdezése:
ifconfig | grep "inet " | grep -v 127.0.0.1
# Másik eszközről: http://<MAC_IP>:8000
```

---

### Korábbi indítási mód (legacy)

```bash
./start_backend.sh
```

**URL**: http://localhost:8000
**API Docs**: http://localhost:8000/docs
**Admin Panel**: http://localhost:8000/admin/users

---

## 📖 Dokumentáció

### Core Dokumentáció

- **Session Rules**: [docs/CURRENT/SESSION_RULES_ETALON.md](docs/CURRENT/SESSION_RULES_ETALON.md) - 6 Session Rule specifikáció
- **Backend Implementáció**: [docs/CURRENT/SESSION_RULES_BACKEND_IMPLEMENTATION_COMPLETE.md](docs/CURRENT/SESSION_RULES_BACKEND_IMPLEMENTATION_COMPLETE.md) - Teljes backend dokumentáció
- **Teljes Összefoglaló**: [docs/CURRENT/SESSION_RULES_COMPLETE_IMPLEMENTATION_SUMMARY.md](docs/CURRENT/SESSION_RULES_COMPLETE_IMPLEMENTATION_SUMMARY.md) - Gyors áttekintés
- **Magyar Handoff**: [docs/CURRENT/KESZ_SESSION_RULES_TELJES.md](docs/CURRENT/KESZ_SESSION_RULES_TELJES.md) - Magyar összefoglaló

### Audit Dokumentáció (2025-12-17) ⭐ ÚJ

- **Database Audit**: [docs/CURRENT/DATABASE_STRUCTURE_AUDIT_COMPLETE.md](docs/CURRENT/DATABASE_STRUCTURE_AUDIT_COMPLETE.md) - 32 model audit, 90.75% minőség
- **API Endpoint Audit**: [docs/CURRENT/API_ENDPOINT_AUDIT_COMPLETE.md](docs/CURRENT/API_ENDPOINT_AUDIT_COMPLETE.md) - N+1 query problémák, optimalizálás
- **Testing Coverage Audit**: [docs/CURRENT/TESTING_COVERAGE_AUDIT_COMPLETE.md](docs/CURRENT/TESTING_COVERAGE_AUDIT_COMPLETE.md) - Test coverage gaps, 4.5/10 quality
- **System Architecture**: [docs/CURRENT/SYSTEM_ARCHITECTURE.md](docs/CURRENT/SYSTEM_ARCHITECTURE.md) - Architektúra diagram + layered design
- **API Endpoint Summary**: [docs/CURRENT/API_ENDPOINT_SUMMARY.md](docs/CURRENT/API_ENDPOINT_SUMMARY.md) - 349 endpoint összefoglaló

### P0 Refactoring - Code Quality (2025-12-21) 🎉 ÚJ

- **Phase 4 Final Report**: [docs/refactoring/P0_PHASE_4_FINAL_REPORT.md](docs/refactoring/P0_PHASE_4_FINAL_REPORT.md) - 12 nagy fájl → 41 modul refactoring ✅
- **Phase 4 Magyar**: [docs/refactoring/P0_PHASE_4_JAVITASI_OSSZEFOGLALO_HU.md](docs/refactoring/P0_PHASE_4_JAVITASI_OSSZEFOGLALO_HU.md) - Teljes javítási összefoglaló 🇭🇺
- **Impact**: 75% kisebb átlagos fájlméret (600→150 sor), 370 route működik ⚡

### P0 + P1 Teljesítés Dokumentáció (2025-12-17) 🎉 ÚJ

- **Deployment Ready Summary**: [DEPLOYMENT_READY_SUMMARY.md](DEPLOYMENT_READY_SUMMARY.md) - Executive summary (95/100) 🚀 ÚJ
- **Production Deployment Checklist**: [PRODUCTION_DEPLOYMENT_CHECKLIST.md](PRODUCTION_DEPLOYMENT_CHECKLIST.md) - Deployment útmutató 🚀 ÚJ
- **P1 Tasks Summary**: [P1_TASKS_COMPLETE_SUMMARY.md](P1_TASKS_COMPLETE_SUMMARY.md) - Teljes P0+P1 összefoglaló
- **P0 Tasks Complete**: [P0_TASKS_COMPLETE.md](P0_TASKS_COMPLETE.md) - 4 HIGH severity N+1 fix + 52 új teszt
- **P1 MEDIUM N+1 Fixes**: [P1_MEDIUM_N+1_FIXES_COMPLETE.md](P1_MEDIUM_N+1_FIXES_COMPLETE.md) - 4 MEDIUM severity N+1 fix

### HTML Frontend / FastAPI Migration (2026-03-12) ✅

- **Migration Summary**: [docs/migrations/streamlit_removal.md](docs/migrations/streamlit_removal.md) - Streamlit decommission details
- **Archive**: `archive/streamlit_app/` — legacy Streamlit source (git history preserved)

### Technical Guides ⭐ ÚJ

- **Credit System Flow**: [docs/CURRENT/CREDIT_SYSTEM_FLOW_COMPLETE.md](docs/CURRENT/CREDIT_SYSTEM_FLOW_COMPLETE.md) - Dual credit system, Mermaid diagramok
- **Slow Query Monitoring**: [docs/CURRENT/SLOW_QUERY_MONITORING_GUIDE.md](docs/CURRENT/SLOW_QUERY_MONITORING_GUIDE.md) - Performance monitoring setup

### Útmutatók és Tesztelés

- **Tesztelési Útmutató**: [docs/GUIDES/GYORS_TESZT_INDITAS.md](docs/GUIDES/GYORS_TESZT_INDITAS.md)
- **Teszt Fiókok**: [docs/GUIDES/TESZT_FIOKOK_UPDATED.md](docs/GUIDES/TESZT_FIOKOK_UPDATED.md)
- **Session Rules Dashboard**: [docs/GUIDES/SESSION_RULES_DASHBOARD_README.md](docs/GUIDES/SESSION_RULES_DASHBOARD_README.md)

### Archív Dokumentumok

Régebbi dokumentáció és legacy fájlok: [docs/ARCHIVED/](docs/ARCHIVED/)

---

## ✅ Rendszer Státusz

**Utolsó frissítés**: 2025-12-21

| Komponens | Státusz | Megjegyzés |
|-----------|---------|------------|
| **Backend API** | ✅ 100% | 370 route, 41 refactored endpoint module 🎉 |
| **Database Models** | ✅ 100% | 32 model, 69+ migráció, 90.75% minőség ⭐ |
| **Session Rules** | ✅ 100% | Mind a 6 szabály implementálva |
| **API Performance** | ✅ 98.7% | 8/12 N+1 pattern fixed, 98.7% query reduction 🎉 |
| **Test Coverage** | ✅ 45% | 221 teszt (+58 új), Session Rules 100% 🎉 |
| **Code Quality** | ✅ 100% | Phase 3+4 refactoring complete, 75% file size reduction ⚡ |
| **Dashboard** | ✅ 100% | Unified workflow dashboard |

---

## 🎉 P0 + P1 Tasks - 100% TELJESÍTVE (2025-12-17)

### Összefoglaló Metrikák

| Kategória | Előtte | Utána | Javulás |
|-----------|--------|-------|---------|
| **DB Queries/Request** | ~1,434 | ~18 | **98.7% ⬇️** |
| **Response Time** | ~7,170ms | ~90ms | **98.7% ⚡** |
| **Test Count** | 163 | **221** | **+58 tests ✅** |
| **Test Coverage** | 25% | **45%** | **+20% 📈** |

### Befejezett Feladatok

#### ✅ P0 Tasks (Week 1)
1. **HIGH Severity N+1 Fixes** - 4 endpoint (1,126 → 13 queries)
2. **Session Rules Tests** - 24 test (100% coverage)
3. **Core Model Tests** - 28 test (~70% coverage)

#### ✅ P1 Tasks (Week 2-3)
1. **MEDIUM Severity N+1 Fixes** - 4 endpoint (~308 → ~5 queries)
2. **Integration Tests** - 6 test (3 critical flows)
3. **Service Layer Tests** - 20 test (már létezett)

**Részletek**: [P1_TASKS_COMPLETE_SUMMARY.md](P1_TASKS_COMPLETE_SUMMARY.md)

### 📅 Következő Lépések (P2 - MEDIUM PRIORITY)

#### Week 4-5 Tervezett Feladatok
1. **LOW Severity N+1 Fixes** - 5 endpoint (pagination, SELECT *)
2. **Model Tests** - 28 további model (~60 test)
3. **Endpoint Tests** - Coverage gaps (~40 test)
4. **Performance Testing Framework** - Load testing setup

**Cél**: 60% test coverage elérése (jelenleg 45%)

---

## 🎯 Session Rules (6/6 Implementálva)

Mind a 6 Session Rule **100% implementálva** és **működik**:

1. **Rule #1**: 24h Booking Deadline - Foglalás csak 24 órával előre
2. **Rule #2**: 12h Cancel Deadline - Törlés csak 12 órával előre
3. **Rule #3**: 15min Check-in Window - Check-in 15 perccel session előtt
4. **Rule #4**: 24h Feedback Window - Feedback 24 órán belül session után
5. **Rule #5**: Session-Type Quiz - Quiz csak HYBRID/VIRTUAL alatt
6. **Rule #6**: Intelligent XP - XP = Base(50) + Instructor(0-50) + Quiz(0-150)

**Részletek**: [docs/CURRENT/SESSION_RULES_ETALON.md](docs/CURRENT/SESSION_RULES_ETALON.md)

---

## 🛠️ Technológiai Stack

**Backend**:
- FastAPI (Python 3.9+)
- PostgreSQL (14+)
- SQLAlchemy ORM
- Alembic (migrations)
- JWT Auth

**Frontend**:
- FastAPI + Jinja2 templates (HTML, served on :8000)
- 198 Cypress E2E tests (`cypress/e2e/web/`)

**Testing**:
- Pytest
- 30+ test fájl

---

## 📁 Projekt Struktúra

```
practice_booking_system/
├── app/                          # Backend alkalmazás
│   ├── api/                      # API endpoints (47 fájl)
│   ├── services/                 # Service layer (23 fájl)
│   ├── models/                   # Database models (32 fájl) ⭐
│   ├── schemas/                  # Pydantic schemas (24 fájl)
│   └── main.py                   # FastAPI app
├── docs/                         # Dokumentáció
│   ├── CURRENT/                  # Aktuális dokumentumok (11 fájl) ⭐
│   │   ├── SESSION_RULES_ETALON.md
│   │   ├── DATABASE_STRUCTURE_AUDIT_COMPLETE.md ⭐
│   │   ├── API_ENDPOINT_AUDIT_COMPLETE.md ⭐
│   │   ├── TESTING_COVERAGE_AUDIT_COMPLETE.md ⭐
│   │   ├── SYSTEM_ARCHITECTURE.md ⭐
│   │   ├── API_ENDPOINT_SUMMARY.md ⭐
│   │   ├── SLOW_QUERY_MONITORING_GUIDE.md ⭐
│   │   └── ... (4 további)
│   ├── GUIDES/                   # Útmutatók (5 fájl)
│   └── ARCHIVED/                 # Archivált dokumentumok (80+ fájl)
├── alembic/                      # Database migrációk (69+ fájl)
├── scripts/                      # Utility scripts
├── tests/                        # Unit és integration tesztek
├── *.py                          # Dashboard és teszt fájlok
├── start_backend.sh              # Backend indító script
└── start_unified_dashboard.sh   # Dashboard indító script
```

---

## 🧪 Tesztelés

### Automated Tests

```bash
# Összes teszt futtatása
pytest

# Session Rules tesztek
pytest test_session_rules_comprehensive.py

# XP rendszer tesztek
pytest test_xp_system.py
```

### Manual Testing - Dashboard

```bash
# Unified workflow dashboard
./start_unified_dashboard.sh

# Session Rules Testing workflow választása
# Login: grandmaster@lfa.com / grandmaster2024
```

---

## 🧪 Testing

### Test Organization

All UI/E2E tests are centralized in `tests/e2e_frontend/`:

```
tests/e2e_frontend/              # 122 tests
├── user_lifecycle/              # 🔥 P0: Production-Critical (18 tests)
│   ├── registration/            # User registration flows
│   ├── onboarding/              # Onboarding workflows
│   └── auth/                    # Authentication
├── business_workflows/          # 🔥 P1: Business Logic (23 tests)
│   ├── instructor/              # Instructor workflows
│   └── admin/                   # Admin workflows
└── tournament_formats/          # P2: Tournament Tests (81 tests)
    ├── group_knockout/
    ├── head_to_head/
    └── individual_ranking/
```

### Running Tests

**Critical Tests (P0 + P1):**
```bash
# User lifecycle (registration, onboarding, auth)
pytest tests/e2e_frontend/user_lifecycle/ -v

# Business workflows (instructor, admin)
pytest tests/e2e_frontend/business_workflows/ -v

# Golden Path (smoke test)
pytest tests/e2e/golden_path/ -v
```

**All E2E Tests:**
```bash
pytest tests/e2e_frontend/ -v
```

**By Marker:**
```bash
pytest -m golden_path          # Production-critical smoke tests
pytest -m user_lifecycle       # User activation tests
pytest -m business_workflow    # Business logic tests
pytest -m tournament           # Tournament tests
```

**Documentation:**
- [MIGRATION_COMPLETE_REPORT.md](MIGRATION_COMPLETE_REPORT.md) - Test migration details
- [TEST_STRUCTURE_FINAL_PROPOSAL.md](TEST_STRUCTURE_FINAL_PROPOSAL.md) - Canonical test structure

---

## 🔒 CI/CD & Quality Gates

### ⚠️ GitHub Actions Status

**Current Status**: GitHub Actions is unavailable at account level (infrastructure limitation).

**Impact**:
- No automated CI/CD pipeline on pull requests
- No automated test runs on main branch pushes
- Manual quality enforcement required

### ✅ Alternative Quality Enforcement

**Critical E2E Suite Validation** (170 tests):

```bash
# Run before pushing to main/develop
./scripts/validate_critical_e2e.sh
```

**Requirements**:
- Backend running on http://localhost:8000
- All critical tests must pass (100% pass rate required)

**Critical Suite Contents**:
- 14 critical spec files (blocking failures)
- 170 tests covering core workflows:
  - Admin dashboard navigation & tournament management
  - Authentication & registration
  - Instructor workflows (dashboard, session check-in, tournament applications)
  - Player workflows (credits, onboarding, specialization)
  - Student workflows (credits, dashboard, enrollment, skill updates)

**Test Manifest**: [tests_cypress/test-manifest.json](tests_cypress/test-manifest.json)

### 🔄 Planned: External CI Integration

**Target**: Migrate to external CI provider when available:
- Option A: GitLab CI / CircleCI / Bitbucket Pipelines
- Option B: Self-hosted GitHub Actions runner (different account)
- Option C: GitHub Actions re-enablement (pending GitHub Support)

**Until then**: Manual validation via `validate_critical_e2e.sh` is mandatory for main/develop branch changes.

---

## 🔐 Teszt Accountok

**Instructor**:
- Email: `grandmaster@lfa.com`
- Password: `grandmaster2024`

**Student**:
- Email: `V4lv3rd3jr@f1stteam.hu`
- Password: `grandmaster2024`

**Részletek**: [docs/GUIDES/TESZT_FIOKOK_UPDATED.md](docs/GUIDES/TESZT_FIOKOK_UPDATED.md)

---

## 📞 További Információ

**API URL**: http://localhost:8000
**API Dokumentáció**: http://localhost:8000/docs (Swagger UI)
**Web UI**: http://localhost:8000

**Fő Dokumentumok**:
- [Session Rules Etalon](docs/CURRENT/SESSION_RULES_ETALON.md) - Hivatalos specifikáció + Mermaid diagramok
- [System Architecture](docs/CURRENT/SYSTEM_ARCHITECTURE.md) - Rendszer architektúra + layered design
- [P1 Tasks Complete Summary](P1_TASKS_COMPLETE_SUMMARY.md) - P0+P1 teljes összefoglaló 🎉 ÚJ
- [P0 Tasks Complete](P0_TASKS_COMPLETE.md) - 4 HIGH N+1 fix + 52 teszt 🎉 ÚJ
- [P1 MEDIUM N+1 Fixes](P1_MEDIUM_N+1_FIXES_COMPLETE.md) - 4 MEDIUM N+1 fix 🎉 ÚJ
- [Database Audit](docs/CURRENT/DATABASE_STRUCTURE_AUDIT_COMPLETE.md) - 32 model audit, 90.75% minőség
- [API Endpoint Audit](docs/CURRENT/API_ENDPOINT_AUDIT_COMPLETE.md) - N+1 query fixes
- [Testing Coverage Audit](docs/CURRENT/TESTING_COVERAGE_AUDIT_COMPLETE.md) - Test gaps analysis
- [Magyar Összefoglaló](docs/CURRENT/KESZ_SESSION_RULES_TELJES.md) - Gyors áttekintés

---

## 🧪 Testing Milestone — Sprint 52 (2026-03-09)

| Metric | Sprint 51 | Sprint 52 | CI Gate |
|--------|-----------|-----------|---------|
| Unit tests passed | 6 843 | **6 865** | — |
| Statement coverage | 88.5% | **88.7%** | ≥ 88% ✅ |
| Branch coverage (pure) | 82.7% | **83.5%** | ≥ 80% ✅ |
| Combined coverage | 87.0% | **88%** | ≥ 85% ✅ |
| Mutation kill rate | 80.2% | **80.2%** | ≥ 80% ✅ |
| web_routes layer | 65% combined | **71% combined** | ≥ 65% ✅ |

**web_routes per-file breakdown (Sprint 52)**:

| File | Coverage | BrPart |
|------|----------|--------|
| attendance.py | **100%** | 0 |
| dashboard.py | **100%** | 0 |
| instructor.py | **100%** | 0 |
| instructor_dashboard.py | **100%** | 0 (new) |
| quiz.py | **100%** | 0 |
| sessions.py | **100%** | 0 |
| admin.py | 77% | 0 (lines 92-233 excluded) |

**CI Gates (test-baseline-check.yml)**:
- Unit step: `--cov-fail-under=85`
- Full scope: `--fail-under=85`
- `check_coverage.py`: stmt ≥ 88%, branch ≥ 80%

**Test files**: see [TESTING.md](TESTING.md)

---

## 🗺️ User Flow Coverage (Sprint 53 — Integration Tests)

End-to-end flow validation via `tests/integration/web_flows/` (26 tests, real PostgreSQL, SAVEPOINT isolation).

| Lifecycle | Positive ✅ | Negative ❌ | DB validated |
|-----------|-------------|-------------|-------------|
| **Session booking** | Book → `Booking(CONFIRMED)` in DB | Book within 12h deadline → 303 error | ✅ row created |
| **Booking cancellation** | Cancel → row deleted from DB | Cancel within 12h deadline → 303 error; cancel without booking → 303 error | ✅ row deleted / preserved |
| **Attendance marking** | Mark present → `Attendance(present, pending)` in DB; mark absent → `check_in_time=None` | No booking → 303 `student_not_enrolled`; non-instructor → 303 `unauthorized` | ✅ row created / unchanged |
| **Attendance confirmation** | Student confirms → `ConfirmationStatus.confirmed`; student disputes → `ConfirmationStatus.disputed` + reason | Instructor confirms → 303 `unauthorized`; no attendance record → 303 `no_attendance` | ✅ status updated / unchanged |
| **Hybrid quiz unlock** | Unlock → `session.quiz_unlocked=True` in DB | Non-instructor → blocked; non-hybrid session → error; unstarted session → error | ✅ flag set / unchanged |
| **Quiz submission** | Submit → `QuizAttempt.completed_at` set, score/passed persisted | Already completed → 400; bad attempt → 404; no booking for session → 403 + attempt unchanged | ✅ completed_at set / unchanged |

**Timing**: all `call` durations ≤ 0.18s (well under 2s threshold). Setup overhead ~0.1–0.6s (TestClient app startup).

**Idempotency guards** (tested in `test_concurrency.py`, 5 tests):

| Scenario | 1st request | 2nd request | DB invariant |
|----------|------------|------------|-------------|
| Double booking | 303 `success=booked` | 303 `already_booked` | exactly 1 `Booking` row |
| Double cancel | 303 `success=cancelled` | 303 `booking_not_found` | row stays deleted |
| Double quiz submit | 200 HTML | 400 already-completed | `completed_at` from 1st, unchanged |
| Double attendance mark | 303 `attendance_marked` (INSERT) | 303 `attendance_marked` (UPDATE) | exactly 1 `Attendance` row |
| Status change (present → absent) | 303 `attendance_marked` | 303 `attendance_marked` | 1 row, `status=absent` |

**Rate limiting**: disabled in pytest (`ENABLE_RATE_LIMITING = not is_testing()`). Production enforcement via `slowapi` middleware, unrelated to these idempotency guards.

---

## 🔒 Database Safety Guarantees (Sprint 53+)

Which invariants are enforced at DB level (survive concurrent writes without application cooperation) versus application level (single-process guard only).

### Invariant Layer Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Layer 1 — PostgreSQL (always enforced, even without application)    │
│                                                                      │
│  UNIQUE indexes / partial unique indexes / FK constraints            │
│  ──────────────────────────────────────────────────────              │
│  uq_active_booking       (user_id, session_id) WHERE not CANCELLED   │
│  uq_attendance_…_null    (user_id, session_id) WHERE booking IS NULL │
│  uq_booking_attendance   UNIQUE (booking_id)  [squashed schema]      │
│  idempotency_key UNIQUE  on credit_transactions                      │
│                                                                      │
│  ▶ Race safety: IntegrityError on concurrent duplicate INSERT        │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ application reads after acquiring lock
┌─────────────────────────────▼────────────────────────────────────────┐
│  Layer 2 — Application (FastAPI routes + SELECT FOR UPDATE)          │
│                                                                      │
│  Pessimistic locking + guard checks                                  │
│  ──────────────────────────────────                                  │
│  quiz submit:   .with_for_update().first() → completed_at check      │
│  booking:       explicit already_booked check + DB constraint backup  │
│  cancel:        booking lookup → None → 404 (no-op on double-cancel) │
│                                                                      │
│  ▶ Race safety: row lock prevents double-completion; friendly 400/303│
└─────────────────────────────┬────────────────────────────────────────┘
                              │ validated by
┌─────────────────────────────▼────────────────────────────────────────┐
│  Layer 3 — Tests (validates both layers)                             │
│                                                                      │
│  Unit (6 865):  MagicMock DB → business logic, SELECT FOR UPDATE     │
│                 _make_db helper loops with_for_update mock chain      │
│  Integration (26 web_flows):  real PostgreSQL SAVEPOINT              │
│                 idempotency tests validate row-count invariants       │
│  Migration (7): real schema DDL, downgrade/upgrade round-trip        │
│                 verifies index definition (UNIQUE + partial WHERE)    │
│                                                                      │
│  ▶ Guarantees: each invariant tested at both app and DB level        │
└──────────────────────────────────────────────────────────────────────┘
```

### DB-Enforced Invariants (hard constraints)

| Table | Constraint | Invariant |
|-------|-----------|-----------|
| `bookings` | `uq_active_booking` — partial unique `(user_id, session_id) WHERE status <> 'CANCELLED'` | One active booking per student per session. Concurrent double-book raises `IntegrityError`. |
| `attendance` | `uq_attendance_user_session_no_booking` — partial unique `(user_id, session_id) WHERE booking_id IS NULL` (**Sprint 53+**) | One attendance per student per session for tournament sessions (NULL booking_id). Concurrent duplicate → `IntegrityError`. |
| `quiz_user_answers` | `unique_user_question UNIQUE (user_id, question_id)` | One answer per student per question. |
| `semester_enrollments` | `UniqueConstraint(user_id, semester_id, user_license_id)` | One enrollment per student per semester per license. |
| `credit_transactions` | `idempotency_key UNIQUE` | Payment transaction deduplication at DB level. |

### Application-Enforced Invariants (serialized)

| Endpoint | Guard | Race risk + status |
|----------|-------|-------------------|
| `POST /quizzes/{id}/submit` | `completed_at IS NOT NULL` → 400; `SELECT FOR UPDATE` on attempt fetch (**Sprint 53+**) | **Fixed**: concurrent submits serialized by row lock; 2nd sees `completed_at` set → 400. |
| `POST /sessions/book/{id}` | `already_booked` check before INSERT | Defence-in-depth with DB `uq_active_booking`. Application guard handles friendly redirect; DB constraint is the final safety net. |
| `POST /sessions/cancel/{id}` | Booking lookup → None → 404 | Second cancel is a no-op (row already deleted). No race risk. |

### Remaining Gaps

| Scenario | Status |
|----------|--------|
| Concurrent `confirm_attendance` by student | Application-level `confirmation_status` check only — no `SELECT FOR UPDATE`. Low production risk (confirmation is student-initiated, one per session). |
| Multiple `QuizAttempt` rows per `(user_id, quiz_id)` | Intentional design (retake allowed). Submit guard prevents double-completion of same attempt. |

---

## 🧱 Test Strategy Overview

Three-layer test architecture covering correctness at each system boundary:

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1 — Unit Tests (tests/unit/)                             │
│                                                                 │
│  Tool: asyncio.run() + MagicMock DB                            │
│  Scope: service logic, business rules, individual functions     │
│  DB: fully mocked (no network)                                  │
│  Count: 6 865 tests  |  Speed: ~12s  |  Coverage: 88.7% stmt  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ services / DB models
┌──────────────────────────▼──────────────────────────────────────┐
│  Layer 2 — Integration: web_flows (tests/integration/web_flows/)│
│                                                                 │
│  Tool: FastAPI TestClient (real HTTP dispatch)                  │
│  Scope: route → service → DB → response chain                  │
│  DB: real PostgreSQL, SAVEPOINT rollback per test               │
│  Count: 26 tests  |  Speed: ~4s  |  Validates: DB state ✅     │
│                                                                 │
│  Sub-flows tested:                                              │
│    booking/cancel · attendance mark/confirm · quiz submit       │
│    hybrid quiz-unlock · idempotency guards                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ full stack
┌──────────────────────────▼──────────────────────────────────────┐
│  Layer 3 — E2E (cypress/ + tests/e2e/)                         │
│                                                                 │
│  Tool: Cypress JS + Playwright Python                           │
│  Scope: browser ↔ app ↔ DB (real user journeys)               │
│  DB: live dev server with seeded E2E data                       │
│  Count: 7/7 Cypress green  |  Speed: ~60s                      │
└─────────────────────────────────────────────────────────────────┘
```

**When each layer catches bugs**:
- Unit: wrong business logic, edge-case calculation errors, service return values
- Integration: route wiring bugs, missing DB commits, wrong redirect URLs, CSRF/auth misconfigurations
- E2E: UI rendering, cookie/session flow, cross-service interactions visible to users

---

## 🚀 Deployment Safety Checklist (Sprint 53+)

Steps to follow before every production deployment that includes DB migrations.

### Pre-deployment

| # | Step | Command / Verification |
|---|------|------------------------|
| 1 | Run full test suite | `pytest tests/unit/ tests/integration/ -q --tb=short` → 0 failures |
| 2 | Verify migration chain | `alembic history --verbose` → head = `2026_03_09_1500` |
| 3 | Dry-run upgrade (staging) | `alembic upgrade head --sql` → review DDL, no unexpected DROP |
| 4 | Run migration rollback tests | `pytest tests/integration/test_migration_rollback.py -v` → 14 passed |
| 5 | Check CI gates | stmt ≥ 88%, branch ≥ 80%, combined ≥ 85% |
| 6 | Snapshot attendance constraints | `SELECT conname FROM pg_constraint WHERE conrelid='attendance'::regclass` → 3 rows: `attendance_pkey`, `attendance_booking_id_fkey`, `uq_booking_attendance` + index `uq_attendance_user_session_no_booking` |

### Migration execution

| # | Step | Command |
|---|------|---------|
| 1 | Backup DB | `pg_dump lfa_intern_system > backup_$(date +%Y%m%d_%H%M%S).sql` |
| 2 | Apply migration | `alembic upgrade head` |
| 3 | Confirm revision | `alembic current` → `2026_03_09_1500 (head)` |
| 4 | Spot-check constraints | repeat snapshot query above |

### Rollback plan

If a migration causes issues:

```bash
# Roll back to before uq_booking_attendance (keeps partial index)
alembic downgrade 2026_03_09_1400

# Roll back to before both attendance constraints
alembic downgrade 2026_03_02_2115

# Verify revision after rollback
alembic current
```

**Safety property**: both `downgrade` paths are tested by `test_migration_rollback.py` and confirmed
to leave `attendance_pkey` and `attendance_booking_id_fkey` intact.

### CI gates summary

| Gate | Threshold | Current |
|------|-----------|---------|
| Statement coverage | ≥ 88% | 88.7% |
| Branch coverage | ≥ 80% | 83.5% |
| Combined coverage | ≥ 85% | 88.0% |
| Mutation kill rate | ≥ 78% (regression) | 80.4% |
| Unit tests | 0 failures | 6 865 passed, 1 xfailed |
| Integration tests | 0 failures | 51 passed (26 web_flows + 14 rollback + 11 tournament) |

---

## 📋 Release Candidate Report — Sprint 53+ (2026-03-09)

### Summary

| Dimension | Status | Detail |
|-----------|--------|--------|
| Unit tests | ✅ Green | 6 865 passed, 1 xfailed (expected) |
| Integration tests | ✅ Green | 51 passed (26 web_flows + 14 migration rollback + 11 tournament) |
| E2E tests | ✅ Green | 7/7 Cypress (CI run `22803983615`) |
| Statement coverage | ✅ 88.7% | Gate: ≥ 88% |
| Branch coverage | ✅ 83.5% | Gate: ≥ 80% |
| Mutation kill rate | ✅ 80.4% | Milestone ≥ 80% achieved Sprint 48 |
| DB migrations | ✅ Applied | Head: `2026_03_09_1500`, rollback tests: 14/14 |
| API smoke tests | ✅ 579 endpoints | 1 737 tests passing (CI) |

### DB Safety State

Two complementary attendance uniqueness constraints in place as of `2026_03_09_1500`:

```
booking_id IS NOT NULL  →  uq_booking_attendance UNIQUE(booking_id)
booking_id IS NULL      →  uq_attendance_user_session_no_booking
                              UNIQUE(user_id, session_id) WHERE booking_id IS NULL
```

Quiz double-completion race is serialized by `SELECT FOR UPDATE` in `quiz.py:submit_quiz` — no DB
unique constraint possible (multi-attempt design; no `session_id` column on `quiz_attempts`).

### Known Gaps

| Gap | Severity | Mitigation |
|-----|----------|------------|
| `admin_enrollments_page` (lines 92–233) excluded from coverage | Low | Function has no pure-unit test path due to `db.refresh` on lazy relationships; covered by E2E |
| Quiz attempt uniqueness has no DB constraint | Low | `SELECT FOR UPDATE` + `completed_at` guard; integration test confirms idempotency |
| Rate limiting not tested at unit/integration level | Info | `ENABLE_RATE_LIMITING = not is_testing()` — enforced by `slowapi` in production only |
| web_routes branch coverage 71% (below 80% project gate) | Info | Layer-level gap; project-level gate (83.5%) is met |

### Go / No-Go

**GO** — all blocking gates pass, DB constraints are consistent and rollback-tested, no open
regressions. The two new migrations (`1400` + `1500`) close the attendance uniqueness matrix.

---

**Verzió**: 2.3 (2025-12-17) → Sprint 53 integration flows (2026-03-09)
**Státusz**: Production Ready
**Database Quality**: 90.75% (A-) ⭐
**API Performance**: 8/12 N+1 fixed (98.7% query reduction) ✅
**Test Coverage**: 88.7% stmt / 83.5% branch (6 865 unit + 26 integration web_flows + 14 migration rollback) ✅
**Response Time**: ~7,170ms → ~90ms (98.7% faster) ⚡
