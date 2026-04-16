# E2E Coverage Baseline — Practice Booking System
**Last updated: 2026-04-16 | Branch: fix/e2e-coverage-clean | Sprint 6 (clean integration to main)**

---

## KPI — COVERED FLOWS / TOTAL FLOWS

**Measurement model:** Business flows, NOT route percentage.
A flow = one end-to-end user action with a verifiable outcome.
A flow is COVERED only when ALL 3 layers are proven:
- **HTTP**: correct status code asserted
- **DB**: business-state field queried and asserted
- **UI**: specific label/balance/status string asserted in HTML response

| Metric | Value |
|--------|-------|
| Total defined flows | **54** |
| Covered (all 3 layers) | **54** |
| Not Implemented on main | **2** (F-26, F-27 — camp enrollment routes absent) |
| Not Covered | **0** |
| **Coverage KPI** | **100%** (54/54 implemented flows) |
| Sprint 1 additions | +5 flows (F-03, F-14, F-15, F-16, F-29) |
| Sprint 2 additions | +8 flows (F-04, F-05, F-12, F-24, F-25, F-32, F-35, F-38) |
| Sprint 3 additions | +2 flows (F-41, F-42) |
| Sprint 4 additions | +4 flows (F-43, F-44, F-45, F-46) — Instructor domain |
| Sprint 5 additions | +5 flows (F-47, F-48, F-49, F-50, F-51) — Communications domain |
| Sprint 6 additions | +5 flows (F-52, F-53, F-54, F-55, F-56) — Admin Operations domain |

---

## CI Status

| Workflow | Last result | SHA |
|----------|-------------|-----|
| Test Baseline Check | ✅ | 4f96870 |
| E2E Lifecycle Visibility | ✅ | 4f96870 |
| Dark Mode CSS Validation | ✅ | 4f96870 |
| E2E Multi-Campus Venue + Instructor | ✅ | 4f96870 |
| E2E Invitation Code Seed Validation | ✅ | 4f96870 |
| E2E Virtual Tournament | ✅ | 4f96870 |
| E2E Tournament Session Types | ✅ | 4f96870 |
| Cypress Web E2E Tests | ✅ | 4f96870 |

---

## Test Suite Size

| Suite | Count |
|-------|-------|
| `tests/integration/web_flows/` | **580** (41 files) |
| `tests/integration/api_smoke/` | **1,741** |
| Cypress (`cypress/e2e/`) | **13** (4 files) |
| **Total** | **2,334** |

---

## Section 0 — FLOW COVERAGE TABLE

**Rules:**
- HTTP ✅ = specific status code asserted in test
- DB ✅ = SQLAlchemy query verifies changed field
- UI ✅ = specific label/balance/state string asserted in `response.text`
- N/A = layer not applicable (read-only endpoints have no DB mutation)
- ❌ = layer not proven by any test

