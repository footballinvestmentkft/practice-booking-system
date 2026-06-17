import SwiftUI

// MARK: — BodyZonePickerView (AN-3B2A P2B-4)
//
// Schematic, frontal body-zone picker. Each of the 10 BodyZone cases maps to
// a tappable labelled rectangle, positioned on a normalised 200×265 pt canvas
// that scales to fill the available frame (aspect-fit, centred).
//
// Tap targets are guaranteed ≥ 44×44 pt by clamping the .frame() min-dimension
// before .position() — the visual zone may be smaller on compact screens but
// the interactive area is always accessible.
//
// "back" and "custom_other" are not represented here (list fallback only).
// VoiceOver: accessibilityLabel = zone.labelHu, accessibilityHint = type count.

struct BodyZonePickerView: View {

    @Binding var selectedZone: BodyZone?
    let taxonomy: TaxonomyDocument?
    // Fires synchronously in the same button-action transaction as selectedZone.
    // Use this instead of .onChange(of: selectedZone) to set dependent state
    // (e.g. selectedKey) in a single SwiftUI transaction, avoiding the 1-frame
    // window where selectedZone is set but selectedKey is still nil.
    var onZoneSelected: ((BodyZone) -> Void)? = nil

    // MARK: — Canvas geometry (logical units)

    private static let canvasW: CGFloat = 200
    private static let canvasH: CGFloat = 265

    private static func canvasRect(for zone: BodyZone) -> CGRect {
        switch zone {
        case .head:          return CGRect(x: 80,  y: 0,   width: 40,  height: 40)
        case .leftShoulder:  return CGRect(x: 0,   y: 45,  width: 50,  height: 35)
        case .chest:         return CGRect(x: 50,  y: 40,  width: 100, height: 50)
        case .rightShoulder: return CGRect(x: 150, y: 45,  width: 50,  height: 35)
        case .leftHip:       return CGRect(x: 5,   y: 95,  width: 80,  height: 50)
        case .rightHip:      return CGRect(x: 115, y: 95,  width: 80,  height: 50)
        case .leftKnee:      return CGRect(x: 10,  y: 150, width: 75,  height: 50)
        case .rightKnee:     return CGRect(x: 115, y: 150, width: 75,  height: 50)
        case .leftFoot:      return CGRect(x: 10,  y: 205, width: 75,  height: 55)
        case .rightFoot:     return CGRect(x: 115, y: 205, width: 75,  height: 55)
        }
    }

    // MARK: — Body

    var body: some View {
        GeometryReader { geo in
            let scale   = min(geo.size.width / Self.canvasW, geo.size.height / Self.canvasH)
            let drawW   = Self.canvasW * scale
            let drawH   = Self.canvasH * scale
            let originX = (geo.size.width  - drawW) / 2
            let originY = (geo.size.height - drawH) / 2

            ZStack(alignment: .topLeading) {
                // Background silhouette — simple outline to give shape context
                silhouettePath(scale: scale, originX: originX, originY: originY)
                    .stroke(Color(.systemGray4), lineWidth: 1.5)

                // Zone buttons
                ForEach(BodyZone.allCases) { zone in
                    let cr = Self.canvasRect(for: zone)
                    let r  = CGRect(
                        x:      cr.minX * scale + originX,
                        y:      cr.minY * scale + originY,
                        width:  cr.width  * scale,
                        height: cr.height * scale
                    )
                    zoneButton(zone, rect: r)
                }
            }
        }
    }

    // MARK: — Zone button

    @ViewBuilder
    private func zoneButton(_ zone: BodyZone, rect: CGRect) -> some View {
        let isSelected  = selectedZone == zone
        let typeCount   = taxonomy.map { zone.contactTypes(in: $0).count } ?? 0
        let tapW        = max(rect.width,  44)
        let tapH        = max(rect.height, 44)

        Button {
            selectedZone = zone
            onZoneSelected?(zone)
        } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 6)
                    .fill(isSelected
                          ? Color.accentColor.opacity(0.22)
                          : Color(.systemBackground).opacity(0.88))
                RoundedRectangle(cornerRadius: 6)
                    .stroke(isSelected ? Color.accentColor : Color(.systemGray3),
                            lineWidth: isSelected ? 2 : 1)

                VStack(spacing: 1) {
                    Text(zone.labelHu)
                        .font(.system(size: min(rect.height * 0.26, 10.5),
                                      weight: isSelected ? .semibold : .regular))
                        .foregroundColor(isSelected ? .accentColor : .primary)
                        .minimumScaleFactor(0.6)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)

                    if typeCount > 1 {
                        Text("\(typeCount) típus")
                            .font(.system(size: min(rect.height * 0.18, 8)))
                            .foregroundColor(.secondary)
                            .minimumScaleFactor(0.7)
                    }
                }
                .padding(3)
            }
            .frame(width: tapW, height: tapH)
        }
        .buttonStyle(.plain)
        .position(x: rect.midX, y: rect.midY)
        .accessibilityLabel(zone.labelHu)
        .accessibilityHint(typeCount == 1
                           ? "1 kontakt típus, automatikusan kiválasztva"
                           : "\(typeCount) kontakt típus közül lehet választani")
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }

    // MARK: — Background silhouette (decorative path)
    //
    // Draws a simple schematic outline behind the zones so the spatial
    // arrangement reads as a body without requiring an image asset.

    private func silhouettePath(scale: CGFloat, originX: CGFloat, originY: CGFloat) -> Path {
        func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: x * scale + originX, y: y * scale + originY)
        }
        return Path { p in
            // Head circle
            let headCR = CGRect(x: 78 * scale + originX, y: 0 * scale + originY,
                                width: 44 * scale, height: 44 * scale)
            p.addEllipse(in: headCR)

            // Torso outline (trapezoid: wider at shoulders, narrower at hips)
            p.move(to: pt(50, 44))          // left shoulder inner top
            p.addLine(to: pt(150, 44))      // right shoulder inner top
            p.addLine(to: pt(160, 95))      // right hip outer top
            p.addLine(to: pt(200, 95))      // right hip outer
            p.addLine(to: pt(200, 145))     // right hip bottom
            p.addLine(to: pt(160, 145))     // right hip bottom inner
            p.addLine(to: pt(155, 260))     // right foot bottom
            p.addLine(to: pt(115, 260))     // right foot bottom inner
            p.addLine(to: pt(115, 145))     // right knee inner bottom
            p.addLine(to: pt(85,  145))     // left knee inner bottom
            p.addLine(to: pt(85,  260))     // left foot bottom inner
            p.addLine(to: pt(45,  260))     // left foot bottom
            p.addLine(to: pt(40,  145))     // left hip bottom inner
            p.addLine(to: pt(0,   145))     // left hip bottom
            p.addLine(to: pt(0,   95))      // left hip outer
            p.addLine(to: pt(40,  95))      // left hip outer top
            p.addLine(to: pt(50,  44))      // back to left shoulder inner
            p.closeSubpath()

            // Left shoulder outer arm
            p.move(to: pt(0, 44))
            p.addLine(to: pt(50, 44))

            // Right shoulder outer arm
            p.move(to: pt(150, 44))
            p.addLine(to: pt(200, 44))

            // Neck
            p.move(to: pt(88,  40))
            p.addLine(to: pt(88,  44))
            p.addLine(to: pt(112, 44))
            p.addLine(to: pt(112, 40))
        }
    }
}
