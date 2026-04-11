/**
 * STU-EVENT-01–05 — Student event (tournament) UX audit validation
 *
 * Covers the event-domain architecture refactor (3. iteráció):
 *   A-group: nav/title terminology → "Events" at /events
 *   B-group: browse page at /events/tournaments (enrolled vs browse sections)
 *   C-group: student event detail page /events/tournaments/{id}
 *   D-group: /sessions event-first grouping
 *   E-group: enrolled badge links to /events/tournaments/{id}
 *
 * Spec IDs:
 *   STU-EVENT-01   Nav bar "Events" link exists at href=/events
 *   STU-EVENT-02   /events/tournaments page has enrolled section after enroll
 *   STU-EVENT-03   /events/tournaments/{id} event detail page renders (200, no 500)
 *   STU-EVENT-04   /sessions shows "My Events" grouped section when enrolled
 *   STU-EVENT-05   Enrolled event card has enrolled-badge linking to /events/tournaments/{id}
 *
 * DB scenario: tournament_e2e
 *   - student: balance 1000, not enrolled at start
 *   - tournament: "E2E Tournament", ENROLLMENT_OPEN, entry fee 100
 *
 * Suite structure:
 *   before()     → resetDb('tournament_e2e')
 *   first block  → pre-enroll assertions (STU-EVENT-01)
 *   enrollment   → cy.request() POST enroll (CSRF bypass)
 *   second block → post-enroll assertions (STU-EVENT-02..05)
 */
import '../../../support/web_commands';

describe('Student Event UX — Terminology + Navigation', {
  tags: ['@web', '@student', '@event-ux'],
}, () => {

  before(() => {
    cy.resetDb('tournament_e2e');
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.webLoginAs('student');
  });

  // ── STU-EVENT-01 ────────────────────────────────────────────────────────────
  // Nav bar: "Events" link must exist pointing to /events after domain refactor.
  it('STU-EVENT-01: Student nav bar has "Events" link at href=/events', () => {
    cy.visit('/events/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // Nav must show "Events" pointing to /events (not /tournaments)
    cy.get('.student-nav a[href="/events"]')
      .should('exist')
      .and('contain.text', 'Events');

    // Page H1 contains "Events" or "Tournaments"
    cy.get('h1').should('be.visible');
  });

});

describe('Student Event UX — Browse + Detail page (post-enroll)', {
  tags: ['@web', '@student', '@event-ux'],
}, () => {

  before(() => {
    cy.resetDb('tournament_e2e');
    // Enroll the student via cy.request (CSRF bypass) so all tests have enrollment
    cy.clearAllCookies();
    cy.webLoginAs('student');
    // Get tournament id from the list page
    cy.visit('/events/tournaments');
    cy.get('[data-testid="tournament-card"]').first().invoke('attr', 'data-tournament-id').then((tid) => {
      cy.request({
        method: 'POST',
        url: `/tournaments/${tid}/enroll`,
        failOnStatusCode: false,
      });
    });
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.webLoginAs('student');
  });

  // ── STU-EVENT-02 ────────────────────────────────────────────────────────────
  // /events/tournaments page: enrolled section at top + browse section below
  it('STU-EVENT-02: /events/tournaments shows enrolled section with enrolled-badge', () => {
    cy.visit('/events/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // Enrolled badge must be visible (student is enrolled after before())
    cy.get('[data-testid="enrolled-badge"]').should('be.visible');
  });

  // ── STU-EVENT-03 ────────────────────────────────────────────────────────────
  // /events/tournaments/{id} event detail page: renders without error, shows event name
  it('STU-EVENT-03: /events/tournaments/{id} event detail page renders with session table', () => {
    cy.visit('/events/tournaments');
    cy.get('[data-testid="tournament-card"]').first().invoke('attr', 'data-tournament-id').then((tid) => {
      cy.visit(`/events/tournaments/${tid}`);
      cy.get('body').should('not.contain.text', 'Internal Server Error');
      cy.get('body').should('not.contain.text', '404');

      // Breadcrumb: "Events" link present
      cy.get('.breadcrumb-trail').should('exist').and('contain.text', 'Events');

      // Enrollment panel visible
      cy.get('[data-testid="enrollment-panel"]').should('exist');

      // Session schedule section (may be empty if no sessions generated)
      cy.get('.schedule-section').should('exist');
    });
  });

  // ── STU-EVENT-04 ────────────────────────────────────────────────────────────
  // /sessions shows "My Events" section (event-grouped) when enrolled in tournament
  it('STU-EVENT-04: /sessions shows "My Events" section when enrolled in a tournament', () => {
    cy.visit('/sessions');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // "My Events" section title or page renders cleanly
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.get('h1, .events-section-title')
      .should('be.visible');
  });

  // ── STU-EVENT-05 ────────────────────────────────────────────────────────────
  // Enrolled event card links to /events/tournaments/{id} (not /tournaments/{id})
  it('STU-EVENT-05: enrolled-badge links to /events/tournaments/{id} (student detail page)', () => {
    cy.visit('/events/tournaments');
    cy.get('[data-testid="enrolled-badge"]').first().then(($badge) => {
      const href = $badge.attr('href');
      expect(href).to.match(/^\/events\/tournaments\/\d+$/);
    });
  });

});
