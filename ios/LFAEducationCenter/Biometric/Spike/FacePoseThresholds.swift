import Foundation

// All detection thresholds in one calibration struct.
//
// These are PROVISIONAL first-prototype values based on published ARKit usage patterns.
// They MUST be validated against physical measurements during the Phase 0 calibration
// session before the spike can be declared stable.
//
// How to calibrate: run the spike with kBiometricAutoCaptureSpikeEnabled = true,
// watch the #if DEBUG overlay, record raw yaw/pitch/blendshape values per gesture
// across 5+ test subjects and 3 lighting conditions, then update these defaults.
//
// Convention (empirically verify with calibration — see ARFaceTrackingView):
//   eulerAngles.x = yaw:   positive when head turns to user's LEFT
//   eulerAngles.y = pitch: positive when chin raises (face tilts upward)
struct FacePoseThresholds {

    // MARK: — Head pose (radians)

    /// Yaw threshold for head-left: eulerAngles.x > yawLeft
    var yawLeft:  Float = 0.30      // ≈ +17° — provisional, calibrate
    /// Yaw threshold for head-right: eulerAngles.x < -yawRight
    var yawRight: Float = 0.30      // ≈ -17° — stored positive, compared as negative
    /// Pitch threshold for chin-up: eulerAngles.y > pitchUp
    var pitchUp:  Float = 0.20      // ≈ +11° — provisional, calibrate

    /// Neutral window: |yaw| < neutralYaw AND |pitch| < neutralPitch
    /// Calibrated from device: rest yaw ≈ -0.143, rest pitch ≈ -0.176 → needs ≥ 0.20.
    /// Set to 0.25 (±14°) to give a comfortable margin above observed rest values.
    var neutralYaw:   Float = 0.25  // ≈ ±14° — widened from 0.12 after device calibration
    var neutralPitch: Float = 0.25  // ≈ ±14° — widened from 0.12 after device calibration

    // MARK: — Blendshapes [0.0 … 1.0]

    /// Eye blink accepted as wink above this value.
    var blinkMin:      Float = 0.75
    /// The OTHER eye must stay below this (prevents both-eyes-shut triggering a wink).
    var blinkOtherMax: Float = 0.35

    /// Smile: average of mouthSmileLeft + mouthSmileRight must exceed this.
    var smileAvg:      Float = 0.45
    /// Smile reinforcement: cheekSquint average must also meet this (reduces false smiles).
    var smileSquintMin: Float = 0.15

    /// All blendshapes below this → neutral expression component satisfied.
    var neutralMaxBlend: Float = 0.25

    // MARK: — Timing (milliseconds)

    /// Gesture must be held continuously for this duration before it is accepted.
    var holdDurationMs: Int = 400   // 300–700 ms range per audit; 400 ms is baseline

    /// Per-step timeout: if no gesture detected within this window, fire .timedOut.
    var stepTimeoutMs:  Int = 15_000

    // MARK: — Defaults

    static let production = FacePoseThresholds()
}