| FID | Flow | HTTP | DB | UI | Status | Test |
|-----|------|------|-----|-----|--------|------|
| F-01 | Login (POST /login → 303 → /dashboard) | ✅ | ✅ | ✅ | **COVERED** | test_login_success_redirects_to_dashboard |
| F-02 | Register with invitation code → credit_balance set | ✅ | ✅ | ✅ | **COVERED** | ICR |
| F-03 | LFA player onboarding → UserLicense.onboarding_completed=True | ✅ | ✅ | ✅ | **COVERED** | test_lfa_player_onboarding_creates_license |
| F-04 | Specialization switch → UserLicense.specialization_type changed | ✅ | ✅ | ✅ | **COVERED** | test_specialization_switch_updates_active_spec |
| F-05 | Profile edit → User.first_name updated | ✅ | ✅ | ✅ | **COVERED** | test_profile_edit_updates_name |
| F-06 | Quiz fail → retry → pass → QuizAttempt.passed=True (best score) | ✅ | ✅ | ✅ | **COVERED** | QRB |
| F-07 | Quiz no booking → 403 enrollment gate | ✅ | ✅ | ✅ | **COVERED** | QEG |
| F-08 | Quiz max attempts → "No More Attempts" UI state | ✅ | ✅ | ✅ | **COVERED** | QAL |
| F-09 | Quiz interrupted → same attempt_id resumed | ✅ | ✅ | ✅ | **COVERED** | QIS |
| F-10 | Quiz UI state machine: no attempt→Start / fail→Retry / pass→PASSED | ✅ | N/A | ✅ | **COVERED** | QPG |
| F-11 | Quiz pass → XP awarded → UserStats.total_xp updated | ✅ | ✅ | ✅ | **COVERED** | GAP-06 |
| F-12 | Quiz attempt review — completed attempt in DB + quiz page renders | ✅ | ✅ | ✅ | **COVERED** | test_quiz_attempt_review_renders_score |
| F-13 | Session capacity=1 + 1 existing booking → POST /api/v1/bookings/ → WAITLISTED | ✅ | ✅ | ✅ | **COVERED** | GAP-07 |
| F-14 | Instructor: POST /start → Session.actual_start_time IS NOT NULL | ✅ | ✅ | ✅ | **COVERED** | test_instructor_session_start_stop |
| F-15 | Instructor: POST /stop → Session.actual_end_time IS NOT NULL | ✅ | ✅ | ✅ | **COVERED** | test_instructor_session_start_stop |
| F-16 | Attendance mark → Attendance(status=present) row created | ✅ | ✅ | ✅ | **COVERED** | test_attendance_mark_creates_record |
| F-17 | Credit history visible after transaction ("Credit balance: X") | ✅ | ✅ | ✅ | **COVERED** | CDE, GAP-02, GAP-04 |
| F-18 | Browse filter: ?status=open → only ENROLLMENT_OPEN cards | ✅ | N/A | ✅ | **COVERED** | BF-CY-01..04 |
| F-19 | Tournament IND enroll → credit_balance -= cost → CreditTransaction | ✅ | ✅ | ✅ | **COVERED** | CDE |
| F-20 | Tournament unenroll → 50% refund → CreditTransaction(REFUND) | ✅ | ✅ | ✅ | **COVERED** | TCR |
| F-21 | Tournament cancel → 100% refund → CreditTransaction(REFUND, amount=full) | ✅ | ✅ | ✅ | **COVERED** | GAP-01 |
| F-22 | Tournament enroll → admin rejection → credit_balance unchanged | ✅ | ✅ | ✅ | **COVERED** | GAP-03 |
| F-23 | TEAM tournament enroll → captain UserLicense.credit_balance -= cost | ✅ | ✅ | ✅ | **COVERED** | GAP-02 |
| F-24 | Team create (captain) → Team + TeamMember(CAPTAIN) rows | ✅ | ✅ | ✅ | **COVERED** | test_team_create_by_captain |
| F-25 | Team invite → accept → TeamMember added | ✅ | ✅ | ✅ | **COVERED** | test_team_invite_accept_adds_member |
| F-26 | Camp enroll → auto-APPROVED → CreditTransaction(−cost) | N/A | N/A | N/A | **NOT IMPLEMENTED** on main (`/events/camps/{id}/enroll` absent) | — |
| F-27 | Camp unenroll → 50% refund → SemesterEnrollment.is_active=False | N/A | N/A | N/A | **NOT IMPLEMENTED** on main (`/events/camps/{id}/unenroll` absent) | — |
| F-28 | Admin grant credit → User.credit_balance += amount + CreditTransaction | ✅ | ✅ | ✅ | **COVERED** | GAP-04 |
| F-29 | Admin deduct credit → User.credit_balance -= amount + CreditTransaction | ✅ | ✅ | ✅ | **COVERED** | test_admin_deduct_credit |
| F-30 | Admin license renewal → UserLicense.expires_at updated + LicenseProgression | ✅ | ✅ | ✅ | **COVERED** | GAP-05 |
| F-31 | Admin license revoke → UserLicense.is_active=False + revoke form absent on edit page | ✅ | ✅ | ✅ | **COVERED** | LRC |
| F-32 | Admin grant license → new UserLicense(is_active=True) created | ✅ | ✅ | ✅ | **COVERED** | test_admin_grant_license_creates_user_license |
| F-33 | Admin password reset → User.password_hash changed → new password valid | ✅ | ✅ | ✅ | **COVERED** | APR |
| F-34 | Admin invitation code create → InvitationCode row + visible in /admin/invitation-codes | ✅ | ✅ | ✅ | **COVERED** | GAP-10 |
| F-35 | Admin booking confirm → Booking.status=CONFIRMED | ✅ | ✅ | ✅ | **COVERED** | test_admin_booking_confirm_updates_status |
| F-36 | Public event group standings → "GD" column in HTML | ✅ | N/A | ✅ | **COVERED** | GAP-08 |
| F-37 | Public event knockout bracket section rendered | ✅ | N/A | ✅ | **COVERED** | GAP-09 |
| F-38 | Public player card (GET /players/{id}/card) → 200 + player data | ✅ | ✅ | ✅ | **COVERED** | test_public_player_card_renders |
| F-39 | Skill delta: tournament → TournamentParticipation.skill_rating_delta → GET /skills | ✅ | ✅ | ✅ | **COVERED** | SDE |
| F-40 | Student full journey: browse → enroll → admin approve → "Enrolled" badge | ✅ | ✅ | ✅ | **COVERED** | SFJ |
| F-41 | Tournament live monitor page renders (GET /admin/tournaments/{id}/live) | ✅ | ✅ | ✅ | **COVERED** | test_admin_live_monitor_renders |
| F-42 | Sport director team remove → TournamentTeamEnrollment.is_active=False | ✅ | ✅ | ✅ | **COVERED** | test_sport_director_team_remove |
| F-43 | Instructor GET skills form → 200 + "Edit Football Skills" rendered | ✅ | ✅ | ✅ | **COVERED** | test_instructor_skills_form_renders |
| F-44 | Instructor POST skills update → UserLicense.football_skills dict + AuditLog(FOOTBALL_SKILLS_UPDATED) | ✅ | ✅ | ✅ | **COVERED** | test_instructor_skills_update_and_audit |
| F-45 | Instructor POST invalid skill (>100) → 200 + error message, no AuditLog | ✅ | ✅ | ✅ | **COVERED** | test_instructor_skills_invalid_value_returns_error |
| F-46 | Instructor GET /enrollments → 200 + PENDING enrollment visible | ✅ | ✅ | ✅ | **COVERED** | test_instructor_enrollments_page_renders |
| F-47 | Message send (POST /messages/send → 303) → Message row(is_read=False) created | ✅ | ✅ | ✅ | **COVERED** | test_message_send_creates_row |
| F-48 | Message detail GET → auto-marks is_read=True + read_at set for recipient | ✅ | ✅ | ✅ | **COVERED** | test_message_detail_auto_marks_read |
| F-49 | Notifications read-all (POST → 303) → all Notification.is_read=True | ✅ | ✅ | ✅ | **COVERED** | test_notifications_read_all_marks_all_read |
| F-50 | Notification single read (POST → 200 JSON) → Notification.is_read=True | ✅ | ✅ | ✅ | **COVERED** | test_notification_single_read_updates_state |
| F-51 | Inbox user separation: recipient sees unread subject, sender row absent | ✅ | ✅ | ✅ | **COVERED** | test_messages_inbox_shows_unread_for_recipient |
| F-52 | Admin invoice verify → InvoiceRequest.status="verified" + User.credit_balance += amount + CreditTransaction(PURCHASE) | ✅ | ✅ | ✅ | **COVERED** | test_admin_invoice_verify_credits_student |
| F-53 | Admin invoice cancel → InvoiceRequest.status="cancelled"; credit_balance unchanged | ✅ | ✅ | ✅ | **COVERED** | test_admin_invoice_cancel_sets_cancelled_status |
| F-54 | Admin invoice unverify → status reverts to "pending", verified_at=None, credit_balance -= amount + CreditTransaction(REFUND) | ✅ | ✅ | ✅ | **COVERED** | test_admin_invoice_unverify_reverts_credits |
| F-55 | Admin player batch-enroll → SemesterEnrollment × N (APPROVED, payment_verified=True, is_active=True) | ✅ | ✅ | ✅ | **COVERED** | test_admin_batch_enroll_players_creates_enrollments |
| F-56 | Admin team bulk-enroll → TournamentTeamEnrollment × N (is_active=True, payment_verified=True) → 303 | ✅ | ✅ | ✅ | **COVERED** | test_admin_team_bulk_enroll_creates_team_enrollments |

