# Cypress Coverage Gap Analysis
## Practice Booking System
**Version: 1.0 | Created: 2026-04-17 | SHA: e556158**
**Scope: 29 spec files, 339 tests — as of cypress-web-e2e.yml run `24538319993`**

---

## §1 — Coverage Assessment: Is It Real or Just Happy-Path?

Short answer: **mixed — substantially behavioral, with identifiable structural-only clusters.**

### Assertion type breakdown

| Type | Count | % |
|------|-------|----|
| Form submission (POST → outcome) | 85 | 25% |
| Page load / HTTP status check | 95 | 28% |
| DOM element visibility / text match | 78 | 23% |
| Redirect verification | 68 | 20% |
| RBAC / auth guard | 20 | 6% |
| Numeric validation (credit balance, score) | 22 | 7% |
| Error message validation | 18 | 5% |
| DB verification (via API or UI text) | 5 | 1% |
| CSS / viewport responsive | 15 | 4% |

**~61% of tests are behavioral** (form submit → asserted outcome; state-dependent UI; error path verification).
**~39% are structural** (page loads, element existence, HTTP status only).

### Where behavioral depth is strong

| Area | Quality | Evidence |
|------|---------|---------|
| Auth flows | ✅ HIGH | Error messages on wrong password, inactive account, future DOB |
| Quiz scoring | ✅ HIGH | Exact score text "50.0%", "0.0%", DB row verification, auto-submit on timer |
| Credit deduction (enrollment) | ✅ HIGH | Numeric balance before/after (1000→900), refund arithmetic (900→950) |
| RBAC guards | ✅ HIGH | 403/401 checked for student, instructor, unauthenticated on every admin path |
| Responsive layout | ✅ HIGH | CSS property checks (overflow-x auto, flex-direction column) per viewport |
| Cross-role lifecycle (XR) | ✅ HIGH | 15 sequential steps: login → booking → start → attend → stop → evaluate |
| Registration | ✅ HIGH | 14-field form; invitation code, DOB validation errors; cookie set on success |

### Where tests are structural only (not behavioral)

| Spec / Test | What it actually tests | Gap |
|-------------|----------------------|-----|
| `student_features.cy.js` FEAT-01..04 | GET → 200, no 500 | No assertion on rendered data content |
| `student_full_workflow.cy.js` STU-WF-05 | GET /progress + /achievements → 200 | No assertion on skill values, XP displayed |
| `instructor_full_workflow.cy.js` INST-WF-01..05 | Page loads, flexible text checks | No form submission or state-change tested |
| `admin_pages_coverage.cy.js` ADM-PAGE-* | Page loads + admin layout presence | No CRUD action tested within most pages |
| `visual_walkthrough.cy.js` | Slow-mode page navigation | Primarily observability, not regression gate |

---

## §2 — Gap List: What Is NOT Covered by Cypress

Gaps are classified by impact tier: **CRITICAL** (state-changing, credit-affecting) / **HIGH** (functional regression risk) / **MEDIUM** (UI correctness) / **LOW** (edge case).

### CRITICAL — No Cypress test exists

| Gap ID | Feature | pytest coverage | Cypress gap |
|--------|---------|----------------|------------|
| CY-GAP-01 | Admin invoice verify (F-52) | ✅ `test_admin_invoice_verify_credits_student` | ❌ No Cypress test for `/admin/invoices/{id}/verify` — credit addition not UI-verified |
| CY-GAP-02 | Admin invoice cancel (F-53) | ✅ `test_admin_invoice_cancel` | ❌ No Cypress test |
| CY-GAP-03 | Admin invoice unverify (F-54) | ✅ `test_admin_invoice_unverify_removes_credits` | ❌ No Cypress test |
| CY-GAP-04 | Message send/receive/read (F-47..51) | ✅ 5 pytest flows | ❌ No Cypress test for `/messages/send`, inbox, read-state |
| CY-GAP-05 | Notification read-all / single read (F-49..50) | ✅ 2 pytest flows | ❌ No Cypress test for notification state change |

