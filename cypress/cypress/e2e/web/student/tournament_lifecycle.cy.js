/**
 * TOUR-S-01–05 — Student tournament lifecycle (DOM-driven)
 * DB scenario: tournament_e2e
 * Role coverage: student (LFA Football Player, 1000 credits, entry fee = 100)
 *
 * Playwright parity (test_tournament_playwright.py):
 *   - TOUR-S-01 ← step 9 (view available tournament list)
 *   - TOUR-S-02 ← step 3 (student enrollment via UI) + step 10 (credits deducted)
 *   - TOUR-S-03 ← step 9 (verify enrolled status in UI)
 *   - TOUR-S-04 ← step 10 (explicit pre-withdrawal balance confirmation)
 *   - TOUR-S-05 ← step 4 (withdrawal with 50% refund) + balance after refund
 *
 * Credit balance invariants (entry fee = 100, refund = 50%):
 *   Initial:           1000
 *   After enroll:       900  (−100)
 *   After withdraw:     950  (+50 = 50% refund)
 *
 * Tests run sequentially, sharing DB state:
 *   before()     → resetDb('tournament_e2e') — balance=1000, no enrollment
 *   beforeEach() → fresh cookie login (DB state persists between tests)
 *
 * Balance assertions use /credits page (.balance-amount element).
 * Each test documents both the BEFORE and AFTER state where applicable.
 */
import '../../../support/web_commands';

const ENTRY_FEE    = 100;
const REFUND       = 50;   // 50% of ENTRY_FEE
const INIT_BAL     = 1000;
const BAL_ENROLLED = INIT_BAL - ENTRY_FEE;    // 900
const BAL_FINAL    = BAL_ENROLLED + REFUND;    // 950

