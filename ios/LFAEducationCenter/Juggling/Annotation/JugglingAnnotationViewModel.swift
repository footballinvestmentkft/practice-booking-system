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

// MARK: — AnnotationSaveStatus
//
// Derived, read-only view of the VM's actual save state — never a separate
// UI-local flag. AnnotationDebugOverlay and JugglingAnnotationScreen both
// read this from the same source of truth (isSaving / saveError / session).
enum AnnotationSaveStatus: Equatable {
    case idle      // no session loaded yet
    case saving    // a localStore.save() call is in flight
    case saved     // last save succeeded (or nothing pending)
    case failed    // last save threw — saveError is non-nil
}

// MARK: — AnnotationScreenMode
//
// Phase 1 (marking) vs. Phase 2 (per-event labeling). Set explicitly by
// enterLabelingMode() / exitLabelingMode() — never inferred implicitly, so
// the Screen can rely on it to drive navigation.
enum AnnotationScreenMode: Equatable {
    case marking
    case labeling
}

// MARK: — LabelingCTAState (AN-3B2A P2B-5A)
//
// Derived from the active event set. Drives both showLabelingCTA and the
// CTA button label in JugglingAnnotationScreen. Priority (highest first):
//   hidden         — no active events at all
//   unlabeled      — at least one .unlabeled event; label-first is most urgent
//   resumeLabeling — no .unlabeled, but stuck .labelPending (e.g. app crash mid-session)
//   hasProblems    — .conflicted or .failedPermanent events need attention
//   reviewLocal    — every event is .localOnly (all labeled, none synced)
//   viewOrEdit     — catch-all: mixed, synced, in-flight, or retry states
enum LabelingCTAState: Equatable {
    case hidden
    case unlabeled
    case resumeLabeling
    case hasProblems
    case reviewLocal
    case viewOrEdit
}

@MainActor
final class JugglingAnnotationViewModel: ObservableObject {

    @Published private(set) var session:           AnnotationSessionFile?
    @Published private(set) var taxonomy:           TaxonomyDocument?
    @Published private(set) var loadWarning:        String?
    @Published private(set) var isFinishing:        Bool = false
    @Published private(set) var finishResult:       FinishAnnotationOut?
    @Published private(set) var finishError:        String?
    // Set when a local persistence write fails. The UI must not claim the
    // affected change is saved while this is non-nil.
    @Published private(set) var saveError:          String?
    // True only while a localStore.save() call is actually in flight.
    @Published private(set) var isSaving:           Bool = false
    #if DEBUG
    // AN-3B2A P0 — read-only diagnostics snapshot for AnnotationDebugOverlay.
    // Never used to make decisions; purely observational.
    @Published private(set) var diagnostics = AnnotationDiagnosticsSnapshot()
    #endif
    // deviceEventId of the conflict waiting for user resolution, or nil.
    @Published private(set) var pendingConflictId:  UUID? = nil
    // AN-3B2A P2 — marking vs. labeling. Set by enterLabelingMode() /
    // exitLabelingMode(); the Screen reads this to drive navigation.
    @Published private(set) var screenMode:         AnnotationScreenMode = .marking

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
        precondition(userId > 0, "JugglingAnnotationViewModel requires a valid, positive userId")
        self.userId  = userId
        self.videoId = videoId