---

## Section 1 — ASSERTION RULES (enforced)

### Rule 1 — UI assertions must prove business state
```
❌ INVALID: assert "LFA" in response.text
✅ VALID:   assert "Credit balance: 300" in response.text
✅ VALID:   assert "Status: ENROLLED" in response.text
✅ VALID:   assert "No More Attempts" in response.text
```

### Rule 2 — 303 flow rule (mandatory for every POST → redirect)
```python
r = client.post(url, data={...}, follow_redirects=False)
assert r.status_code == 303
redirect_url = r.headers["location"]
r_page = client.get(redirect_url)
assert r_page.status_code == 200
assert "<specific business state>" in r_page.text
```
If `POST → 303 → GET` is missing → test is INVALID.

### Rule 3 — Layer completeness (CI enforced)
Every test in `test_critical_e2e.py` must have:
- HTTP assertion (`assert resp.status_code == ...`)
- DB assertion (`db.query(...).filter(...).first()`)
- UI assertion (`assert "..." in r.text`)

Violation → `scripts/verify_coverage_layers.py` fails → CI fails.

---

## Section 2 — NOT IMPLEMENTED (no test needed)

| Flow | Reason |
|------|--------|
| Student self-service password reset | No `/forgot-password` endpoint. Only admin can reset. |
| Email verification | No SMTP integration. No email sent anywhere. |
| Session booking credit refund | Sessions are free to book. No credit deduction = no refund. |
| Concurrent credit double-spend | 3-layer guard: app check + atomic SQL + DB constraint. See RISK-01 below. |

