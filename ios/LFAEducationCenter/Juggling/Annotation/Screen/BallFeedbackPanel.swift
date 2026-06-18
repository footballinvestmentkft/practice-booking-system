import SwiftUI

// MARK: — BallFeedbackPanel (AN-3B2B1)
//
// Compact horizontal action panel placed below the video area and above
// PlaybackControlBar. Visible only when isFeedbackMode = true.
//
// Layout (D1: non-covering, D4: session counter):
//   ┌─────────────────────────────────────────────────────┐
//   │ Frame: 4 230 ms  ·  Bizonytalanság: 78%  ·  1 / 3  │  header
//   │  [✓ Helyes]  [✗ Nincs labda]  [✎ Javítom] [→ Skip] │  actions
//   │               [error label if any]                  │  error
//   └─────────────────────────────────────────────────────┘
//
// Transitions: none intentional — parent handles open/close animation.
// The corrected action calls onCorrect(); the parent activates the tap overlay.

struct BallFeedbackPanel: View {

    @ObservedObject var vm: BallFeedbackViewModel

    let onConfirm: () -> Void
    let onNoBall:  () -> Void
    let onCorrect: () -> Void
    let onSkip:    () -> Void
    let onClose:   () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            switch vm.sessionState {
            case .loading:
                loadingRow
            case .ready:
                if let item = vm.currentItem {
                    activePanel(item: item)
                }
            case .empty:
                emptyRow
            case .sessionComplete:
                sessionCompleteRow
            case .unavailable:
                unavailableRow
            case .error(let msg):
                errorRow(msg)
            case .idle:
                EmptyView()
            }
        }
        .background(Color(.secondarySystemBackground))
    }

    // MARK: — Active panel

    @ViewBuilder
    private func activePanel(item: BallFeedbackQueueItem) -> some View {
        VStack(spacing: 6) {
            // Header
            HStack(spacing: 8) {
                Text("Frame: \(item.frameMs) ms")
                    .font(.system(size: 12, weight: .medium).monospacedDigit())
                    .foregroundColor(.secondary)

                if let conf = item.modelConfidence {
                    Text("·")
                        .foregroundColor(.secondary)
                    Text("Bizonytalanság: \(Int((1.0 - conf) * 100))%")
                        .font(.system(size: 12))
                        .foregroundColor(uncertaintyColor(conf: conf))
                } else {
                    Text("·  Elveszett frame")
                        .font(.system(size: 12))
                        .foregroundColor(.red)
                }

                Spacer()

                Text("\(vm.submittedCount) / \(vm.maxPerSession)")
                    .font(.system(size: 12, weight: .semibold).monospacedDigit())
                    .foregroundColor(.secondary)

                Button(action: onClose) {
                    Image(systemName: "xmark")
                        .font(.system(size: 13))
                        .foregroundColor(.secondary)
                }
                .accessibilityLabel("Visszajelzés bezárása")
            }
            .padding(.horizontal, 12)
            .padding(.top, 6)

            // Action buttons
            HStack(spacing: 8) {
                actionButton(
                    title: "Helyes",
                    icon: "checkmark.circle.fill",
                    color: .green,
                    action: onConfirm
                )
                actionButton(
                    title: "Nincs labda",
                    icon: "xmark.circle.fill",
                    color: .red,
                    action: onNoBall
                )
                actionButton(
                    title: "Javítom",
                    icon: "pencil.circle.fill",
                    color: .orange,
                    action: onCorrect
                )
                actionButton(
                    title: "Kihagyom",
                    icon: "arrow.right.circle",
                    color: .secondary,
                    action: onSkip
                )
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 4)

            // Error row
            if let err = vm.lastErrorMessage {
                Text(err)
                    .font(.system(size: 11))
                    .foregroundColor(.red)
                    .padding(.bottom, 4)
                    .onTapGesture { vm.clearError() }
            }
        }
    }

    // MARK: — State rows

    private var loadingRow: some View {
        HStack(spacing: 8) {
            ProgressView().scaleEffect(0.75)
            Text("Visszajelzési lista betöltése…")
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Spacer()
            closeButton
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var emptyRow: some View {
        HStack {
            Image(systemName: "checkmark.seal.fill")
                .foregroundColor(.green)
            Text("Nincs több ellenőrizendő frame.")
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Spacer()
            closeButton
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var sessionCompleteRow: some View {
        HStack {
            Image(systemName: "hand.thumbsup.fill")
                .foregroundColor(.green)
            Text("Köszönjük! \(vm.maxPerSession) visszajelzés elküldve.")
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Spacer()
            closeButton
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var unavailableRow: some View {
        HStack {
            Image(systemName: "exclamationmark.circle")
                .foregroundColor(.orange)
            Text("A visszajelzés jelenleg nem érhető el.")
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Spacer()
            closeButton
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func errorRow(_ msg: String) -> some View {
        HStack {
            Image(systemName: "exclamationmark.triangle")
                .foregroundColor(.red)
            Text(msg)
                .font(.system(size: 13))
                .foregroundColor(.red)
            Spacer()
            closeButton
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var closeButton: some View {
        Button(action: onClose) {
            Image(systemName: "xmark")
                .font(.system(size: 13))
                .foregroundColor(.secondary)
        }
        .accessibilityLabel("Visszajelzés bezárása")
    }

    // MARK: — Sub-views

    @ViewBuilder
    private func actionButton(
        title: String,
        icon: String,
        color: Color,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Image(systemName: icon)
                    .font(.system(size: 20))
                    .foregroundColor(color)
                Text(title)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(color)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .background(color.opacity(0.08))
            .cornerRadius(8)
        }
        .accessibilityLabel(title)
    }

    private func uncertaintyColor(conf: Double) -> Color {
        let uncertainty = 1.0 - conf
        if uncertainty >= 0.60 { return .red }
        if uncertainty >= 0.30 { return .orange }
        return .secondary
    }
}

// MARK: — BallFeedbackPanelColorHelper (testable)

enum BallFeedbackPanelColorHelper {
    static func decisionColor(_ decision: String) -> Color {
        switch decision {
        case "confirm":   return .green
        case "no_ball":   return .red
        case "corrected": return .orange
        default:          return .secondary
        }
    }
}