### HIGH — Functional regression risk

| Gap ID | Feature | pytest coverage | Cypress gap |
|--------|---------|----------------|------------|
| CY-GAP-06 | Batch player enroll (F-55) | ✅ `test_admin_batch_enroll_players` | ❌ No Cypress test for admin batch-enroll UI |
| CY-GAP-07 | Team bulk-enroll (F-56) | ✅ `test_admin_bulk_enroll_teams` | ❌ No Cypress test |
| CY-GAP-08 | Admin tournament create/edit full flow | `admin_pages_coverage` ADM-PAGE-09 = page load only | ❌ No POST to create tournament; no session generation assertion |
| CY-GAP-09 | Student evaluates instructor — UI form (F-63) | ✅ pytest; XR-15 exists in cross-role | ⚠️ XR-15 uses `cy.request()` (API call), not DOM form fill |
| CY-GAP-10 | Virtual/hybrid session — "Join Meeting" button | ✅ `test_virtual_tournament.py` VT-01..05 | ❌ No Cypress test verifying meeting_link button renders in session list |
| CY-GAP-11 | Public event page filter (`?status=open&delivery=virtual`) | ❌ pytest gap (ZERO route) | ❌ No Cypress test for browse filter URL params |
| CY-GAP-12 | Quiz enrollment gate: no booking → 403 result visible in UI | ✅ pytest (partial) | ❌ No Cypress test for "not enrolled" error shown to student |

### MEDIUM — UI correctness, not behavior

| Gap ID | Feature | Note |
|--------|---------|------|
| CY-GAP-13 | Skill delta / profile update after tournament | `student_journey.cy.js` tests EMA chart but does not trigger an actual tournament then verify delta |
| CY-GAP-14 | Credit transaction history rendered in `/credits` page | `tournament_lifecycle.cy.js` verifies balance (900) but not transaction history rows |
| CY-GAP-15 | Multi-campus venue display in session schedule | No Cypress test for `campus_name` / `pitch_name` appearing in session cards |
| CY-GAP-16 | Session postpone admin action (F-60) | pytest ✅; no Cypress test for `/admin/sessions/{id}/postpone` |
| CY-GAP-17 | Player check-in flow (F-62) | pytest ✅; no Cypress test for admin check-in UI |
| CY-GAP-18 | Instructor slot creation (F-61) | pytest ✅; no Cypress test for admin instructor slot assign |

### LOW — Edge cases / observability

| Gap ID | Feature | Note |
|--------|---------|------|
| CY-GAP-19 | Session full → waitlist or booking blocked | No Cypress test for `capacity_exceeded` UI message |
| CY-GAP-20 | Double credit deduction race (concurrent enrollment) | Not testable via Cypress (requires concurrent requests) |
| CY-GAP-21 | 404 / 403 error page rendering | No Cypress test verifying error page UI structure |
| CY-GAP-22 | `instructor_full_workflow.cy.js` — no form submission | All 5 INST-WF tests are structural page loads; no state change verified |

---

## §3 — What "Real UI Regression Protection" Requires

The current Cypress suite is a **page-render and happy-path regression guard**, with strong behavioral depth in auth, quiz, credit arithmetic, and RBAC. It does NOT yet provide full UI regression protection.

The gap between "it runs" and "real regression protection" is:

### P0 — Prevent critical UI regressions (highest priority)

| Action | Addresses |
|--------|---------|
| **CY-GAP-01..03**: Invoice verify/cancel/unverify Cypress tests | Credit-affecting admin actions have zero UI-level gate |
| **CY-GAP-09 fix**: XR-15 must use DOM form fill (not `cy.request`) | `cy.request` bypasses UI rendering — template regressions are invisible |
| **CY-GAP-04..05**: Message/notification Cypress tests | Communication flows have zero UI regression guard |

