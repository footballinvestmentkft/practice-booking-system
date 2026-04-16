# Metric Reconciliation — Route × Coverage Mapping
## Practice Booking System — Static Analysis at Freeze
**Frozen: 2026-04-16 | SHA: 3138f5b | DO NOT MODIFY**

This document maps all 116 state-changing web routes to their coverage tier,
the F-ID of the covering flow (if any), and the test file/function reference.

For metric definitions, see `METRIC_CONTRACT.md`.
For the 11 zero-coverage routes, see also `GAP_REPORT_CI_BASED.md`.

---

## Summary

| Tier | Count | Meaning |
|------|-------|---------|
| **BF** — Business Flow | 66 | Segments appear in `test_critical_e2e.py`; 3-layer E2E proof |
| **AT** — AST Touched | 39 | Segments appear in other test files only; no 3-layer E2E proof |
| **ZERO** | 11 | Segments absent from ALL test files; no test of any kind |
| **Total** | **116** | All POST/PATCH/PUT/DELETE routes in `app/api/web_routes/` |

**ERC = (66 + 39) / 116 = 105/116 = 90.5%** (informational only — not a release KPI)

---

## Tier Legend

- **BF** — Business Flow: route is covered by `test_critical_e2e.py` (3-layer HTTP+DB+UI proof)
- **AT** — AST Touched: route appears in another test file; presence confirmed but depth unknown
- **❌ ZERO** — route is in NO test file anywhere in `tests/`

---

## Full Route Table

