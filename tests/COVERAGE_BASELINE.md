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

**Verified: 3-layer production guard exists (NOT a hidden bug)**

| Layer | Guard | Location |
|-------|-------|----------|
| **Layer 1 — App check** | `if user.credit_balance < cost: return error` | `tournaments.py:170` |
| **Layer 2 — Atomic SQL** | `UPDATE users SET credit_balance = credit_balance - :cost WHERE id = :id AND credit_balance >= :cost` — rowcount=0 if race lost | `tournaments.py:203` |
| **Layer 3 — DB constraint** | `CONSTRAINT chk_credit_balance_non_negative CHECK ((credit_balance >= 0))` | `alembic/versions/2026_02_21_..._squashed_baseline_schema.py:2699` |

Layer 2 is the key: the conditional atomic UPDATE (`WHERE credit_balance >= cost`) means only one of two concurrent requests can win — the loser gets `rowcount=0` and is immediately rolled back with an "Insufficient credits (concurrent update)" error. Layer 3 is the final backstop regardless of application logic.

**Why not integration-tested:**
Integration tests run in a single OS process. Simulating concurrent HTTP requests within a test process tests the test harness, not the application. The correct verification is: (a) the atomic UPDATE pattern exists in code (verified above), and (b) the DB CHECK constraint is in the schema migration (verified above).

**Existing partial coverage:**
`test_concurrency.py` (7 tests) covers double-booking the same session (returns 409) and insufficient-credit rejection flow.

**Status:** ✅ Adequately protected at production level. No additional test needed.
**Verified by:** Engineering team, 2026-04-13

---

### RISK-02 (resolved): License revoke did not cascade to SemesterEnrollment
~~**Risk:** `admin_revoke_license()` and `bulk_check_expirations()` set `UserLicense.is_active=False` but left `SemesterEnrollment.is_active=True`, creating orphaned active enrollments for inactive licenses.~~

**Fixed 2026-04-13.** Both paths now cascade:
- `admin.py::admin_revoke_license` — explicit bulk UPDATE after `license.is_active = False`
- `license_renewal_service.py::bulk_check_expirations` — tracks `expired_license_ids`, batch UPDATE after loop

**E2E test:** `test_critical_e2e.py::test_license_revoke_cascades_to_enrollments` (LRC) — admin revoke → enrollment.is_active=False asserted.

---

### DESIGN-01 (known debt): No session cancellation endpoint
**Gap:** `DELETE /sessions/{id}` blocks deletion when bookings exist — correct guard, but there is no `POST /sessions/{id}/cancel` endpoint. Contrast: tournaments have a full `/cancel` endpoint with refund logic.

**Business impact:** Admin has no clean way to mark a session cancelled while preserving booking records. Workaround: manually update session status via DB or admin panel if available.

**Financial impact:** LOW — on-site/virtual session bookings do not deduct credits, so no refund logic is needed. The absence of a cancel endpoint is a UX/data-integrity issue, not a financial one.

**When to address:** When a future feature requires session-level cancellation with student notification or attendance record cleanup.

---

## Section 4: BASELINE RULES

From 2026-04-13 forward:

1. **Every new route must have at least 1 E2E test** before merge to main.
2. **CI must remain 8/8 green** at all times on `main`.
3. **New business logic must have a DB assertion** (not just HTTP 200).
4. **No new "unit-only" coverage** for flows that touch credit balance, enrollment status, or user state.
5. **This file is updated** when a new feature is added that changes the coverage picture.
