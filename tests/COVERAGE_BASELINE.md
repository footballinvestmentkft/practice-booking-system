# E2E Coverage Baseline — Practice Booking System
**Frozen: 2026-04-13 | Branch: fix/e2e-ops-seed-1024**

This document is the reference baseline for end-to-end test coverage.
Every future feature **must** add a corresponding E2E test and keep CI green.

---

## CI Status (frozen state)

| Workflow | Result |
|----------|--------|
| Test Baseline Check (Unit + Web Flows + E2E Matrix) | ✅ |
| E2E Lifecycle Visibility | ✅ |
| Dark Mode CSS Validation | ✅ |
| E2E Multi-Campus Venue + Instructor | ✅ |
| E2E Invitation Code Seed Validation | ✅ |
| E2E Virtual Tournament | ✅ |
| E2E Tournament Session Types (virtual + hybrid) | ✅ |
| Cypress Web E2E Tests | ✅ |

---

## Test Suite Size (baseline)

| Suite | Count |
|-------|-------|
| `tests/integration/web_flows/` | **553** (41 files) |
| `tests/integration/api_smoke/` | **1,741** |
| Cypress (`cypress/e2e/`) | **13** (4 files) |
| **Total** | **2,307** |

---

## Section 1: COVERED — Proven E2E chains

Each entry has: at least 1 HTTP assertion + DB assertion + UI assertion.

### Quiz domain
| Test | Chain |
|------|-------|
| QRB — Quiz Retry Best Score | GET /take → fail submit → GET /take (new attempt) → pass submit → QuizAttempt.passed=True |
| QEG — Quiz Enrollment Gate | POST submit without Booking → 403; add Booking → 200 |
| QAL — Quiz Attempt Limit | fail × max_attempts → GET /sessions/{id} → "No More Attempts" |
| QIS — Quiz Interrupted Resume | GET /take → GET /take again → same attempt_id, count=1 → complete |
| QPG — Quiz State Progression | /sessions/{id}: no attempt → "Start Exam"; fail → "Retry"; pass → "PASSED" |

### Tournament domain
| Test | Chain |
|------|-------|
| SFJ — Student Full Journey | GET /events/tournaments → POST /enroll → enrolled badge visible |
| CDE — Credit Deduction | POST /enroll (paid) → credit_balance−200 → CreditTransaction → history page |
| TCR — Tournament Credit Refund | POST /enroll → POST /unenroll → 50% refund → CreditTransaction(REFUND) |
| TOUR-S-01..05 (Cypress) | UI: balance=1000 → enroll → 900 → unenroll → 950 |

### Admin domain
| Test | Chain |
|------|-------|
| ISC — Instructor Slot Conflict | POST instructor slot → POST same instructor again → 409 → DB count=1 |
| APR — Admin Password Reset | POST /reset-password → verify_password(new)=True → old login 200 → new login 303 |
| test_admin_smoke (143 tests) | Page loads, CRUD operations, capacity, ban, license revoke |

### Credit / Registration domain
| Test | Chain |
|------|-------|
| ICR — Invitation Code Registration | Create InvitationCode → POST /register → User.credit_balance=500, code.is_used=True |

### Session domain
| Test | Chain |
|------|-------|
| test_booking_cancellation (6 tests) | Cancel within deadline → Booking deleted; after deadline → redirect error |
| test_session_virtual_bugs (10 tests) | Virtual/hybrid session visibility, quiz gate, is_enrolled |
| test_tournament_session_types (10 tests) | session_type_config lifecycle, meeting_link, hybrid enrollment |

### Skill / Profile domain
| Test | Chain |
|------|-------|
| SDE — Skill Delta E2E | TournamentFactory → TournamentParticipation.skill_rating_delta → GET /skills |

### Browse / Filter domain
| Test | Chain |
|------|-------|
| BF-CY-01..04 (Cypress) | ?status=open → 2 cards; ?delivery=virtual → 1 card; combined → 1 card |

### Other domains
| Domain | Coverage |
|--------|----------|
| Multi-campus venue | 8 MCV tests: venue in schedule, cross-campus, phase split |
| Dark mode | 17 HTTP + 11 static: CSS token audit, no hardcoded colors |
| Invitation code seed | CI seed validation: 52 used / 5 unused, 44k credits |
| Virtual tournament | 5 VT tests: session_type=virtual, XP, gate |
| Lifecycle/state machine | 40 tournament_lifecycle + 23 reward_matrix |

---

## Section 2: NOT IMPLEMENTED — No test needed

These flows have no test because the feature does not exist in the application.

| Flow | Reason |
|------|--------|
| Student self-service password reset (forgot password) | No `/forgot-password` endpoint. Only admin can reset passwords via `POST /admin/users/{id}/reset-password`. |
| Email verification / token-based auth | No SMTP integration. No email is sent anywhere in the codebase. |
| Session booking credit cost refund | `POST /sessions/cancel/{id}` deletes the Booking row with no credit deduction (sessions are free to book). No credit to refund. |
| Concurrent credit prevention (negative balance from race) | See Section 3. |

---

## Section 3: KNOWN ACCEPTED RISKS

Risks that are understood, deliberately accepted, and not tested at integration level.

### RISK-01: Concurrent credit deduction race condition
**Risk:** Two requests could theoretically both read `credit_balance=800` and both deduct 200, resulting in `credit_balance=400` (double spend) instead of one succeeding and one failing.

**Why not integration-tested:**
Integration tests run in a single OS process. Simulating concurrent HTTP requests within a test process tests the test harness, not the application. A meaningful test would require a proper load testing tool (e.g., Locust, k6) against a real server with a real PostgreSQL instance.

**Actual production guard:**
PostgreSQL's row-level locking (`SELECT ... FOR UPDATE`) or serializable transactions prevent concurrent double-spend in production. The enrollment route uses `db.commit()` which is atomic at the DB level.

**Existing partial coverage:**
`test_concurrency.py` (7 tests) covers double-booking the same session (returns 409) and insufficient-credit rejection. The credit balance race specifically is not covered.

**Accepted by:** Engineering team, 2026-04-13
**Mitigation:** Monitor `credit_balance < 0` in production with a DB constraint or alert.

---

## Section 4: BASELINE RULES

From 2026-04-13 forward:

1. **Every new route must have at least 1 E2E test** before merge to main.
2. **CI must remain 8/8 green** at all times on `main`.
3. **New business logic must have a DB assertion** (not just HTTP 200).
4. **No new "unit-only" coverage** for flows that touch credit balance, enrollment status, or user state.
5. **This file is updated** when a new feature is added that changes the coverage picture.