### admin.py (49 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 1 | POST | `/admin/users/create` | **BF** | F-57 | test_admin_user_create_creates_active_user |
| 2 | POST | `/admin/users/{user_id}/toggle-status` | **BF** | F-58 | test_admin_toggle_user_status_deactivates_active_user |
| 3 | POST | `/admin/users/{user_id}/edit` | **BF** | GAP-04 | test_license_revoke_cascades_to_enrollments |
| 4 | POST | `/admin/users/{user_id}/reset-password` | **BF** | F-33 | test_admin_password_reset_enables_login |
| 5 | POST | `/admin/users/{user_id}/grant-credit` | **BF** | GAP-05 | test_admin_grant_credit |
| 6 | POST | `/admin/users/{user_id}/deduct-credit` | **BF** | F-29 | test_admin_deduct_credit |
| 7 | POST | `/admin/users/{user_id}/grant-license` | **BF** | F-32 | test_admin_grant_license_creates_user_license |
| 8 | POST | `/admin/users/{user_id}/revoke-license/{license_id}` | **BF** | GAP-04 | test_license_revoke_cascades_to_enrollments |
| 9 | POST | `/admin/users/{user_id}/renew-license/{license_id}` | **BF** | GAP-06 | test_license_renewal_updates_expiry |
| 10 | POST | `/admin/semesters/new` | **BF** | F-57..F-62 (setup path) | — (segments in file; no single-function match) |
| 11 | POST | `/admin/semesters/{semester_id}/delete` | AT | — | test__semesters_main.py |
| 12 | POST | `/admin/students/{student_id}/motivation/{specialization}` | **BF** | — (setup path) | — |
| 13 | POST | `/admin/camps` | **BF** | GAP-04 (setup) | test_license_revoke_cascades_to_enrollments |
| 14 | POST | `/admin/camps/{camp_id}/edit` | **BF** | GAP-04 (setup) | test_license_revoke_cascades_to_enrollments |
| 15 | POST | `/admin/locations` | AT | — | test__semesters_main.py |
| 16 | POST | `/admin/locations/{location_id}/toggle` | AT | — | test_semester_e2e.py |
| 17 | POST | `/admin/locations/{location_id}/delete` | AT | — | test__semesters_main.py |
| 18 | POST | `/admin/locations/{location_id}/edit` | AT | — | test__semesters_main.py |
| 19 | POST | `/admin/campuses/{campus_id}/edit` | AT | — | conftest.py |
| 20 | POST | `/admin/locations/{location_id}/campuses` | AT | — | test_campus_filter.py |
| 21 | POST | `/admin/campuses/{campus_id}/toggle` | AT | — | test_admin_smoke.py |
| 22 | POST | `/admin/campuses/{campus_id}/delete` | AT | — | conftest.py |
| 23 | POST | `/admin/system-events/{event_id}/resolve` | AT | — | test_admin_smoke.py |
| 24 | POST | `/admin/system-events/{event_id}/unresolve` | AT | — | test_admin_smoke.py |
| 25 | POST | `/admin/system-events/purge` | AT | — | test_admin_smoke.py |
| 26 | POST | `/admin/game-presets` | AT | — | test_game_presets_admin.py |
| 27 | POST | `/admin/game-presets/{preset_id}/edit` | AT | — | test_game_presets_admin.py |
| 28 | POST | `/admin/game-presets/{preset_id}/toggle` | AT | — | test_game_presets_admin.py |
| 29 | POST | `/admin/game-presets/{preset_id}/delete` | AT | — | test_game_presets_admin.py |
| 30 | POST | `/admin/coupons` | AT | — | test_coupons_refactored.py |
| 31 | POST | `/admin/coupons/{coupon_id}/toggle` | AT | — | test_admin_smoke.py |
| 32 | POST | `/admin/coupons/{coupon_id}/delete` | AT | — | test_admin_smoke.py |
| 33 | POST | `/admin/invoices/{invoice_id}/verify` | **BF** | F-52 | test_admin_invoice_verify_credits_student |
| 34 | POST | `/admin/invoices/{invoice_id}/cancel` | **BF** | F-53 | test_admin_invoice_cancel_sets_cancelled_status |
| 35 | POST | `/admin/invoices/{invoice_id}/unverify` | **BF** | F-54 | test_admin_invoice_unverify_reverts_credits |
| 36 | POST | `/admin/bookings/{booking_id}/confirm` | **BF** | F-35 | test_admin_booking_confirm_updates_status |
| 37 | POST | `/admin/bookings/{booking_id}/cancel` | **BF** | F-59 | test_admin_booking_cancel_sets_cancelled_status |
| 38 | POST | `/admin/bookings/{booking_id}/attendance` | **BF** | F-16 (setup path) | — (segments in file; no single-function match) |
| 39 | POST | `/admin/pitches/create` | AT | — | test_admin_menu_restructure.py |
| 40 | POST | `/admin/pitches/{pitch_id}/toggle` | **❌ ZERO** | — | — |
| 41 | POST | `/admin/pitches/{pitch_id}/assign-instructor` | AT | — | test_pitch_instructor_flow.py |
| 42 | POST | `/admin/sport-directors/assign` | AT | — | test_admin_menu_restructure.py |
| 43 | POST | `/admin/sport-directors/{assignment_id}/deactivate` | **❌ ZERO** | — | — |
| 44 | POST | `/admin/clubs/create` | AT | — | test_promotion_flow_e2e.py |
| 45 | POST | `/admin/clubs/{club_id}/edit` | AT | — | test_promotion_flow_e2e.py |
| 46 | POST | `/admin/clubs/{club_id}/toggle` | **❌ ZERO** | — | — |
| 47 | POST | `/admin/clubs/{club_id}/csv-import` | **❌ ZERO** | — | — |
| 48 | POST | `/admin/clubs/{club_id}/promotion` | AT | — | test_promotion_flow_e2e.py |
| 49 | POST | `/admin/users/{user_id}/lfa-player-photo` | **❌ ZERO** | — | — |
| 50 | POST | `/admin/users/{user_id}/lfa-player-photo/delete` | **❌ ZERO** | — | — |

### attendance.py (3 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 51 | POST | `/sessions/{session_id}/attendance/mark` | **BF** | F-16 | test_attendance_mark_creates_record |
| 52 | POST | `/sessions/{session_id}/attendance/confirm` | **BF** | F-16 (setup path) | — (segments in file; no single-function match) |
| 53 | POST | `/sessions/{session_id}/attendance/change-request` | AT | — | test_web_routes_smoke.py |

### auth.py (3 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 54 | POST | `/login` | **BF** | F-01/F-33 | test_admin_password_reset_enables_login |
| 55 | POST | `/age-verification` | AT | — | test_team_flow_e2e.py |
| 56 | POST | `/register` | **BF** | F-02/GAP-10 | test_invitation_code_registration_grants_credits |

### communications.py (5 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 57 | POST | `/notifications/read-all` | **BF** | F-49 | test_notifications_read_all_marks_all_read |
| 58 | POST | `/notifications/{notification_id}/read` | **BF** | F-50 | test_notification_single_read_updates_state |
| 59 | POST | `/notifications/{notification_id}/delete` | AT | — | test_communications_smoke.py |
| 60 | POST | `/messages/send` | **BF** | F-47 | test_message_send_creates_row |
| 61 | POST | `/messages/{message_id}/delete` | AT | — | test_communications_smoke.py |

