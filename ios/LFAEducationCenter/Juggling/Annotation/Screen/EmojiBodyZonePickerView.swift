import SwiftUI

// MARK: — EmojiBodyZonePickerView (AN-3B2A SILO-1)
//
// Emoji-based body zone selector; intended to replace BodyZonePickerView (SILO-2).
//
// Layout:
//   Row 1   🙂 Fej         — full width, 1 type → auto-select
//   Row 2   🫁 Mellkas     — full width, 1 type → auto-select
//   Row 3   💪 Bal váll  /  💪 Jobb váll   — 2-col, 1 type each → auto-select
//   Row 4   🦴 Bal csípő /  🦴 Jobb csípő  — 2-col, 1 type each
//   Row 5   🦵 Bal térd  /  🦵 Jobb térd   — 2-col, 1 type each
//   Row 6   🦶 Bal lábfej / 🦶 Jobb lábfej — 2-col, 4 types each → expansion
//   [expansion] — 2×2 chip grid for the tapped foot zone's 4 sub-types
//
// Auto-select: zones with exactly 1 contact type set selectedKey + selectedSide
//              in the same tap transaction and call onZoneSelected.
// Foot expansion: zone tap toggles a 2×2 chip grid below the foot row.
//                 Chip tap sets selectedKey + selectedSide directly.
// Taxonomy nil: buttons render normally; auto-select and expansion are skipped.
//
// API — matches and extends BodyZonePickerView:
//   selectedZone  — binding to selected zone (mirrors BodyZonePickerView)
//   selectedKey   — set directly for auto-select zones and foot sub-type chips
//   selectedSide  — set directly from TaxonomyContactType.side
//   taxonomy      — required for auto-select logic and foot expansion
//   onZoneSelected — fires synchronously on every zone button tap (not on chip taps)
//
// EventLabelDetailView wiring: SILO-2 only — not connected in this commit.

struct EmojiBodyZonePickerView: View {

    @Binding var selectedZone: BodyZone?
    @Binding var selectedKey:  String?
    @Binding var selectedSide: String?
    let taxonomy: TaxonomyDocument?
    var onZoneSelected: ((BodyZone) -> Void)? = nil

    // Design contract: all zone buttons and sub-type chips are at least this tall.
    // Enforced by .frame(minHeight:) on every button label.
    static let minButtonHeight: CGFloat = 44

    @State private var expandedFootZone: BodyZone? = nil

    // MARK: — Body

