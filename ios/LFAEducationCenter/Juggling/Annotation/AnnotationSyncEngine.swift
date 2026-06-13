import Foundation

// MARK: — AnnotationSyncEngine
//
// Implements the 10-state ContactEventSyncStatus machine for one annotation
// session. All methods operate on a single ContactEventDraft (or the whole
// session for flush/reconcile) and return updated values — the caller
// (JugglingAnnotationViewModel) owns persistence via LocalAnnotationStore.
//
// Retry policy: max 3 attempts. Delay schedule (2s / 4s / 8s, ± jitter) is
// the caller's responsibility — this engine only tracks retryCount and
// reports whether the cap has been reached.
//
// Ambiguity handling:
//   - POST timeout: idempotent via device_event_id → retryPending (safe to retry)
//   - PATCH timeout: optimistic-lock side effect unknown → needsReconciliation
//   - DELETE timeout or 404: outcome unknown → needsReconciliation
//   - reconcile(): GET /contacts is the only source of truth for needsReconciliation

@MainActor
final class AnnotationSyncEngine {

    static let maxRetries = 3
    // Base retry delays in seconds; caller applies ± jitter before scheduling.
    static let retryDelaysSeconds: [Double] = [2.0, 4.0, 8.0]

    private let apiClient: JugglingAnnotationAPIClientProtocol

    init(apiClient: JugglingAnnotationAPIClientProtocol) {
        self.apiClient = apiClient
    }

    // MARK: — Flush pending

    // Pushes every localOnly / retryPending draft to the server.
    // Drafts that were deleted locally before ever syncing are marked
    // .deleted without a network call (nothing exists server-side).
    // AN-3A: also pushes .updating drafts (events that were edited after
    // reaching .synced — see editEvent on JugglingAnnotationViewModel).
    // Mutates session.drafts in place; caller persists afterwards.
    func flushPending(session: inout AnnotationSessionFile) async {
        for index in session.drafts.indices {
            let draft = session.drafts[index]
            guard draft.syncStatus == .localOnly || draft.syncStatus == .retryPending else { continue }

            if draft.deletedLocally {
                session.drafts[index].syncStatus = .deleted
                continue
            }

            session.drafts[index] = await pushCreate(draft: draft, videoId: session.videoId)
        }

        // Drafts that finished syncing but were marked for deletion in the
        // meantime now need a DELETE pushed to the server.
        for index in session.drafts.indices {
            let draft = session.drafts[index]
            guard draft.deletedLocally, draft.syncStatus == .synced, draft.serverEventId != nil else { continue }
            session.drafts[index] = await pushDelete(draft: draft, videoId: session.videoId)
        }

        // Edited drafts: .updating means a local edit is pending a PATCH.
        // If the draft was also deleted before the PATCH was sent, push a
        // DELETE instead (the user edited then immediately removed the event).
        for index in session.drafts.indices {
            let draft = session.drafts[index]
            guard draft.syncStatus == .updating else { continue }
            // pushPatch handles the serverEventId == nil defensive case internally
            // (returns .failedPermanent with reason "patch_without_server_id").
            if draft.deletedLocally {
                session.drafts[index] = await pushDelete(draft: draft, videoId: session.videoId)
            } else {
                let request = ContactEventPatchRequest(
                    version:              draft.version,
                    contactType:          draft.contactType,
                    annotationConfidence: draft.annotationConfidence,
                    side:                 draft.side,
                    customLabel:          draft.customLabel,
                    customDescription:    draft.customDescription
                )
                session.drafts[index] = await pushPatch(
                    draft:   draft,
                    videoId: session.videoId,
                    request: request
                )
            }
        }
    }

    // MARK: — Reconciliation (GET /contacts → align local state with server)

    // Resolves every draft in .needsReconciliation by comparing against the
    // authoritative server list. A draft absent from the server response is
    // treated as deleted (covers both confirmed DELETE and 404-ambiguous DELETE).
    func reconcile(session: inout AnnotationSessionFile) async throws {
        let list = try await apiClient.listContacts(videoId: session.videoId)
        let serverByDeviceId = Dictionary(
            uniqueKeysWithValues: list.events.map { ($0.deviceEventId, $0) }
        )

        for index in session.drafts.indices {
            let draft = session.drafts[index]
            guard draft.syncStatus == .needsReconciliation else { continue }

            if let serverEvent = serverByDeviceId[draft.deviceEventId] {
                session.drafts[index] = applyServerState(to: draft, from: serverEvent)
            } else {
                session.drafts[index].syncStatus     = .deleted
                session.drafts[index].failureReason  = nil
            }
        }
    }

    // MARK: — Single-draft operations

    // POST /contacts. 201 → new, 200 → exact duplicate — both are .synced.
    func pushCreate(draft: ContactEventDraft, videoId: String) async -> ContactEventDraft {
        var updated = draft
        updated.syncStatus = .syncing

        let request = ContactEventCreateRequest(
            deviceEventId:        draft.deviceEventId,
            timestampMs:          draft.timestampMs,
            contactType:          draft.contactType,
            annotationConfidence: draft.annotationConfidence,
            side:                 draft.side,
            customLabel:          draft.customLabel,
            customDescription:    draft.customDescription
        )

        do {
            let result = try await apiClient.createContact(videoId: videoId, request: request)
            updated = applyServerState(to: updated, from: result.event)
        } catch let error as AnnotationAPIError {
            updated = applyCreateFailure(to: updated, error: error)
        } catch {
            updated = applyCreateFailure(to: updated, error: .retryable(code: nil))
        }
        return updated
    }

