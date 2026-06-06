'use strict';
/**
 * calib-center.js — Calibration Center P0 IIFE
 *
 * Self-contained diagnostic module for /profile/calibration.
 * No dependency on gaze-consent.js or any game-specific JS.
 * No data is sent to the server — all checks are local.
 *
 * Public API:
 *   CalibCenter.start()   — begin calibration run
 *   CalibCenter.retry()   — reset state and re-run
 *   CalibCenter.stop()    — stop camera + worker, return to idle
 *   CalibCenter.pause()   — pause frame loop (visibilitychange)
 *   CalibCenter.resume()  — resume frame loop (visibilitychange)
 *
 * DOM IDs (all on profile_calibration_center.html):
 *   ccBtnRun, ccBtnRetry, ccBtnStop
 *   ccIntro, ccCameraWrap, ccChecklist, ccHint
 *   ccPass, ccPassDesc, ccError, ccErrorTitle, ccErrorDesc
 *   ccFpsWarn, ccActions
 *   ccSecure, ccCamera, ccStream, ccModel, ccFrames, ccCanvas, ccHand
 *   cc-video (video element), cc-canvas (canvas element)
 */
var CalibCenter = (function () {

    // ── Diagnostic helpers (DIAG — remove after iPhone QA) ───────────────────
    // Logs go to both browser console AND the on-screen debug panel (ccDbgAdd).
    // ccDbgAdd is defined in the page's inline <script> (profile_calibration_center.html).
    function _D(msg) {
        var line = '[CALIB_MAIN] ' + msg;
        console.log(line);
        if (typeof window.ccDbgAdd === 'function') window.ccDbgAdd(line, 'info');
    }
    function _Derr(msg, err) {
        var detail = err ? (err.name + ': ' + (err.message || String(err))) : '(no error)';
        var line   = '[CALIB_MAIN] ' + msg + ' — ' + detail;
        console.error(line);
        if (typeof window.ccDbgAdd === 'function') window.ccDbgAdd(line, 'error');
    }

    // ── Runtime patch fingerprint (TEMP diagnostic) ──────────────────────────
    // Fetches the WASM loader JS with a timestamp param so Safari cannot serve
    // a cached copy. Confirms the null-guard patch is present at runtime and
    // logs the actual Cache-Control header the server sends.
    function _mpVerifyLoader(variant, url) {
        var bustUrl = url + '?_t=' + Date.now();
        var prefix  = '[MP_VERIFY] ' + variant + ':';
        fetch(bustUrl, { cache: 'no-store' })
            .then(function (resp) {
                var cc = resp.headers.get('cache-control') || '(none)';
                var status = resp.status;
                _D(prefix + ' HTTP ' + status + ' cache-control=' + cc);
                return resp.text();
            })
            .then(function (txt) {
                var hasGuard   = txt.indexOf('if(!t)return-3;HEAP32') !== -1;
                var hasUnguard = txt.indexOf('getContextAttributes();HEAP32') !== -1;
                _D(prefix + ' null-guard present=' + hasGuard
                    + ' unguarded-access=' + hasUnguard
                    + ' size=' + txt.length + 'B');
                if (!hasGuard) {
                    _Derr(prefix + ' PATCH MISSING — iPhone loading unpatched file!', null);
                }
            })
            .catch(function (err) {
                _Derr(prefix + ' fetch failed', err);
            });
    }

    // ── Config ───────────────────────────────────────────────────────────────
    var MIN_HAND_CONF    = 0.60;
    var HAND_FRAMES_NEED = 5;      // consecutive frames with hand required
    var FRAME_LOOP_MIN   = 10;     // frames received before loop confirmed
    var LOOP_TIMEOUT_MS  = 8000;
    var HAND_TIMEOUT_MS  = 25000;
    var FPS_SLOW         = 4.0;

    // iOS requires extra time: CPU-only WASM JIT cold-start (9 MB binary) + model
    // loading takes 30–45 s on iPhone without the GPU delegate warming up the JIT.
    // Desktop keeps the standard 25 s timeout.
    var _iosDevice       = /iP(hone|ad|od)/.test(navigator.userAgent || '');
    var MODEL_TIMEOUT_MS = _iosDevice ? 50000 : 25000;

    // ── State ────────────────────────────────────────────────────────────────
    var _running     = false;
    var _paused      = false;
    var _stream      = null;
    var _worker      = null;
    var _modelReady  = false;
    var _framesRcvd  = 0;
    var _handConsec  = 0;
    var _rafId       = null;
    var _loopTimer   = null;
    var _handTimer   = null;
    var _modelTimer  = null;
    var _fpsSamples  = [];
    var _effectiveFPS = null;

    // ── Error messages ───────────────────────────────────────────────────────
    var _ERRORS = {
        not_secure: {
            title: 'Secure connection required',
            desc:  'Camera access requires HTTPS. Open this page via the https:// link.',
        },
        no_media_api: {
            title: 'Browser not supported',
            desc:  'This browser does not support camera access. Try Safari 15+ on iOS or Chrome on Android.',
        },
        camera_blocked: {
            title: 'Camera blocked',
            desc:  'Camera permission was denied. On iOS: Settings → Safari → Camera → Allow, then tap Retry.',
        },
        camera_not_found: {
            title: 'No camera found',
            desc:  'No camera hardware was detected on this device.',
        },
        camera_in_use: {
            title: 'Camera in use',
            desc:  'Another app is using the camera. Close other apps and tap Retry.',
        },
        model_failed: {
            title: 'Hand-tracking model failed to load',
            desc:  'The AI model could not be initialised. Check your internet connection and tap Retry.',
        },
        model_crashed: {
            title: 'Hand tracking stopped unexpectedly',
            desc:  'The hand-tracking model crashed during operation. Reload the page and try again.',
        },
        no_frames: {
            title: 'Video not streaming',
            desc:  'The camera started but no frames arrived. Try rotating the device, then tap Retry.',
        },
        canvas_zero: {
            title: 'Display error',
            desc:  'The camera overlay has zero size. Try rotating the screen, then tap Retry.',
        },
        no_hand: {
            title: 'No hand detected',
            desc:  'Hold your open hand, palm facing forward, in the upper half of the camera view.',
        },
    };

    // ── DOM helpers ──────────────────────────────────────────────────────────
    function _el(id) { return document.getElementById(id); }

    function _setStatus(id, text, cls) {
        var el = _el(id);
        if (!el) return;
        el.textContent = text;
        el.className   = 'cc-status ' + (cls || 'cc-s-pending');
    }

    function _showError(code) {
        var m   = _ERRORS[code] || _ERRORS.model_failed;
        var t   = _el('ccErrorTitle');
        var d   = _el('ccErrorDesc');
        var box = _el('ccError');
        if (t) t.textContent = m.title;
        if (d) d.textContent = m.desc;
        if (box) box.className = 'cc-error cc-err-visible';
        // Show Retry button
        var r = _el('ccBtnRetry');
        if (r) r.className = 'cc-btn-retry cc-retry-visible';
    }

    function _hideError() {
        var box = _el('ccError');
        if (box) box.className = 'cc-error';
        var r = _el('ccBtnRetry');
        if (r) r.className = 'cc-btn-retry';
    }

    function _showHint(visible) {
        var h = _el('ccHint');
        if (h) h.className = 'cc-hint' + (visible ? ' cc-hint-visible' : '');
    }

    function _showPass(fpsWarn) {
        var p = _el('ccPass');
        if (p) p.className = 'cc-pass cc-pass-visible';
        if (fpsWarn) {
            var w = _el('ccFpsWarn');
            if (w) w.className = 'cc-fps-warn cc-fps-visible';
        }
        // Hide run button, show stop (cleanup)
        var run = _el('ccBtnRun');
        if (run) run.style.display = 'none';
        var stop = _el('ccBtnStop');
        if (stop) stop.className = 'cc-btn-stop cc-stop-visible';
    }

    function _resetChecklist() {
        ['ccSecure','ccCamera','ccStream','ccModel',
         'ccFrames','ccCanvas','ccHand'].forEach(function (id) {
            _setStatus(id, '—', 'cc-s-pending');
        });
        _hideError();
        _showHint(false);
        var p = _el('ccPass');
        if (p) p.className = 'cc-pass';
        var w = _el('ccFpsWarn');
        if (w) w.className = 'cc-fps-warn';
    }

    // ── Camera helpers ───────────────────────────────────────────────────────
    function _stopCamera() {
        if (_stream) {
            _stream.getTracks().forEach(function (t) { t.stop(); });
            _stream = null;
        }
        var vid = _el('cc-video');
        if (vid) vid.srcObject = null;
    }

    function _stopWorker() {
        if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
        _mainInFlight = false;
        if (_worker) {
            try { _worker.postMessage({ type: 'stop' }); } catch (_) {}
            _worker.onmessage = null;
            _worker.onerror   = null;
            _worker = null;
        }
        _modelReady = false;
        _framesRcvd = 0;
        _fpsSamples = [];
        _effectiveFPS = null;
    }

    function _clearTimers() {
        if (_loopTimer) { clearTimeout(_loopTimer);  _loopTimer = null; }
        if (_handTimer) { clearTimeout(_handTimer);  _handTimer = null; }
        if (_modelTimer){ clearTimeout(_modelTimer); _modelTimer = null; }
    }

    function _activateCamera(videoEl) {
        var wrap = _el('ccCameraWrap');
        if (!wrap || !videoEl) return;
        // Move video into the wrap (it starts hidden in the body)
        wrap.insertBefore(videoEl, wrap.firstChild);
        videoEl.style.cssText  = '';
        videoEl.style.position = 'absolute';
        videoEl.style.inset    = '0';
        videoEl.style.width    = '100%';
        videoEl.style.height   = '100%';
        videoEl.style.objectFit = 'cover';
        videoEl.style.transform = 'scaleX(-1)';
        // Sync canvas size
        videoEl.addEventListener('loadedmetadata', _syncCanvas, { once: true });
        _syncCanvas();
    }

    function _syncCanvas() {
        var canvas = _el('cc-canvas');
        var wrap   = _el('ccCameraWrap');
        if (!canvas || !wrap) return;
        var r = wrap.getBoundingClientRect();
        if (r.width > 0) {
            canvas.width  = Math.round(r.width);
            canvas.height = Math.round(r.height);
        }
    }

    // ── Canvas overlay — wrist dot ───────────────────────────────────────────
    function _drawWrists(hands) {
        var canvas = _el('cc-canvas');
        if (!canvas) return;
        _syncCanvas();
        var ctx = canvas.getContext('2d');
        var W   = canvas.width;
        var H   = canvas.height;
        ctx.clearRect(0, 0, W, H);
        if (!W || !H) return;

        hands.forEach(function (h) {
            if (h.confidence < MIN_HAND_CONF) return;
            var cx = (1 - h.wrist.x) * W;   // mirror: video CSS scaleX(-1)
            var cy = h.wrist.y * H;
            var isLeft = h.side === 'Right'; // MediaPipe 'Right' → user's left (selfie)
            var color  = isLeft ? '#4f46e5' : '#059669';

            ctx.beginPath();
            ctx.arc(cx, cy, 18, 0, Math.PI * 2);
            ctx.fillStyle = color + '44';
            ctx.fill();

            ctx.beginPath();
            ctx.arc(cx, cy, 12, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
        });
    }

    // ── Frame loop ───────────────────────────────────────────────────────────
    var _mainInFlight = false;  // one createImageBitmap in-flight at a time
    var _framesSent   = 0;      // diagnostic counter

    function _startFrameLoop(videoEl) {
        _D('_startFrameLoop started');
        function loop() {
            _rafId = requestAnimationFrame(loop);
            if (_paused) return;
            if (!videoEl || !videoEl.srcObject || !_modelReady) return;
            // Guard: skip if a previous bitmap is still being created.
            // Prevents bitmap back-pressure on slow devices (iPhone) and ensures
            // the timestamp passed to detectForVideo is always fresh and monotonic.
            if (_mainInFlight) return;
            _mainInFlight = true;
            try {
                createImageBitmap(videoEl).then(function (bmp) {
                    _mainInFlight = false;
                    _framesSent++;
                    if (_framesSent === 1) {
                        _D('first bitmap created and posted to worker');
                    }
                    if (_worker) {
                        // Use performance.now() at bitmap-ready time, not the stale
                        // RAF ts captured before the async createImageBitmap call.
                        // detectForVideo requires strictly increasing timestamps (VIDEO mode).
                        _worker.postMessage(
                            { type: 'frame', bitmap: bmp, timestamp: performance.now() },
                            [bmp]
                        );
                    } else {
                        bmp.close();
                    }
                }).catch(function (err) {
                    _mainInFlight = false;
                    if (_framesSent === 0) {
                        _Derr('createImageBitmap FAILED on first frame', err);
                    }
                });
            } catch (ex) {
                _mainInFlight = false;
                if (_framesSent === 0) { _Derr('createImageBitmap THREW', ex); }
            }
        }
        _rafId = requestAnimationFrame(loop);
    }

    // ── Checks ───────────────────────────────────────────────────────────────
    function _checkSecureContext() {
        if (!window.isSecureContext) {
            _setStatus('ccSecure', 'FAIL', 'cc-s-fail');
            _showError('not_secure');
            return false;
        }
        _setStatus('ccSecure', 'OK', 'cc-s-ok');
        return true;
    }

    function _checkMediaDevices() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            _setStatus('ccCamera', 'FAIL', 'cc-s-fail');
            _showError('no_media_api');
            return false;
        }
        return true;
    }

    // ── Main run sequence ────────────────────────────────────────────────────
    function _run() {
        if (!_checkSecureContext()) { _running = false; return; }
        if (!_checkMediaDevices()) { _running = false; return; }

        _setStatus('ccCamera', 'Checking…', 'cc-s-checking');

        // Show checklist and camera wrap
        var cl = _el('ccChecklist');
        if (cl) cl.style.display = '';
        var cw = _el('ccCameraWrap');
        if (cw) cw.className = 'cc-camera-wrap cc-cam-active';

        // Step 3: request camera
        var constraints = { video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } } };
        navigator.mediaDevices.getUserMedia(constraints)
            .catch(function (err) {
                // iOS OverconstrainedError fallback
                if (err.name === 'OverconstrainedError' ||
                    err.name === 'ConstraintNotSatisfiedError') {
                    return navigator.mediaDevices.getUserMedia({ video: true });
                }
                throw err;
            })
            .then(function (stream) {
                _stream = stream;
                var tracks = stream.getVideoTracks();
                if (!tracks.length || tracks[0].readyState !== 'live') {
                    _setStatus('ccCamera', 'FAIL', 'cc-s-fail');
                    _setStatus('ccStream',  'FAIL', 'cc-s-fail');
                    _showError('camera_in_use');
                    _running = false;
                    return;
                }
                _setStatus('ccCamera', 'OK', 'cc-s-ok');
                _setStatus('ccStream',  'OK', 'cc-s-ok');

                // Attach stream to video element
                var vid = _el('cc-video');
                if (vid) {
                    vid.srcObject = stream;
                    // Hide placeholder
                    var ph = _el('ccCamPlaceholder');
                    if (ph) ph.style.display = 'none';
                    _activateCamera(vid);
                }

                _startWorkerPhase(vid);
            })
            .catch(function (err) {
                var code = 'camera_blocked';
                if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
                    code = 'camera_not_found';
                } else if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
                    code = 'camera_in_use';
                }
                _setStatus('ccCamera', 'FAIL', 'cc-s-fail');
                _showError(code);
                _running = false;
            });
    }

    function _startWorkerPhase(videoEl) {
        // On iOS, WASM JIT cold-start without GPU warm-up takes 30–45 s.
        // Show a longer status label so the user knows it has not frozen.
        var modelLoadLabel = _iosDevice
            ? 'Loading… (iPhone: up to 50 s)'
            : 'Loading…';
        _setStatus('ccModel', modelLoadLabel, 'cc-s-checking');
        _D('_startWorkerPhase — creating Worker');
        _D('env: createImageBitmap=' + (typeof createImageBitmap !== 'undefined')
            + ' WebAssembly=' + (typeof WebAssembly !== 'undefined')
            + ' crossOriginIsolated=' + (typeof crossOriginIsolated !== 'undefined'
                ? crossOriginIsolated : 'N/A'));

        try {
            _worker = new Worker('/static/js/vt/calib-hand-worker.js');
            _D('Worker created OK');
        } catch (err) {
            _Derr('new Worker() THREW', err);
            _setStatus('ccModel', 'FAIL', 'cc-s-fail');
            _showError('model_failed');
            _running = false;
            return;
        }

        _worker.onmessage = function (e) {
            var msg = e.data;
            if (!msg) return;

            if (msg.type === 'ready') {
                _D('worker → ready — delegate=' + msg.delegate
                    + ' elapsed=' + (performance.now() | 0) + 'ms');
                clearTimeout(_modelTimer); _modelTimer = null;
                _modelReady = true;
                _setStatus('ccModel', 'OK', 'cc-s-ok');
                _startFramePhase(videoEl);

            } else if (msg.type === 'error') {
                _D('worker → error — code=' + msg.code + ' msg=' + msg.message
                    + ' _modelReady=' + _modelReady);
                clearTimeout(_modelTimer); _modelTimer = null;
                _setStatus('ccModel', 'FAIL', 'cc-s-fail');
                _showError('model_failed');
                _running = false;

            } else if (msg.type === 'log') {
                // Worker relays its diagnostic logs to the main thread so they
                // appear in the on-screen debug panel (no Remote Inspector needed).
                var workerLine = msg.msg || '';
                if (msg.level === 'error') {
                    console.error(workerLine);
                } else {
                    console.log(workerLine);
                }
                if (typeof window.ccDbgAdd === 'function') {
                    window.ccDbgAdd(workerLine, msg.level || 'info');
                }

            } else if (msg.type === 'hands') {
                if (_framesRcvd === 0) {
                    _D('first hands message received — hands=' + (msg.hands || []).length);
                }
                _framesRcvd++;
                _onHands(msg.hands);

            } else if (msg.type === 'no_hands') {
                if (_framesRcvd === 0) { _D('first no_hands message received'); }
                _framesRcvd++;
                _onHands([]);
            }
        };

        _worker.onerror = function (ev) {
            _D('worker.onerror fired — _modelReady=' + _modelReady
                + ' msg=' + (ev && ev.message ? ev.message : 'none')
                + ' filename=' + (ev && ev.filename ? ev.filename : '?')
                + ' lineno=' + (ev && ev.lineno ? ev.lineno : '?'));
            clearTimeout(_modelTimer); _modelTimer = null;
            _setStatus('ccModel', 'FAIL', 'cc-s-fail');
            // Distinguish init failure from post-ready crash:
            // model_failed  = worker never sent 'ready' (init/WASM error)
            // model_crashed = worker sent 'ready', then crashed during frame processing
            _showError(_modelReady ? 'model_crashed' : 'model_failed');
            _running = false;
        };

        _worker.postMessage({ type: 'init' });
        _D('init message posted — modelTimer=' + MODEL_TIMEOUT_MS + 'ms');

        // Model load timeout — iOS: 50 s, desktop: 25 s (see MODEL_TIMEOUT_MS config)
        _modelTimer = setTimeout(function () {
            _D('modelTimer FIRED — _modelReady=' + _modelReady);
            if (!_modelReady) {
                _setStatus('ccModel', 'FAIL', 'cc-s-fail');
                _showError('model_failed');
                _running = false;
            }
        }, MODEL_TIMEOUT_MS);

        _startFrameLoop(videoEl);
    }

    function _startFramePhase(videoEl) {
        // Step 5: wait for frame loop to produce FRAME_LOOP_MIN frames
        _setStatus('ccFrames', 'Checking…', 'cc-s-checking');

        _loopTimer = setTimeout(function () {
            _setStatus('ccFrames', 'FAIL', 'cc-s-fail');
            _showError('no_frames');
            _running = false;
        }, LOOP_TIMEOUT_MS);

        var _poll = setInterval(function () {
            if (_framesRcvd >= FRAME_LOOP_MIN) {
                clearInterval(_poll);
                clearTimeout(_loopTimer); _loopTimer = null;
                _setStatus('ccFrames', 'OK', 'cc-s-ok');
                _startHandPhase();
            }
        }, 200);
    }

    function _startHandPhase() {
        // Step 6: canvas size check (best-effort — wrap may not be sized yet)
        var wrap = _el('ccCameraWrap');
        if (wrap) {
            var r = wrap.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) {
                _setStatus('ccCanvas', 'FAIL', 'cc-s-fail');
                _showError('canvas_zero');
                _running = false;
                return;
            }
            _setStatus('ccCanvas', 'OK', 'cc-s-ok');
        }

        // Step 7: wait for HAND_FRAMES_NEED consecutive frames with hand
        _setStatus('ccHand', 'Waiting…', 'cc-s-checking');
        _showHint(true);
        _handConsec = 0;

        _handTimer = setTimeout(function () {
            _setStatus('ccHand', 'FAIL', 'cc-s-fail');
            _showHint(false);
            _showError('no_hand');
            _running = false;
        }, HAND_TIMEOUT_MS);
    }

    function _onHands(hands) {
        // FPS measurement
        if (_fpsSamples.length < 30) {
            _fpsSamples.push(performance.now());
            if (_fpsSamples.length === 30 && _effectiveFPS === null) {
                _effectiveFPS = 29000 / (_fpsSamples[29] - _fpsSamples[0]);
            }
        }

        // Draw wrist overlay during hand-wait phase
        _drawWrists(hands);

        // Only count hand frames during hand-wait phase
        if (!_handTimer) return;

        var hasValid = hands.some(function (h) { return h.confidence >= MIN_HAND_CONF; });
        if (hasValid) {
            _handConsec++;
        } else {
            _handConsec = 0;
        }

        if (_handConsec >= HAND_FRAMES_NEED) {
            clearTimeout(_handTimer); _handTimer = null;
            _setStatus('ccHand', 'OK', 'cc-s-ok');
            _showHint(false);

            // Final canvas check
            var wrap = _el('ccCameraWrap');
            if (wrap) {
                var r2 = wrap.getBoundingClientRect();
                if (r2.width === 0 || r2.height === 0) {
                    _setStatus('ccCanvas', 'FAIL', 'cc-s-fail');
                    _showError('canvas_zero');
                    _running = false;
                    return;
                }
                _setStatus('ccCanvas', 'OK', 'cc-s-ok');
            }

            var lowFps = (_effectiveFPS !== null && _effectiveFPS < FPS_SLOW);
            _showPass(lowFps);
            _running = false;
        }
    }

    // ── Public API ───────────────────────────────────────────────────────────
    function start() {
        if (_running) return;
        _running = true;
        _paused  = false;

        _D('start() — UA=' + navigator.userAgent.substring(0, 100));
        _D('isSecureContext=' + window.isSecureContext
            + ' createImageBitmap=' + (typeof createImageBitmap !== 'undefined')
            + ' WebAssembly=' + (typeof WebAssembly !== 'undefined')
            + ' crossOriginIsolated=' + (typeof crossOriginIsolated !== 'undefined'
                ? crossOriginIsolated : 'N/A'));

        // ── Runtime patch fingerprint (TEMP diagnostic) ───────────────────────
        // Fetches each WASM loader JS with a cache-busting timestamp so Safari
        // cannot serve a cached copy. Logs whether the null-guard patch is present
        // and what Cache-Control the server actually sent. This runs in parallel
        // with the calibration flow and never blocks it.
        _mpVerifyLoader('nosimd', '/static/mediapipe/vision_wasm_nosimd_internal.js');
        _mpVerifyLoader('simd',   '/static/mediapipe/vision_wasm_internal.js');

        _resetChecklist();

        // Hide intro button while running, show Stop
        var run  = _el('ccBtnRun');
        if (run)  run.disabled = true;
        var stop = _el('ccBtnStop');
        if (stop) stop.className = 'cc-btn-stop cc-stop-visible';

        _run();
    }

    function retry() {
        stop();
        _resetChecklist();
        // Re-enable run button (stop() hid camera wrap but reset happens here)
        var run = _el('ccBtnRun');
        if (run) { run.disabled = false; run.style.display = ''; }
        var stopBtn = _el('ccBtnStop');
        if (stopBtn) stopBtn.className = 'cc-btn-stop';
        var retryBtn = _el('ccBtnRetry');
        if (retryBtn) retryBtn.className = 'cc-btn-retry';
        var cl = _el('ccChecklist');
        if (cl) cl.style.display = 'none';
        var cw = _el('ccCameraWrap');
        if (cw) cw.className = 'cc-camera-wrap';
        // Move video back out of wrap (cleanup)
        var vid = _el('cc-video');
        if (vid && vid.parentElement && vid.parentElement.id === 'ccCameraWrap') {
            document.body.appendChild(vid);
            vid.style.cssText = 'display:none; position:absolute; width:1px; height:1px; ' +
                                 'opacity:0; pointer-events:none; overflow:hidden;';
        }
        var ph = _el('ccCamPlaceholder');
        if (ph) ph.style.display = '';
        start();
    }

    function stop() {
        _clearTimers();
        _stopWorker();
        _stopCamera();
        _running = false;
        _paused  = false;
        _handConsec = 0;
        _framesRcvd = 0;
    }

    function pause() {
        _paused = true;
    }

    function resume() {
        _paused = false;
    }

    return { start: start, retry: retry, stop: stop, pause: pause, resume: resume };
}());
