import Foundation

// MARK: — BodyZone (AN-3B2A P2B-1)
//
// Pure taxonomy → body-zone mapping layer for the planned frontal body-zone
// picker (P2B-4, not yet implemented). No UI here, no new taxonomy data —
// every zone is a filter over the existing ContactTaxonomyStore /
// TaxonomyDocument (contact_types_v1.json), keyed by group_id / key / side.
//
// "Back" (group upper_body, key "back", side_policy=center) and
// "custom_other" (group custom) are intentionally NOT mapped to a zone —
// per AN3B2A_P2B_LABELING_UX_AUDIT_AND_PLAN.md §1.4 / §7.1 they remain
// reachable only through the taxonomy list fallback ("Egyéb / Lista nézet").
//
// Side handling: for every zone below, side_policy is "fixed" or "center"
// in the taxonomy — the zone tap fully determines `side` (taken from
// TaxonomyContactType.side). There is no separate left/right picker for
// these zones. explicit_required (custom_other) is out of scope for
// BodyZone and is handled only by the list fallback.

enum BodyZone: String, CaseIterable, Identifiable {
    case rightFoot
    case leftFoot
    case rightKnee
    case leftKnee
    case rightHip
    case leftHip
    case chest
    case head
    case rightShoulder
    case leftShoulder

    var id: String { rawValue }

    // Hungarian label for the future body-zone picker UI (P2B-4) and its
    // accessibility labels.
    var labelHu: String {
        switch self {
        case .rightFoot:     return "Jobb lábfej"
        case .leftFoot:      return "Bal lábfej"
        case .rightKnee:     return "Jobb térd"
        case .leftKnee:      return "Bal térd"
        case .rightHip:      return "Jobb csípő"
        case .leftHip:       return "Bal csípő"
        case .chest:         return "Mellkas"
        case .head:          return "Fej"
        case .rightShoulder: return "Jobb váll"
        case .leftShoulder:  return "Bal váll"
        }
    }

    // Returns the contact types belonging to this zone, sorted by
    // taxonomy sort_order. Filters purely on existing group_id / key /
    // side fields — never duplicates taxonomy content.
    func contactTypes(in document: TaxonomyDocument) -> [TaxonomyContactType] {
        switch self {
        case .rightFoot:     return types(inGroup: "foot",       document: document).filter { $0.side == "right" }
        case .leftFoot:      return types(inGroup: "foot",       document: document).filter { $0.side == "left" }
        case .rightKnee:     return types(inGroup: "knee",       document: document).filter { $0.side == "right" }
        case .leftKnee:      return types(inGroup: "knee",       document: document).filter { $0.side == "left" }
        case .rightHip:      return types(inGroup: "hip",        document: document).filter { $0.side == "right" }
        case .leftHip:       return types(inGroup: "hip",        document: document).filter { $0.side == "left" }
        case .chest:         return types(inGroup: "upper_body", document: document).filter { $0.key == "chest" }
        case .head:          return types(inGroup: "upper_body", document: document).filter { $0.key == "head" }
        case .rightShoulder: return types(inGroup: "upper_body", document: document).filter { $0.key == "right_shoulder" }
        case .leftShoulder:  return types(inGroup: "upper_body", document: document).filter { $0.key == "left_shoulder" }
        }
    }

    private func types(inGroup groupId: String, document: TaxonomyDocument) -> [TaxonomyContactType] {
        document.groups
            .first { $0.groupId == groupId }?
            .contactTypes
            .sorted { $0.sortOrder < $1.sortOrder } ?? []
    }
}
