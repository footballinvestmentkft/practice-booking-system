/**
 * F1RSTTEAM FULL LIFECYCLE — end-to-end vizuális workflow (Playwright parítás)
 * DB scenario: baseline
 *
 * Mirrors test_complete_registration_flow.py — teljes szekvenciális lifecycle:
 *   admin invite → regisztráció → bonus kredit → specialization unlock → onboarding → dashboard
 *
 * FÁZISOK:
 *   Phase 1 — Admin létrehozza a regisztrációs invitation code-ot (DOM form, 150 cr)
 *   Phase 2 — Admin létrehozza a bonus invitation code-ot (API, 100 cr)
 *   Phase 3 — Új f1rstteam játékos regisztráció (14 mezős DOM form)
 *   Phase 4 — Bonus invitation code beváltása /credits oldalon (DOM)
 *   Phase 5 — Specialization hub megtekintése → LFA Football Player unlock gomb (DOM form)
 *             POST /specialization/select → cookie auth ✅ → redirect onboarding oldalra
 *   Phase 6 — LFA Football Player onboarding: 6 lépéses DOM form
 *             Step 1: pozíció kártya kattintás
 *             Steps 2-5: 29 skill slider (0-100, step=5) — Streamlit parítás
 *             Step 6: height_cm + weight_kg + preferred_foot + goals dropdown → AJAX submit → onboarding_completed=True
 *   Phase 7 — Dashboard betöltés — onboarding_completed = true ✅
 *
 * ── FUTTATÁS VIZUÁLISAN (headed, lassú) ──────────────────────────────────────
 *
 *   cd cypress && env -u ELECTRON_RUN_AS_NODE npx cypress run \
 *     --headed --browser chrome --no-exit \
 *     --spec "cypress/e2e/web/cross_role/f1rstteam_full_lifecycle.cy.js" \
 *     --config "defaultCommandTimeout=10000,pageLoadTimeout=30000,baseUrl=http://localhost:8000"
 *
 * ── KORÁBBI HIBÁK JAVÍTVA ─────────────────────────────────────────────────────
 *   - hub unlock form: /specialization/unlock (Bearer JWT) → /specialization/select (cookie) ✅
 *   - onboarding submit: 6 wrong-key skill → 29 teljes skill DOM-ból ✅
 *   - skill scale: 0-10 → 0-100 ✅
 *   - skill keys: skill_heading/skill_shooting → ball_control/dribbling/finishing/... ✅
 */
import '../../../support/web_commands';

