'use strict';
/*
 * GazeConsent — camera permission and GDPR consent management for PV gaze validation.
 *
 * Phase 1 scope:
 *   - Pre-game consent modal display
 *   - getUserMedia wrapper with graceful denial handling
 *   - Camera stream lifecycle (start, stop, visibility, unload)
 *   - sessionStorage consent caching (per-session only)
 *
 * Nothing in this file sends any data to the server.
 * The camera stream stays local; only the consent decision
 * ('granted'|'denied'|'skip') is cached in sessionStorage.
 */
const GazeConsent = (() => {
    const CONSENT_KEY = 'pv_gaze_consent';
    let _stream      = null;
    let _hideTimer   = null;

    function _stop() {
        if (_stream) {
            _stream.getTracks().forEach(t => t.stop());
            _stream = null;
        }
        const video = document.getElementById('pv-gaze-video');
        if (video) { video.srcObject = null; }
    }

    async function request() {
        return new Promise(resolve => {
            const modal = document.getElementById('pv-gaze-consent-modal');
            if (!modal) {
                resolve('skip');
                return;
            }

            modal.removeAttribute('hidden');

            function onEnable() {
                modal.setAttribute('hidden', '');
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                    sessionStorage.setItem(CONSENT_KEY, 'denied');
                    resolve('denied');
                    return;
                }
                navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: 'user',
                        width:  { ideal: 640 },
                        height: { ideal: 480 },
                    },
                })
                .then(stream => {
                    _stream = stream;
                    const video = document.getElementById('pv-gaze-video');
                    if (video) {
                        video.srcObject = stream;
                        video.play().catch(() => {});
                    }
                    sessionStorage.setItem(CONSENT_KEY, 'granted');
                    resolve('granted');
                })
                .catch(() => {
                    sessionStorage.setItem(CONSENT_KEY, 'denied');
                    resolve('denied');
                });
            }

            function onSkip() {
                modal.setAttribute('hidden', '');
                sessionStorage.setItem(CONSENT_KEY, 'skip');
                resolve('skip');
            }

            const enableBtn = document.getElementById('pv-gaze-enable-btn');
            const skipBtn   = document.getElementById('pv-gaze-skip-btn');
            if (enableBtn) enableBtn.addEventListener('click', onEnable, { once: true });
            if (skipBtn)   skipBtn.addEventListener('click',   onSkip,   { once: true });
        });
    }

    function stopCamera() { _stop(); }

    function getStream() { return _stream; }

    // Stop camera when page unloads
    window.addEventListener('beforeunload', _stop);

    // Pause/stop on tab hide to respect camera LED expectations
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            _hideTimer = setTimeout(_stop, 30000);
        } else {
            clearTimeout(_hideTimer);
            _hideTimer = null;
        }
    });

    return { request, stopCamera, getStream };
})();
