/**
 * SCHED-01..05 — Admin Semester Scheduling UI
 * DB scenario: semester_scheduling (reset before first test, re-used across suite)
 *
 * Covers:
 *   SCHED-01  Schedule page loads
 *   SCHED-02  Generate form fields visible
 *   SCHED-03  Submit form → success flash + generated banner
 *   SCHED-04  Session table rows visible after generation
 *   SCHED-05  Delete sessions → table empty + generate form reappears
 */
import '../../../support/web_commands';

describe('Semester Scheduling — Admin', {
  tags: ['@web', '@admin', '@scheduling'],
}, () => {
  let semesterId;

  before(() => {
    // Seed once for the whole suite (idempotent)
    cy.task('resetDb', 'semester_scheduling');
    cy.task('getSchedSemesterId').then((id) => {
      expect(id).to.be.a('number');
      semesterId = id;
    });
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.on('window:confirm', () => true);
    cy.webLogin('sched-admin@lfa.com', 'SchedAdmin123!');
  });

  // ── SCHED-01 ────────────────────────────────────────────────────────────────
  it('SCHED-01: schedule page loads with semester info', () => {
    cy.visit(`/admin/semesters/${semesterId}/schedule`);
    cy.contains('Mini Season Cypress').should('exist');
    cy.contains('Schedule').should('exist');
    cy.contains('MINI SEASON').should('exist');
  });

  // ── SCHED-02 ────────────────────────────────────────────────────────────────
  it('SCHED-02: generate form fields are visible', () => {
    cy.visit(`/admin/semesters/${semesterId}/schedule`);
    cy.get('[data-testid="schedule-form"]').should('exist');
    cy.get('select[name="day_of_week"]').should('exist');
    cy.get('input[name="start_time"]').should('exist');
    cy.get('select[name="duration_minutes"]').should('exist');
    cy.get('[data-testid="generate-btn"]').should('exist');
  });

  // ── SCHED-03 ────────────────────────────────────────────────────────────────
  it('SCHED-03: submit generate form → success flash + generated banner', () => {
    cy.visit(`/admin/semesters/${semesterId}/schedule`);
    cy.get('select[name="day_of_week"]').select('0');     // Monday
    cy.get('input[name="start_time"]').invoke('val', '17:00').trigger('change');
    cy.get('[data-testid="generate-btn"]').click();

    // Redirected back to schedule page with flash
    cy.url().should('include', `/admin/semesters/${semesterId}/schedule`);
    cy.contains('sessions generated').should('exist');
  });

  // ── SCHED-04 ────────────────────────────────────────────────────────────────
  it('SCHED-04: session table rows visible after generation', () => {
    // Sessions were generated in SCHED-03; page should show them now
    cy.visit(`/admin/semesters/${semesterId}/schedule`);
    cy.get('[data-testid="session-row"]').should('have.length.greaterThan', 0);
  });

  // ── SCHED-05 ────────────────────────────────────────────────────────────────
  it('SCHED-05: delete sessions → table empty + generate form reappears', () => {
    cy.visit(`/admin/semesters/${semesterId}/schedule`);
    // Delete button only visible if sessions_generated=True and no attendance
    cy.get('[data-testid="delete-sessions-btn"]').should('exist').click();

    cy.url().should('include', `/admin/semesters/${semesterId}/schedule`);
    cy.contains('sessions deleted').should('exist');

    // Generate form should reappear; no session rows
    cy.get('[data-testid="generate-btn"]').should('exist');
    cy.get('[data-testid="session-row"]').should('not.exist');
  });
});