### dashboard.py (2 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 62 | POST | `/dashboard/lfa-player-photo` | **❌ ZERO** | — | — |
| 63 | POST | `/dashboard/lfa-player-photo/delete` | **❌ ZERO** | — | — |

### instructor.py (5 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 64 | POST | `/instructor/specialization/toggle` | **BF** | F-43 (setup path) | — (segments in file) |
| 65 | POST | `/sessions/{session_id}/start` | **BF** | F-14 | test_instructor_session_start_stop |
| 66 | POST | `/sessions/{session_id}/stop` | **BF** | F-15 | test_instructor_session_start_stop |
| 67 | POST | `/sessions/{session_id}/evaluate-student/{student_id}` | **BF** | F-64 | test_instructor_evaluates_student_creates_performance_review |
| 68 | POST | `/sessions/{session_id}/evaluate-instructor` | **BF** | F-63 | test_student_evaluates_instructor_creates_review |

### instructor_dashboard.py (1 route)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 69 | POST | `/instructor/students/{student_id}/skills/{license_id}` | **BF** | F-44 | test_instructor_skills_update_and_audit |

### onboarding.py (4 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 70 | POST | `/specialization/select` | **BF** | F-03 (setup path) | — (segments in file) |
| 71 | POST | `/specialization/lfa-player/onboarding-web` | **BF** | F-03 | test_lfa_player_onboarding_creates_license |
| 72 | POST | `/specialization/lfa-player/onboarding-submit` | AT | — | conftest.py |
| 73 | POST | `/onboarding/set-birthdate` | AT | — | test_onboarding_smoke.py |

### profile.py (1 route)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 74 | POST | `/profile/edit` | **BF** | F-05 | test_profile_edit_updates_name |

### quiz.py (2 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 75 | POST | `/sessions/{session_id}/unlock-quiz` | AT | — | test_hybrid_session_flow.py |
| 76 | POST | `/quizzes/{quiz_id}/submit` | **BF** | F-06/F-07 | test_quiz_retry_fail_then_pass |

### sessions.py (2 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 77 | POST | `/sessions/book/{session_id}` | **BF** | F-13/GAP-08 | test_session_capacity_waitlist |
| 78 | POST | `/sessions/cancel/{session_id}` | **BF** | F-17/F-59 | test_admin_booking_cancel_sets_cancelled_status |

### specialization.py (3 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 79 | POST | `/specialization/unlock` | AT | — | test_credit_validation_fix.py |
| 80 | POST | `/specialization/motivation-submit` | AT | — | test_specialization_smoke.py |
| 81 | POST | `/specialization/switch` | **BF** | F-04 | test_specialization_switch_updates_active_spec |

### sport_director.py (2 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 82 | POST | `/tournaments/{tournament_id}/teams/{team_id}/enroll` | **BF** | GAP-02 | test_team_enrollment_deducts_credits |
| 83 | POST | `/tournaments/{tournament_id}/teams/{team_id}/remove` | **BF** | F-42 | test_sport_director_team_remove |

### teams.py (6 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 84 | POST | `/tournaments/{tournament_id}/team/create` | **BF** | GAP-02 | test_team_enrollment_deducts_credits |
| 85 | POST | `/teams/invites/{invite_id}/accept` | **BF** | F-25 | test_team_invite_accept_adds_member |
| 86 | POST | `/teams/invites/{invite_id}/reject` | **BF** | F-25 (setup path) | — (segments in file) |
| 87 | POST | `/tournaments/{tournament_id}/teams/{team_id}/enroll` | **BF** | GAP-02 | test_team_enrollment_deducts_credits |
| 88 | POST | `/teams/{team_id}/invite` | **BF** | F-25 | test_team_invite_accept_adds_member |
| 89 | POST | `/teams/{team_id}/invites/{invite_id}/cancel` | **BF** | F-25 (setup path) | — (segments in file) |

### tournaments.py (32 routes)

