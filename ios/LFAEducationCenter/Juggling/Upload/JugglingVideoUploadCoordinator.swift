import Foundation

// MARK: — JugglingVideoUploadCoordinator
//
// B3: shared "open the upload sheet" logic for JugglingVideoListView's
// toolbar "+" button and empty-state CTA. Both entry points call open(),
// which guards against a second sheet/active upload and reuses the same
// JugglingVideoUploadViewModel across re-presentations.
//
// makeClient/onReload are configured by the owning view (it has access to
// AuthManager via @EnvironmentObject, which this coordinator does not).

@MainActor
final class JugglingVideoUploadCoordinator: ObservableObject {

    @Published var showSheet = false

    private(set) var uploadViewModel: JugglingVideoUploadViewModel?

    // Builds a fresh API client on first open(). Configured by the owning
    // view once AuthManager is available (unavailable at coordinator init).
    var makeClient: (() -> JugglingAnnotationAPIClientProtocol)?

    // Override the export service for testing. nil → JugglingVideoExportService().
    var makeExportService: (() -> JugglingVideoExportServiceProtocol)?

    // Called exactly once, after a full successful upload (sheet already closed).
    var onReload: (() -> Void)?

    init() {}

    // Test-only: inject a pre-built ViewModel, bypassing makeClient.
    init(uploadViewModel: JugglingVideoUploadViewModel) {
        self.uploadViewModel = uploadViewModel
    }

    // Entry point for both the toolbar "+" button and the empty-state CTA.
    // Only one sheet / one active upload at a time: a second call while the
    // sheet is already presented is a no-op.
    func open() {
        guard !showSheet else { return }

        if uploadViewModel == nil {
            guard let makeClient = makeClient else { return }
            let exportService = makeExportService?() ?? JugglingVideoExportService()
            uploadViewModel = JugglingVideoUploadViewModel(
                apiClient: makeClient(),
                exportService: exportService
            )
        }

        // Re-opening after a prior success/failure starts from a clean .idle
        // state. cancel() is unconditional and safe — nothing is active to
        // tear down, it just clears the terminal state.
        if let vm = uploadViewModel, vm.state != .idle {
            vm.cancel()
        }

        uploadViewModel?.onSuccess = { [weak self] in
            self?.showSheet = false
            self?.onReload?()
        }
        showSheet = true
    }

    // Fires on any sheet dismissal — swipe-to-dismiss, the "Mégse" button, or
    // the success-driven close. If an upload is still active (preparing /
    // uploading / completing), cancel() tears it down so no orphan task or
    // temp file survives the sheet's lifetime.
    func handleDismiss() {
        if let vm = uploadViewModel, vm.isActive {
            vm.cancel()
        }
    }
}
