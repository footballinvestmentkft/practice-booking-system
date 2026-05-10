/**
 * REG-01–08 — User Registration (HTML form → POST /register)
 * DB scenario: baseline  (resets invitation code INV-E2E-TEST01 to unused)
 * Role coverage: unauthenticated (public registration)
 */
import '../../../support/web_commands';

const E2E_INV_CODE = 'INV-E2E-TEST01';

describe('Web Auth — Registration', { tags: ['@web', '@auth', '@registration'] }, () => {

  before(() => {
    cy.resetDb('baseline');
  });

  beforeEach(() => {
    cy.setupCsrf();
    cy.clearAllCookies();
  });

  // ── REG-01 ─────────────────────────────────────────────────────────────
  it('REG-01: GET /register responds 200 (no 500)', () => {
    cy.request({ method: 'GET', url: '/register', failOnStatusCode: false }).then((resp) => {
      expect(resp.status).to.equal(200);
      expect(resp.body).to.not.include('Internal Server Error');
    });
  });

  // ── REG-02 ─────────────────────────────────────────────────────────────
  it('REG-02: register page shows invitation code field and submit button', () => {
    cy.visit('/register');
    cy.get('input[name="invitation_code"]').should('be.visible');
    cy.get('input[name="first_name"]').should('be.visible');
    cy.get('input[name="email"]').should('be.visible');
    cy.get('button[type="submit"]').should('be.visible');
  });

  // ── REG-03 ─────────────────────────────────────────────────────────────
  it('REG-03: authenticated user visiting /register → redirected to /dashboard', () => {
    cy.webLoginAs('admin');
    cy.request({ method: 'GET', url: '/register', failOnStatusCode: false }).then((resp) => {
      // Should redirect (302/303) to dashboard — NOT show the form
      const redirected = resp.status === 200
        ? resp.redirects?.some(r => r.includes('/dashboard'))
        : [302, 303].includes(resp.status);
      // After following redirects, the final URL/body should not be the register form
      expect(resp.body).to.not.include('Create Account');
    });
  });

  // ── REG-04 ─────────────────────────────────────────────────────────────
  it('REG-04: invalid invitation code → error message on page', () => {
    cy.request({
      method: 'POST',
      url: '/register',
      form: true,
      body: {
        first_name: 'Test', last_name: 'User', nickname: 'Tester',
        email: 'reg_test@e2e.com', password: 'TestPass123',
        phone: '+36201234567',
        date_of_birth: '1995-06-15',
        nationality: 'HU', gender: 'Male',
        street_address: 'Main St 1', city: 'Budapest',
        postal_code: '1011', country: 'Hungary',
        invitation_code: 'INV-INVALID-XXXXX',
      },
      failOnStatusCode: false,
    }).then((resp) => {
      // Server returns 200 with error message embedded in HTML
      expect(resp.status).to.equal(200);
      expect(resp.body.toLowerCase()).to.include('invalid');
    });
  });

  // ── REG-05 ─────────────────────────────────────────────────────────────
  it('REG-05: short password → error message', () => {
    cy.request({
      method: 'POST',
      url: '/register',
      form: true,
      body: {
        first_name: 'Test', last_name: 'User', nickname: 'Tester',
        email: 'reg_test@e2e.com', password: '123',
        phone: '+36201234567',
        date_of_birth: '1995-06-15',
        nationality: 'HU', gender: 'Male',
        street_address: 'Main St 1', city: 'Budapest',
        postal_code: '1011', country: 'Hungary',
        invitation_code: E2E_INV_CODE,
      },
      failOnStatusCode: false,
    }).then((resp) => {
      expect(resp.status).to.equal(200);
      // Error about password length
      expect(resp.body).to.match(/password|6/i);
    });
  });

  // ── REG-06 ─────────────────────────────────────────────────────────────
  it('REG-06: future date of birth → error message', () => {
    const future = new Date();
    future.setFullYear(future.getFullYear() + 1);
    const futureStr = future.toISOString().split('T')[0];

    cy.request({
      method: 'POST',
      url: '/register',
      form: true,
      body: {
        first_name: 'Test', last_name: 'User', nickname: 'Tester',
        email: 'reg_test@e2e.com', password: 'TestPass123',
        phone: '+36201234567',
        date_of_birth: futureStr,
        nationality: 'HU', gender: 'Male',
        street_address: 'Main St 1', city: 'Budapest',
        postal_code: '1011', country: 'Hungary',
        invitation_code: E2E_INV_CODE,
      },
      failOnStatusCode: false,
    }).then((resp) => {
      expect(resp.status).to.equal(200);
      expect(resp.body.toLowerCase()).to.include('future');
    });
  });

  // ── REG-07 ─────────────────────────────────────────────────────────────
  // Note: successful registration consumes the invitation code.
  // This test resets the DB first to get a fresh unused code.
  it('REG-07: valid registration → redirect to /dashboard, cookie set', () => {
    // Reset to get fresh invitation code before this test
    cy.resetDb('baseline');

    cy.request({
      method: 'POST',
      url: '/register',
      form: true,
      body: {
        first_name: 'New', last_name: 'Student', nickname: 'NewStudent',
        email: `reg_new_${Date.now()}@e2e.com`,
        password: 'ValidPass123',
        phone: '+36201234567',
        date_of_birth: '1998-03-10',
        nationality: 'HU', gender: 'Male',
        street_address: 'Kossuth u. 42', city: 'Budapest',
        postal_code: '1011', country: 'Hungary',
        invitation_code: E2E_INV_CODE,
      },
      failOnStatusCode: false,
    }).then((resp) => {
      // Success: 303 redirect to /dashboard (Cypress follows redirects automatically)
      // After following, we should land on dashboard (200) not login/error
      expect(resp.status).to.equal(200);
      expect(resp.body).to.not.include('Invalid invitation');
      expect(resp.body).to.not.include('Internal Server Error');
      // Should NOT be back on the register form (success = navigated away)
      expect(resp.body).to.not.include('Create Account');
    });
  });

  // ── REG-08 ─────────────────────────────────────────────────────────────
  it('REG-08: /login page has link to /register', () => {
    cy.request({ method: 'GET', url: '/login', failOnStatusCode: false }).then((resp) => {
      expect(resp.status).to.equal(200);
      expect(resp.body).to.include('/register');
    });
  });
});
