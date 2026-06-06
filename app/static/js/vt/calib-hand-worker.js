/**
 * calib-hand-worker.js — Calibration Center hand detection worker (P0)
 *
 * Classic Worker (not module worker): MediaPipe's vision_bundle.mjs uses
 * importScripts() internally; module workers break this path.
 *
 * Self-hosted assets (never fetched from an external CDN):
 *   /static/mediapipe/vision_bundle.mjs
 *   /static/mediapipe/vision_wasm_internal.js / .wasm   (SIMD)
 *   /static/mediapipe/vision_wasm_nosimd_internal.js / .wasm  (iOS Safari)
 *   /static/mediapipe/hand_landmarker.task
 *
 * Privacy: only wrist {x,y,z} + handedness category/score are posted to the
 * main thread. No pixel data, no face data, nothing sent to the server.
 *
 * Protocol — main → worker:
 *   { type: 'init' }
 *   { type: 'frame', bitmap: ImageBitmap, timestamp: number }
 *   { type: 'stop' }
 *
 * Protocol — worker → main:
 *   { type: 'ready',    delegate: 'GPU'|'CPU' }
 *   { type: 'error',    code: string, message: string }
 *   { type: 'hands',    hands: HandData[] }
 *   { type: 'no_hands' }
 *
 * HandData = { side: 'Left'|'Right', confidence: number, wrist: {x,y,z} }
 * NOTE: 'side' is MediaPipe's raw label. With facingMode='user' (selfie),
 *       'Left' = user's RIGHT hand. The main thread applies the mirror flip.
 */

const MEDIAPIPE_BASE = '/static/mediapipe';
const MODEL_PATH     = '/static/mediapipe/hand_landmarker.task';

let _landmarker      = null;
let _modelReady      = false;
let _frameInFlight   = false;
let _frameCount      = 0;    // diagnostic only — logged for first frame

// ── Diagnostic helpers (DIAG prefix — remove after iPhone QA) ────────────────
// Logs go to console AND are relayed to the main thread via postMessage so
// they appear in the on-screen debug panel without needing Remote Inspector.
function _D(msg) {
    var line = '[CALIB_WORKER] ' + msg;
    console.log(line);
    self.postMessage({ type: 'log', level: 'info', msg: line });
}
function _Derr(msg, err) {
    var detail = err ? (err.name + ': ' + (err.message || String(err))) : '(no error object)';
    var line   = '[CALIB_WORKER] ' + msg + ' — ' + detail;
    console.error(line);
    self.postMessage({ type: 'log', level: 'error', msg: line });
}

