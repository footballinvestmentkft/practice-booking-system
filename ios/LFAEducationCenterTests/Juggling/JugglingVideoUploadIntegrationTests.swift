import XCTest
@testable import LFAEducationCenter

// MARK: — B3-01..11: JugglingVideoUploadCoordinator (list/upload sheet integration)
//
// Exercises the coordinator that backs JugglingVideoListView's toolbar "+"
// button and empty-state CTA — the sheet/ViewModel lifecycle, success/failure/
// retry behavior, and the contract that onReload runs exactly once on full
// success and never on failure.
//
// No network, no AuthManager — B3MockUploadClient injects via
// JugglingAnnotationAPIClientProtocol, same pattern as BV-* (B2).
//
// Naming: B3 = B-phase list/upload integration

@MainActor
final class JugglingVideoUploadIntegrationTests: XCTestCase {

    // MARK: — Helpers

    private func makeTempVideoFile(size: Int = 1024) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("mp4")
        try Data(repeating: 0xFF, count: size).write(to: url)
        return url
    }

    private func makeCoordinator(mock: B3MockUploadClient) -> (JugglingVideoUploadCoordinator, ReloadCounter) {
        let coordinator = JugglingVideoUploadCoordinator()
        let counter = ReloadCounter()
        coordinator.makeClient = { mock }
        coordinator.makeExportService = { B3MockExportService() }
        coordinator.onReload = { counter.count += 1 }
        return (coordinator, counter)
    }

    // Runs the full picker → upload flow for a ViewModel and awaits completion.
    private func runUpload(
        vm: JugglingVideoUploadViewModel,
        tempURL: URL,
        mimeType: String = "video/mp4"
    ) async {
        vm.startPicker()
        vm.pickerDidSelect(tempURL: tempURL, mimeType: mimeType)
        await vm.uploadTask?.value
    }

    private func makeVideoItem(videoId: String, status: String) -> JugglingVideoItem {
        let json = """
        {
          "video_id": "\(videoId)",
          "status": "\(status)",
          "transcode_status": "done",
          "quality_status": null,
          "quality_score": null,
          "created_at": "2026-06-14T10:00:00Z",
          "updated_at": "2026-06-14T10:00:00Z",
          "duration_seconds": null,
          "processed_resolution": null,
          "processed_fps": null,
          "processed_file_size_bytes": null,
          "has_thumbnail": true,
          "has_media": true,
          "upload_source": "gallery",
          "source_type": "uploaded_video",
          "annotation_status": null
        }
        """.data(using: .utf8)!
        return try! JSONDecoder().decode(JugglingVideoItem.self, from: json)
    }

    // MARK: — B3-01: toolbar "+" opens the sheet

    func test_B3_01_openCreatesSheetAndViewModel() {
        let (coordinator, _) = makeCoordinator(mock: B3MockUploadClient())
        XCTAssertFalse(coordinator.showSheet)
        XCTAssertNil(coordinator.uploadViewModel)

        coordinator.open()

        XCTAssertTrue(coordinator.showSheet)
        XCTAssertNotNil(coordinator.uploadViewModel)
    }

    // MARK: — B3-02: empty-state CTA reuses the same sheet/ViewModel

    func test_B3_02_secondEntryPointReusesSameViewModel() async throws {
        let mock = B3MockUploadClient()
        let (coordinator, _) = makeCoordinator(mock: mock)

        coordinator.open()                                 // toolbar "+"
        let firstVM = coordinator.uploadViewModel
        XCTAssertNotNil(firstVM)

        let url = try makeTempVideoFile()
        await runUpload(vm: firstVM!, tempURL: url)
        XCTAssertFalse(coordinator.showSheet, "Sheet closes after success")

        coordinator.open()                                 // empty-state CTA
        XCTAssertTrue(coordinator.uploadViewModel === firstVM, "Both entry points must reuse the same ViewModel")
        XCTAssertTrue(coordinator.showSheet)
    }

    // MARK: — B3-03: only one sheet openable at a time

    func test_B3_03_onlyOneSheetAtATime() {
        let (coordinator, _) = makeCoordinator(mock: B3MockUploadClient())
        coordinator.open()
        let vm = coordinator.uploadViewModel
        XCTAssertTrue(coordinator.showSheet)

        coordinator.open()   // second call while already presented — no-op

        XCTAssertTrue(coordinator.uploadViewModel === vm, "open() while presented must not replace the ViewModel")
        XCTAssertTrue(coordinator.showSheet)
    }

    // MARK: — B3-04: success closes the sheet and reloads exactly once

    func test_B3_04_successClosesSheetAndReloadsExactlyOnce() async throws {
        let mock = B3MockUploadClient()
        let (coordinator, counter) = makeCoordinator(mock: mock)
        coordinator.open()
        let vm = coordinator.uploadViewModel!

        let url = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: url)

        XCTAssertEqual(vm.state, .success)
        XCTAssertFalse(coordinator.showSheet)
        XCTAssertEqual(counter.count, 1)
    }

    // MARK: — B3-05: upload failure keeps the sheet open

    func test_B3_05_uploadFailureKeepsSheetOpen() async throws {
        let mock = B3MockUploadClient()
        mock.uploadInitResult = .failure(JugglingUploadError.unauthorized)
        let (coordinator, counter) = makeCoordinator(mock: mock)
        coordinator.open()
        let vm = coordinator.uploadViewModel!

        let url = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: url)

        guard case .failure = vm.state else {
            return XCTFail("Expected .failure state")
        }
        XCTAssertTrue(coordinator.showSheet, "Sheet must remain open after upload failure")
        XCTAssertEqual(counter.count, 0)
    }

    // MARK: — B3-06: retry after failure succeeds and closes the sheet

    func test_B3_06_retryAfterFailureSucceedsAndCloses() async throws {
        let mock = B3MockUploadClient()
        mock.uploadInitResult = .failure(JugglingUploadError.unauthorized)
        let (coordinator, counter) = makeCoordinator(mock: mock)
        coordinator.open()
        let vm = coordinator.uploadViewModel!

        let url1 = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: url1)
        guard case .failure = vm.state else { return XCTFail("Expected .failure") }
        XCTAssertTrue(coordinator.showSheet)

        // Export already succeeded; only the upload init failed.
        // retry() reuses the existing exported output — no new picker needed.
        mock.uploadInitResult = .success(
            JugglingUploadInitResponse(videoId: "v1", status: "pending_upload", uploadUrl: "/upload")
        )
        vm.retry()
        await vm.uploadTask?.value

        XCTAssertEqual(vm.state, .success)
        XCTAssertFalse(coordinator.showSheet)
        XCTAssertEqual(counter.count, 1)
    }

    // MARK: — B3-07: picker cancel keeps the sheet open

    func test_B3_07_pickerCancelKeepsSheetOpen() {
        let (coordinator, _) = makeCoordinator(mock: B3MockUploadClient())
        coordinator.open()
        let vm = coordinator.uploadViewModel!

        vm.startPicker()
        XCTAssertEqual(vm.state, .selecting)
        vm.pickerCancelled()

        XCTAssertEqual(vm.state, .idle)
        XCTAssertTrue(coordinator.showSheet, "Picker cancel must not close the upload sheet")
    }

    // MARK: — B3-08: completeUpload error → no reload

    func test_B3_08_completeUploadErrorDoesNotReload() async throws {
        let mock = B3MockUploadClient()
        mock.completeUploadResult = .failure(JugglingUploadError.networkError(URLError(.timedOut)))
        let (coordinator, counter) = makeCoordinator(mock: mock)
        coordinator.open()
        let vm = coordinator.uploadViewModel!

        let url = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: url)

        guard case .failure = vm.state else { return XCTFail("Expected .failure") }
        XCTAssertTrue(coordinator.showSheet)
        XCTAssertEqual(counter.count, 0, "No reload after a failed completeUpload")
    }

    // MARK: — B3-09: double-start blocked while an upload is active

    func test_B3_09_doubleStartBlockedDuringActiveUpload() async throws {
        let mock = B3MockUploadClient()
        mock.holdUploadVideoFile = true
        let (coordinator, _) = makeCoordinator(mock: mock)
        coordinator.open()
        let vm = coordinator.uploadViewModel!

        let url = try makeTempVideoFile()
        vm.startPicker()
        vm.pickerDidSelect(tempURL: url, mimeType: "video/mp4")
        await mock.waitUntilUploadVideoFileStarted()

        XCTAssertEqual(vm.state, .uploading(progress: 0))
        vm.startPicker()   // must be a no-op while an upload is active
        XCTAssertEqual(vm.state, .uploading(progress: 0))

        mock.resumeUpload()
        await vm.uploadTask?.value
        XCTAssertEqual(vm.state, .success)
    }

    // MARK: — B3-10: delete and refresh toolbar are unaffected
    //
    // Deleting the only item transitions listState to .empty (item is removed,
    // not mutated to media_deleted — see applyDeleteSuccess in ViewModel).

    func test_B3_10_deleteAndRefreshUnaffectedByUploadCoordinator() async {
        let item = makeVideoItem(videoId: "vid-1", status: "analyzed")
        let deleteMock = B3MockDeleteClient()
        deleteMock.deleteVideoResult = .success(())
        let listVM = JugglingVideoListViewModel(deleteClient: deleteMock)
        listVM.setLoadedForTests([item])

        await listVM.deleteVideo(videoId: "vid-1")

        guard case .empty = listVM.listState else {
            return XCTFail("Expected .empty after deleting the only item")
        }
        XCTAssertNil(listVM.errorMessage)
    }

    // MARK: — B3-11: empty and loaded list states are distinct

    func test_B3_11_emptyAndLoadedStatesAreDistinctCases() {
        let emptyState: JugglingListState = .empty
        let loadedEmptyState: JugglingListState = .loaded([])

        switch emptyState {
        case .empty: break
        default: XCTFail("Expected .empty")
        }

        switch loadedEmptyState {
        case .loaded(let items): XCTAssertTrue(items.isEmpty)
        default: XCTFail("Expected .loaded([])")
        }
    }
}

