/**
 * Calibration Center — P0 Cypress mock tests
 *
 * CC-E2E-01  GET /profile/calibration renders the page (auth + HTML check)
 * CC-E2E-02  Mock: insecure context → ccSecure shows FAIL, error visible
 * CC-E2E-03  Mock: camera blocked → ccCamera shows FAIL, error message shown
 * CC-E2E-04  Mock: 5 hand frames with confidence ≥ 0.60 → ccHand OK, pass shown
 * CC-E2E-05  Mock: no hand for 25 s → ccHand FAIL, no_hand error shown
 * CC-E2E-06  Profile page /profile contains Camera & Tracking card link
 *
 * Mock strategy:
 *   cy.window() stubs CalibCenter internals via _testHooks injected by the IIFE.
 *   For secure-context / camera / hand tests we stub at the browser API level
 *   (window.isSecureContext, navigator.mediaDevices, CalibCenter._testInject).
 *
 * All tests use the 'baseline' DB fixture (student user already enrolled).
 * No real camera or MediaPipe worker is needed — all mocked at JS level.
 */
import '../../../support/web_commands';

const CC_URL   = '/profile/calibration';
const PROF_URL = '/profile';

// ── CC-E2E-01 ─────────────────────────────────────────────────────────────────
describe('CC-E2E-01: Calibration Center page renders', {
  tags: ['@profile', '@calibration'],
}, () => {
  before(() => { cy.resetDb('baseline'); });
  beforeEach(() => { cy.clearAllCookies(); cy.webLoginAs('student'); });

  it('GET /profile/calibration → 200 with CalibCenter UI', () => {
    cy.request({ url: CC_URL, failOnStatusCode: false }).then((resp) => {
      expect(resp.status).to.equal(200);
      expect(resp.body).to.include('id="ccChecklist"');
      expect(resp.body).to.include('CalibCenter');
      expect(resp.body).to.include('id="ccSecure"');
      expect(resp.body).to.include('id="ccHand"');
      expect(resp.body).to.include('/static/js/calib-center.js');
    });
  });
});

// ── CC-E2E-02 ─────────────────────────────────────────────────────────────────
describe('CC-E2E-02: Insecure context → ccSecure FAIL', {
  tags: ['@profile', '@calibration'],
}, () => {
  before(() => { cy.resetDb('baseline'); });
  beforeEach(() => { cy.clearAllCookies(); cy.webLoginAs('student'); });

  it('window.isSecureContext=false → ccSecure shows FAIL, error box visible', () => {
    cy.visit(CC_URL);

    cy.window().then((win) => {
      // Override isSecureContext to false
      Object.defineProperty(win, 'isSecureContext', { value: false, writable: true });
    });

    cy.get('#ccBtnRun').click();

    cy.get('#ccSecure', { timeout: 3000 }).should('contain', 'FAIL');
    cy.get('#ccError').should('have.class', 'cc-err-visible');
    cy.get('#ccErrorTitle').should('contain', 'Secure connection required');
  });
});

// ── CC-E2E-03 ─────────────────────────────────────────────────────────────────
describe('CC-E2E-03: Camera blocked → ccCamera FAIL + error shown', {
  tags: ['@profile', '@calibration'],
}, () => {
  before(() => { cy.resetDb('baseline'); });
  beforeEach(() => { cy.clearAllCookies(); cy.webLoginAs('student'); });

  it('getUserMedia rejects with NotAllowedError → camera_blocked error shown', () => {
    cy.visit(CC_URL);

    cy.window().then((win) => {
      // Ensure secure context passes
      Object.defineProperty(win, 'isSecureContext', { value: true, writable: true });

      // Stub mediaDevices.getUserMedia to reject
      if (!win.navigator.mediaDevices) {
        Object.defineProperty(win.navigator, 'mediaDevices', {
          value: { getUserMedia: () => {} }, writable: true, configurable: true,
        });
      }
      cy.stub(win.navigator.mediaDevices, 'getUserMedia').rejects(
        Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' })
      );
    });

    cy.get('#ccBtnRun').click();

    cy.get('#ccCamera', { timeout: 5000 }).should('contain', 'FAIL');
    cy.get('#ccError').should('have.class', 'cc-err-visible');
    cy.get('#ccErrorTitle').should(($el) => {
      const text = $el.text();
      expect(text).to.satisfy(
        (t) => t.includes('Camera blocked') || t.includes('camera'),
        `Expected camera error title, got: "${text}"`
      );
    });
  });
});

