import XCTest
@testable import LFAEducationCenter

// MARK: — AN2-T36..T37: JugglingAnnotationViewModel finish() flow

@MainActor
final class JugglingAnnotationViewModelTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("an2_viewmodel_tests_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    private func makeViewModel(apiClient: MockAnnotationAPIClient) -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-1",
            apiClient: apiClient,
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // AN2-T36: zero active events → finish() calls confirm_zero_contacts=true
    // and clears the local session on success.
    func test_AN2_T36_finishWithZeroContactsCallsConfirmZeroAndClearsSession() async {
        let mock = MockAnnotationAPIClient()
        mock.finishAnnotationResult = .success(FinishAnnotationOut(
            videoId: "vid-1", annotationStatus: "human_review_pending",
            totalJugglingCount: 0, contactEventCount: 0, annotationFinishedAt: Date()
        ))

        let vm = makeViewModel(apiClient: mock)
        await vm.onAppear()
        XCTAssertNotNil(vm.session)

        await vm.finish()

        XCTAssertNil(vm.finishError)
        XCTAssertEqual(vm.finishResult?.contactEventCount, 0)
        XCTAssertNil(vm.session, "session must be cleared after a successful finish")
    }

    // AN2-T37: finishAnnotation throws a permanent API error → finishError
    // is set to its description; session is preserved so the user can retry.
    func test_AN2_T37_finishMapsPermanentApiErrorToFinishError() async {
        let mock = MockAnnotationAPIClient()
        mock.finishAnnotationResult = .failure(AnnotationAPIError.permanent(code: 403, detail: "consent_blocked"))

        let vm = makeViewModel(apiClient: mock)
        await vm.onAppear()

        await vm.finish()

        XCTAssertNotNil(vm.finishError)
        XCTAssertEqual(vm.finishError, AnnotationAPIError.permanent(code: 403, detail: "consent_blocked").errorDescription)
        XCTAssertNotNil(vm.session, "session must be preserved when finish fails so the user can retry")
        XCTAssertNil(vm.finishResult)
    }
}
