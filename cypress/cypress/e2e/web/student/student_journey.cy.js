/**
 * Student Skill Progression — End-to-End Validation Suite
 *
 * Purpose: Prove that the skill progression system works correctly end-to-end.
 * This is a correctness test, not a coverage test.
 *
 * Suite structure:
 *   A. Core journey + multi-skill causal proof  [student_skill_history]
 *   B. Edge case: no tournaments → empty state  [tournament_e2e]
 *   C. Edge case: single tournament → EMA not distorted  [student_1tournament]
 *
 * Seeded EMA chain (student_skill_history scenario):
 *   Baseline: all 29 skills @ 70.0
 *   T1 (TOURN-E2E-HIST-1): student 2nd of 2 → skill dips  (negative delta)
 *   T2 (TOURN-E2E-HIST-2): student 1st of 2 → skill rises (positive delta)
 *   Skills verified: passing (-6.0/+7.2), ball_control (-5.1/+6.0), dribbling (-4.6/+5.3)
 *
 * Spec IDs:
 *   STU-JOURNEY-01–04   Core journey (dashboard, spec access, skills page, skills data)
 *   STU-JOURNEY-05      Visual: chart renders with data (not blank canvas)
 *   STU-JOURNEY-06      Causal DOM: table rows → name, placement badge, delta class
 *   STU-JOURNEY-07      Causal API: per-tournament delta sign matches placement rank
 *   STU-JOURNEY-MS      Multi-skill: same causal proof for ball_control + dribbling
 *   STU-JOURNEY-EC-01   Edge: no tournaments → empty state shown
 *   STU-JOURNEY-EC-02   Edge: single tournament → EMA value in valid range (no distortion)
 */
import '../../../support/web_commands';


// ─── A. CORE JOURNEY + MULTI-SKILL CAUSAL PROOF ──────────────────────────────

