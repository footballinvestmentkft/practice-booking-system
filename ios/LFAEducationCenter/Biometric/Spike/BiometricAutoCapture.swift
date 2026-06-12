import Foundation

// Feature flag for the ARKit auto-capture spike (feat/biometric-auto-capture-spike).
//
// Default: false  → existing manual BiometricLivenessView runs unchanged.
// Set to true     → SpikeLivenessView (ARKit auto-detection) is presented instead.
//
// This flag is intentionally a `var` in DEBUG so it can be flipped at runtime from
// a test or launch argument without a recompile.  In Release it is a constant `false`
// and the entire spike code path is dead-code-eliminated by the compiler.
//
// NEVER merge a change that sets this to `true` in the non-DEBUG branch.
#if DEBUG
var kBiometricAutoCaptureSpikeEnabled: Bool = false
#else
let kBiometricAutoCaptureSpikeEnabled: Bool = false   // compile-time constant → optimizer strips spike code
#endif
