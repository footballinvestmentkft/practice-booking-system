import Foundation

// MARK: — TaxonomyDocument

// Codable mirror of datasets/juggling/contact_types_v1.json.
// Source-of-truth: the dataset JSON file.
// Bundled copy: LFAEducationCenter/Juggling/Annotation/Resources/contact_types_v1.json
// Do NOT add Swift enum keys by hand — the JSON is the authoritative key list.

struct TaxonomyDocument: Codable {
    let taxonomyVersion: String       // "v1"
    let stableCount:     Int          // 17
    let totalCount:      Int          // 18
    let groups:          [TaxonomyGroup]
    let stableKeys:      [String]
    let allKeys:         [String]

    enum CodingKeys: String, CodingKey {
        case taxonomyVersion = "taxonomy_version"
        case stableCount     = "stable_count"
        case totalCount      = "total_count"
        case groups
        case stableKeys      = "stable_keys"
        case allKeys         = "all_keys"
    }
}

struct TaxonomyGroup: Codable, Identifiable {
    let groupId:        String
    let groupLabelHu:   String
    let groupLabelEn:   String
    let groupSortOrder: Int
    let iosIcon:        String?
    let contactTypes:   [TaxonomyContactType]

    var id: String { groupId }

    enum CodingKeys: String, CodingKey {
        case groupId        = "group_id"
        case groupLabelHu   = "group_label_hu"
        case groupLabelEn   = "group_label_en"
        case groupSortOrder = "group_sort_order"
        case iosIcon        = "ios_section_icon"
        case contactTypes   = "contact_types"
    }
}

struct TaxonomyContactType: Codable, Identifiable {
    let key:                      String   // taxonomy key — never hardcode in Swift
    let labelHu:                  String
    let labelEn:                  String
    let side:                     String?  // nil for custom_other
    let sidePolicy:               String   // "fixed" | "center" | "explicit_required"
    let isStable:                 Bool
    let sortOrder:                Int
    let iosIcon:                  String?
    let excludedFromTrainingAuto: Bool
    let requiresCustomLabel:      Bool?
    let requiresCustomDescription:Bool?
    let requiresExplicitSide:     Bool?

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, side
        case labelHu                  = "label_hu"
        case labelEn                  = "label_en"
        case sidePolicy               = "side_policy"
        case isStable                 = "is_stable"
        case sortOrder                = "sort_order"
        case iosIcon                  = "ios_icon"
        case excludedFromTrainingAuto = "excluded_from_training_auto"
        case requiresCustomLabel      = "requires_custom_label"
        case requiresCustomDescription = "requires_custom_description"
        case requiresExplicitSide     = "requires_explicit_side"
    }
}

// MARK: — TaxonomyError

enum TaxonomyError: Error, LocalizedError {
    case invalidTotalCount(Int)
    case invalidStableCount(Int)
    case missingCustomOther
    case forbiddenThighKey
    case wrongVersion(String)
    case decodeFailed(Error)
    case bundleFileMissing

    var errorDescription: String? {
        switch self {
        case .invalidTotalCount(let n):  return "Taxonomy total_count must be 18, got \(n)"
        case .invalidStableCount(let n): return "Taxonomy stable_count must be 17, got \(n)"
        case .missingCustomOther:        return "Taxonomy missing required key: custom_other"
        case .forbiddenThighKey:         return "Taxonomy contains forbidden key: thigh"
        case .wrongVersion(let v):       return "Taxonomy version must be v1, got \(v)"
        case .decodeFailed(let e):       return "Taxonomy decode failed: \(e)"
        case .bundleFileMissing:         return "Bundled contact_types_v1.json not found in app bundle"
        }
    }
}
