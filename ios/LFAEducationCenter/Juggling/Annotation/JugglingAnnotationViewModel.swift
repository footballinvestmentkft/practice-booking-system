import Foundation

// MARK: — JugglingAnnotationViewModel
//
// Orchestrates taxonomy loading, local draft persistence, and server sync
// for one (userId, videoId) annotation session. No SwiftUI screen exists yet
// (AN-3) — this view model exposes @Published state so a future screen can
// bind directly.
//
// User isolation: userId is fixed at init and threaded through every store
// call. clearSession() must be called on logout/user switch so a new
// instance is created for the next user — this view model never re-targets
// an existing session to a different userId.

@MainActor
final class JugglingAnnotationViewModel: ObservableObject {

    @Published private(set) var session:           AnnotationSessionFile?
    @Published private(set) var taxonomy:           TaxonomyDocument?
    @Published private(set) var loadWarning:        String?
    @Published private(set) var isFinishing:        Bool = false
    @Published private(set) var finishResult:       FinishAnnotationOut?
    @Published private(set) var finishError:        String?
    // deviceEventId of the conflict waiting for user resolution, or nil.
    @Published private(set) var pendingConflictId:  UUID? = nil

    let userId:  Int
    let videoId: String

    private let taxonomyStore: ContactTaxonomyStore
    private let localStore:    LocalAnnotationStore
    private let syncEngine:    AnnotationSyncEngine
    private let apiClient:     JugglingAnnotationAPIClientProtocol

    init(
        userId:      Int,
        videoId:     String,
        authManager: AuthManager
    ) {
        self.userId  = userId
        self.videoId = videoId

        let api = JugglingAnnotationAPIClient(authManager: authManager)
        self.apiClient    = api
        self.taxonomyStore = ContactTaxonomyStore(authManager: authManager)
        self.localStore    = LocalAnnotationStore()
        self.syncEngine    = AnnotationSyncEngine(apiClient: api)
    }

    // Test/DI entry point — bypasses AuthManager/JugglingAnnotationAPIClient construction.
    init(
        userId:        Int,
        videoId:       String,
        apiClient:     JugglingAnnotationAPIClientProtocol,
        taxonomyStore: ContactTaxonomyStore,
        localStore:    LocalAnnotationStore
    ) {
        self.userId        = userId
        self.videoId       = videoId
        self.apiClient     = apiClient
        self.taxonomyStore = taxonomyStore
        self.localStore    = localStore
        self.syncEngine    = AnnotationSyncEngine(apiClient: apiClient)
    }

    // MARK: — Lifecycle

    // Call once when the annotation screen appears. Loads bundled taxonomy
    // synchronously (always available), loads/creates the local session,
    // then attempts a background taxonomy refresh.
    func onAppear() async {
        taxonomyStore.loadBundled()
        taxonomy = taxonomyStore.document

        switch localStore.load(userId: userId, videoId: videoId) {
        case .empty:
            var fresh = localStore.emptySession(
                userId: userId, videoId: videoId,
                taxonomyVersion: taxonomyStore.document?.taxonomyVersion ?? "v1"
            )
            try? localStore.save(session: &fresh)
            session = fresh

        case .loaded(let loaded):
            session = loaded

        case .quarantined(_, let hasLocalOnlyEvents):
            loadWarning = hasLocalOnlyEvents
                ? "A korábbi annotációs adatok megsérültek és karanténba kerültek. Néhány nem-szinkronizált esemény elveszhetett."
                : "A korábbi annotációs adatok megsérültek és karanténba kerültek. Új session indul."
            var fresh = localStore.emptySession(
                userId: userId, videoId: videoId,
                taxonomyVersion: taxonomyStore.document?.taxonomyVersion ?? "v1"
            )
            try? localStore.save(session: &fresh)
            session = fresh
        }

        await taxonomyStore.refreshFromBackend()
        taxonomy = taxonomyStore.document
    }

    // Call when the annotation screen disappears — persists any in-memory changes.
    func onDisappear() {
        guard var current = session else { return }
        try? localStore.save(session: &current)
    }

    // Call on logout / user switch. Drops in-memory state for this instance;
    // the caller must construct a new view model for the next user (userId
    // is immutable here by design).
    func clearSession() {
        session          = nil
        loadWarning      = nil
        finishResult     = nil
        finishError      = nil
        pendingConflictId = nil
    }

    // MARK: — Draft management

    // Phase 1 entry point: marks the current playback position as a contact event.
    // contactType is nil; the event stays .unlabeled until Phase 2 labels it (AN-3B2B).
    // Dedup: ignores taps within 200 ms of an existing active event (guards
    // against accidental double-tap on the same video position).
    // Returns the created draft, or nil if dedup blocked the mark.
    @discardableResult
    func markTimestamp(ms: Int) -> ContactEventDraft? {
        guard var current = session else { return nil }

        let dedupWindowMs = 200
        let isDuplicate = current.drafts.contains { draft in
            !draft.deletedLocally &&
            draft.syncStatus != .deleted &&
            abs(draft.timestampMs - ms) < dedupWindowMs
        }
        guard !isDuplicate else { return nil }

        let draft = ContactEventDraft.timestamp(ms: ms)
        current.drafts.append(draft)
        try? localStore.save(session: &current)
        session = current
        return draft
    }

