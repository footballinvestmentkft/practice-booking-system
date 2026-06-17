import SwiftUI
import AVFoundation

// MARK: — EventTimelineView

// Horizontal scrubber bar with event pins.
//
// Layout:
//   A full-width track (Capsule, 4 pt) with:
//   - A white playhead (3 pt wide) at the current position.
//   - Colour-coded circular pins (10 pt) at each event's timestamp.
//   - A drag gesture on the entire bar to seek.
//   - Tap on a pin to seek to that event's timestamp.
//
// Pin colour encodes ContactEventSyncStatus:
//   .unlabeled / .labelPending       → gray   (Phase 1: no contact type yet)
//   .localOnly / .retryPending       → orange (pending upload)
//   .syncing / .updating / .deleting → blue   (in-flight)
//   .synced                          → green
//   .conflicted / .failedPermanent   → red
//   .needsReconciliation             → yellow
//   .deleted                         → hidden
//
// Seek precision: DragGesture uses .positiveInfinity tolerance (same as
// PlaybackController.seek(toTimestampMs:)) — coarse, fast, no frame-exact jitter.

struct EventTimelineView: View {

    let events:              [ContactEventDraft]
    let duration:            CMTime
    let currentMs:           Int
    var onTap:   (UUID) -> Void
    var onSeek:  (Int)  -> Void
    // AN-3B2C-1: optional ball detection states for badge superscripts on pins.
    var ballDetectionStates: [UUID: BallDetectionState] = [:]

    private var durationMs: Int { duration.asMilliseconds }

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .leading) {
                track
                playhead(width: width)
                pins(width: width)
            }
            .contentShape(Rectangle())
            .gesture(seekGesture(width: width))
        }
        .frame(height: 44)
        .padding(.horizontal, 16)
        .accessibilityLabel("Videó idővonal")
        .accessibilityHint("Húzd a kereséshez, kopints az eseményekre")
    }

    // MARK: — Sub-views

    private var track: some View {
        Capsule()
            .fill(Color(.systemGray4))
            .frame(height: 4)
            .padding(.horizontal, 8)
    }

    private func playhead(width: CGFloat) -> some View {
        Capsule()
            .fill(Color.white)
            .frame(width: 3, height: 22)
            .shadow(color: .black.opacity(0.3), radius: 1)
            .offset(x: xPosition(ms: currentMs, trackWidth: width) - 1.5)
            .animation(.linear(duration: 0.1))  // iOS 14: value: param is iOS 15+
            .accessibilityHidden(true)
    }

    private func pins(width: CGFloat) -> some View {
        ForEach(events.filter { $0.syncStatus != .deleted && !$0.deletedLocally }) { draft in
            let x = xPosition(ms: draft.timestampMs, trackWidth: width)
            ZStack(alignment: .topTrailing) {
                Circle()
                    .fill(pinColor(for: draft.syncStatus))
                    .frame(width: 10, height: 10)
                    .shadow(color: .black.opacity(0.25), radius: 1)
                ballBadge(for: draft)
            }
            .offset(x: x - 5)
            .onTapGesture { onTap(draft.deviceEventId) }
            .accessibilityLabel("Esemény \(PlaybackControlBar.formatTimestamp(ms: draft.timestampMs))")
            .accessibilityAddTraits(.isButton)
        }
    }

    @ViewBuilder
    private func ballBadge(for draft: ContactEventDraft) -> some View {
        if let serverEventId = draft.serverEventId,
           case .loaded(let detection) = ballDetectionStates[serverEventId] {
            let badgeColor: Color = detection.noBallDetected
                ? .gray
                : (detection.detectionSource == "manual" ? .blue : .green)
            let badgeName: String = detection.noBallDetected ? "xmark" : "soccerball"
            Image(systemName: badgeName)
                .resizable()
                .scaledToFit()
                .frame(width: 6, height: 6)
                .foregroundColor(badgeColor)
                .offset(x: 4, y: -4)
        }
    }

    // MARK: — Geometry

    // Exposed as static for direct unit-test coverage without GeometryReader.
    static func xPosition(ms: Int, durationMs: Int, trackWidth: CGFloat) -> CGFloat {
        guard durationMs > 0 else { return 8 }
        let fraction = CGFloat(ms) / CGFloat(durationMs)
        return 8 + fraction * (trackWidth - 16)
    }

    private func xPosition(ms: Int, trackWidth: CGFloat) -> CGFloat {
        Self.xPosition(ms: ms, durationMs: durationMs, trackWidth: trackWidth)
    }

    // MARK: — Gesture

    private func seekGesture(width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard durationMs > 0 else { return }
                let usable  = width - 16
                let clamped = max(0, min(usable, value.location.x - 8))
                let ms      = Int((clamped / usable) * CGFloat(durationMs))
                onSeek(ms)
            }
    }

    // MARK: — Colour

    static func pinColor(for status: ContactEventSyncStatus) -> Color {
        switch status {
        case .unlabeled, .labelPending:         return Color(.systemGray)
        case .localOnly, .retryPending:         return .orange
        case .syncing, .updating, .deleting:    return .blue
        case .synced:                            return .green
        case .conflicted, .failedPermanent:     return .red
        case .needsReconciliation:              return .yellow
        case .deleted:                           return .clear
        }
    }

    private func pinColor(for status: ContactEventSyncStatus) -> Color {
        Self.pinColor(for: status)
    }
}
