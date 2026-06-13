import Foundation

// MARK: — ContactEventSyncStatus

// 10-state sync machine.
// Transitions are documented in the sync engine.
// "failed" is split into failedPermanent and retryPending for clear retry routing.
enum ContactEventSyncStatus: String, Codable, Equatable {
    case localOnly            // draft created locally, never sent
    case syncing              // POST /contacts in-flight
    case synced               // server confirmed 201 or 200 (exact dup)
    case updating             // PATCH in-flight
    case deleting             // DELETE in-flight
    case deleted              // server confirmed 204 (or reconciled as absent)
    case failedPermanent      // non-retryable: 403, 404, 409 idempotency, 422
    case retryPending         // retryable: network, timeout, 502, 503, 504 (retryCount < max)
    case conflicted           // PATCH 409 version_conflict — needs re-fetch + user decision
    case needsReconciliation  // timeout/network on PATCH or DELETE — outcome unknown
}

// MARK: — ContactEventDraft

// Persistent local representation of one annotation event.
// device_event_id is immutable after creation — it is the idempotency key.
// All fields that can change are var; identity fields are let.
struct ContactEventDraft: Codable, Identifiable, Equatable {
    let deviceEventId:        UUID        // immutable; idempotency key; never changes
    var serverEventId:        UUID?       // set after server 201/200 response
    var syncStatus:           ContactEventSyncStatus
    var version:              Int         // mirrors server version for optimistic locking
    var timestampMs:          Int
    var contactType:          String      // validated taxonomy key
    var side:                 String?
    var annotationConfidence: String      // "certain"|"probable"|"uncertain"
    var customLabel:          String?
    var customDescription:    String?
    var deletedLocally:       Bool
    var failureReason:        String?     // error detail for failedPermanent / conflicted
    var retryCount:           Int
    var createdAtLocal:       Date
    var serverCreatedAt:      Date?
    var serverUpdatedAt:      Date?

    var id: UUID { deviceEventId }

    static func new(
        timestampMs:          Int,
        contactType:          String,
        side:                 String?,
        annotationConfidence: String,
        customLabel:          String? = nil,
        customDescription:    String? = nil
    ) -> ContactEventDraft {
        ContactEventDraft(
            deviceEventId:        UUID(),
            serverEventId:        nil,
            syncStatus:           .localOnly,
            version:              1,
            timestampMs:          timestampMs,
            contactType:          contactType,
            side:                 side,
            annotationConfidence: annotationConfidence,
            customLabel:          customLabel,
            customDescription:    customDescription,
            deletedLocally:       false,
            failureReason:        nil,
            retryCount:           0,
            createdAtLocal:       Date(),
            serverCreatedAt:      nil,
            serverUpdatedAt:      nil
        )
    }
}

// MARK: — FinishReadiness

enum FinishReadiness: Equatable {
    case readyWithCount(Int)          // >0 synced active events
    case readyZero                    // 0 active events; confirmZeroContacts must be true
    case blocked([ContactEventSyncStatus]) // at least one event in a blocking state
}