---

## Section 3 — KNOWN ACCEPTED RISKS

### RISK-01: Concurrent credit deduction race condition
**Status:** ✅ Adequately protected at production level.

| Layer | Guard | Location |
|-------|-------|----------|
| App check | `if credit_balance < cost: return error` | `tournaments.py:170` |
| Atomic SQL | `UPDATE users SET credit_balance -= cost WHERE id=:id AND credit_balance >= cost` → rowcount=0 if race lost | `tournaments.py:203` |
| DB constraint | `CHECK (credit_balance >= 0)` | squashed baseline migration |

**Why not E2E tested:** Single-process test harness cannot simulate true concurrent HTTP requests. The guard is verified at code level (atomic UPDATE pattern exists) and schema level (CHECK constraint in migration). See `test_concurrency.py` (7 tests) for partial coverage.

---

### DESIGN-01 (known debt): No session cancellation endpoint
`DELETE /sessions/{id}` blocks when bookings exist. No `POST /sessions/{id}/cancel`.
**Financial impact:** LOW (sessions are free to book, no refund needed).
**When to address:** When admin needs to cancel sessions while preserving booking records.

---

## Section 4 — BASELINE RULES (enforced from 2026-04-15)

1. **KPI is COVERED FLOWS / TOTAL FLOWS** — not route percentage.
2. **Every new feature = new flow in this table** before merge to main.
3. **All 3 layers required** (HTTP + DB + UI) — missing any layer = NOT COVERED.
4. **303 flow rule** — every POST → redirect must follow the 2-step pattern.
5. **UI assertions must prove business state** — generic render checks are INVALID.
6. **CI 8/8 green on same SHA** — required before any PR merge.
7. **`scripts/verify_coverage_layers.py` must pass** — runs in CI as part of Test Baseline Check.
8. **No path-filter gaps** — all 8 E2E workflows trigger on every push (no path filters) and every PR to main.