    @discardableResult
    func addEvent(
        timestampMs:          Int,
        contactType:          String,
        side:                 String?,
        annotationConfidence: String,
        customLabel:          String? = nil,
        customDescription:    String? = nil
    ) -> ContactEventDraft? {
        guard var current = session else { return nil }

        let draft = ContactEventDraft.new(
            timestampMs:          timestampMs,
            contactType:          contactType,
            side:                 side,
            annotationConfidence: annotationConfidence,
            customLabel:          customLabel,
            customDescription:    customDescription
        )
        current.drafts.append(draft)
        try? localStore.save(session: &current)
        session = current
        return draft
    }

    // Edits an existing draft in-place. Permitted statuses and resulting transitions:
    //   .localOnly     → stays .localOnly      (edit before first POST, included in pushCreate)
    //   .retryPending  → .localOnly            (reset retry, POST the updated payload)
    //   .synced        → .updating             (PATCH will be sent on next flushPending)
    //   .failedPermanent (no serverEventId, not idempotency_conflict) → .localOnly  (retry POST)
    //   .failedPermanent (serverEventId != nil, not idempotency_conflict) → .updating (retry PATCH)
    //
    // Blocked (no state change):
    //   .failedPermanent with idempotency_conflict reason → caller must use resolveConflict
    //   .conflicted, .needsReconciliation → caller must resolve / reconcile first
    //   .syncing, .updating, .deleting    → in-flight, cannot edit
    //   .deleted                          → unreachable from active timeline
    //
    // Invariants: deviceEventId (let) never changes. version is NOT mutated here —
    // the PATCH carries the last-known-good version; applyServerState updates it on success.
    @discardableResult
    func editEvent(
        deviceEventId:        UUID,
        contactType:          String,
        side:                 String?,
        annotationConfidence: String,
        customLabel:          String? = nil,
        customDescription:    String? = nil
    ) -> Bool {
        guard var current = session,
              let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId })
        else { return false }

        var draft = current.drafts[index]

        switch draft.syncStatus {
        case .localOnly:
            draft.contactType          = contactType
            draft.side                 = side
            draft.annotationConfidence = annotationConfidence
            draft.customLabel          = customLabel
            draft.customDescription    = customDescription

        case .retryPending:
            draft.contactType          = contactType
            draft.side                 = side
            draft.annotationConfidence = annotationConfidence
            draft.customLabel          = customLabel
            draft.customDescription    = customDescription
            draft.syncStatus           = .localOnly
            draft.retryCount           = 0
            draft.failureReason        = nil

        case .synced:
            draft.contactType          = contactType
            draft.side                 = side
            draft.annotationConfidence = annotationConfidence
            draft.customLabel          = customLabel
            draft.customDescription    = customDescription
            draft.syncStatus           = .updating

        case .failedPermanent:
            // idempotency_conflict cannot be fixed by editing alone — requires resolveConflict
            if let reason = draft.failureReason, reason.hasPrefix("idempotency_conflict:") {
                return false
            }
            draft.contactType          = contactType
            draft.side                 = side
            draft.annotationConfidence = annotationConfidence
            draft.customLabel          = customLabel
            draft.customDescription    = customDescription
            draft.failureReason        = nil
            draft.retryCount           = 0
            draft.syncStatus           = draft.serverEventId != nil ? .updating : .localOnly

        case .unlabeled, .labelPending:
            // Phase 1 events have no contactType yet — edit is Phase 2 (AN-3B2B) scope.
            return false

        case .conflicted, .needsReconciliation, .syncing, .updating, .deleting, .deleted:
            return false
        }

        current.drafts[index] = draft
        try? localStore.save(session: &current)
        session = current
        return true
    }

    // Resolves a .conflicted draft by fetching the authoritative server state via
    // GET /contacts. If conflictRetryCount ≤ 3, applies server state silently
    // (.synced). If retries exceeded, sets pendingServerSnapshot and
    // pendingConflictId for user resolution (AN-3B2B scope).
    // If the draft is absent from the server response it is treated as .deleted.
    // On network error the draft stays .conflicted so the caller can retry.
    func resolveConflict(deviceEventId: UUID) async {
        guard var current = session,
              let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId }),
              current.drafts[index].syncStatus == .conflicted
        else { return }

        do {
            let list = try await apiClient.listContacts(videoId: videoId)
            if let serverEvent = list.events.first(where: { $0.deviceEventId == deviceEventId }) {
                current.drafts[index].conflictRetryCount += 1
                if current.drafts[index].conflictRetryCount <= 3 {
                    // Auto-resolve: server wins silently (mirrors AN-3A server-wins policy)
                    current.drafts[index] = syncEngine.resolveConflictedDraft(
                        draft:       current.drafts[index],
                        serverEvent: serverEvent
                    )
                    current.drafts[index].pendingServerSnapshot = nil
                    pendingConflictId = firstPendingConflict(in: current)
                } else {
                    // Max retries exceeded: surface to user for manual decision
                    current.drafts[index].pendingServerSnapshot = serverEvent
                    pendingConflictId = deviceEventId
                }
            } else {
                current.drafts[index].syncStatus    = .deleted
                current.drafts[index].failureReason = nil
                current.drafts[index].pendingServerSnapshot = nil
                pendingConflictId = firstPendingConflict(in: current)
            }
            try? localStore.save(session: &current)
            session = current
        } catch {
            // Network error: draft remains .conflicted; UI should offer "Retry resolve".
        }
    }

    // User accepts the server version: applies pendingServerSnapshot → .synced.
    // pendingConflictId advances to the next conflict if one exists.
    func acceptServerVersion(deviceEventId: UUID) {
        guard var current = session,
              let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId }),
              let snapshot = current.drafts[index].pendingServerSnapshot
        else { return }

        current.drafts[index] = syncEngine.resolveConflictedDraft(
            draft:       current.drafts[index],
            serverEvent: snapshot
        )
        current.drafts[index].pendingServerSnapshot = nil
        pendingConflictId = firstPendingConflict(in: current)
        try? localStore.save(session: &current)
        session = current
    }

    // User keeps the local version: clears the snapshot, resets to .localOnly
    // so the next flushPending will POST the local payload again.
    func keepLocalVersion(deviceEventId: UUID) {
        guard var current = session,
              let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId })
        else { return }

        current.drafts[index].pendingServerSnapshot = nil
        current.drafts[index].conflictRetryCount    = 0
        current.drafts[index].syncStatus            = .localOnly
        current.drafts[index].failureReason         = nil
        pendingConflictId = firstPendingConflict(in: current)
        try? localStore.save(session: &current)
        session = current
    }

    // Marks a draft for deletion. Drafts never synced are removed locally
    // immediately; synced drafts are flagged for a DELETE push on next flush.
    func markDeleted(deviceEventId: UUID) {
        guard var current = session else { return }
        guard let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId }) else { return }

        current.drafts[index].deletedLocally = true
        let s = current.drafts[index].syncStatus
        if s == .localOnly || s == .unlabeled {
            // Never reached the server — nothing to delete remotely.
            current.drafts[index].syncStatus = .deleted
        }

        try? localStore.save(session: &current)
        session = current
    }

    var activeEvents: [ContactEventDraft] {
        session?.drafts.filter { !$0.deletedLocally && $0.syncStatus != .deleted } ?? []
    }

    var finishReadiness: FinishReadiness {
        guard let current = session else { return .readyZero }
        return syncEngine.finishReadiness(for: current)
    }

    // MARK: — Background sync

    // Pushes all localOnly / retryPending drafts. Safe to call repeatedly
    // (e.g. from a periodic retry timer in the future UI layer).
    func flushPending() async {
        guard var current = session else { return }
        await syncEngine.flushPending(session: &current)
        try? localStore.save(session: &current)
        session = current
    }

    // MARK: — Finish flow
    //
    // 1. Flush localOnly / retryPending drafts.
    // 2. Reconcile needsReconciliation drafts via GET /contacts.
    // 3. Re-check readiness — abort with finishError if still blocked.
    // 4. Call POST /finish (confirm_zero_contacts = true iff zero active events).
    // 5. On success, delete the local session file (server is now authoritative).
    func finish() async {
        guard var current = session else { return }

        isFinishing  = true
        finishError  = nil
        finishResult = nil
        defer { isFinishing = false }

        await syncEngine.flushPending(session: &current)
        try? localStore.save(session: &current)

        if current.drafts.contains(where: { $0.syncStatus == .needsReconciliation }) {
            do {
                try await syncEngine.reconcile(session: &current)
                try? localStore.save(session: &current)
            } catch {
                session = current
                finishError = "A befejezés sikertelen: nem sikerült egyeztetni a szerverrel. Ellenőrizd a kapcsolatot, és próbáld újra."
                return
            }
        }

        let readiness = syncEngine.finishReadiness(for: current)
        session = current

        switch readiness {
        case .blocked:
            finishError = "A befejezés sikertelen: néhány esemény még nincs szinkronizálva. Próbáld újra később."
            return

        case .readyZero:
            await callFinish(confirmZero: true)

        case .readyWithCount:
            await callFinish(confirmZero: false)
        }
    }

    // MARK: — Private

    private func firstPendingConflict(in session: AnnotationSessionFile) -> UUID? {
        session.drafts.first(where: {
            $0.syncStatus == .conflicted && $0.pendingServerSnapshot != nil
        })?.deviceEventId
    }

    private func callFinish(confirmZero: Bool) async {
        do {
            let result = try await apiClient.finishAnnotation(videoId: videoId, confirmZero: confirmZero)
            localStore.delete(userId: userId, videoId: videoId)
            session      = nil
            finishResult = result
        } catch let error as AnnotationAPIError {
            finishError = error.errorDescription
        } catch {
            finishError = "A befejezés sikertelen: \(error.localizedDescription)"
        }
    }
}
