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

    @Published private(set) var session:      AnnotationSessionFile?
    @Published private(set) var taxonomy:      TaxonomyDocument?
    @Published private(set) var loadWarning:   String?
    @Published private(set) var isFinishing:   Bool = false
    @Published private(set) var finishResult:  FinishAnnotationOut?
    @Published private(set) var finishError:   String?

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
        session     = nil
        loadWarning  = nil
        finishResult = nil
        finishError  = nil
    }

    // MARK: — Draft management

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

    // Marks a draft for deletion. Drafts never synced are removed locally
    // immediately; synced drafts are flagged for a DELETE push on next flush.
    func markDeleted(deviceEventId: UUID) {
        guard var current = session else { return }
        guard let index = current.drafts.firstIndex(where: { $0.deviceEventId == deviceEventId }) else { return }

        current.drafts[index].deletedLocally = true
        if current.drafts[index].syncStatus == .localOnly {
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