    var body: some View {
        VStack(spacing: 8) {
            zoneButton(.head)
            zoneButton(.chest)
            pairedRow(.leftShoulder, .rightShoulder)
            pairedRow(.leftHip,      .rightHip)
            pairedRow(.leftKnee,     .rightKnee)
            pairedRow(.leftFoot,     .rightFoot)

            // Foot sub-type expansion — inline, directly below the foot row.
            if let expanded = expandedFootZone, let doc = taxonomy {
                footExpansionGrid(zone: expanded, taxonomy: doc)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: — Full-width zone button

    private func zoneButton(_ zone: BodyZone) -> some View {
        let isSelected = selectedZone == zone
        let typeCount  = taxonomy.map { zone.contactTypes(in: $0).count } ?? 0

        return Button {
            handleZoneTap(zone)
        } label: {
            HStack(spacing: 10) {
                Text(zone.emoji)
                    .font(.system(size: 22))
                    .accessibilityHidden(true)

                Text(zone.labelHu)
                    .font(.subheadline.weight(.medium))
                    .foregroundColor(isSelected ? .white : .primary)
                    .lineLimit(1)

                Spacer(minLength: 4)

                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.caption.weight(.bold))
                        .foregroundColor(.white)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, minHeight: Self.minButtonHeight)
            .background(isSelected ? Color.accentColor : Color(.systemGray6))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(isSelected ? Color.accentColor : Color(.systemGray4),
                            lineWidth: isSelected ? 0 : 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(zone.labelHu)
        .accessibilityHint(accessibilityHint(for: zone, typeCount: typeCount))
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }

    // MARK: — Paired left/right row

    private func pairedRow(_ left: BodyZone, _ right: BodyZone) -> some View {
        HStack(spacing: 8) {
            zoneButton(left)
            zoneButton(right)
        }
    }

    // MARK: — Foot sub-type expansion (2×2 chip grid)

    private func footExpansionGrid(zone: BodyZone, taxonomy: TaxonomyDocument) -> some View {
        let types = zone.contactTypes(in: taxonomy)   // 4 for any foot zone

        return VStack(spacing: 0) {
            Rectangle()
                .fill(Color.accentColor.opacity(0.25))
                .frame(height: 2)
                .padding(.horizontal, 24)

            LazyVGrid(
                columns: [GridItem(.flexible()), GridItem(.flexible())],
                spacing: 8
            ) {
                ForEach(types) { type in
                    footSubTypeChip(type)
                }
            }
            .padding(10)
            .background(Color(.systemGray6).opacity(0.7))
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }

    // MARK: — Foot sub-type chip

    private func footSubTypeChip(_ type: TaxonomyContactType) -> some View {
        let isSelected = selectedKey == type.key
        let label      = Self.shortLabel(for: type)

        return Button {
            selectedKey  = type.key
            selectedSide = type.side
        } label: {
            HStack(spacing: 6) {
                Image(systemName: type.iosIcon ?? "shoe.fill")
                    .font(.caption)
                    .foregroundColor(isSelected ? .white : .accentColor)
                    .accessibilityHidden(true)

                Text(label)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(isSelected ? .white : .primary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, minHeight: Self.minButtonHeight)
            .background(isSelected ? Color.accentColor : Color(.systemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(isSelected ? Color.accentColor : Color(.systemGray4),
                            lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(type.labelHu)
        .accessibilityHint("\(label) — \(type.labelEn)")
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }

    // MARK: — Zone tap handler

    private func handleZoneTap(_ zone: BodyZone) {
        guard let doc = taxonomy else {
            selectedZone = zone
            onZoneSelected?(zone)
            return
        }

        let types = zone.contactTypes(in: doc)

        if types.count > 1 {
            // Foot zone: toggle expansion, preserve or clear sub-type key.
            selectedZone = zone
            expandedFootZone = (expandedFootZone == zone) ? nil : zone
            if let key = selectedKey, !types.contains(where: { $0.key == key }) {
                selectedKey  = nil
                selectedSide = nil
            }
            onZoneSelected?(zone)
            return
        }

        // Single-type zone: auto-select in the same transaction.
        guard let single = types.first else { return }
        selectedZone = zone
        selectedKey  = single.key
        selectedSide = single.side
        expandedFootZone = nil
        onZoneSelected?(zone)
    }

    // MARK: — Accessibility hint

    private func accessibilityHint(for zone: BodyZone, typeCount: Int) -> String {
        switch typeCount {
        case 0: return "Taxonomy betöltése…"
        case 1: return "1 érintés, automatikusan kiválasztva"
        default: return "\(typeCount) altípus — egy további érintés szükséges"
        }
    }

    // MARK: — Short label for foot sub-type chips (internal for testability)
    //
    // Strips "Bal " or "Jobb " prefix and capitalises the first letter.
    // "Jobb rüszt" → "Rüszt" / "Bal belső" → "Belső"
    // Falls back to the full label_hu if the prefix is absent.

    static func shortLabel(for type: TaxonomyContactType) -> String {
        let parts = type.labelHu.split(separator: " ", maxSplits: 1)
        guard parts.count == 2,
              let first = parts.first,
              (String(first) == "Bal" || String(first) == "Jobb")
        else { return type.labelHu }
        let rest = String(parts[1])
        return rest.prefix(1).uppercased() + String(rest.dropFirst())
    }
}