// MARK: — Test support

private final class ReloadCounter {
    var count = 0
}

private final class B3MockUploadClient: JugglingAnnotationAPIClientProtocol {

    var uploadInitResult: Result<JugglingUploadInitResponse, Error> = .success(
        JugglingUploadInitResponse(videoId: "v1", status: "pending_upload", uploadUrl: "/upload")
    )
    var uploadVideoFileResult: Result<JugglingUploadFileResponse, Error> = .success(
        JugglingUploadFileResponse(videoId: "v1", status: "uploaded", fileSizeBytes: 1024, checksumSha256: "abc123")
    )
    var completeUploadResult: Result<JugglingCompleteResponse, Error> = .success(
        JugglingCompleteResponse(videoId: "v1", status: "transcoding", message: "queued")
    )

    // When true, uploadVideoFile resumes startedContinuation (so the test
    // knows .uploading has been reached) and then suspends until
    // resumeUpload() is called.
    var holdUploadVideoFile = false
    private var holdContinuation: CheckedContinuation<Void, Never>?
    private var startedContinuation: CheckedContinuation<Void, Never>?

    func waitUntilUploadVideoFileStarted() async {
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            startedContinuation = cont
        }
    }

    func resumeUpload() {
        holdContinuation?.resume()
        holdContinuation = nil
    }

    func uploadInit(sourceType: String, uploadSource: String) async throws -> JugglingUploadInitResponse {
        try uploadInitResult.get()
    }

    func uploadVideoFile(videoId: String, fileURL: URL, mimeType: String) async throws -> JugglingUploadFileResponse {
        if holdUploadVideoFile {
            startedContinuation?.resume()
            startedContinuation = nil
            await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                holdContinuation = cont
            }
        }
        return try uploadVideoFileResult.get()
    }

    func completeUpload(videoId: String) async throws -> JugglingCompleteResponse {
        try completeUploadResult.get()
    }

    func listContacts(videoId: String) async throws -> ContactEventListOut {
        ContactEventListOut(videoId: videoId, annotationStatus: nil, events: [])
    }
    func createContact(videoId: String, request: ContactEventCreateRequest) async throws -> CreateContactResult {
        throw AnnotationAPIError.unauthorized
    }
    func patchContact(videoId: String, eventId: UUID, request: ContactEventPatchRequest) async throws -> ContactEventOut {
        throw AnnotationAPIError.unauthorized
    }
    func deleteContact(videoId: String, eventId: UUID) async throws -> DeleteContactResult {
        throw AnnotationAPIError.unauthorized
    }
    func finishAnnotation(videoId: String, confirmZero: Bool) async throws -> FinishAnnotationOut {
        throw AnnotationAPIError.unauthorized
    }
    func deleteVideo(videoId: String) async throws {
        throw VideoDeleteError.unauthorized
    }
    func fetchBallDetection(videoId: String, eventId: UUID) async throws -> BallDetectionOut {
        throw AnnotationAPIError.unauthorized
    }
    func postBallDetection(videoId: String, eventId: UUID, request: BallDetectionManualRequest) async throws -> BallDetectionOut {
        throw AnnotationAPIError.unauthorized
    }

    // AN-3B2B1 stubs
    func fetchFeedbackQueue(videoId: String, limit: Int) async -> BallFeedbackQueueResponse? { nil }
    func submitBallFeedback(videoId: String, request: BallFeedbackRequest) async throws -> BallFeedbackOut {
        throw BallFeedbackAPIError.unavailable
    }
}