describe('A. Student Core Journey — Skill Progression', {
  tags: ['@web', '@student', '@journey'],
}, () => {

  before(() => {
    cy.resetDb('student_skill_history');
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.webLoginAs('student');
  });

  // ── STU-JOURNEY-01 ───────────────────────────────────────────────────────
  it('STU-JOURNEY-01: /dashboard renders student layout, no 500', () => {
    cy.visit('/dashboard');
    cy.url().should('include', '/dashboard');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.get('nav, .s-header, .student-header, .site-header').should('exist');
  });

  // ── STU-JOURNEY-02 ───────────────────────────────────────────────────────
  it('STU-JOURNEY-02: /dashboard/LFA_FOOTBALL_PLAYER accessible after onboarding', () => {
    cy.request({
      method: 'GET',
      url: '/dashboard/LFA_FOOTBALL_PLAYER',
      failOnStatusCode: false,
    }).then((resp) => {
      expect(resp.status).to.equal(200);
      expect(resp.body).to.not.include('Internal Server Error');
    });
  });

  // ── STU-JOURNEY-03 ───────────────────────────────────────────────────────
  it('STU-JOURNEY-03: /skills page loads — has_lfa_license=True, no 500', () => {
    cy.visit('/skills');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.get('h1, h2, .page-title, .s-header').should('exist');
  });

  // ── STU-JOURNEY-04 ───────────────────────────────────────────────────────
  it('STU-JOURNEY-04: GET /skills/data → 29 skills present, average_level > 0', () => {
    cy.request({ method: 'GET', url: '/skills/data', failOnStatusCode: false })
      .then((resp) => {
        expect(resp.status).to.equal(200);
        expect(Object.keys(resp.body.skills).length).to.be.at.least(29);
        expect(resp.body.average_level).to.be.greaterThan(0);
      });
  });

  // ── STU-JOURNEY-05 — Visual: chart renders with data (NOT blank canvas) ──
  it('STU-JOURNEY-05: /skills/history — Chart.js EMA chart renders with data, not empty canvas', () => {
    cy.visit('/skills/history?skill=passing');

    // Wait for async fetch to complete — #sh-content appears after data loads
    cy.get('#sh-content', { timeout: 15000 }).should('be.visible');

    // Empty state must NOT be shown (we have seeded tournament data)
    cy.get('#sh-empty').should('not.be.visible');

    // Chart wrapper must be visible (renderChart sets display:block when tl.length > 0)
    cy.get('#sh-chart-wrap').should('be.visible');

    // Chart.js only sets canvas dimensions after drawing — a blank canvas stays at 0×0
    cy.get('canvas#skill-chart').then(($canvas) => {
      expect($canvas[0].width, 'canvas width must be > 0 (Chart.js drew the chart)').to.be.greaterThan(0);
      expect($canvas[0].height, 'canvas height must be > 0').to.be.greaterThan(0);
    });

    // Stat cards must show real (non-placeholder) values
    cy.get('#sh-count').should('not.contain.text', '—').and('contain.text', '2');
    cy.get('#sh-baseline').should('not.contain.text', '—');

    // Net delta is positive (2nd→1st arc ends above baseline)
    cy.get('#sh-delta').should('have.class', 'sh-delta-pos');

    // Audit table is visible with exactly 2 rows
    cy.get('#sh-table-card').should('be.visible');
    cy.get('#sh-table-body tr').should('have.length', 2);
  });

  // ── STU-JOURNEY-06 — Causal DOM: name → placement badge → delta class ───
  it('STU-JOURNEY-06: Causal DOM proof — tournament name → placement badge → delta class per row', () => {
    cy.visit('/skills/history?skill=passing');
    cy.get('#sh-content', { timeout: 15000 }).should('be.visible');
    cy.get('#sh-table-body tr').should('have.length', 2);

    // Row 1: placed 2nd of 2 (last) → skill signal was LOW → delta NEGATIVE
    cy.get('#sh-table-body tr').eq(0).within(() => {
      cy.contains('E2E History Tournament 1').should('exist');
      cy.get('.sh-placement-badge.sh-p2').should('exist');      // 🥈 2nd place
      cy.get('td[class*="sh-delta"]').should('have.class', 'sh-delta-neg'); // negative delta
    });

    // Row 2: placed 1st of 2 (winner) → skill signal was HIGH → delta POSITIVE
    cy.get('#sh-table-body tr').eq(1).within(() => {
      cy.contains('E2E History Tournament 2').should('exist');
      cy.get('.sh-placement-badge.sh-p1').should('exist');      // 🥇 1st place
      cy.get('td[class*="sh-delta"]').should('have.class', 'sh-delta-pos'); // positive delta
    });
  });

  // ── STU-JOURNEY-07 — Causal API: delta sign matches placement rank ───────
  it('STU-JOURNEY-07: Causal API proof — delta sign matches placement, EMA compounds correctly', () => {
    cy.request({ method: 'GET', url: '/skills/history/data?skill=passing', failOnStatusCode: false })
      .then((resp) => {
        expect(resp.status).to.equal(200);
        const { timeline, baseline, current_level, total_delta } = resp.body;

        expect(timeline).to.have.length(2);

        const t1 = timeline[0];
        expect(t1.tournament_name).to.equal('E2E History Tournament 1');
        expect(t1.placement).to.equal(2);
        expect(t1.total_players).to.equal(2);
        expect(t1.delta_from_previous).to.be.lessThan(0,
          `T1: placed ${t1.placement}/${t1.total_players} (last) → expected negative delta, got ${t1.delta_from_previous}`);

        const t2 = timeline[1];
        expect(t2.tournament_name).to.equal('E2E History Tournament 2');
        expect(t2.placement).to.equal(1);
        expect(t2.total_players).to.equal(2);
        expect(t2.delta_from_previous).to.be.greaterThan(0,
          `T2: placed ${t2.placement}/${t2.total_players} (winner) → expected positive delta, got ${t2.delta_from_previous}`);

        // EMA compounds: winning after losing recovers and exceeds the loss
        expect(t2.skill_value_after).to.be.greaterThan(t1.skill_value_after,
          `EMA must rise after placing 1st: T2=${t2.skill_value_after} should exceed T1=${t1.skill_value_after}`);

        // Net positive: the 2nd→1st arc ends above baseline
        expect(total_delta).to.be.greaterThan(0,
          `Net delta after 2nd→1st arc should be positive (baseline=${baseline}, current=${current_level})`);
      });
  });

  // ── STU-JOURNEY-MS — Same causal proof across 3 skills ──────────────────
  // Verifies the EMA formula is skill-agnostic: different weights, same directional logic.
  ['ball_control', 'dribbling'].forEach((skill) => {
    it(`STU-JOURNEY-MS-${skill}: causal proof — placement direction is consistent for ${skill}`, () => {
      cy.request({ method: 'GET', url: `/skills/history/data?skill=${skill}`, failOnStatusCode: false })
        .then((resp) => {
          expect(resp.status).to.equal(200);
          const { timeline, total_delta } = resp.body;

          expect(timeline).to.have.length(2);

          const t1 = timeline[0];
          const t2 = timeline[1];

          // Last-place finish → delta negative (same direction regardless of skill weight)
          expect(t1.placement).to.equal(2);
          expect(t1.delta_from_previous).to.be.lessThan(0,
            `${skill} T1: placed last → delta should be negative, got ${t1.delta_from_previous}`);

          // Winner finish → delta positive
          expect(t2.placement).to.equal(1);
          expect(t2.delta_from_previous).to.be.greaterThan(0,
            `${skill} T2: placed 1st → delta should be positive, got ${t2.delta_from_previous}`);

          // Values compound correctly
          expect(t2.skill_value_after).to.be.greaterThan(t1.skill_value_after,
            `${skill}: EMA after winning should exceed EMA after losing`);

          // Net positive arc
          expect(total_delta).to.be.greaterThan(0,
            `${skill}: net delta after 2nd→1st arc should be positive`);
        });
    });
  });

  // ── STU-JOURNEY-08 — Spec dashboard structure: student nav + breadcrumb + sections present ──
  it('STU-JOURNEY-08: /dashboard/LFA_FOOTBALL_PLAYER — student nav present, breadcrumb correct, dashboard sections visible', () => {
    cy.visit('/dashboard/LFA_FOOTBALL_PLAYER');

    // Student nav must be rendered by student_base.html (not the old base.html header)
    cy.get('.student-header').should('exist');

    // Nav has LFA Player link — Hub is in footer, not header nav
    cy.get('.student-nav a[href="/dashboard/lfa-football-player"]').should('contain.text', 'LFA Player');
    cy.get('.student-nav a[href="/dashboard/lfa-football-player"]').should('have.class', 'active');
    cy.get('.student-nav a[href="/dashboard"]').should('not.exist');
    // Hub link is in footer
    cy.get('.footer-links a[href="/dashboard"]').should('exist');

    // Breadcrumb must contain the spec name — proves student is on spec dashboard, not hub
    cy.get('.s-breadcrumb').should('exist');
    cy.get('.s-breadcrumb').should('contain.text', 'LFA Football Player');
    cy.get('.s-breadcrumb').should('contain.text', 'Hub');

    // KPI row must be present with 4 cards
    cy.get('.s-kpi-row').should('exist');
    cy.get('.s-kpi-card').should('have.length', 4);

    // Skill Snapshot + Last Result sections visible
    cy.contains('h2', 'Skill Snapshot').should('be.visible');
    cy.contains('h2', 'Last Skill Event').should('be.visible');

    // 2×3 mod-nav: 6 primary domain cards must be present
    cy.get('.mod-nav-card[href="/events"]').should('exist');
    cy.get('.mod-nav-card[href="/my-cards"]').should('exist');
    cy.get('.mod-nav-card[href="/training"]').should('exist');
    cy.get('.mod-nav-card[href="/skills/history?skill=passing"]').should('exist');
    cy.get('.mod-nav-card[href="/calendar"]').should('exist');
    cy.get('.mod-nav-card[href="/achievements"]').should('exist');

    // Secondary nav in footer: Sessions / Progress / Skills accessible
    cy.get('a[href="/sessions"]').should('exist');
    cy.get('a[href="/progress"]').should('exist');
    cy.get('a[href="/skills"]').should('exist');

    // Page must not show any server error
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.get('body').should('not.contain.text', '400 Bad Request');
  });

  // ── STU-JOURNEY-09 — Dashboard KPI lazy-loads + snapshot bars + last result ──
  it('STU-JOURNEY-09: spec dashboard KPI row + skill snapshot + last result load', () => {
    cy.visit('/dashboard/LFA_FOOTBALL_PLAYER');

    // KPI avg should fill in from /skills/data (not stay as —)
    cy.get('#kpi-avg', { timeout: 10000 }).should('not.contain.text', '—');
    cy.get('#kpi-tournaments').should('contain.text', '2');

    // Skill snapshot renders at least 1 bar
    cy.get('#s-snapshot .s-snapshot-row', { timeout: 8000 }).should('have.length.gte', 1);

    // Last skill event shows the most recent event (T2: placed 1st, positive delta)
    cy.get('#s-last-result', { timeout: 8000 }).should('not.contain.text', 'No skill events yet');
    cy.get('#s-last-result').should('contain.text', 'E2E History Tournament 2');
    cy.get('#s-last-result .s-delta-pos').should('exist');
  });

});


