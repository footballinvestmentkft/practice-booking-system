import SwiftUI

// Interactive football pitch for primary + secondary position selection.
//
// Layout: landscape pitch (GK left → ST right) with circular tappable nodes.
// ST has two visual nodes (ST1, ST2) both representing canonical "striker".
// second_striker and centre_back have no pitch node — shown as chips below.
//
// Tap rules:
//   tap unselected node  → primary (if no primary yet) else secondary (if < 3)
//   tap primary node     → deselect primary
//   tap secondary node   → remove from secondary
//   tap when 3 secondaries already selected → no-op
struct PitchSelectorView: View {

    @Binding var primaryPosition:    FootballPosition?
    @Binding var secondaryPositions: [FootballPosition]

    // Inner margin keeps edge nodes (GK at x=0.02, LW at y=0.07) fully visible.
    private let margin: Double = 14
    private let nodeSize: Double = 26

    var body: some View {
        VStack(spacing: 8) {
            pitchMap
            noPitchChips
        }
    }

    // MARK: — Pitch map

    private var pitchMap: some View {
        GeometryReader { geo in
            let innerW = geo.size.width  - 2 * margin
            let innerH = geo.size.height - 2 * margin

            ZStack {
                PitchBackground()

                ForEach(FootballPosition.pitchNodes) { node in
                    PitchNodeButton(
                        short: node.short,
                        state: nodeState(positionId: node.positionId)
                    )
                    .frame(width: nodeSize, height: nodeSize)
                    .position(
                        x: margin + innerW * node.x,
                        y: margin + innerH * node.y
                    )
                    .onTapGesture { handleTap(positionId: node.positionId) }
                }
            }
        }
        .aspectRatio(1.9, contentMode: .fit)
        .cornerRadius(6)
    }

    // MARK: — No-pitch-node chips (SS, CB)

    @ViewBuilder
    private var noPitchChips: some View {
        if !FootballPosition.noPitchPositions.isEmpty {
            HStack(spacing: 6) {
                Text("Also:")
                    .font(.caption2)
                    .foregroundColor(Theme.Color.muted)
                ForEach(FootballPosition.noPitchPositions) { pos in
                    Button { handleTap(positionId: pos.id) } label: {
                        Text(pos.short)
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(chipTextColor(positionId: pos.id))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(chipBgColor(positionId: pos.id))
                            .cornerRadius(4)
                    }
                }
                Spacer()
            }
        }
    }

    // MARK: — State helpers

    private func nodeState(positionId: String) -> PitchNodeState {
        if primaryPosition?.id == positionId { return .primary }
        if secondaryPositions.contains(where: { $0.id == positionId }) { return .secondary }
        return .none
    }

    private func chipBgColor(positionId: String) -> Color {
        switch nodeState(positionId: positionId) {
        case .primary:   return Theme.Color.primary
        case .secondary: return Theme.Color.secondary
        case .none:      return Theme.Color.muted.opacity(0.15)
        }
    }

    private func chipTextColor(positionId: String) -> Color {
        nodeState(positionId: positionId) == .none ? Theme.Color.onSurface : .white
    }

    // MARK: — Tap logic

    private func handleTap(positionId: String) {
        guard let position = FootballPosition.byId(positionId) else { return }

        if primaryPosition?.id == positionId {
            primaryPosition = nil
        } else if secondaryPositions.contains(where: { $0.id == positionId }) {
            secondaryPositions.removeAll { $0.id == positionId }
        } else if primaryPosition == nil {
            primaryPosition = position
        } else if secondaryPositions.count < 3 {
            secondaryPositions.append(position)
        }
        // max 3 secondary already set — tap ignored silently
    }
}

// MARK: — Pitch background

private struct PitchBackground: View {
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            let paW = w * 0.13
            let paH = h * 0.44

            ZStack {
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color(red: 0.18, green: 0.52, blue: 0.22))

                RoundedRectangle(cornerRadius: 4)
                    .stroke(Color.white.opacity(0.55), lineWidth: 1.5)
                    .padding(3)

                // Center line
                Path { p in
                    p.move(to:    CGPoint(x: w / 2, y: 4))
                    p.addLine(to: CGPoint(x: w / 2, y: h - 4))
                }
                .stroke(Color.white.opacity(0.4), lineWidth: 1)

                // Center circle
                let cR = h * 0.16
                Path { p in
                    p.addEllipse(in: CGRect(x: w/2 - cR, y: h/2 - cR, width: cR * 2, height: cR * 2))
                }
                .stroke(Color.white.opacity(0.4), lineWidth: 1)

                // Left penalty area
                Path { p in
                    p.addRect(CGRect(x: 3, y: h/2 - paH/2, width: paW, height: paH))
                }
                .stroke(Color.white.opacity(0.38), lineWidth: 1)

                // Right penalty area
                Path { p in
                    p.addRect(CGRect(x: w - 3 - paW, y: h/2 - paH/2, width: paW, height: paH))
                }
                .stroke(Color.white.opacity(0.38), lineWidth: 1)
            }
        }
    }
}

// MARK: — Node button

private enum PitchNodeState { case none, primary, secondary }

private struct PitchNodeButton: View {
    let short: String
    let state: PitchNodeState

    var body: some View {
        ZStack {
            Circle()
                .fill(bgColor)
                .shadow(color: .black.opacity(0.28), radius: 2, x: 0, y: 1)

            if state != .none {
                Circle().stroke(Color.white, lineWidth: 1.5)
            }

            Text(short)
                .font(.system(size: short.count > 2 ? 7 : 9, weight: .bold))
                .foregroundColor(textColor)
                .lineLimit(1)
                .minimumScaleFactor(0.5)
        }
    }

    private var bgColor: Color {
        switch state {
        case .primary:   return Theme.Color.primary
        case .secondary: return Theme.Color.secondary
        case .none:      return Color.white.opacity(0.88)
        }
    }

    private var textColor: Color {
        state == .none ? Color.black.opacity(0.8) : .white
    }
}