private final class B3MockExportService: JugglingVideoExportServiceProtocol {
    func export(
        sourceURL: URL,
        progressHandler: @escaping (Double) -> Void
    ) async -> Result<JugglingVideoExportResult, JugglingVideoExportError> {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("b3_mock_export_\(UUID().uuidString).mp4")
        try? Data([0xAB]).write(to: url)
        return .success(JugglingVideoExportResult(
            outputURL: url,
            fileSizeBytes: 1,
            width: 640, height: 360,
            codec: "avc1",
            fileType: "mp4",
            mimeType: "video/mp4"
        ))
    }
    func cancelExport() {}
}

private final class B3MockDeleteClient: JugglingAnnotationAPIClientProtocol {
    var deleteVideoResult: Result<Void, Error> = .success(())

    func deleteVideo(videoId: String) async throws {
        try deleteVideoResult.get()
    }

    func listContacts(videoId: String) async throws -> ContactEventListOut {
        ContactEventListOut(videoId: videoId, annotationStatus: nil, events: [])
    }
    func createContact(videoId: String, request: ContactEventCreateRequest) async throws -> CreateContactResult {
        throw AnnotationAPIError.unauthorized
    }
    func patchContact(videoId: String, eventId: UUID, request: ContactEventPatchRequest) async throws -> ContactEventOut {
        throw AnnotationAPIError.unauthorized
    }
    func deleteContact(videoId: String, eventId: UUID) async throws -> DeleteContactResult {
        throw AnnotationAPIError.unauthorized
    }
    func finishAnnotation(videoId: String, confirmZero: Bool) async throws -> FinishAnnotationOut {
        throw AnnotationAPIError.unauthorized
    }
    func uploadInit(sourceType: String, uploadSource: String) async throws -> JugglingUploadInitResponse {
        throw JugglingUploadError.unauthorized
    }
    func uploadVideoFile(videoId: String, fileURL: URL, mimeType: String) async throws -> JugglingUploadFileResponse {
        throw JugglingUploadError.unauthorized
    }
    func completeUpload(videoId: String) async throws -> JugglingCompleteResponse {
        throw JugglingUploadError.unauthorized
    }
    func fetchBallDetection(videoId: String, eventId: UUID) async throws -> BallDetectionOut {
        throw AnnotationAPIError.unauthorized
    }
    func postBallDetection(videoId: String, eventId: UUID, request: BallDetectionManualRequest) async throws -> BallDetectionOut {
        throw AnnotationAPIError.unauthorized
    }

    // AN-3B2B1 stubs
    func fetchFeedbackQueue(videoId: String, limit: Int) async -> BallFeedbackQueueResponse? { nil }
    func submitBallFeedback(videoId: String, request: BallFeedbackRequest) async throws -> BallFeedbackOut {
        throw BallFeedbackAPIError.unavailable
    }
}