// ─── B. EDGE CASE: NO TOURNAMENTS → EMPTY STATE ──────────────────────────────

describe('B. Edge Case: No Tournaments — Empty State', {
  tags: ['@web', '@student', '@journey', '@edge'],
}, () => {

  before(() => {
    // Student has LFA license + onboarding but zero tournament participations
    cy.resetDb('tournament_e2e');
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.webLoginAs('student');
  });

  it('STU-JOURNEY-EC-01a: GET /skills/history/data → empty timeline (not 404/500)', () => {
    cy.request({ method: 'GET', url: '/skills/history/data?skill=passing', failOnStatusCode: false })
      .then((resp) => {
        expect(resp.status).to.equal(200);
        expect(resp.body.timeline).to.have.length(0);
        expect(resp.body.total_delta).to.equal(0);
      });
  });

  it('STU-JOURNEY-EC-01b: /skills/history page shows empty state — chart NOT rendered', () => {
    cy.visit('/skills/history?skill=passing');

    // Content container must appear (data loaded, just empty)
    cy.get('#sh-content', { timeout: 15000 }).should('be.visible');

    // Empty state div must be VISIBLE
    cy.get('#sh-empty').should('be.visible');

    // Chart wrapper must be HIDDEN (no data → no chart)
    cy.get('#sh-chart-wrap').should('not.be.visible');

    // Table must be hidden
    cy.get('#sh-table-card').should('not.be.visible');

    // Tournament count stat shows 0
    cy.get('#sh-count').should('contain.text', '0');
  });

});


