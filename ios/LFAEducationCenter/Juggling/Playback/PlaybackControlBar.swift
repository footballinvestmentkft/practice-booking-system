import SwiftUI

// MARK: — PlaybackControlBar

/// Transport control bar for the juggling annotation player (AN-3B1).
///
/// Displays: play/pause toggle, frame step (−1 / +1), speed selector (0.25× / 0.5× / 1×),
/// and a millisecond-precision timestamp readout.
///
/// Scope:
///   - NOT IN SCOPE: event timeline, tap-to-seek, event picker (AN-3B2).
///   - All controls delegate directly to PlaybackController — no local state.
///
/// Accessibility:
///   - All interactive controls have explicit accessibilityLabel.
///   - Timestamp readout carries .updatesFrequently trait.
///   - Reduce Motion: no animations in this bar (icon swap is instant).
///   - Touch targets are 44×44 pt minimum throughout.
struct PlaybackControlBar: View {
    @ObservedObject var controller: PlaybackController
    /// Set to false to disable all controls — e.g. while AnnotationVideoLoader is not yet .ready.
    var isEnabled: Bool = true

    var body: some View {
        VStack(spacing: 6) {
            timestampReadout

            HStack(spacing: 8) {
                frameStepButton(forward: false)
                playPauseButton
                frameStepButton(forward: true)
                Spacer()
                rateMenuButton
            }
            .disabled(!isEnabled)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color.black.opacity(0.82))
        .cornerRadius(12)
    }

    // MARK: — Subviews

    private var timestampReadout: some View {
        Text(Self.formatTimestamp(ms: controller.currentTimestampMs))
            .font(.system(.caption, design: .monospaced))
            .foregroundColor(.white)
            .frame(maxWidth: .infinity, alignment: .center)
            .accessibilityLabel("Timestamp \(Self.formatTimestamp(ms: controller.currentTimestampMs))")
            .accessibilityAddTraits(.updatesFrequently)
    }

    private var playPauseButton: some View {
        Button { controller.togglePlayPause() } label: {
            Image(systemName: controller.isPlaying ? "pause.fill" : "play.fill")
                .font(.system(size: 22, weight: .medium))
                .foregroundColor(.white)
                .frame(width: 44, height: 44)
        }
        .accessibilityLabel(controller.isPlaying ? "Pause" : "Play")
    }

    private func frameStepButton(forward: Bool) -> some View {
        Button {
            if forward { controller.stepForward() } else { controller.stepBackward() }
        } label: {
            Image(systemName: forward ? "forward.frame" : "backward.frame")
                .font(.system(size: 20, weight: .medium))
                .foregroundColor(.white)
                .frame(width: 44, height: 44)
        }
        .accessibilityLabel(forward ? "Step forward one frame" : "Step backward one frame")
    }

    private var rateMenuButton: some View {
        Menu {
            ForEach(PlaybackRate.allCases) { rate in
                Button { controller.setRate(rate) } label: {
                    if rate == controller.selectedRate {
                        Label(rate.label, systemImage: "checkmark")
                    } else {
                        Text(rate.label)
                    }
                }
            }
        } label: {
            Text(controller.selectedRate.label)
                .font(.system(.footnote, design: .monospaced).weight(.semibold))
                .foregroundColor(.white)
                .frame(minWidth: 44, minHeight: 44)
        }
        .accessibilityLabel("Playback speed \(controller.selectedRate.label). Double-tap to change.")
    }

    // MARK: — Timestamp formatting (internal for unit tests)

    /// Formats a millisecond timestamp as M:SS.mmm  (e.g. 75 500 ms → "1:15.500").
    static func formatTimestamp(ms: Int) -> String {
        let totalSeconds = ms / 1000
        let millis       = ms % 1000
        let minutes      = totalSeconds / 60
        let seconds      = totalSeconds % 60
        return String(format: "%d:%02d.%03d", minutes, seconds, millis)
    }
}