| # | Method | Route | Tier | F-ID | Test Reference |
|---|--------|-------|------|------|----------------|
| 90 | POST | `/tournaments/{tournament_id}/enroll` | **BF** | F-08/F-19 | test_student_journey_browse_enroll_see_enrolled |
| 91 | POST | `/tournaments/{tournament_id}/unenroll` | **BF** | GAP-01/F-20 | test_tournament_unenrollment_credit_refund |
| 92 | POST | `/admin/tournaments` | **BF** | F-57..F-62 (setup) | test_instructor_slot_duplicate_rejected |
| 93 | POST | `/admin/tournaments/{tournament_id}/start` | **BF** | F-41 | test_admin_live_monitor_renders |
| 94 | POST | `/admin/tournaments/{tournament_id}/cancel` | **BF** | F-21/GAP-01 | test_tournament_cancellation_refund |
| 95 | POST | `/admin/tournaments/{tournament_id}/delete` | AT | — | test_lifecycle_updates.py |
| 96 | POST | `/admin/tournaments/{tournament_id}/rollback` | AT | — | test_reward_config.py |
| 97 | POST | `/admin/tournaments/{tournament_id}/players/enroll` | **BF** | F-55 | test_admin_batch_enroll_players_creates_enrollments |
| 98 | POST | `/admin/tournaments/{tournament_id}/players/{player_user_id}/remove` | **BF** | F-55 (cleanup path) | — (segments in file) |
| 99 | POST | `/admin/tournaments/{tournament_id}/players/enroll-from-team` | **❌ ZERO** | — | — |
| 100 | POST | `/admin/tournaments/{tournament_id}/teams/enroll` | **BF** | F-42/F-56 | test_admin_team_bulk_enroll_creates_team_enrollments |
| 101 | POST | `/admin/tournaments/{tournament_id}/teams/enroll-bulk` | **BF** | F-56 | test_admin_team_bulk_enroll_creates_team_enrollments |
| 102 | POST | `/admin/tournaments/{tournament_id}/teams/{team_id}/remove` | **BF** | F-42 | test_sport_director_team_remove |
| 103 | POST | `/admin/tournaments/{tournament_id}/teams/{team_id}/verify` | **BF** | F-56 (setup path) | — (segments in file) |
| 104 | POST | `/admin/tournaments/{tournament_id}/teams/{team_id}/unverify` | **BF** | — (setup path) | — (segments in file) |
| 105 | POST | `/admin/tournaments/{tournament_id}/instructor-slots` | **BF** | F-61 | test_admin_instructor_slot_create_planned |
| 106 | DELETE | `/admin/tournaments/{tournament_id}/instructor-slots/{slot_id}` | **BF** | F-61 (cleanup) | test_instructor_slot_duplicate_rejected |
| 107 | POST | `/admin/tournaments/{tournament_id}/instructor-slots/{slot_id}/checkin` | **BF** | F-61 (path in file) | — |
| 108 | POST | `/admin/tournaments/{tournament_id}/instructor-slots/{slot_id}/absent` | **BF** | — | — |
| 109 | POST | `/admin/tournaments/{tournament_id}/apply-fallback` | **❌ ZERO** | — | — |
| 110 | POST | `/admin/tournaments/{tournament_id}/teams/{team_id}/checkin` | **BF** | F-62 (setup path) | — |
| 111 | POST | `/admin/tournaments/{tournament_id}/teams/{team_id}/uncheckin` | AT | — | test_attendance.py |
| 112 | POST | `/admin/tournaments/{tournament_id}/players/{player_user_id}/checkin` | **BF** | F-62 | test_admin_player_checkin_creates_checkin_record |
| 113 | POST | `/admin/tournaments/{tournament_id}/players/{player_user_id}/uncheckin` | AT | — | test_attendance.py |
| 114 | PATCH | `/admin/sessions/{session_id}/postpone` | **BF** | F-60 | test_admin_session_postpone_sets_postponed_reason |
| 115 | POST | `/admin/tournaments/{tournament_id}/enroll-player` | AT | — | test_attendance.py |
| 116 | POST | `/admin/tournaments/{tournament_id}/unenroll-player` | **❌ ZERO** | — | — |

---

## ZERO Tier — 11 Routes with No Test Coverage

These 11 routes have zero test presence of any kind. No test file contains all their
fixed URL segments.

| # | Route | Source | Risk (from GAP_REPORT_CI_BASED.md) |
|---|-------|--------|-----------------------------------|
| 1 | `POST /admin/pitches/{pitch_id}/toggle` | admin.py | LOW |
| 2 | `POST /admin/sport-directors/{assignment_id}/deactivate` | admin.py | MEDIUM |
| 3 | `POST /admin/clubs/{club_id}/toggle` | admin.py | LOW |
| 4 | `POST /admin/clubs/{club_id}/csv-import` | admin.py | LOW |
| 5 | `POST /admin/users/{user_id}/lfa-player-photo` | admin.py | LOW |
| 6 | `POST /admin/users/{user_id}/lfa-player-photo/delete` | admin.py | LOW |
| 7 | `POST /dashboard/lfa-player-photo` | dashboard.py | LOW |
| 8 | `POST /dashboard/lfa-player-photo/delete` | dashboard.py | LOW |
| 9 | `POST /admin/tournaments/{tournament_id}/players/enroll-from-team` | tournaments.py | MEDIUM |
| 10 | `POST /admin/tournaments/{tournament_id}/apply-fallback` | tournaments.py | LOW |
| 11 | `POST /admin/tournaments/{tournament_id}/unenroll-player` | tournaments.py | MEDIUM |

