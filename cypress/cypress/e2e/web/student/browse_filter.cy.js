/**
 * BF-CY — Browse Tournament Filter
 *
 * Verifies that ?status and ?delivery URL params drive server-side filtering
 * on /events/tournaments.
 *
 * Seed (browse_filter_e2e scenario):
 *   T1: ENROLLMENT_OPEN + on_site  → badge "🟢 Open"
 *   T2: IN_PROGRESS     + on_site  → badge "⏳ In Progress"
 *   T3: ENROLLMENT_OPEN + virtual  → badge "💻 Virtual" + "🟢 Open"
 */
import '../../../support/web_commands';

describe('Browse Tournament Filter — URL-driven status + delivery', {
  tags: ['@web', '@student', '@browse'],
}, () => {
  before(() => {
    cy.resetDb('browse_filter_e2e');
  });

  beforeEach(() => {
    cy.webLoginAs('student');
  });

  it('BF-CY-01: unfiltered page shows at least 2 tournament cards', () => {
    cy.visit('/events/tournaments');
    cy.get('.browse-card').should('have.length.gte', 2);
  });

  it('BF-CY-02: status=open shows only ENROLLMENT_OPEN cards (T1 + T3)', () => {
    cy.visit('/events/tournaments?status=open');
    cy.get('.browse-card').should('have.length', 2);
    cy.get('.browse-card').each(($card) => {
      cy.wrap($card).should('contain.text', 'Open');
    });
  });

  it('BF-CY-03: delivery=virtual shows only the virtual card (T3)', () => {
    cy.visit('/events/tournaments?delivery=virtual');
    cy.get('.browse-card').should('have.length', 1);
    cy.get('.browse-card').should('contain.text', 'Virtual');
  });

  it('BF-CY-04: combined status=open&delivery=virtual shows only T3', () => {
    cy.visit('/events/tournaments?status=open&delivery=virtual');
    cy.get('.browse-card').should('have.length', 1);
    cy.get('.browse-card')
      .should('contain.text', 'Open')
      .and('contain.text', 'Virtual');
  });
});
