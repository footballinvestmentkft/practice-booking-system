/**
 * STU-WF-01–05 — Student full business workflow (DOM-driven)
 * DB scenarios: baseline (registration + credits), session_ready (booking lifecycle)
 * Role coverage: unauthenticated → student
 *
 * These tests cover the complete student business process via real DOM interactions:
 * 14-field registration form, credits page coupon redemption, profile edit —
 * complementing the cy.request() tests in registration.cy.js and profile.cy.js
 * with browser-visible form fill + submit workflows.
 *
 * Playwright parity:
 *   - STU-WF-01 ← test_user_registration_basic.py (14-field form fill + submit)
 *   - STU-WF-02 ← test_onboarding_with_coupon.py (coupon/invite code redemption via DOM)
 *   - STU-WF-03 ← Streamlit profile edit via UI (update name via form)
 *   - STU-WF-04 ← Student credits balance page render
 *   - STU-WF-05 ← Student booking lifecycle (complementary DOM check)
 */
import '../../../support/web_commands';

describe('Business Workflow — Student Full Process', {
  tags: ['@web', '@student', '@business-workflow'],
}, () => {

  beforeEach(() => {
    cy.clearAllCookies();
    cy.on('window:alert', () => {});
    // Register CSRF interceptors for all browser-level POSTs in this spec.
    // Required: POST /register uses Double Submit Cookie CSRF protection.
    // The interceptor reads csrf_token from Cookie header and adds X-CSRF-Token.
    cy.setupCsrf();
  });

  // ── STU-WF-01 ────────────────────────────────────────────────────────────
  // Student fills the complete 14-field registration form via real DOM interactions.
  // This test mirrors test_user_registration_basic.py — the Playwright test that
  // filled each input field individually before submitting.
  //
  // Fields: invitation_code, first_name, last_name, nickname, gender (select),
  //         date_of_birth, nationality, email, password, phone,
  //         street_address, city, postal_code, country
  //
  // CSRF: cy.setupCsrf() (registered in beforeEach) injects X-CSRF-Token into the
  // POST /register request. Without this, the server returns 403 and the
  // page load hangs indefinitely.
  it('STU-WF-01: student fills 14-field registration form via DOM → submits → no 500', () => {
    cy.resetDb('baseline');  // ensures INV-E2E-TEST01 is unused

    // GET /register sets the csrf_token cookie — setupCsrf() will read it on POST
    cy.visit('/register');
    cy.contains('Create Account').should('be.visible');

    // ── Invitation code ──────────────────────────────────────────────────
    cy.get('input[name="invitation_code"]').type('INV-E2E-TEST01');

    // ── Personal information ─────────────────────────────────────────────
    cy.get('input[name="first_name"]').type('E2E');
    cy.get('input[name="last_name"]').type('DomStudent');
    cy.get('input[name="nickname"]').type('DomStud');
    cy.get('select[name="gender"]').select('Male');

    // date_of_birth: bypass Chrome's max attribute constraint by setting val directly
    cy.get('input[name="date_of_birth"]').invoke('val', '1998-03-10');

    cy.get('select[name="nationality"]').select('HU');

    // ── Account details ──────────────────────────────────────────────────
    // Use unique email per run to avoid duplicate key errors on retries
    const email = `dom.reg.${Date.now()}@e2e.test`;
    cy.get('input[name="email"]').type(email);
    cy.get('input[name="password"]').type('DomPass123');
    cy.get('input[name="phone"]').type('+36201234567');

    // ── Address ──────────────────────────────────────────────────────────
    cy.get('input[name="street_address"]').type('Kossuth u. 42');
    cy.get('input[name="city"]').type('Budapest');
    cy.get('input[name="postal_code"]').type('1011');
    cy.get('input[name="country"]').type('Hungary');

    // ── Submit (form has novalidate — browser won't block on HTML5 validation) ──
    cy.get('button[type="submit"]').click();

    // POST /register → validates code, creates user, auto-logs in, 303 → /dashboard
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    // Should redirect away from the register page
    cy.url().should('not.include', '/register');
  });

  // ── STU-WF-02 ────────────────────────────────────────────────────────────
  // Student visits /credits and redeems the seeded invitation code via DOM form.
  // Mirrors test_onboarding_with_coupon.py — Playwright test that filled the
  // Bonus Code input and clicked Redeem Code button.
  //
  // DOM: input#invitation-code (no name attr) + button onclick="redeemInvitationCode()"
  // JS: fetch POST /api/v1/invitation-codes/redeem → shows message in #invitation-message
  it('STU-WF-02: student fills invitation code input on /credits → clicks Redeem → message visible', () => {
    cy.resetDb('baseline');  // INV-E2E-TEST01 unused, student has 0 credits

    cy.webLoginAs('student');
    cy.visit('/credits');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // Find the invitation code input (by id — no name attribute on this JS-driven input)
    cy.get('#invitation-code').should('be.visible').type('INV-E2E-TEST01');

    // Click the Redeem button
    cy.get('button[onclick*="redeemInvitationCode"]').click();

    // JS fetch completes → #invitation-message updates (success or known error)
    // Assert the message element becomes visible (proves DOM interaction worked)
    cy.get('#invitation-message', { timeout: 8000 }).should('be.visible');
  });

  // ── STU-WF-03 ────────────────────────────────────────────────────────────
  // Student fills profile edit form via DOM and saves.
  // Mirrors Streamlit profile edit — previously only tested via cy.request().
  // At minimum requires: name + date_of_birth (the two fields /profile/edit validates).
  it('STU-WF-03: student fills profile edit form via DOM → saves → no 500', () => {
    cy.resetDb('baseline');
    cy.webLoginAs('student');
    cy.visit('/profile/edit');

    // Form: method=POST action="/profile/edit"
    // Clear name and type new value
    cy.get('input[name="name"]').clear().type('Updated DOM Name');

    // date_of_birth: set value directly to bypass any browser date constraints
    cy.get('input[name="date_of_birth"]').invoke('val', '1998-05-14');

    // Submit
    cy.get('button[type="submit"]').click();

    // 303 redirect to /profile → 200 — no server error
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.url().should('include', '/profile');
  });

  // ── STU-WF-04 ────────────────────────────────────────────────────────────
  // Student views credits balance + transaction history page via DOM.
  // Credits page is JS-heavy (fetch-driven) — this validates the page renders fully.
  //
  // Bug fixed (2026-03-10): credits.html had `user.role.value == 'STUDENT'` (uppercase)
  // but UserRole.STUDENT.value is lowercase 'student'. Fixed across all 8 templates
  // (credits, profile, hub_specializations, about_specializations, unified_header,
  // dashboard_student_switcher, dashboard_student_new).
  // .credit-badge now correctly renders for authenticated students.
  it('STU-WF-04: student visits /credits via DOM → credit-badge + balance + coupon visible', () => {
    cy.resetDb('student_with_credits');  // rdias has 200 credits

    cy.webLoginAs('student');
    cy.visit('/credits');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    // .credit-badge renders because credits.html now checks role.value == 'student' (lowercase)
    cy.get('.credit-badge').should('be.visible').and('contain.text', 'Credits');
    // Credit balance amount section in main content
    cy.get('.balance-amount').should('be.visible');
    // Coupon sections visible
    cy.get('.coupon-section').should('exist');
  });

  // ── STU-WF-05 ────────────────────────────────────────────────────────────
  // Student visits /progress and /achievements pages via DOM.
  // These are gamification pages — previously only tested via smoke routes.
  it('STU-WF-05: student visits /progress and /achievements → both render without 500', () => {
    cy.resetDb('student_with_credits');

    cy.webLoginAs('student');

    cy.visit('/progress');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    cy.visit('/achievements');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
  });
});