describe('Tournament Lifecycle — Student', {
  tags: ['@web', '@student', '@tournament'],
}, () => {

  before(() => {
    cy.resetDb('tournament_e2e');
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.on('window:alert', () => {});
    // cy.webLoginAs calls cy.webLogin which calls cy.setupCsrf() —
    // CSRF interceptors are active for all form POSTs within this test.
    cy.webLoginAs('student');
  });

  // ── TOUR-S-01 ────────────────────────────────────────────────────────────
  // Pre-flight: tournament list renders correctly AND initial credit balance is
  // exactly INIT_BAL (1000). Establishes the known-good starting state for the
  // enroll flow in TOUR-S-02.
  //
  // Balance check: before any action → must equal 1000.
  it(`TOUR-S-01: Initial state — tournament visible, initial balance = ${INIT_BAL}`, () => {
    // ── Credit balance: initial state ─────────────────────────────────────
    cy.visit('/credits');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.get('.balance-amount').should('be.visible').and('contain.text', String(INIT_BAL));

    // ── Tournament list ───────────────────────────────────────────────────
    cy.visit('/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    cy.contains('Events').should('be.visible');  // nav/title renamed A-group
    cy.get('[data-testid="tournament-card"]').should('have.length.at.least', 1);
    cy.contains('E2E Tournament').should('be.visible');
    cy.contains('Enrollment Open').should('be.visible');

    // Enroll button shows the entry fee cost
    cy.get('[data-testid="enroll-btn"]').should('be.visible')
      .and('contain.text', `${ENTRY_FEE} credits`);

    // No enrolled badge before any action
    cy.get('[data-testid="enrolled-badge"]').should('not.exist');
  });

  // ── TOUR-S-02 ────────────────────────────────────────────────────────────
  // Enrollment flow with explicit credit balance validation:
  //   before enroll: balance = INIT_BAL (1000)
  //   click Enroll button (form POST)
  //   after enroll:  balance = BAL_ENROLLED (900)
  //   deduction:     ENTRY_FEE (100) — confirmed by arithmetic, not just UI flash
  //
  // DB state after: SemesterEnrollment.request_status = APPROVED, is_active = True.
  it(`TOUR-S-02: Enroll — balance ${INIT_BAL} → ${BAL_ENROLLED} (−${ENTRY_FEE} entry fee)`, () => {
    // ── BEFORE: confirm balance = 1000 ────────────────────────────────────
    cy.visit('/credits');
    cy.get('.balance-amount').should('be.visible').and('contain.text', String(INIT_BAL));

    // ── ACTION: click Enroll ──────────────────────────────────────────────
    cy.visit('/tournaments');
    cy.get('[data-testid="enroll-btn"]').click();

    // 303 redirect → /tournaments?flash=Successfully+enrolled+...
    cy.url().should('include', '/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // Success flash and enrolled badge
    cy.get('.alert').should('be.visible').and('contain.text', 'enrolled');
    cy.get('[data-testid="enrolled-badge"]').should('be.visible');
    cy.get('[data-testid="unenroll-btn"]').should('be.visible');
    cy.get('[data-testid="enroll-btn"]').should('not.exist');

    // ── AFTER: confirm balance = 900 (deducted ENTRY_FEE = 100) ──────────
    cy.visit('/credits');
    cy.get('.balance-amount').should('be.visible').and('contain.text', String(BAL_ENROLLED));
  });

  // ── TOUR-S-03 ────────────────────────────────────────────────────────────
  // Re-visit /tournaments after enrollment — enrolled state persists across
  // page reloads. No balance change expected here.
  it('TOUR-S-03: Enrolled state persists after page reload', () => {
    cy.visit('/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    cy.get('[data-testid="enrolled-badge"]').should('be.visible');
    cy.get('[data-testid="unenroll-btn"]').should('be.visible');
    cy.get('[data-testid="enroll-btn"]').should('not.exist');
  });

  // ── TOUR-S-04 ────────────────────────────────────────────────────────────
  // Explicit pre-withdrawal balance confirmation.
  // Verifies that the deduction from TOUR-S-02 is persisted in the DB and
  // the balance is stable at BAL_ENROLLED (900) before the refund action.
  // This provides an independent checkpoint between the enroll and withdraw flows.
  it(`TOUR-S-04: Pre-withdrawal balance confirmed at ${BAL_ENROLLED} (deduction persisted)`, () => {
    cy.visit('/credits');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // Balance must be exactly BAL_ENROLLED — neither 1000 (unenrolled) nor 950 (refunded)
    cy.get('.balance-amount').should('be.visible').and('contain.text', String(BAL_ENROLLED));

    // Also verify no accidental second charge: balance does NOT contain INIT_BAL
    cy.get('.balance-amount').invoke('text').then((txt) => {
      const balance = parseInt(txt.replace(/[^0-9]/g, ''), 10);
      expect(balance).to.equal(BAL_ENROLLED,
        `Expected balance ${BAL_ENROLLED} (after −${ENTRY_FEE} entry fee), got ${balance}`);
    });
  });

  // ── TOUR-S-05 ────────────────────────────────────────────────────────────
  // Withdrawal flow with explicit credit balance validation:
  //   before withdraw: balance = BAL_ENROLLED (900)
  //   click Withdraw button (50% refund)
  //   after withdraw:  balance = BAL_FINAL (950)
  //   refund:          REFUND (50 = 50% of 100) — confirmed by arithmetic
  //
  // DB state after: enrollment is_active = False, request_status = WITHDRAWN.
  it(`TOUR-S-05: Withdraw — balance ${BAL_ENROLLED} → ${BAL_FINAL} (+${REFUND} = 50% refund)`, () => {
    // ── BEFORE: confirm balance = 900 ─────────────────────────────────────
    cy.visit('/credits');
    cy.get('.balance-amount').should('be.visible').and('contain.text', String(BAL_ENROLLED));

    // ── ACTION: click Withdraw ────────────────────────────────────────────
    cy.visit('/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    cy.on('window:confirm', () => true);
    cy.get('[data-testid="unenroll-btn"]').click();

    // 303 redirect → /tournaments?flash=Unenrolled.+50+credits+refunded.
    cy.url().should('include', '/tournaments');
    cy.get('body').should('not.contain.text', 'Internal Server Error');

    // Flash message mentions credits (refund)
    cy.get('.alert').should('be.visible').and('contain.text', 'credits');

    // Enroll button restored; enrolled badge gone
    cy.get('[data-testid="enroll-btn"]').should('be.visible');
    cy.get('[data-testid="enrolled-badge"]').should('not.exist');

    // ── AFTER: confirm balance = 950 (refund = 50 = 50% of ENTRY_FEE) ────
    cy.visit('/credits');
    cy.get('.balance-amount').should('be.visible').and('contain.text', String(BAL_FINAL));

    // Explicit arithmetic assertion: final = enrolled + refund
    cy.get('.balance-amount').invoke('text').then((txt) => {
      const balance = parseInt(txt.replace(/[^0-9]/g, ''), 10);
      expect(balance).to.equal(BAL_FINAL,
        `Expected balance ${BAL_FINAL} (${BAL_ENROLLED} + ${REFUND} refund), got ${balance}`);
      expect(balance - BAL_ENROLLED).to.equal(REFUND,
        `Refund should be exactly ${REFUND} (50% of ${ENTRY_FEE} entry fee)`);
    });
  });
});
