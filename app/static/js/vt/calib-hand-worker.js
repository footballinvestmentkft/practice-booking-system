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

let _landmarker    = null;
let _modelReady    = false;
let _frameInFlight = false;

async function initModel() {
    let HandLandmarker, FilesetResolver;
    try {
        ({ HandLandmarker, FilesetResolver } =
            await import('/static/mediapipe/vision_bundle.mjs'));
    } catch (err) {
        self.postMessage({ type: 'error', code: 'import_failed', message: err.message });
        return;
    }

    for (const delegate of ['GPU', 'CPU']) {
        try {
            const vision = await FilesetResolver.forVisionTasks(MEDIAPIPE_BASE);
            _landmarker = await HandLandmarker.createFromOptions(vision, {
                baseOptions: { modelAssetPath: MODEL_PATH, delegate },
                numHands:                   2,
                minHandDetectionConfidence: 0.60,
                minHandPresenceConfidence:  0.60,
                minTrackingConfidence:      0.50,
                runningMode:                'VIDEO',
            });
            _modelReady = true;
            self.postMessage({ type: 'ready', delegate });
            return;
        } catch (err) {
            if (delegate === 'CPU') {
                self.postMessage({ type: 'error', code: 'model_init_failed', message: err.message });
            }
        }
    }
}

function processFrame(bitmap, timestamp) {
    if (!_landmarker || !_modelReady) { bitmap.close(); return; }
    let result;
    try {
        result = _landmarker.detectForVideo(bitmap, timestamp);
    } catch (err) {
        bitmap.close();
        return;
    }
    bitmap.close();

    const handedness = result.handedness || [];
    const landmarks  = result.landmarks  || [];

    if (!handedness.length) {
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
            _modelReady = false;
            if (_landmarker) {
                try { _landmarker.close(); } catch (_) {}
                _landmarker = null;
            }
            break;
    }
};
