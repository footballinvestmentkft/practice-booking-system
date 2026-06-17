import Foundation

// MARK: — ContactEventOut
// Exact mirror of AN-1 OpenAPI schema ContactEventOut.
// All fields verified against tests/snapshots/openapi_snapshot.json @ f1054056.

struct ContactEventOut: Codable, Identifiable, Equatable {
    let eventId:                UUID     // required, uuid string
    let deviceEventId:          UUID     // required, uuid string
    let timestampMs:            Int      // required
    let contactType:            String   // required
    let side:                   String?  // required but NULLABLE
    let annotationConfidence:   String   // required
    let annotationReviewStatus: String   // required
    let taxonomyReviewStatus:   String   // required
    let excludedFromTraining:   Bool     // required
    let customLabel:            String?  // required but NULLABLE
    let customDescription:      String?  // required but NULLABLE
    let version:                Int      // required
    let createdAt:              Date     // required, date-time
    let updatedAt:              Date     // required, date-time

    var id: UUID { eventId }

    enum CodingKeys: String, CodingKey {
        case eventId                = "event_id"
        case deviceEventId          = "device_event_id"
        case timestampMs            = "timestamp_ms"
        case contactType            = "contact_type"
        case side
        case annotationConfidence   = "annotation_confidence"
        case annotationReviewStatus = "annotation_review_status"
        case taxonomyReviewStatus   = "taxonomy_review_status"
        case excludedFromTraining   = "excluded_from_training"
        case customLabel            = "custom_label"
        case customDescription      = "custom_description"
        case version
        case createdAt              = "created_at"
        case updatedAt              = "updated_at"
    }
}

// MARK: — ContactEventListOut
// GET /contacts response envelope. annotation_status is NULLABLE.

struct ContactEventListOut: Codable {
    let videoId:          String    // required
    let annotationStatus: String?   // required but NULLABLE
    let events:           [ContactEventOut] // required

    enum CodingKeys: String, CodingKey {
        case videoId          = "video_id"
        case annotationStatus = "annotation_status"
        case events
    }
}

// MARK: — ContactEventBatchItemResult
// Per-item result inside batch response.
// event_id and detail are both optional AND nullable per OpenAPI spec.

struct ContactEventBatchItemResult: Codable {
    let deviceEventId: UUID      // required
    let status:        String    // required: "created"|"duplicate"|"conflict"
    let eventId:       UUID?     // optional + NULLABLE
    let detail:        String?   // optional + NULLABLE

    enum CodingKeys: String, CodingKey {
        case deviceEventId = "device_event_id"
        case status
        case eventId       = "event_id"
        case detail
    }
}

// MARK: — ContactEventBatchResult
// POST /contacts/batch response.
// HTTP status: 201 all-new / 200 all-dup / 207 mixed.

struct ContactEventBatchResult: Codable {
    let created:          Int    // required
    let duplicateSkipped: Int    // required; JSON key: "duplicate_skipped"
    let conflict:         Int    // required
    let results:          [ContactEventBatchItemResult] // required

    enum CodingKeys: String, CodingKey {
        case created
        case duplicateSkipped = "duplicate_skipped"
        case conflict
        case results
    }
}

// MARK: — FinishAnnotationOut
// POST /contacts/finish response.

struct FinishAnnotationOut: Codable {
    let videoId:              String  // required
    let annotationStatus:     String  // required
    let totalJugglingCount:   Int     // required
    let contactEventCount:    Int     // required
    let annotationFinishedAt: Date    // required, date-time

    enum CodingKeys: String, CodingKey {
        case videoId              = "video_id"
        case annotationStatus     = "annotation_status"
        case totalJugglingCount   = "total_juggling_count"
        case contactEventCount    = "contact_event_count"
        case annotationFinishedAt = "annotation_finished_at"
    }
}

// MARK: — Request bodies

struct ContactEventCreateRequest: Encodable {
    let deviceEventId:       UUID
    let timestampMs:         Int
    let contactType:         String
    let annotationConfidence:String
    let side:                String?
    let customLabel:         String?
    let customDescription:   String?

    enum CodingKeys: String, CodingKey {
        case deviceEventId       = "device_event_id"
        case timestampMs         = "timestamp_ms"
        case contactType         = "contact_type"
        case annotationConfidence = "annotation_confidence"
        case side
        case customLabel         = "custom_label"
        case customDescription   = "custom_description"
    }
}

struct ContactEventPatchRequest: Encodable {
    let version:              Int     // required for optimistic lock
    let contactType:          String?
    let annotationConfidence: String?
    let side:                 String?
    let customLabel:          String?
    let customDescription:    String?

    enum CodingKeys: String, CodingKey {
        case version
        case contactType          = "contact_type"
        case annotationConfidence = "annotation_confidence"
        case side
        case customLabel          = "custom_label"
        case customDescription    = "custom_description"
    }
}

struct ContactEventBatchRequest: Encodable {
    let events: [ContactEventCreateRequest]
}

struct FinishAnnotationRequest: Encodable {
    let confirmZeroContacts: Bool

    enum CodingKeys: String, CodingKey {
        case confirmZeroContacts = "confirm_zero_contacts"
    }
}