// ── CC-E2E-04 ─────────────────────────────────────────────────────────────────
describe('CC-E2E-04: 5 hand frames → ccHand OK, pass shown', {
  tags: ['@profile', '@calibration'],
}, () => {
  before(() => { cy.resetDb('baseline'); });
  beforeEach(() => { cy.clearAllCookies(); cy.webLoginAs('student'); });

  it('inject 5 hand frames via _testInjectHands → ccHand OK, cc-pass-visible', () => {
    cy.visit(CC_URL);

    cy.window().then((win) => {
      // Stub entire pipeline: isSecureContext, getUserMedia (mock stream), Worker
      Object.defineProperty(win, 'isSecureContext', { value: true, writable: true });

      // Fake live MediaStream
      var fakeTrack = { readyState: 'live', stop: function() {} };
      var fakeStream = {
        getVideoTracks: function() { return [fakeTrack]; },
        getTracks: function() { return [fakeTrack]; },
      };
      if (!win.navigator.mediaDevices) {
        Object.defineProperty(win.navigator, 'mediaDevices', {
          value: { getUserMedia: () => {} }, writable: true, configurable: true,
        });
      }
      cy.stub(win.navigator.mediaDevices, 'getUserMedia').resolves(fakeStream);

      // Suppress Worker creation — inject frames directly via _qaFramesRcvd + callback
      var OrigWorker = win.Worker;
      win.Worker = function(url) {
        this.onmessage = null;
        this.onerror   = null;
        this.postMessage = function(msg) {
          if (msg.type === 'init') {
            var self = this;
            setTimeout(function() {
              if (self.onmessage) self.onmessage({ data: { type: 'ready', delegate: 'CPU' } });
            }, 50);
          }
          if (msg.type === 'frame' && msg.bitmap) {
            try { msg.bitmap.close(); } catch(_) {}
          }
        };
        this.onerror = null;
        // store reference so test can send hand frames
        win._calibTestWorker = this;
      };
    });

    cy.get('#ccBtnRun').click();

    // Wait for model OK (worker sends 'ready' after 50ms)
    cy.get('#ccModel', { timeout: 5000 }).should('contain', 'OK');

    // Advance frame counter past FRAME_LOOP_MIN (10) so frame-loop check passes
    cy.window().then((win) => {
      win._qaFramesRcvd_calib = 15; // internal variable exposed via CalibCenter
      // Patch CalibCenter internal _framesRcvd — simulate via direct counter increment
      // CalibCenter polls _framesRcvd; we can't easily reach inside the closure,
      // so instead drive via the worker onmessage callback directly.
      if (win._calibTestWorker && win._calibTestWorker.onmessage) {
        // Send 15 no_hands messages to pass frame loop check
        for (var i = 0; i < 15; i++) {
          win._calibTestWorker.onmessage({
            data: { type: 'no_hands' },
          });
        }
        // Then send 5 hands messages to pass hand check
        for (var j = 0; j < 5; j++) {
          win._calibTestWorker.onmessage({
            data: {
              type: 'hands',
              hands: [{ side: 'Left', confidence: 0.95, wrist: { x: 0.5, y: 0.3, z: 0 } }],
            },
          });
        }
      }
    });

    cy.get('#ccHand', { timeout: 6000 }).should('contain', 'OK');
    cy.get('#ccPass', { timeout: 2000 }).should('have.class', 'cc-pass-visible');
  });
});

// ── CC-E2E-05 ─────────────────────────────────────────────────────────────────
describe('CC-E2E-05: No hand detected timeout → FAIL + instruction shown', {
  tags: ['@profile', '@calibration'],
}, () => {
  before(() => { cy.resetDb('baseline'); });
  beforeEach(() => { cy.clearAllCookies(); cy.webLoginAs('student'); });

  it('no hand frames for 25 s → ccHand FAIL, no_hand error shown', () => {
    cy.visit(CC_URL);
    cy.clock();  // fake timers

    cy.window().then((win) => {
      Object.defineProperty(win, 'isSecureContext', { value: true, writable: true });

      var fakeTrack  = { readyState: 'live', stop: function() {} };
      var fakeStream = {
        getVideoTracks: function() { return [fakeTrack]; },
        getTracks:      function() { return [fakeTrack]; },
      };
      if (!win.navigator.mediaDevices) {
        Object.defineProperty(win.navigator, 'mediaDevices', {
          value: { getUserMedia: () => {} }, writable: true, configurable: true,
        });
      }
      cy.stub(win.navigator.mediaDevices, 'getUserMedia').resolves(fakeStream);

      win.Worker = function() {
        this.onmessage = null;
        this.postMessage = function(msg) {
          if (msg.type === 'init') {
            var self = this;
            setTimeout(function() {
              if (self.onmessage) {
                // Send model ready + 15 no_hands to pass frame loop
                self.onmessage({ data: { type: 'ready', delegate: 'CPU' } });
                for (var i = 0; i < 15; i++) {
                  self.onmessage({ data: { type: 'no_hands' } });
                }
              }
            }, 50);
          }
          if (msg.type === 'frame' && msg.bitmap) {
            try { msg.bitmap.close(); } catch(_) {}
          }
        };
      };
    });

    cy.get('#ccBtnRun').click();
    cy.get('#ccModel', { timeout: 5000 }).should('contain', 'OK');

    // Advance fake clock past HAND_TIMEOUT_MS (25 000 ms)
    cy.tick(26000);

    cy.get('#ccHand', { timeout: 3000 }).should('contain', 'FAIL');
    cy.get('#ccError').should('have.class', 'cc-err-visible');
    cy.get('#ccErrorTitle').should('contain', 'No hand detected');
  });
});

// ── CC-E2E-06 ─────────────────────────────────────────────────────────────────
describe('CC-E2E-06: Profile page contains calibration card', {
  tags: ['@profile', '@calibration'],
}, () => {
  before(() => { cy.resetDb('baseline'); });
  beforeEach(() => { cy.clearAllCookies(); cy.webLoginAs('student'); });

  it('/profile contains Camera & Tracking link to /profile/calibration', () => {
    cy.request({ url: PROF_URL, failOnStatusCode: false }).then((resp) => {
      expect(resp.status).to.equal(200);
      expect(resp.body).to.include('/profile/calibration');
      expect(resp.body).to.include('Camera');
    });
  });
});