// ─── C. EDGE CASE: SINGLE TOURNAMENT → EMA NOT DISTORTED ─────────────────────

describe('C. Edge Case: Single Tournament — EMA Valid Range', {
  tags: ['@web', '@student', '@journey', '@edge'],
}, () => {

  before(() => {
    // Student has LFA license + exactly 1 completed tournament (placed 2nd of 2)
    cy.resetDb('student_1tournament');
  });

  beforeEach(() => {
    cy.clearAllCookies();
    cy.webLoginAs('student');
  });

  it('STU-JOURNEY-EC-02a: GET /skills/history/data → 1 entry, skill_value_after in valid range [40–99]', () => {
    cy.request({ method: 'GET', url: '/skills/history/data?skill=passing', failOnStatusCode: false })
      .then((resp) => {
        expect(resp.status).to.equal(200);
        const { timeline } = resp.body;

        // Exactly 1 entry — single tournament scenario
        expect(timeline).to.have.length(1);

        const entry = timeline[0];

        // EMA value must be within the defined skill range [40, 99]
        expect(entry.skill_value_after).to.be.at.least(40,
          `Single-tournament EMA must be ≥ MIN_SKILL_VALUE=40, got ${entry.skill_value_after}`);
        expect(entry.skill_value_after).to.be.at.most(99,
          `Single-tournament EMA must be ≤ MAX_SKILL_VALUE=99, got ${entry.skill_value_after}`);

        // Value must be finite (no NaN / Infinity from formula edge case)
        expect(Number.isFinite(entry.skill_value_after)).to.be.true;

        // delta_from_previous must also be finite
        expect(Number.isFinite(entry.delta_from_previous)).to.be.true;

        // Verify causal direction: placed 2nd of 2 (last) → skill dipped below baseline
        expect(entry.placement).to.equal(2);
        expect(entry.delta_from_previous).to.be.lessThan(0,
          `Placed last in a 2-player tournament → delta must be negative`);
      });
  });

  it('STU-JOURNEY-EC-02b: /skills/history page renders chart with 1 entry (not empty state)', () => {
    cy.visit('/skills/history?skill=passing');

    cy.get('#sh-content', { timeout: 15000 }).should('be.visible');

    // 1 tournament → chart must render (NOT empty state)
    cy.get('#sh-empty').should('not.be.visible');
    cy.get('#sh-chart-wrap').should('be.visible');

    // Canvas has dimensions (Chart.js drew the single-point line)
    cy.get('canvas#skill-chart').then(($canvas) => {
      expect($canvas[0].width).to.be.greaterThan(0);
    });

    // Count shows exactly 1
    cy.get('#sh-count').should('contain.text', '1');

    // Table shows 1 row
    cy.get('#sh-table-card').should('be.visible');
    cy.get('#sh-table-body tr').should('have.length', 1);

    // The one row shows 2nd place badge and negative delta
    cy.get('#sh-table-body tr').eq(0).within(() => {
      cy.get('.sh-placement-badge.sh-p2').should('exist');      // 🥈 2nd
      cy.get('td[class*="sh-delta"]').should('have.class', 'sh-delta-neg'); // placed last → negative
    });
  });

});