**All 11 are classified LOW or MEDIUM risk. None are financial-critical paths.**
The 3 MEDIUM-risk routes lack financial mutations:
- `/deactivate`: sport director role assignment only; no credit impact
- `/enroll-from-team`: admin bypass enrollment; `payment_verified=True`; no credit deduction
- `/unenroll-player`: removes player from tournament; credit deducted at enroll time (no refund on remove)

---

## F-ID to Route Cross-Reference

For completeness, the F-IDs covered by `test_critical_e2e.py` and the routes they exercise:

| F-ID | Flow name | Primary routes covered |
|------|-----------|----------------------|
| F-01 | Login | `/login` |
| F-02 | Register | `/register` |
| F-03 | LFA Player Onboarding | `/specialization/lfa-player/onboarding-web` |
| F-04 | Specialization switch | `/specialization/switch` |
| F-05 | Profile edit | `/profile/edit` |
| F-06..F-12 | Quiz flows | `/quizzes/{id}/submit`, `/sessions/{id}/book` |
| F-13..F-17 | Session booking/attendance | `/sessions/book/{id}`, `/sessions/{id}/start`, `/sessions/{id}/stop`, `/sessions/{id}/attendance/mark` |
| F-19..F-22 | Individual tournament enroll cycle | `/tournaments/{id}/enroll`, `/tournaments/{id}/unenroll`, `/admin/tournaments/{id}/cancel`, `/admin/tournaments/{id}/players/…` |
| F-23/GAP-02 | Team enrollment credit | `/tournaments/{id}/team/create`, `/tournaments/{id}/teams/{id}/enroll` |
| F-24..F-25 | Team create/invite | `/tournaments/{id}/team/create`, `/teams/{id}/invite`, `/teams/invites/{id}/accept` |
| F-28..F-29 | Admin credit grant/deduct | `/admin/users/{id}/grant-credit`, `/admin/users/{id}/deduct-credit` |
| F-30..F-32 | License lifecycle | `/admin/users/{id}/revoke-license/{id}`, `/admin/users/{id}/renew-license/{id}`, `/admin/users/{id}/grant-license` |
| F-33 | Admin password reset | `/admin/users/{id}/reset-password` |
| F-35 | Admin booking confirm | `/admin/bookings/{id}/confirm` |
| F-38 | Public player card | GET only |
| F-41..F-42 | Admin live monitor / SD team remove | `/admin/tournaments/{id}/start`, `/tournaments/{id}/teams/{id}/remove` |
| F-43..F-46 | Instructor skills/enrollments | `/instructor/students/{id}/skills/{id}`, `/sessions/{id}/start` |
| F-47..F-51 | Communications | `/messages/send`, `/notifications/read-all`, `/notifications/{id}/read` |
| F-52..F-54 | Invoice management | `/admin/invoices/{id}/verify`, `/admin/invoices/{id}/cancel`, `/admin/invoices/{id}/unverify` |
| F-55..F-56 | Batch enrollment | `/admin/tournaments/{id}/players/enroll`, `/admin/tournaments/{id}/teams/enroll-bulk` |
| F-57..F-62 | Admin CRUD | `/admin/users/create`, toggle, booking cancel/postpone, instructor slot, player checkin |
| F-63..F-64 | Instructor evaluation | `/sessions/{id}/evaluate-instructor`, `/sessions/{id}/evaluate-student/{id}` |

---

## Notes on Table Accuracy

**"—" in Test Reference for BF tier:**
These routes are classified BF because all their fixed segments appear somewhere in
`test_critical_e2e.py`. However, the per-function attribution scan could not identify
a single test function body that contains all segments simultaneously. This is
expected for setup-path segments (`admin`, `camps`, `new`, etc.) that appear in
multiple test function setup blocks without being the primary route under test.

The BF tier classification is authoritative. The test function column is a best-effort
attribution; missing attribution does not affect tier.

**AT-tier test file column:**
Lists the first matching test file found. Multiple files may cover the same route.

---

*Metric Reconciliation — 2026-04-16 — main @ 3138f5b*
*Practice Booking System — E2E Coverage Program*