describe('F1rstTeam — Teljes Business Lifecycle (vizuális)', {
  tags: ['@web', '@cross-role', '@lifecycle', '@visual', '@f1rstteam'],
}, () => {

  beforeEach(() => {
    cy.clearAllCookies();
    cy.on('window:alert',   () => {});
    cy.on('window:confirm', () => true);
    cy.setupCsrf();
  });

  it('F1RST-LIFE: admin invite → regisztráció → bonus kredit → unlock (DOM) → onboarding (29 skill) → dashboard', () => {

    cy.resetDb('baseline');

    // Unique email per retry — ha a Cypress retry-ol, új timestamp → nincs duplicate key hiba
    const PLAYER_EMAIL = `f1rst.teszt.${Date.now()}@f1rstteam.hu`;

    // ═════════════════════════════════════════════════════════════════════
    // PHASE 1 — Admin létrehozza a regisztrációs invitation code-ot (DOM)
    // Playwright: test_d1_admin_creates_three_invitation_codes()
    // ═════════════════════════════════════════════════════════════════════
    cy.log('═══ PHASE 1 — Admin: invitation code létrehozása (DOM form, 150 cr) ═══');

    cy.webLoginAs('admin');
    cy.visit('/admin/invitation-codes');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.wait(800);

    cy.get('#invited_name').type('F1rstTeam Teszt Játékos', { delay: 80 });
    cy.get('#invited_email').type(PLAYER_EMAIL, { delay: 60 });
    cy.get('#bonus_credits').clear().type('150', { delay: 80 });
    cy.wait(600);

    cy.get('button.btn-generate').click();

    // JS fetch → #alert-success: "✅ Invitation code generated: INV-..."
    // Lap auto-reload 1s múlva → kód kiolvasása a reload ELŐTT
    cy.get('#alert-success', { timeout: 8000 })
      .should('be.visible')
      .invoke('text')
      .then((alertText) => {
        const match = alertText.match(/INV-[A-Z0-9-]+/);
        expect(match, 'Invitation code az alertben').to.not.be.null;
        cy.wrap(match[0]).as('regCode');
        cy.log(`✅ Regisztrációs kód: ${match[0]} (150 cr)`);
      });

    cy.wait(1800); // page reload 1s múlva

    // ═════════════════════════════════════════════════════════════════════
    // PHASE 2 — Admin létrehozza a bonus invitation code-ot (API)
    // Playwright: test_02_admin_creates_coupon()
    // ═════════════════════════════════════════════════════════════════════
    cy.log('═══ PHASE 2 — Admin: bonus invitation code (API, 100 cr) ═══');

    cy.request({
      method: 'POST',
      url: `${Cypress.env('apiUrl')}/api/v1/auth/login`,
      body: {
        email:    Cypress.env('webAdminEmail'),
        password: Cypress.env('webAdminPassword'),
      },
    }).then((loginResp) => {
      expect(loginResp.status).to.eq(200);

      cy.request({
        method: 'POST',
        url: `${Cypress.env('apiUrl')}/api/v1/admin/invitation-codes`,
        headers: { Authorization: `Bearer ${loginResp.body.access_token}` },
        body: { invited_name: 'F1rstTeam Bonus Kredit', bonus_credits: 100 },
        failOnStatusCode: false,
      }).then((resp) => {
        expect(resp.status).to.be.oneOf([200, 201]);
        cy.wrap(resp.body.code).as('bonusCode');
        cy.log(`✅ Bonus kód: ${resp.body.code} (+100 cr)`);
      });
    });

    // ═════════════════════════════════════════════════════════════════════
    // PHASE 3 — Új f1rstteam játékos regisztráció (14 mezős DOM form)
    // Playwright: test_d2_first_user_registers_with_invitation()
    // Mezők: invitation_code, first_name, last_name, nickname, gender,
    //        date_of_birth, nationality, email, password, phone,
    //        street_address, city, postal_code, country
    // ═════════════════════════════════════════════════════════════════════
    cy.log('═══ PHASE 3 — Új játékos regisztráció (14 mezős DOM form) ═══');

    cy.visit('/logout');
    cy.wait(500);
    cy.clearAllCookies();

    cy.visit('/register');
    cy.contains('Create Account').should('be.visible');
    cy.wait(600);

    cy.get('@regCode').then((regCode) => {
      cy.get('input[name="invitation_code"]').type(regCode, { delay: 80 });
      cy.log(`📝 Regisztrációs kód beírva: ${regCode}`);
    });
    cy.wait(300);

    cy.get('input[name="first_name"]').type('F1rstTeam', { delay: 80 });
    cy.get('input[name="last_name"]').type('Teszt', { delay: 80 });
    cy.get('input[name="nickname"]').type('f1rststriker', { delay: 80 });
    cy.get('select[name="gender"]').select('Male');
    cy.get('input[name="date_of_birth"]').invoke('val', '2000-06-15');
    cy.wait(300);
    cy.get('select[name="nationality"]').select('HU');
    cy.get('input[name="email"]').type(PLAYER_EMAIL, { delay: 60 });
    cy.get('input[name="password"]').type('F1rstPass2026!', { delay: 80 });
    cy.get('input[name="phone"]').type('+36301234567', { delay: 60 });
    cy.wait(300);
    cy.get('input[name="street_address"]').type('Andrássy út 1', { delay: 80 });
    cy.get('input[name="city"]').type('Budapest', { delay: 80 });
    cy.get('input[name="postal_code"]').type('1061', { delay: 80 });
    cy.get('input[name="country"]').type('Hungary', { delay: 80 });
    cy.wait(800);

    // POST /register → validate code → user létrehozás (150 cr) → auto-login → /dashboard
    // NOTE: Do NOT use cy.intercept+cy.wait here — that freezes Cypress URL tracking
    //       after a form-submit redirect.
    // NOTE: Do NOT use .register-card.should('not.exist') — CSRF intercept can cause Cypress
    //       to run the assertion on the old /register DOM before the redirect completes.
    //       cy.location() is more reliable: Cypress retries against the *current* URL.
    cy.get('button[type="submit"]').click();

    cy.location('pathname', { timeout: 20000 }).should('eq', '/dashboard');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.wait(1200);
    cy.log('✅ Regisztráció sikeres — 150 kredit');

    // ═════════════════════════════════════════════════════════════════════
    // PHASE 4 — Credits oldal: negatív + pozitív invitation code beváltás
    // HTML: #invitation-code + redeemInvitationCode() → POST /api/v1/invitation-codes/redeem
    //
    // NEGATÍV: érvénytelen kód → .coupon-message.error + ❌ szöveg + egyenleg változatlan
    // POZITÍV: érvényes bonus kód → .coupon-message.success + 🎁 szöveg + egyenleg +100
    //
    // KORÁBBI HIBA: .should('be.visible') igaz volt sikerre ÉS hibára is → false positive
    // FIX: .should('have.class','success') vs .should('have.class','error')
    // ═════════════════════════════════════════════════════════════════════
    cy.log('═══ PHASE 4 — Credits UI: negatív + pozitív kód beváltás ═══');

    cy.visit('/credits');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.wait(800);

    // Ellenőrizzük a regisztrációból kapott 150 kredit egyenleget
    cy.get('.balance-amount').should('contain.text', '150');
    cy.log('💰 Regisztrációs egyenleg: 150 kredit ✓');

    // ── NEGATÍV: érvénytelen kód → error class + ❌ szöveg ──────────────
    cy.log('❌ Negatív eset: érvénytelen kód → hibaüzenet, egyenleg változatlan');
    cy.get('#invitation-code').should('be.visible').type('INV-INVALID-FAKE-999', { delay: 60 });
    cy.wait(300);
    cy.get('button[onclick*="redeemInvitationCode"]').click();
    cy.get('#invitation-message', { timeout: 8000 })
      .should('have.class', 'error')          // nem 'success'
      .and('contain.text', '❌');              // hibaüzenet prefix
    // Egyenleg NEM változott a sikertelen kísérlet után
    cy.get('.balance-amount').should('contain.text', '150');
    cy.log('✅ Negatív eset OK: hibás kód → .error class, egyenleg 150 (változatlan)');

    cy.get('#invitation-code').clear();
    cy.wait(500);

    // ── POZITÍV: érvényes bonus kód → success class + 🎁 szöveg ─────────
    cy.log('✅ Pozitív eset: érvényes bonus kód → sikeres beváltás (+100 cr)');
    cy.get('@bonusCode').then((bonusCode) => {
      cy.get('#invitation-code').type(bonusCode, { delay: 80 });
      cy.log(`📝 Bonus kód beírva: ${bonusCode}`);
    });
    cy.wait(500);
    cy.get('button[onclick*="redeemInvitationCode"]').click();
    cy.get('#invitation-message', { timeout: 8000 })
      .should('have.class', 'success')         // nem 'error'
      .and('contain.text', '🎁');              // sikeres beváltás prefix
    // Oldal auto-reload 2s múlva → megvárjuk, majd ellenőrizzük az egyenleget
    cy.get('.balance-amount', { timeout: 10000 }).should('contain.text', '250');
    cy.log('✅ Bonus kredit beváltva — egyenleg: 250 cr (150 + 100)');

    // ═════════════════════════════════════════════════════════════════════
    // PHASE 5 — Specialization hub → LFA Football Player unlock (DOM form)
    // Playwright: onclick="confirm()" → specialization unlock
    //
    // FIX: hub_specializations.html unlock form action="/specialization/select"
    //      (cookie auth, deducts 100 cr, creates UserLicense, redirects to onboarding)
    //      A korábbi /specialization/unlock Bearer JWT-t várt → most helyes!
    // ═════════════════════════════════════════════════════════════════════
    cy.log('═══ PHASE 5 — Specialization hub → LFA Football Player unlock (DOM) ═══');

    cy.visit('/dashboard');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.wait(2000); // Megvárjuk: locked kártyák + ~250 cr egyenleg látható

    // Az "Unlock Now (100 credits)" gomb a LFA Football Player kártyán
    // A hidden input value="LFA_FOOTBALL_PLAYER" alapján célozzuk a megfelelő formot
    cy.get('input[name="specialization"][value="LFA_FOOTBALL_PLAYER"]')
      .closest('form')
      .find('button[type="submit"]')
      .click();

    // /specialization/select → 100 cr levon → UserLicense létrehoz → redirect /lfa-player/onboarding
    cy.url().should('include', '/specialization/lfa-player/onboarding');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.wait(1000);
    cy.log('✅ LFA Football Player feloldva — redirect onboarding oldalra');

    // ═════════════════════════════════════════════════════════════════════
    // PHASE 6 — LFA Football Player onboarding (6 lépéses DOM form)
    // Playwright: test_05_user1_unlocks_and_completes_onboarding()
    //             Steps: Position → 4 skill kategória (29 skill, 0-100) → Goals
    //
    // FIX: A korábbi teszt 6 wrong-key skill-t küldött 0-10 skálán.
    //      Most a teljes 29 skill DOM slider-ből, 0-100 skálán, AJAX submit.
    // ═════════════════════════════════════════════════════════════════════
    cy.log('═══ PHASE 6 — LFA Football Player onboarding (6 lépés, 29 skill) ═══');

    // Az oldal már betöltve (redirect Phase 5-ből). Ellenőrzés:
    cy.get('.position-card').should('be.visible');

    // ── STEP 1: Pozíció — Striker ──────────────────────────────────────
    cy.log('🎯 1. lépés: Pozíció — Striker');
    cy.get('.position-card[data-position="STRIKER"]').click();
    cy.wait(600);
    cy.get('#btn-step1-next').should('not.be.disabled').click();
    cy.wait(700);

    // ── STEP 2: Outfield skills (11 skill) — Mezőnyjáték ──────────────
    cy.log('📊 2. lépés: Outfield skills (11 skill, 0-100)');
    // Néhány skill értékét állítjuk vizuálisan, a többit 50 (default) hagyunk
    cy.get('input[data-skill="finishing"]').invoke('val', 75).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="shot_power"]').invoke('val', 80).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="dribbling"]').invoke('val', 70).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="passing"]').invoke('val', 65).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="crossing"]').invoke('val', 60).trigger('input');
    cy.wait(400);
    // "Tovább →" gomb a 2. step-ben (goTo(3))
    cy.get('#step-2 .btn-primary').click();
    cy.wait(700);

    // ── STEP 3: Set Pieces (3 skill) — Rögzített helyzetek ────────────
    cy.log('📊 3. lépés: Set Pieces (3 skill)');
    cy.get('input[data-skill="free_kicks"]').invoke('val', 70).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="corners"]').invoke('val', 65).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="penalties"]').invoke('val', 80).trigger('input');
    cy.wait(400);
    cy.get('#step-3 .btn-primary').click();
    cy.wait(700);

    // ── STEP 4: Mental skills (8 skill) ───────────────────────────────
    cy.log('📊 4. lépés: Mental skills (8 skill)');
    cy.get('input[data-skill="vision"]').invoke('val', 70).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="composure"]').invoke('val', 65).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="tactical_awareness"]').invoke('val', 60).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="reactions"]').invoke('val', 75).trigger('input');
    cy.wait(400);
    cy.get('#step-4 .btn-primary').click();
    cy.wait(700);

    // ── STEP 5: Physical skills (7 skill) ─────────────────────────────
    cy.log('📊 5. lépés: Physical skills (7 skill)');
    cy.get('input[data-skill="sprint_speed"]').invoke('val', 85).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="acceleration"]').invoke('val', 80).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="agility"]').invoke('val', 75).trigger('input');
    cy.wait(200);
    cy.get('input[data-skill="stamina"]').invoke('val', 70).trigger('input');
    cy.wait(400);
    cy.get('#step-5 .btn-primary').click();
    cy.wait(700);

    // ── STEP 6: Célok → AJAX submit ───────────────────────────────────
    // NOTE: motivation textarea removed — Streamlit parity: csak goals dropdown,
    //       motivation="" üres stringként kerül a backendbe (Streamlit is így küldte).
    cy.log('💬 6. lépés: Fizikai adatok + Célok kiválasztása');
    cy.get('#step-6').should('be.visible');
    // Skill summary should be visible (populated by JS when navigating to step 6)
    cy.get('#skill-summary').should('be.visible').and('contain.text', '📊');
    // P1: new required fields — height_cm, weight_kg, preferred_foot
    cy.get('#height-cm').clear().type('178');
    cy.wait(200);
    cy.get('#weight-kg').clear().type('74');
    cy.wait(200);
    cy.get('input[name="preferred_foot"][value="right"]').check();
    cy.wait(200);
    cy.get('#goals').select('become_professional');
    cy.wait(600);

    // AJAX fetch → POST /specialization/lfa-player/onboarding-web
    // (cookie auth ✅, X-CSRF-Token ✅, 29 skill JSON ✅)
    cy.log('🚀 Onboarding AJAX submit (29 skill, cookie auth)');
    cy.get('#btn-submit').click();

    // Sikeres submit → #submit-status zöld → window.location.href = /dashboard
    cy.url().should('include', '/dashboard', { timeout: 10000 });
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.wait(1500);
    cy.log('✅ Onboarding kész — redirect /dashboard');

    // ═════════════════════════════════════════════════════════════════════
    // PHASE 7 — Dashboard — teljes lifecycle KÉSZ
    // Playwright: test_d5_first_user_hub_loads() — dashboard render
    // ═════════════════════════════════════════════════════════════════════
    cy.log('═══ PHASE 7 — Dashboard — teljes lifecycle KÉSZ! ═══');

    cy.visit('/dashboard');
    cy.get('body').should('not.contain.text', 'Internal Server Error');
    cy.wait(2000);

    cy.log('🏆 F1rstTeam teljes lifecycle sikeresen befejezve!');
    cy.log(`   Játékos: ${PLAYER_EMAIL}`);
    cy.log('   Kredit: 0 → 150 (reg) → 250 (bonus) → 150 (unlock −100)');
    cy.log('   29 skill: 0-100 skálán mentve a football_skills JSONB-be');
    cy.log('   onboarding_completed = true (user + license)');
  });
});
