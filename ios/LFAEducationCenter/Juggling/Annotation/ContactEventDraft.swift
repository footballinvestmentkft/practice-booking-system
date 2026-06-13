import Foundation

// MARK: — ContactEventSyncStatus

// 12-state sync machine.
// Transitions are documented in the sync engine.
// "failed" is split into failedPermanent and retryPending for clear retry routing.
// Phase 1 (AN-3B2A): .unlabeled and .labelPending are local-only; they never sync.
enum ContactEventSyncStatus: String, Codable, Equatable {
    // --- Phase 1 (AN-3B2A) — local-only, never sent to server ---
    case unlabeled            // timestamp marked; contactType == nil; awaits Phase 2 labeling
    case labelPending         // Phase 1→2 boundary; labeled but not yet transitioned to .localOnly

    // --- Standard sync states ---
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
struct ContactEventDraft: Identifiable, Equatable {
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
    // Set by resolveConflict when the server event is fetched; cleared on resolution.
    var pendingServerSnapshot: ContactEventOut?
    // How many times resolveConflict was retried before surfacing to the user.
    var conflictRetryCount:   Int

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
            serverUpdatedAt:      nil,
            pendingServerSnapshot: nil,
            conflictRetryCount:   0
        )
    }
}

// MARK: — ContactEventDraft: Codable
//
// Custom Codable implementation so that fields added across AN releases default
// gracefully when absent from persisted session files (backward-compat).
//  • pendingServerSnapshot → nil when absent
//  • conflictRetryCount   → 0 when absent

extension ContactEventDraft: Codable {
    enum CodingKeys: String, CodingKey {
        case deviceEventId, serverEventId, syncStatus, version
        case timestampMs, contactType, side
        case annotationConfidence, customLabel, customDescription
        case deletedLocally, failureReason, retryCount
        case createdAtLocal, serverCreatedAt, serverUpdatedAt
        case pendingServerSnapshot, conflictRetryCount
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        deviceEventId        = try c.decode(UUID.self,                    forKey: .deviceEventId)
        serverEventId        = try c.decodeIfPresent(UUID.self,           forKey: .serverEventId)
        syncStatus           = try c.decode(ContactEventSyncStatus.self,  forKey: .syncStatus)
        version              = try c.decode(Int.self,                     forKey: .version)
        timestampMs          = try c.decode(Int.self,                     forKey: .timestampMs)
        contactType          = try c.decode(String.self,                  forKey: .contactType)
        side                 = try c.decodeIfPresent(String.self,         forKey: .side)
        annotationConfidence = try c.decode(String.self,                  forKey: .annotationConfidence)
        customLabel          = try c.decodeIfPresent(String.self,         forKey: .customLabel)
        customDescription    = try c.decodeIfPresent(String.self,         forKey: .customDescription)
        deletedLocally       = try c.decode(Bool.self,                    forKey: .deletedLocally)
        failureReason        = try c.decodeIfPresent(String.self,         forKey: .failureReason)
        retryCount           = try c.decode(Int.self,                     forKey: .retryCount)
        createdAtLocal       = try c.decode(Date.self,                    forKey: .createdAtLocal)
        serverCreatedAt      = try c.decodeIfPresent(Date.self,           forKey: .serverCreatedAt)
        serverUpdatedAt      = try c.decodeIfPresent(Date.self,           forKey: .serverUpdatedAt)
        // AN-3B2 fields: default to nil/0 when key is absent (backward-compat with old session files)
        pendingServerSnapshot = try c.decodeIfPresent(ContactEventOut.self, forKey: .pendingServerSnapshot)
        conflictRetryCount    = try c.decodeIfPresent(Int.self,             forKey: .conflictRetryCount) ?? 0
    }
}

// MARK: — FinishReadiness

enum FinishReadiness: Equatable {
    case readyWithCount(Int)          // >0 synced active events
    case readyZero                    // 0 active events; confirmZeroContacts must be true
    case blocked([ContactEventSyncStatus]) // at least one event in a blocking state
}