    // PATCH /contacts/{id}. Optimistic-lock conflict → .conflicted.
    // Network/timeout → .needsReconciliation (PATCH is not idempotent: the
    // server may have applied it before the response was lost).
    func pushPatch(
        draft:   ContactEventDraft,
        videoId: String,
        request: ContactEventPatchRequest
    ) async -> ContactEventDraft {
        guard let serverEventId = draft.serverEventId else {
            var updated = draft
            updated.syncStatus    = .failedPermanent
            updated.failureReason = "patch_without_server_id"
            return updated
        }

        var updated = draft
        updated.syncStatus = .updating

        do {
            let event = try await apiClient.patchContact(
                videoId: videoId, eventId: serverEventId, request: request
            )
            updated = applyServerState(to: updated, from: event)
        } catch let error as AnnotationAPIError {
            switch error {
            case .versionConflict(let detail):
                updated.syncStatus    = .conflicted
                updated.failureReason = detail
            case .retryable:
                updated.syncStatus    = .needsReconciliation
                updated.failureReason = "patch_timeout_or_unavailable"
            default:
                updated.syncStatus    = .failedPermanent
                updated.failureReason = error.errorDescription
            }
        } catch {
            updated.syncStatus    = .needsReconciliation
            updated.failureReason = "patch_network_error"
        }
        return updated
    }

    // DELETE /contacts/{id} (soft-delete). 404 is ownership-ambiguous and
    // network/timeout outcomes are unknown — both route to .needsReconciliation
    // rather than being assumed successful.
    func pushDelete(draft: ContactEventDraft, videoId: String) async -> ContactEventDraft {
        guard let serverEventId = draft.serverEventId else {
            // Never reached the server — nothing to delete remotely.
            var updated = draft
            updated.syncStatus = .deleted
            return updated
        }

        var updated = draft
        updated.syncStatus = .deleting

        do {
            switch try await apiClient.deleteContact(videoId: videoId, eventId: serverEventId) {
            case .deleted:
                updated.syncStatus    = .deleted
                updated.failureReason = nil
            case .notFoundAmbiguous:
                updated.syncStatus    = .needsReconciliation
                updated.failureReason = "delete_404_ambiguous"
            }
        } catch let error as AnnotationAPIError {
            switch error {
            case .retryable:
                updated.syncStatus    = .needsReconciliation
                updated.failureReason = "delete_timeout_or_unavailable"
            default:
                updated.syncStatus    = .failedPermanent
                updated.failureReason = error.errorDescription
            }
        } catch {
            updated.syncStatus    = .needsReconciliation
            updated.failureReason = "delete_network_error"
        }
        return updated
    }

    // MARK: — Finish readiness

    // An active event is one neither deleted locally nor confirmed deleted
    // on the server. Finish is blocked while any active event is in an
    // in-flight, retryable, conflicted, or unresolved state.
    func finishReadiness(for session: AnnotationSessionFile) -> FinishReadiness {
        let activeEvents = session.drafts.filter { !$0.deletedLocally && $0.syncStatus != .deleted }

        let blockingStatuses = activeEvents
            .map { $0.syncStatus }
            .filter(Self.isBlocking)

        if !blockingStatuses.isEmpty {
            return .blocked(blockingStatuses)
        }

        let syncedCount = activeEvents.filter { $0.syncStatus == .synced }.count
        return syncedCount > 0 ? .readyWithCount(syncedCount) : .readyZero
    }

    static func isBlocking(_ status: ContactEventSyncStatus) -> Bool {
        switch status {
        case .localOnly, .syncing, .updating, .deleting,
             .retryPending, .conflicted, .needsReconciliation:
            return true
        case .synced, .deleted, .failedPermanent:
            return false
        }
    }

    // MARK: — Conflict resolution (AN-3A)

    // Applies authoritative server state to a .conflicted draft, transitioning
    // it to .synced with the server's current version. Used by
    // JugglingAnnotationViewModel.resolveConflict() which fetches the server
    // event via listContacts before calling this.
    func resolveConflictedDraft(
        draft:       ContactEventDraft,
        serverEvent: ContactEventOut
    ) -> ContactEventDraft {
        applyServerState(to: draft, from: serverEvent)
    }

    // MARK: — Private helpers

    private func applyServerState(to draft: ContactEventDraft, from server: ContactEventOut) -> ContactEventDraft {
        var updated = draft
        updated.serverEventId      = server.eventId
        updated.version             = server.version
        updated.contactType         = server.contactType
        updated.side                = server.side
        updated.annotationConfidence = server.annotationConfidence
        updated.customLabel         = server.customLabel
        updated.customDescription   = server.customDescription
        updated.serverCreatedAt     = server.createdAt
        updated.serverUpdatedAt     = server.updatedAt
        updated.syncStatus          = .synced
        updated.retryCount          = 0
        updated.failureReason       = nil
        return updated
    }

    private func applyCreateFailure(to draft: ContactEventDraft, error: AnnotationAPIError) -> ContactEventDraft {
        var updated = draft
        switch error {
        case .retryable:
            if updated.retryCount < Self.maxRetries {
                updated.retryCount += 1
                updated.syncStatus  = .retryPending
            } else {
                updated.syncStatus  = .failedPermanent
            }
            updated.failureReason = error.errorDescription
        case .idempotencyConflict(let detail):
            // Same device_event_id with a different payload — not retryable
            // automatically; requires the user to resolve the conflict.
            updated.syncStatus    = .failedPermanent
            updated.failureReason = "idempotency_conflict: \(detail)"
        default:
            updated.syncStatus    = .failedPermanent
            updated.failureReason = error.errorDescription
        }
        return updated
    }
}