        let api = JugglingAnnotationAPIClient(authManager: authManager)
        self.apiClient    = api
        self.taxonomyStore = ContactTaxonomyStore(authManager: authManager)
        self.localStore    = LocalAnnotationStore()
        self.syncEngine    = AnnotationSyncEngine(apiClient: api)
        #if DEBUG
        AnnotationDiagnosticsLog.log("VM init — userId=\(userId) videoId=\(videoId) authCurrentUserId=\(authManager.currentUserId.map(String.init) ?? "nil") build=\(AnnotationBuildInfo.tag)")
        #endif
    }

    // Test/DI entry point — bypasses AuthManager/JugglingAnnotationAPIClient construction.
    init(
        userId:        Int,
        videoId:       String,
        apiClient:     JugglingAnnotationAPIClientProtocol,
        taxonomyStore: ContactTaxonomyStore,
        localStore:    LocalAnnotationStore
    ) {
        precondition(userId > 0, "JugglingAnnotationViewModel requires a valid, positive userId")
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
        #if DEBUG
        AnnotationDiagnosticsLog.log("onAppear — userId=\(userId) videoId=\(videoId) path=\(localStore.sessionFileURL(userId: userId, videoId: videoId).path) fileExists=\(localStore.sessionFileExists(userId: userId, videoId: videoId))")
        #endif

        taxonomyStore.loadBundled()
        taxonomy = taxonomyStore.document

        let loadResult = localStore.load(userId: userId, videoId: videoId)

        #if DEBUG
        switch loadResult {
        case .notFound:
            diagnostics.loadResult = .notFound
            diagnostics.quarantinePath = nil
        case .loaded(let loaded):
            diagnostics.loadResult = .loaded(draftCount: loaded.drafts.count)
            diagnostics.quarantinePath = nil
        case .quarantined(let path, let hasLocalOnly):
            diagnostics.loadResult = .quarantined(path: path, hasLocalOnlyEvents: hasLocalOnly)
            diagnostics.quarantinePath = path
        }
        diagnostics.lastLoadAt = Date()
        AnnotationDiagnosticsLog.log("onAppear — load result: \(diagnostics.loadResult)")
        #endif

        switch loadResult {
        case .notFound:
            // No file exists for this (userId, videoId) — safe to create one.
            createAndPersistFreshSession()

        case .loaded(let loaded):
            session = loaded

        case .quarantined(_, let hasLocalOnlyEvents):
            // The corrupt file has already been moved to quarantine/ (original
            // path is vacated, original bytes preserved) — creating a fresh
            // session here does not overwrite anything.
            loadWarning = hasLocalOnlyEvents
                ? "A korábbi annotációs adatok megsérültek és karanténba kerültek. Néhány nem-szinkronizált esemény elveszhetett."
                : "A korábbi annotációs adatok megsérültek és karanténba kerültek. Új session indul."
            createAndPersistFreshSession()
        }

        await taxonomyStore.refreshFromBackend()
        taxonomy = taxonomyStore.document
    }

    // Explicit, awaitable save of the current session. This is the single
    // source of truth for "save" — both the "Mentés és bezárás" CTA and the
    // SwiftUI .onDisappear lifecycle hook call this same method, so there is
    // exactly one save path (no separate UI-local save logic, no double-save
    // race: isSaving makes the in-flight state observable to the UI).
    //
    // Returns true if there is nothing to save, or the save succeeded.
    // Returns false only if a save was attempted and threw — saveError is
    // set in that case and the caller (Screen) must not dismiss.
    @discardableResult
    func saveNow() -> Bool {
        #if DEBUG
        AnnotationDiagnosticsLog.log("saveNow — userId=\(userId) videoId=\(videoId) activeEvents=\(activeEvents.count)")
        #endif
        guard var current = session else { return true }
        let ok = persistSession(&current, logContext: "saveNow")
        session = current
        return ok
    }

    // Call when the annotation screen disappears — persists any in-memory
    // changes via saveNow(). Idempotent: the Screen guards against calling
    // this more than once per appearance (didCleanUp).
    @discardableResult
    func onDisappear() -> Bool {
        #if DEBUG
        AnnotationDiagnosticsLog.log("onDisappear — userId=\(userId) videoId=\(videoId) activeEvents=\(activeEvents.count)")
        #endif
        return saveNow()
    }

    // Shared persistence helper — every save path (markTimestamp, saveNow,
    // ...) routes through this so isSaving/saveError/diagnostics always
    // reflect the real outcome of the most recent localStore.save() call.
    private func persistSession(_ current: inout AnnotationSessionFile, logContext: String) -> Bool {
        isSaving = true
        defer { isSaving = false }
        do {
            try localStore.save(session: &current)
            saveError = nil
            #if DEBUG
            diagnostics.lastSaveResult = .success
            diagnostics.lastSaveAt = Date()
            AnnotationDiagnosticsLog.log("\(logContext) — save result: success")
            #endif
            return true
        } catch {
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
            #if DEBUG
            diagnostics.lastSaveResult = .failed(error.localizedDescription)
            diagnostics.lastSaveAt = Date()
            AnnotationDiagnosticsLog.log("\(logContext) — save result: failed: \(error.localizedDescription)")
            #endif
            return false
        }
    }

    // Clears a previously-set save error (e.g. after the user dismisses an alert).
    func clearSaveError() {
        saveError = nil
    }

    // Phase 2A: upload a pose snapshot for a synced event.
    // Casts the internal apiClient to the concrete type; returns immediately
    // if the client is a test mock (cast returns nil → no upload, no error).
    func uploadPendingPoseSnapshot(serverEventId: UUID, request: PoseSnapshotUploadRequest) async {
        guard let client = apiClient as? JugglingAnnotationAPIClient else { return }
        await client.uploadPoseSnapshot(videoId: videoId, eventId: serverEventId, request: request)
    }

    // Phase 2A: fetch all existing pose snapshots for this video.
    // Returns [] when the feature flag is off (503 is swallowed in the API client)
    // or when the client is a test mock.
    func fetchPoseSnapshots() async -> [PoseSnapshotOut] {
        guard let client = apiClient as? JugglingAnnotationAPIClient else { return [] }
        return await client.fetchPoseSnapshots(videoId: videoId)
    }

    // Persist user display rotation to the server (non-throwing).
    // Returns immediately for test mocks; errors are logged in the API client.
    func patchRotation(degrees: Int) async {
        guard let client = apiClient as? JugglingAnnotationAPIClient else { return }
        await client.patchRotation(videoId: videoId, degrees: degrees)
    }

    // Creates an empty session and persists it immediately. Used by onAppear()
    // for .notFound and .quarantined results. session is set in-memory even if
    // the save fails (there is nothing to roll back to), but saveError is set
    // so the UI can surface the failure.
    private func createAndPersistFreshSession() {
        var fresh = localStore.emptySession(
            userId: userId, videoId: videoId,
            taxonomyVersion: taxonomyStore.document?.taxonomyVersion ?? "v1"
        )
        do {
            try localStore.save(session: &fresh)
            saveError = nil
        } catch {
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
        }
        session = fresh
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
        #if DEBUG
        AnnotationDiagnosticsLog.log("markTimestamp — saving before: drafts=\(session?.drafts.count ?? -1) → after-append=\(current.drafts.count) path=\(localStore.sessionFileURL(userId: userId, videoId: videoId).path)")
        #endif
        let ok = persistSession(&current, logContext: "markTimestamp")
        guard ok else {
            // Roll back — do not present a memory-only event as persisted.
            return nil
        }
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
        do {
            try localStore.save(session: &current)
            saveError = nil
            session = current
            return draft
        } catch {
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
            return nil
        }
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
        do {
            try localStore.save(session: &current)
            saveError = nil
            session = current
            return true
        } catch {
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
            return false
        }
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
            do {
                try localStore.save(session: &current)
                saveError = nil
            } catch {
                saveError = "A mentés sikertelen: \(error.localizedDescription)"
            }
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
        do {
            try localStore.save(session: &current)
            saveError = nil
        } catch {
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
        }
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
        do {
            try localStore.save(session: &current)
            saveError = nil
        } catch {
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
        }
        session = current
    }

    // Marks a draft for deletion. Drafts never synced are removed locally
    // immediately; synced drafts are flagged for a DELETE push on next flush.
    func markDeleted(deviceEventId: UUID) {
        guard var current = session else { return }
        guard let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId }) else { return }

        current.drafts[index].deletedLocally = true
        let s = current.drafts[index].syncStatus
        if s == .localOnly || s == .unlabeled || s == .labelPending {
            // Never reached the server — nothing to delete remotely.
            current.drafts[index].syncStatus = .deleted
        }

        do {
            try localStore.save(session: &current)
            saveError = nil
            session = current
        } catch {
            // Roll back — the event must not appear deleted if the change wasn't persisted.
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
        }
    }

    // Derived from real state only — never set independently of isSaving/
    // saveError/session, so the UI cannot drift from what actually happened.
    var saveStatus: AnnotationSaveStatus {
        if isSaving { return .saving }
        if saveError != nil { return .failed }
        if session != nil { return .saved }
        return .idle
    }

    var activeEvents: [ContactEventDraft] {
        session?.drafts.filter { !$0.deletedLocally && $0.syncStatus != .deleted } ?? []
    }

    var unlabeledCount: Int {
        activeEvents.filter { $0.syncStatus == .unlabeled }.count
    }

    var labelPendingCount: Int {
        activeEvents.filter { $0.syncStatus == .labelPending }.count
    }

    // MARK: — P2B-5A: labeling overview helpers

    // Events with a contactType assigned (regardless of sync state).
    var labeledCount: Int {
        activeEvents.filter { $0.contactType != nil }.count
    }

    // First event (by timestamp) still awaiting labeling: .unlabeled or stuck .labelPending.
    var nextUnlabeledId: UUID? {
        activeEvents
            .sorted { $0.timestampMs < $1.timestampMs }
            .first { $0.syncStatus == .unlabeled || $0.syncStatus == .labelPending }
            .map { $0.deviceEventId }
    }

    // Derived CTA state — never set independently; computed fresh on every access.
    var labelingCTAState: LabelingCTAState {
        let events = activeEvents
        guard !events.isEmpty else { return .hidden }
        if events.contains(where: { $0.syncStatus == .unlabeled })            { return .unlabeled }
        if events.contains(where: { $0.syncStatus == .labelPending })         { return .resumeLabeling }
        if events.contains(where: {
            $0.syncStatus == .conflicted || $0.syncStatus == .failedPermanent
        })                                                                     { return .hasProblems }
        if events.allSatisfy({ $0.syncStatus == .localOnly })                 { return .reviewLocal }
        return .viewOrEdit
    }

    var showLabelingCTA: Bool { labelingCTAState != .hidden }

    var labelingCTAText: String {
        switch labelingCTAState {
        case .hidden:          return ""
        case .unlabeled:       return "Tovább a címkézéshez"
        case .resumeLabeling:  return "Címkézés folytatása"
        case .hasProblems:     return "Problémás események"
        case .reviewLocal:     return "Címkék áttekintése"
        case .viewOrEdit:      return "Megtekintés / szerkesztés"
        }
    }

    // Transitions all .unlabeled drafts to .labelPending, persists, and
    // switches screenMode to .labeling so the Screen can navigate to the
    // labeling flow.
    //
    // Idempotent: safe to call when .labelPending events already exist (e.g. after
    // an app crash mid-session). If no .unlabeled events are present, no persist is
    // performed — screenMode is set directly if labelable events exist.
    func enterLabelingMode() {
        guard var current = session else { return }
        var changed = false
        for index in current.drafts.indices {
            if current.drafts[index].syncStatus == .unlabeled {
                current.drafts[index].syncStatus = .labelPending
                changed = true
            }
        }
        if changed {
            let ok = persistSession(&current, logContext: "enterLabelingMode")
            session = current
            if ok { screenMode = .labeling }
        } else {
            // No transitions needed — set .labeling if there are events the user can act on.
            let hasLabelable = current.drafts.contains {
                !$0.deletedLocally && $0.syncStatus != .deleted &&
                ($0.syncStatus == .labelPending || $0.syncStatus == .localOnly ||
                 $0.syncStatus == .synced       || $0.syncStatus == .retryPending)
            }
            if hasLabelable { screenMode = .labeling }
        }
    }

    // Transitions a single .unlabeled draft to .labelPending (AN-3B2A P2B-5D).
    //
    // Called by LabelingOverviewView when the user taps a card CTA for a
    // specific .unlabeled event. Only that draft is transitioned; other
    // .unlabeled events are not affected (cf. enterLabelingMode which
    // transitions all).
    //
    // Returns true when the event is now editable:
    //   .unlabeled      → transitions to .labelPending, persists, returns true
    //   already editable (.labelPending / .localOnly / .synced / .retryPending /
    //                     .failedPermanent) → no-op, returns true
    // Returns false for blocked states or unknown ID.
    @discardableResult
    func markEventForLabeling(deviceEventId: UUID) -> Bool {
        guard var current = session,
              let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId })
        else { return false }

        switch current.drafts[index].syncStatus {
        case .labelPending, .localOnly, .synced, .retryPending, .failedPermanent:
            return true  // already editable — caller may open detail immediately
        case .unlabeled:
            break        // transition below
        case .syncing, .updating, .deleting, .conflicted, .needsReconciliation, .deleted:
            return false // blocked
        }

        current.drafts[index].syncStatus = .labelPending
        let ok = persistSession(&current, logContext: "markEventForLabeling")
        guard ok else { return false }
        session = current
        return true
    }

    // Returns to marking mode. Called when the labeling screen is closed.
    // Does not touch session state — labelEvent() already persisted every
    // change made while labeling.
    func exitLabelingMode() {
        screenMode = .marking
    }

    // AN-3B2A P2 — assigns contact type/side/confidence to a .labelPending
    // event (Phase 1 → Phase 2 labeling) or re-labels an already-.localOnly
    // event (back-navigation within the labeling flow). Persists immediately.
    //
    // Returns true on success (draft is now .localOnly with the given
    // fields). Returns false if the draft is not in a labelable state, or if
    // the save failed — saveError is set in the latter case and the in-memory
    // session is left unchanged (no rollback needed: `current` is a local copy).
    @discardableResult
    func labelEvent(
        deviceEventId:        UUID,
        contactType:          String,
        side:                 String?,
        annotationConfidence: String,
        customLabel:          String? = nil,
        customDescription:    String? = nil
    ) -> Bool {
        guard var current = session,
              let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId }),
              current.drafts[index].syncStatus == .labelPending || current.drafts[index].syncStatus == .localOnly
        else { return false }

        current.drafts[index].contactType          = contactType
        current.drafts[index].side                 = side
        current.drafts[index].annotationConfidence = annotationConfidence
        current.drafts[index].customLabel          = customLabel
        current.drafts[index].customDescription    = customDescription
        current.drafts[index].syncStatus           = .localOnly

        let ok = persistSession(&current, logContext: "labelEvent")
        guard ok else { return false }
        session = current
        return true
    }

    // Unified relabeling write path for the LabelingOverviewView flow (AN-3B2A P2B-5A).
    //
    // Routing:
    //   .labelPending / .localOnly  → labelEvent()   (first-time label or in-session relabel)
    //   .synced / .retryPending /
    //   .failedPermanent (non-idempotency) → editEvent()  (post-sync correction)
    //
    // Blocked (returns false, no state change):
    //   .unlabeled          — enterLabelingMode() must be called first
    //   .syncing / .updating / .deleting — in-flight; caller must wait
    //   .conflicted         — resolveConflict() required
    //   .needsReconciliation — reconcile() required
    //   .deleted            — unreachable from active timeline
    //
    // Note: for .failedPermanent with idempotency_conflict reason, editEvent()
    // returns false internally — this method propagates that result unchanged.
    // No backend sync is triggered here.
    @discardableResult
    func relabelEvent(
        deviceEventId:        UUID,
        contactType:          String,
        side:                 String?,
        annotationConfidence: String,
        customLabel:          String? = nil,
        customDescription:    String? = nil
    ) -> Bool {
        guard let draft = activeEvents.first(where: { $0.deviceEventId == deviceEventId })
        else { return false }

        switch draft.syncStatus {
        case .labelPending, .localOnly:
            return labelEvent(
                deviceEventId:        deviceEventId,
                contactType:          contactType,
                side:                 side,
                annotationConfidence: annotationConfidence,
                customLabel:          customLabel,
                customDescription:    customDescription
            )
        case .synced, .retryPending, .failedPermanent:
            return editEvent(
                deviceEventId:        deviceEventId,
                contactType:          contactType,
                side:                 side,
                annotationConfidence: annotationConfidence,
                customLabel:          customLabel,
                customDescription:    customDescription
            )
        case .unlabeled, .syncing, .updating, .deleting, .conflicted, .needsReconciliation, .deleted:
            return false
        }
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
        do {
            try localStore.save(session: &current)
            saveError = nil
        } catch {
            // Sync state already changed server-side; surface the local
            // persistence failure without discarding the updated sync state.
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
        }
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
        do {
            try localStore.save(session: &current)
            saveError = nil
        } catch {
            saveError = "A mentés sikertelen: \(error.localizedDescription)"
        }

        if current.drafts.contains(where: { $0.syncStatus == .needsReconciliation }) {
            do {
                try await syncEngine.reconcile(session: &current)
                do {
                    try localStore.save(session: &current)
                    saveError = nil
                } catch {
                    saveError = "A mentés sikertelen: \(error.localizedDescription)"
                }
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

    #if DEBUG
    // MARK: — Diagnostics accessors (AnnotationDebugOverlay)

    var diagSessionFilePath: URL {
        localStore.sessionFileURL(userId: userId, videoId: videoId)
    }

    var diagSessionFileExists: Bool {
        localStore.sessionFileExists(userId: userId, videoId: videoId)
    }

    var diagQuarantineDirectory: URL {
        localStore.quarantineDirectory(userId: userId, videoId: videoId)
    }
    #endif

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
