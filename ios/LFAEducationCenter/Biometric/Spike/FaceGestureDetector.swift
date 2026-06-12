import ARKit

// MARK: — Input abstraction (enables mock injection in unit tests)

// All gesture detection logic runs against this protocol, not ARFaceAnchor directly.
// Tests create MockFaceAnchorInput; production code passes real ARFaceAnchor.
//
// Privacy rule: NO raw angle or blendshape value is stored or transmitted.
// The protocol and detector are local/ephemeral only — outputs are Bool per gesture.
protocol FaceAnchorInput {
    /// Head orientation as (yaw, pitch, roll) in radians.
    /// Convention: x = yaw (+ = user's left), y = pitch (+ = chin up).
    /// Verified during Phase 0 calibration — see calibration note in FacePoseThresholds.
    var faceEulerAngles: SIMD3<Float> { get }
    /// ARKit blendshape coefficients [0.0 … 1.0].
    var faceBlendShapes: [ARFaceAnchor.BlendShapeLocation: NSNumber] { get }
}

// MARK: — ARFaceAnchor conformance

extension ARFaceAnchor: FaceAnchorInput {

    // Extract approximate Euler angles from the face anchor's transform matrix.
    //
    // The face's local Z-axis (columns.2) in world/camera space points "outward" from
    // the face (toward the TrueDepth camera when at neutral).
    // Derivation: YXZ intrinsic Euler decomposition from rotation matrix columns.
    //
    // Sign check for Phase 0 calibration (watch the DEBUG overlay):
    //   Head turns LEFT  → faceEulerAngles.x should be POSITIVE
    //   Chin raises      → faceEulerAngles.y should be POSITIVE
    // If signs are inverted, negate the respective component here.
    var faceEulerAngles: SIMD3<Float> {
        let c = transform.columns
        let yaw   = atan2f(-c.2.x, c.2.z)  // rotation around Y axis
        let pitch = asinf( c.2.y)           // rotation around X axis (clamped by asin)
        return SIMD3(x: yaw, y: pitch, z: 0)
    }

    var faceBlendShapes: [ARFaceAnchor.BlendShapeLocation: NSNumber] {
        blendShapes
    }
}

// MARK: — Stateless gesture detector

// Stateless: call detect(gesture:from:) every frame.
// Returns true when the anchor satisfies the threshold for the requested gesture.
// All anti-false-positive guards are applied here.
struct FaceGestureDetector {

    let thresholds: FacePoseThresholds

    init(thresholds: FacePoseThresholds = .production) {
        self.thresholds = thresholds
    }

    func detect(gesture: FaceGestureType, from anchor: FaceAnchorInput) -> Bool {
        switch gesture {
        case .neutral:    return detectNeutral(anchor)
        case .headLeft:   return detectHeadLeft(anchor)
        case .headRight:  return detectHeadRight(anchor)
        case .chinUp:     return detectChinUp(anchor)
        case .blinkRight: return detectBlinkRight(anchor)
        case .blinkLeft:  return detectBlinkLeft(anchor)
        case .smile:      return detectSmile(anchor)
        }
    }

    // MARK: — Head pose

    private func detectNeutral(_ a: FaceAnchorInput) -> Bool {
        let e          = a.faceEulerAngles
        let yawOK      = abs(e.x) < thresholds.neutralYaw
        let pitchOK    = abs(e.y) < thresholds.neutralPitch
        let blendMax   = a.faceBlendShapes.values.map { $0.floatValue }.max() ?? 0
        let exprOK     = blendMax < thresholds.neutralMaxBlend
        return yawOK && pitchOK && exprOK
    }

    private func detectHeadLeft(_ a: FaceAnchorInput) -> Bool {
        a.faceEulerAngles.x > thresholds.yawLeft
    }

    private func detectHeadRight(_ a: FaceAnchorInput) -> Bool {
        a.faceEulerAngles.x < -thresholds.yawRight
    }

    private func detectChinUp(_ a: FaceAnchorInput) -> Bool {
        a.faceEulerAngles.y > thresholds.pitchUp
    }

    // MARK: — Blink

    private func detectBlinkRight(_ a: FaceAnchorInput) -> Bool {
        let right = blend(a, .eyeBlinkRight)
        let left  = blend(a, .eyeBlinkLeft)
        // Guard: left eye must stay open to prevent full-squint from triggering a wink.
        return right > thresholds.blinkMin && left < thresholds.blinkOtherMax
    }

    private func detectBlinkLeft(_ a: FaceAnchorInput) -> Bool {
        let left  = blend(a, .eyeBlinkLeft)
        let right = blend(a, .eyeBlinkRight)
        return left > thresholds.blinkMin && right < thresholds.blinkOtherMax
    }

    // MARK: — Smile

    private func detectSmile(_ a: FaceAnchorInput) -> Bool {
        let smileAvg = (blend(a, .mouthSmileLeft) + blend(a, .mouthSmileRight)) / 2
        guard smileAvg > thresholds.smileAvg else { return false }
        // Reinforcement: genuine smiles produce cheek squint; posed "flat" smiles often don't.
        let squintAvg = (blend(a, .cheekSquintLeft) + blend(a, .cheekSquintRight)) / 2
        return squintAvg >= thresholds.smileSquintMin
    }

    // MARK: — Helper

    private func blend(_ a: FaceAnchorInput, _ key: ARFaceAnchor.BlendShapeLocation) -> Float {
        a.faceBlendShapes[key]?.floatValue ?? 0
    }
}