### P1 — Close structural-only test clusters

| Action | Addresses |
|--------|---------|
| **CY-GAP-22**: Add one state-change assertion to `instructor_full_workflow.cy.js` | Currently 5/5 tests are page-load only |
| **CY-GAP-13**: Add tournament → delta → skills page DOM assertion | EMA chart test exists but no causal proof from action to display |
| **CY-GAP-14**: Add transaction row assertion to credit history | Balance check exists; history row text does not |

### P2 — Business flow completeness

| Action | Addresses |
|--------|---------|
| **CY-GAP-08**: Admin tournament creation POST → session generation → student can see event | Admin CRUD pages tested structurally only |
| **CY-GAP-10**: Virtual session "Join Meeting" button render test | session_type_config='virtual' path has zero Cypress coverage |
| **CY-GAP-11**: Browse filter (`?status=open&delivery=virtual`) Cypress test | URL-driven filter has zero Cypress coverage at any level |

### P3 — Edge case hardening (future sprint)

| Action | Addresses |
|--------|---------|
| CY-GAP-19: Session-full booking blocked UI message | Capacity guard visible to user |
| CY-GAP-21: 404 / 403 error page structure test | Error page rendering regression |
| CY-GAP-12: Quiz enrollment gate UI (not enrolled → error message in browser) | UX regression: student sees confusing error |

---

## §4 — Prioritized Implementation Order

| Sprint | Gap IDs | Effort | Risk if missed |
|--------|---------|--------|---------------|
| **Next sprint** | CY-GAP-01..03 (invoice flows) | ~3 tests | Credit-affecting admin UI unprotected |
| **Next sprint** | CY-GAP-09 (XR-15 DOM form) | 1 test rewrite | Template regression invisible |
| **Next sprint** | CY-GAP-04..05 (messages) | ~4 tests | Communication UI unprotected |
| Sprint+1 | CY-GAP-08 (tournament CRUD) | ~5 tests | Admin tournament workflow unprotected |
| Sprint+1 | CY-GAP-10..11 (virtual/filter) | ~3 tests | Virtual path invisible to Cypress |
| Sprint+2 | CY-GAP-13..14 (skill delta, credit history) | ~2 tests | UI data display unverified |
| Sprint+2 | CY-GAP-22 (instructor workflow) | ~2 tests | 5 tests are structural-only |
| Backlog | CY-GAP-19, 21, 12 | ~3 tests | Edge case observability |

---

## §5 — Current Cypress Suite Verdict

| Dimension | Assessment |
|-----------|-----------|
| Auth / RBAC | ✅ COMPLETE — all roles, all error states, guard tested |
| Credit arithmetic | ✅ COMPLETE — balance before/after with numeric assertions |
| Quiz scoring | ✅ COMPLETE — exact score text, DB row, timer auto-submit |
| Admin page rendering | ⚠️ PARTIAL — all pages load; CRUD forms sparse |
| Student journey | ✅ MOSTLY COMPLETE — onboarding, booking, lifecycle |
| Invoice / payment UI | ❌ MISSING — zero Cypress coverage |
| Message / notification UI | ❌ MISSING — zero Cypress coverage |
| Cross-role (full lifecycle) | ✅ COMPLETE — 15-step XR flow (was PR-only; now fixed) |
| Virtual/hybrid session UI | ❌ MISSING — zero Cypress coverage |
| Browse filter (URL params) | ❌ MISSING — zero Cypress coverage |
| Responsive layout | ✅ COMPLETE — 12 viewport-specific tests |

**Overall verdict:** Current Cypress suite is a solid regression guard for auth, student flows, and cross-role lifecycle. It is NOT a complete UI regression suite — 5 CRITICAL/HIGH gaps (CY-GAP-01..05) represent real UI state changes with no browser-level regression gate.

---

*Cypress Coverage Gap Analysis v1.0 — 2026-04-17 — main @ e556158*
*Practice Booking System — Engineering Lead*
