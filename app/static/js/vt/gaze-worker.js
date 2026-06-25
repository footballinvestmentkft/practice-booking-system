'use strict';
/*
 * PV Gaze Worker — Phase 1 skeleton
 *
 * Phase 1: proves Worker infrastructure (creation, message passing, honor-mode
 * fallback). MediaPipe model loading is intentionally deferred to Phase 2,
 * which will also update connect-src CSP to allow https://cdn.jsdelivr.net for
 * WASM model weight downloads.
 *
 * Phase 2 will replace the 'init' handler with:
 *   import { FaceLandmarker, FilesetResolver } from
 *     'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.min.js'
 *
 * Phase 3 will implement the 'frame' handler to extract iris landmarks and
 * post them back to the main thread for the calibrated regression estimator.
 *
 * No video frames, iris coordinates, or biometric data ever leave this Worker
 * toward the network. Only aggregated summary scalars are included in the
 * submit payload (handled in gaze-session.js, Phase 3).
 */

self.onmessage = function (event) {
    var msg = event.data;
    if (!msg || !msg.type) return;

    switch (msg.type) {

        case 'init':
            // Phase 1: Worker alive — model load deferred to Phase 2.
            // Main thread receives this error → honor mode (gaze_validated=false).
            self.postMessage({
                type:    'error',
                code:    'model_deferred',
                message: 'Phase 1 — MediaPipe model load deferred to Phase 2',
            });
            break;

        case 'frame':
            // Phase 3 will extract iris landmarks from the ImageBitmap here.
            // For now, just release the bitmap memory.
            if (msg.bitmap) {
                msg.bitmap.close();
            }
            break;

        case 'stop':
            // Clean shutdown — nothing to tear down in Phase 1.
            break;

        default:
            break;
    }
};
