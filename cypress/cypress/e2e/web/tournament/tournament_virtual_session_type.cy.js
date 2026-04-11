/**
 * TVST-01–04 — Virtual/Hybrid tournament session type UI tests
 * DB scenario: tournament_virtual_e2e
 *
 * What it proves (browser-level, DOM-driven):
 *   TVST-01  Admin create tournament form → session_type_config select has virtual/hybrid options
 *            AND toggles meeting_link visibility
 *   TVST-02  Admin edit page for virtual tournament → meeting_link input pre-filled with stored URL
 *   TVST-03  Public event page /events/{id} → '💻 Online' meta chip visible for virtual tournament
 *   TVST-04  Student /sessions → '💻 Join Meeting' button visible for enrolled virtual session
 *
 * DB state (tournament_virtual_e2e scenario):
 *   - Admin, instructor, student (rdias@manchestercity.com) users
 *   - Student: credit_balance=1000, onboarding_completed=True, LFA_FOOTBALL_PLAYER license
 *   - Semester code=TOURN-VIRTUAL-E2E-2026, name='E2E Virtual Tournament 2026'
 *     status=IN_PROGRESS, sessions_generated=True
 *   - TournamentConfiguration: session_type_config='virtual',
 *     meeting_link='https://meet.example.com/e2e-virtual-session'
 *   - 1 Session: type=virtual, meeting_link set, date_start=tomorrow, status=scheduled
 *   - SemesterEnrollment for student: APPROVED, is_active=True
 *
 * Tests run sequentially, sharing DB state (single before() reset).
 */
import '../../../support/web_commands';

const VIRTUAL_MEETING_LINK = 'https://meet.example.com/e2e-virtual-session';
const TOURNAMENT_CODE      = 'TOURN-VIRTUAL-E2E-2026';
const SESSION_TITLE        = 'E2E Virtual Session';

describe('Virtual Tournament Session Type — UI', {
  tags: ['@web', '@admin', '@tournament', '@virtual'],
}, () => {

  // Tournament DB id resolved in before() via task — shared across all tests
  let tournamentId = null;

  before(() => {
    cy.resetDb('tournament_virtual_e2e');
    // Resolve the virtual tournament's DB id via direct DB query (no auth needed)
    cy.task('getTournamentIdByCode', TOURNAMENT_CODE).then((id) => {
      expect(id, `tournament ${TOURNAMENT_CODE} must exist in DB after seed`).to.not.be.null;
      tournamentId = id;
    });
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.on('window:alert', () => {});
  });

  // ── TVST-01 ────────────────────────────────────────────────────────────────
  // Admin create tournament form must include a session_type_config select
  // with all three delivery options: on_site, virtual, hybrid.
  // The meeting_link input must appear only when virtual or hybrid is selected.
  it('TVST-01: Admin create form — session_type_config select has virtual option, meeting_link toggles', () => {
    cy.webLoginAs('admin');
    cy.visit('/admin/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // Click the "➕ Create Tournament" tab button to switch to the create form
    cy.get('button.tab-btn').contains('Create Tournament').click();

    // Wait for the create tab to become visible
    cy.get('#tab-create').should('be.visible');

    cy.get('#tab-create').within(() => {
      // session_type_config select must have all three options
      cy.get('select[name="session_type_config"]').should('exist').within(() => {
        cy.get('option[value="on_site"]').should('exist');
        cy.get('option[value="virtual"]').should('exist');
        cy.get('option[value="hybrid"]').should('exist');
      });

      // Default is on_site — meeting_link row hidden
      cy.get('select[name="session_type_config"]').should('have.value', 'on_site');
      cy.get('#meeting_link_row').should('not.be.visible');

      // Select virtual → meeting_link row becomes visible
      cy.get('select[name="session_type_config"]').select('virtual');
      cy.get('#meeting_link_row').should('be.visible');

      // Select hybrid → still visible
      cy.get('select[name="session_type_config"]').select('hybrid');
      cy.get('#meeting_link_row').should('be.visible');

      // Back to on_site → hidden again
      cy.get('select[name="session_type_config"]').select('on_site');
      cy.get('#meeting_link_row').should('not.be.visible');
    });
  });

  // ── TVST-02 ────────────────────────────────────────────────────────────────
  // Admin edit page for a virtual tournament must have the meeting_link input
  // pre-filled with the stored URL.
  it('TVST-02: Admin edit page — virtual tournament meeting_link input pre-filled', () => {
    cy.webLoginAs('admin');
    // Navigate directly to the edit page using the resolved tournament id
    cy.visit(`/admin/tournaments/${tournamentId}/edit`);
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // meeting_link input must be pre-filled with the stored URL
    cy.get('#basic-meeting-link')
      .should('exist')
      .and('have.value', VIRTUAL_MEETING_LINK);

    // session_type_config must reflect 'virtual' (hidden input when sessions_generated=True)
    cy.get('#basic-session-type-config').should('have.value', 'virtual');
  });

  // ── TVST-03 ────────────────────────────────────────────────────────────────
  // Public event page for a virtual tournament must display the '💻 Online' chip.
  // The page is accessible without authentication.
  it('TVST-03: Public event page → "💻 Online" meta chip visible for virtual tournament', () => {
    // Public page — no login needed (unauthenticated access)
    cy.visit(`/events/${tournamentId}`);
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // The meta row must contain the '💻 Online' chip
    cy.contains('💻 Online').should('be.visible');
  });

  // ── TVST-04 ────────────────────────────────────────────────────────────────
  // Student sessions list — a virtual session with meeting_link set AND the student
  // enrolled via SemesterEnrollment must show a '💻 Join Meeting' button on the card.
  it('TVST-04: Student /sessions → "💻 Join Meeting" button visible for enrolled virtual session', () => {
    cy.webLoginAs('student');
    cy.visit('/sessions');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // The virtual session card must appear (student enrolled via SemesterEnrollment)
    cy.contains(SESSION_TITLE).should('be.visible');

    // The 'Join Meeting' link must be visible on the same card
    cy.contains(SESSION_TITLE)
      .closest('.card')
      .contains('a', 'Join Meeting')
      .should('be.visible')
      .and('have.attr', 'href', VIRTUAL_MEETING_LINK);
  });
});
