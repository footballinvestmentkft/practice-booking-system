const { defineConfig } = require('cypress');

module.exports = defineConfig({
  // ── Cypress Cloud Integration ──────────────────────────────────────────
  // Cypress Cloud Project ID (practice-booking-system-e2e)
  // See: docs/CYPRESS_CLOUD_SETUP.md for full setup instructions
  projectId: 'k5j9m2',

  e2e: {
    // ── Target application ──────────────────────────────────────────────────
    // FastAPI Jinja2 HTML frontend. Override with CYPRESS_BASE_URL env var.
    baseUrl: process.env.CYPRESS_BASE_URL || 'http://localhost:8000',

    // ── Test discovery ──────────────────────────────────────────────────────
    specPattern: 'cypress/e2e/**/*.cy.{js,jsx}',
    supportFile: 'cypress/support/e2e.js',
    fixturesFolder: 'cypress/fixtures',

    // ── Timeouts ────────────────────────────────────────────────────────────
    // Streamlit redraws the full React tree on every state change.
    // These generous timeouts prevent flaky failures during rerenders.
    defaultCommandTimeout:  15000,  // cy.get(), cy.contains()
    requestTimeout:         20000,  // cy.request(), cy.intercept()
    responseTimeout:        20000,
    pageLoadTimeout:        60000,  // full Streamlit boot can take ~5–10 s
    execTimeout:            60000,

    // ── Viewport ────────────────────────────────────────────────────────────
    viewportWidth:  1440,
    viewportHeight: 900,

    // ── Artifacts ───────────────────────────────────────────────────────────
    screenshotsFolder: 'cypress/screenshots',
    videosFolder:      'cypress/videos',
    video:             false,          // enable in CI: CYPRESS_video=true
    screenshotOnRunFailure: true,

    // ── Retries ─────────────────────────────────────────────────────────────
    retries: {
      runMode:  2,   // CI: retry failing tests twice
      openMode: 0,   // Interactive: no retries (see failures immediately)
    },

    // ── Environment variables ────────────────────────────────────────────────
    // Override any of these with CYPRESS_<KEY>=value or cypress.env.json.
    env: {
      // API base URL (FastAPI backend)
      apiUrl:           process.env.CYPRESS_API_URL     || 'http://localhost:8000',

      // Test user credentials (admin)
      adminEmail:       process.env.CYPRESS_ADMIN_EMAIL    || 'admin@lfa.com',
      adminPassword:    process.env.CYPRESS_ADMIN_PASSWORD || 'admin123',

      // Test user credentials (instructor)
      instructorEmail:  process.env.CYPRESS_INSTRUCTOR_EMAIL    || 'grandmaster@lfa.com',
      instructorPassword: process.env.CYPRESS_INSTRUCTOR_PASSWORD || 'TestInstructor2026',

      // Test user credentials (player / student)
      playerEmail:      process.env.CYPRESS_PLAYER_EMAIL    || 'rdias@manchestercity.com',
      playerPassword:   process.env.CYPRESS_PLAYER_PASSWORD || 'TestPlayer2026',

      // Flag to skip tests that require a live backend
      skipApiTests:     process.env.CYPRESS_SKIP_API_TESTS === 'true',

      // Streamlit URL — set to enable UI tests; absent = UI tests self-skip.
      // Smoke Suite sets CYPRESS_STREAMLIT_URL; Critical Specs (API-only) do not.
      streamlitUrl:     process.env.CYPRESS_STREAMLIT_URL || '',

      // @cypress/grep options — prevent non-matching tests from showing as "pending"
      // grepFilterSpecs:  entire spec files without matching tags are excluded upfront
      // grepOmitFiltered: tests that don't match the tag are omitted, not shown as pending
      grepFilterSpecs:  true,
      grepOmitFiltered: true,

      // ── Web E2E (FastAPI Jinja2) credentials ──────────────────────────────
      // Used by cypress/e2e/web/** specs (baseUrl=http://localhost:8000)
      // Accessible via Cypress.env('webAdminEmail') etc.
      webAdminEmail:       process.env.CYPRESS_webAdminEmail       || 'admin@lfa.com',
      webAdminPassword:    process.env.CYPRESS_webAdminPassword    || 'admin123',
      webInstructorEmail:  process.env.CYPRESS_webInstructorEmail  || 'grandmaster@lfa.com',
      webInstructorPassword: process.env.CYPRESS_webInstructorPassword || 'TestInstructor2026',
      webStudentEmail:     process.env.CYPRESS_webStudentEmail     || 'rdias@manchestercity.com',
      webStudentPassword:  process.env.CYPRESS_webStudentPassword  || 'TestPlayer2026',
      webFreshEmail:       process.env.CYPRESS_webFreshEmail       || 'fresh.e2e@lfa.com',
      webFreshPassword:    process.env.CYPRESS_webFreshPassword    || 'FreshE2E2026',
    },

    // ── Plugin setup ─────────────────────────────────────────────────────────
    setupNodeEvents(on, config) {
      // @cypress/grep — tag-based test filtering
      try {
        require('@cypress/grep/src/plugin')(config);
      } catch {}

      // cy.task("resetDb", scenario) — calls Python DB reset script
      // Used by web/e2e specs before each suite (baseUrl=http://localhost:8000)
      on('task', {
        resetDb(scenario = 'baseline') {
          const { execSync } = require('child_process');
          const cwd = require('path').resolve(__dirname, '..');
          execSync(
            `python scripts/reset_e2e_web_db.py --scenario ${scenario}`,
            { cwd, stdio: 'inherit' }
          );
          return null;
        },

        // Returns the DB id of a semester/tournament by code.
        // Used by TVST-03 to get the virtual tournament's id for the public event URL.
        getTournamentIdByCode(code) {
          const { execSync } = require('child_process');
          const cwd = require('path').resolve(__dirname, '..');
          const script = [
            'import sys; sys.path.insert(0, ".")',
            'from app.database import SessionLocal',
            'from app.models.semester import Semester',
            'db = SessionLocal()',
            `t = db.query(Semester).filter(Semester.code == "${code}").first()`,
            'db.close()',
            'print(t.id if t else "null")',
          ].join('; ');
          const out = execSync(`python -c '${script}'`, { cwd, encoding: 'utf8' }).trim();
          return out === 'null' ? null : parseInt(out, 10);
        },

        // Returns the DB id of the "E2E UI Quiz" seeded by reset_e2e_web_db.py
        getE2eQuizId() {
          const { execSync } = require('child_process');
          const cwd = require('path').resolve(__dirname, '..');
          const script = [
            'import sys; sys.path.insert(0, ".")',
            'from app.database import SessionLocal',
            'from app.models.quiz import Quiz',
            'db = SessionLocal()',
            'q = db.query(Quiz).filter(Quiz.title == "E2E UI Quiz").first()',
            'db.close()',
            'print(q.id if q else "null")',
          ].join('; ');
          const out = execSync(`python -c '${script}'`, { cwd, encoding: 'utf8' }).trim();
          return out === 'null' ? null : parseInt(out, 10);
        },

        // Returns {score, correct_answers, passed} for a QuizAttempt row by id.
        // Used by QUIZ-13 to verify the DB record matches the displayed result.
        getQuizAttemptData(attemptId) {
          const { execSync } = require('child_process');
          const cwd = require('path').resolve(__dirname, '..');
          const script = [
            'import sys, json; sys.path.insert(0, ".")',
            'from app.database import SessionLocal',
            'from app.models.quiz import QuizAttempt',
            'db = SessionLocal()',
            `a = db.query(QuizAttempt).filter(QuizAttempt.id == ${parseInt(attemptId, 10)}).first()`,
            'db.close()',
            'print(json.dumps({"score": a.score, "correct_answers": a.correct_answers, "passed": a.passed}) if a else "null")',
          ].join('; ');
          const out = execSync(`python -c '${script}'`, { cwd, encoding: 'utf8' }).trim();
          return out === 'null' ? null : JSON.parse(out);
        },
      });

      // cypress-mochawesome-reporter
      try {
        require('cypress-mochawesome-reporter/plugin')(on);
      } catch {}

      return config;
    },
  },

  // ── Mochawesome HTML report (web E2E coverage visualization) ────────────────
  reporter: 'cypress-mochawesome-reporter',
  reporterOptions: {
    reportDir:          'cypress/reports/html',
    charts:             true,
    reportPageTitle:    'Web E2E Coverage — FastAPI Jinja2',
    embeddedScreenshots: true,
    inlineAssets:       true,
    saveAllAttempts:    false,
  },
});
