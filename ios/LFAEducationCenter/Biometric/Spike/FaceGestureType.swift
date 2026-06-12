import Foundation

// The 7 gestures targeted in the Phase 0 spike.
//
// Excluded from spike:
//   sad expression  — higher false-positive risk vs. neutral; deferred to Phase 4 research.
//   head pitch down — pitch-down threshold overlaps with neutral on short necks; Phase 2 item.
//
// Sequence order used in the spike liveness flow:
//   neutral → headLeft → headRight → chinUp → blinkRight → blinkLeft → smile
enum FaceGestureType: String, CaseIterable, Equatable {

    case neutral    = "neutral"
    case headLeft   = "head_left"
    case headRight  = "head_right"
    case chinUp     = "chin_up"
    case blinkRight = "blink_right"
    case blinkLeft  = "blink_left"
    case smile      = "smile"

    // Instruction shown to the user during auto-capture flow.
    var instruction: String {
        switch self {
        case .neutral:    return "Look straight at the camera"
        case .headLeft:   return "Turn your head LEFT"
        case .headRight:  return "Turn your head RIGHT"
        case .chinUp:     return "Tilt your chin UP"
        case .blinkRight: return "Wink your RIGHT eye"
        case .blinkLeft:  return "Wink your LEFT eye"
        case .smile:      return "Smile!"
        }
    }

    // SF Symbol icon for the instruction card.
    var systemIcon: String {
        switch self {
        case .neutral:    return "face.smiling"
        case .headLeft:   return "arrow.left.circle"
        case .headRight:  return "arrow.right.circle"
        case .chinUp:     return "arrow.up.circle"
        case .blinkRight: return "eye.slash"
        case .blinkLeft:  return "eye.slash"
        case .smile:      return "face.smiling.fill"
        }
    }

    // Short label for the #if DEBUG overlay.
    var debugLabel: String { rawValue }
}