async function initModel() {
    _D('initModel start — UA=' + (self.navigator && self.navigator.userAgent
        ? self.navigator.userAgent.substring(0, 80) : 'N/A'));
    _D('WebAssembly exists: ' + (typeof WebAssembly !== 'undefined'));
    _D('crossOriginIsolated: ' + (typeof crossOriginIsolated !== 'undefined'
        ? crossOriginIsolated : 'N/A'));

    let HandLandmarker, FilesetResolver;
    _D('dynamic import vision_bundle.mjs — start');
    try {
        ({ HandLandmarker, FilesetResolver } =
            await import('/static/mediapipe/vision_bundle.mjs'));
        _D('vision_bundle.mjs import OK — HandLandmarker=' + typeof HandLandmarker
            + ' FilesetResolver=' + typeof FilesetResolver);
    } catch (err) {
        _Derr('vision_bundle.mjs import FAILED', err);
        self.postMessage({ type: 'error', code: 'import_failed', message: err.message });
        return;
    }

    // iOS/iPadOS: CPU-only — skip GPU entirely.
    // On iOS Safari, the GPU delegate fails with "kGpuService not provided /
    // Error querying for GL extensions". This failure leaves the WASM module's
    // internal WebGL/OpenGL state partially initialised and corrupted. When the
    // CPU delegate subsequently calls detectForVideo(), the corrupted state
    // causes a C++ assertion failure → RuntimeError: Aborted() on every frame.
    // By skipping GPU on iOS, the CPU path starts from a clean state.
    //
    // Desktop (non-iOS): keep GPU → CPU fallback for better performance.
    const _isIOS = typeof self.navigator !== 'undefined'
        && /iP(hone|ad|od)/.test(self.navigator.userAgent);
    const _delegates = _isIOS ? ['CPU'] : ['GPU', 'CPU'];
    _D('platform: isIOS=' + _isIOS + ' delegates=' + JSON.stringify(_delegates));

    // forVisionTasks() is called inside the loop so each delegate attempt gets
    // a fresh fileset object (GPU failure contaminates the vision object's
    // internal WebGL/canvas state on iOS).
    for (const delegate of _delegates) {
        _D('FilesetResolver.forVisionTasks start — delegate=' + delegate
            + ' base=' + MEDIAPIPE_BASE);
        let vision;
        try {
            vision = await FilesetResolver.forVisionTasks(MEDIAPIPE_BASE);
            _D('FilesetResolver.forVisionTasks OK — delegate=' + delegate);
        } catch (err) {
            _Derr('FilesetResolver.forVisionTasks FAILED — delegate=' + delegate, err);
            if (delegate === 'CPU') {
                self.postMessage({ type: 'error', code: 'fileset_failed', message: err.message });
            }
            continue;
        }

        _D('HandLandmarker.createFromOptions try — delegate=' + delegate);
        try {
            _landmarker = await HandLandmarker.createFromOptions(vision, {
                baseOptions: { modelAssetPath: MODEL_PATH, delegate },
                numHands:                   2,
                minHandDetectionConfidence: 0.60,
                minHandPresenceConfidence:  0.60,
                minTrackingConfidence:      0.50,
                runningMode:                'VIDEO',
            });
            _modelReady = true;
            _D('createFromOptions OK — delegate=' + delegate + ' → posting ready');
            self.postMessage({ type: 'ready', delegate });
            return;
        } catch (err) {
            _Derr('createFromOptions FAIL — delegate=' + delegate, err);
            if (delegate === 'CPU') {
                self.postMessage({ type: 'error', code: 'model_init_failed', message: err.message });
            }
        }
    }
    _D('initModel exhausted all delegates — no ready sent');
}

function processFrame(bitmap, timestamp) {
    if (!_landmarker || !_modelReady) { bitmap.close(); return; }
    _frameCount++;
    if (_frameCount === 1) {
        _D('first detectForVideo call — timestamp=' + timestamp.toFixed(1));
    }
    let result;
    try {
        result = _landmarker.detectForVideo(bitmap, timestamp);
    } catch (err) {
        if (_frameCount <= 5) {
            _Derr('detectForVideo THREW (frame #' + _frameCount + ')', err);
        }
        bitmap.close();
        return;
    }
    if (_frameCount === 1) {
        _D('first detectForVideo OK — handLandmarks='
            + (result.handLandmarks || []).length
            + ' handedness=' + (result.handedness || []).length);
    }
    bitmap.close();

    // Use result.handLandmarks (correct MediaPipe Tasks API field).
    // result.landmarks does not exist in HandLandmarkerResult — using it
    // returns undefined, causing 'always no_hands' even with hands present.
    const handedness = result.handedness    || [];
    const landmarks  = result.handLandmarks || [];

    if (!landmarks.length) {
        self.postMessage({ type: 'no_hands' });
        return;
    }

    const hands = handedness.map(function (cats, i) {
        const h  = cats[0] || {};
        const lm = landmarks[i] || [];
        return {
            side:       h.categoryName || 'Unknown',
            confidence: h.score        || 0,
            wrist:      lm[0] ? { x: lm[0].x, y: lm[0].y, z: lm[0].z }
                               : { x: 0.5, y: 0.5, z: 0 },
        };
    });

    self.postMessage({ type: 'hands', hands });
}

self.onmessage = async function (event) {
    const msg = event.data;
    if (!msg || !msg.type) return;

    switch (msg.type) {
        case 'init':
            _D('received init message');
            await initModel();
            break;
        case 'frame':
            if (!msg.bitmap) break;
            if (_frameInFlight) { msg.bitmap.close(); break; }
            _frameInFlight = true;
            processFrame(msg.bitmap, msg.timestamp || performance.now());
            _frameInFlight = false;
            break;
        case 'stop':
            _D('received stop — _frameCount=' + _frameCount);
            _modelReady = false;
            if (_landmarker) {
                try { _landmarker.close(); } catch (_) {}
                _landmarker = null;
            }
            break;
    }
};
