import XCTest
@testable import LFAEducationCenter

// MARK: — JMD-01..10: JugglingVideoListViewModel.deleteVideo()
//
// All tests are pure logic (no network, no AuthManager).
// The ViewModel is created via the test-only init(deleteClient:) which injects
// a MockAnnotationAPIClient. No live server required.
//
// Naming: JMD = Juggling Media Delete

@MainActor
final class JugglingVideoListViewModelDeleteTests: XCTestCase {

    // MARK: — Helpers

    private func makeItem(
        videoId: String = UUID().uuidString,
        status: String = "analyzed",
        hasThumbnail: Bool = true,
        hasMedia: Bool = true,
        qualityScore: Double? = 87.5,
        qualityStatus: String? = "pass",
        annotationStatus: String? = nil,
        durationSeconds: Double? = 15.0,
        processedResolution: String? = "1920x1080"
    ) -> JugglingVideoItem {
        let json = """
        {
          "video_id": "\(videoId)",
          "status": "\(status)",
          "transcode_status": "done",
          "quality_status": \(qualityStatus.map { "\"\($0)\"" } ?? "null"),
          "quality_score": \(qualityScore.map { "\($0)" } ?? "null"),
          "created_at": "2026-06-14T10:00:00Z",
          "updated_at": "2026-06-14T10:00:00Z",
          "duration_seconds": \(durationSeconds.map { "\($0)" } ?? "null"),
          "processed_resolution": \(processedResolution.map { "\"\($0)\"" } ?? "null"),
          "processed_fps": 30.0,
          "processed_file_size_bytes": 14800000,
          "has_thumbnail": \(hasThumbnail),
          "has_media": \(hasMedia),
          "upload_source": "gallery",
          "source_type": "uploaded_video",
          "annotation_status": \(annotationStatus.map { "\"\($0)\"" } ?? "null")
        }
        """.data(using: .utf8)!
        return try! JSONDecoder().decode(JugglingVideoItem.self, from: json)
    }

    private func makeVM(
        items: [JugglingVideoItem],
        mock: MockAnnotationAPIClient
    ) -> JugglingVideoListViewModel {
        let vm = JugglingVideoListViewModel(deleteClient: mock)
        // Pre-load the ViewModel with the given items so deleteVideo has state to mutate.
        vm.setLoadedForTests(items)
        return vm
    }

    // MARK: — JMD-01: 204 success → item removed from list; single item → .empty state

    func test_JMD01_204_success_removes_item_from_list() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed", hasThumbnail: true, hasMedia: true)
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        guard case .empty = vm.listState else {
            return XCTFail("Expected .empty after deleting the only item, got \(vm.listState)")
        }
        XCTAssertNil(vm.errorMessage)
    }

    // MARK: — JMD-02: 410 (already deleted) → item removed, no error

    func test_JMD02_410_idempotent_success_removes_item() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let mock = MockAnnotationAPIClient()
        // 410 is swallowed in JugglingAnnotationAPIClient.deleteVideo → throws nothing.
        // We simulate this by making the mock return success (the real client handles 410 internally).
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        guard case .empty = vm.listState else {
            return XCTFail("Expected .empty after 410 idempotent delete, got \(vm.listState)")
        }
        XCTAssertNil(vm.errorMessage)
    }

    // MARK: — JMD-03: 404 → item unchanged, errorMessage set

    func test_JMD03_404_item_unchanged_error_set() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .failure(VideoDeleteError.notFound)
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        guard case .loaded(let items) = vm.listState else {
            return XCTFail("Expected .loaded")
        }
        let unchanged = items.first { $0.videoId == id }
        XCTAssertEqual(unchanged?.status, "analyzed", "Item must be unchanged on 404")
        XCTAssertTrue(unchanged?.hasMedia ?? false)
        XCTAssertNotNil(vm.errorMessage)
    }

    // MARK: — JMD-04: network error → item unchanged, errorMessage set

    func test_JMD04_network_error_item_unchanged_error_set() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .failure(VideoDeleteError.networkError(URLError(.notConnectedToInternet)))
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        guard case .loaded(let items) = vm.listState else {
            return XCTFail("Expected .loaded")
        }
        let unchanged = items.first { $0.videoId == id }
        XCTAssertEqual(unchanged?.status, "analyzed", "Item must be unchanged on network error")
        XCTAssertNotNil(vm.errorMessage)
    }

    // MARK: — JMD-05: auth error → item unchanged, errorMessage set

    func test_JMD05_auth_error_item_unchanged_error_set() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .failure(VideoDeleteError.unauthorized)
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        guard case .loaded(let items) = vm.listState else {
            return XCTFail("Expected .loaded")
        }
        let unchanged = items.first { $0.videoId == id }
        XCTAssertEqual(unchanged?.status, "analyzed")
        XCTAssertNotNil(vm.errorMessage)
        XCTAssertTrue(vm.errorMessage?.contains("Session") ?? false)
    }

    // MARK: — JMD-06: duplicate delete call for same videoId is blocked

    func test_JMD06_duplicate_delete_same_id_is_blocked() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let mock = MockAnnotationAPIClient()
        // Use a slow mock: record how many times deleteVideo was called
        var callCount = 0
        let blockingMock = CountingMockDeleteClient(onDelete: {
            callCount += 1
        }, result: .success(()))
        let vm = JugglingVideoListViewModel(deleteClient: blockingMock)
        vm.setLoadedForTests([item])

        // Manually insert the ID to simulate in-flight delete
        vm.simulateInFlightDelete(videoId: id)

        await vm.deleteVideo(videoId: id)

        // The second call should have been blocked — callCount stays 0
        XCTAssertEqual(callCount, 0, "Second DELETE for same videoId must be blocked")
    }

    // MARK: — JMD-07: deleting video A removes A; video B untouched

    func test_JMD07_delete_removes_target_does_not_affect_sibling() async {
        let idA = UUID().uuidString
        let idB = UUID().uuidString
        let itemA = makeItem(videoId: idA, status: "analyzed")
        let itemB = makeItem(videoId: idB, status: "analyzed", qualityScore: 92.0)
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [itemA, itemB], mock: mock)

        await vm.deleteVideo(videoId: idA)

        guard case .loaded(let items) = vm.listState else {
            return XCTFail("Expected .loaded with 1 remaining item")
        }
        XCTAssertNil(items.first { $0.videoId == idA }, "Deleted video A must be removed from list")
        let b = items.first { $0.videoId == idB }
        XCTAssertEqual(b?.status, "analyzed", "Video B status must be unchanged")
        XCTAssertTrue(b?.hasMedia ?? false, "Video B hasMedia must be unchanged")
        XCTAssertTrue(b?.hasThumbnail ?? false, "Video B hasThumbnail must be unchanged")
    }

    // MARK: — JMD-08: deleting A does not alter sibling B's quality/analysis fields

    func test_JMD08_sibling_quality_fields_preserved_after_delete() async {
        let idA = UUID().uuidString
        let idB = UUID().uuidString
        let itemA = makeItem(videoId: idA, status: "analyzed")
        let itemB = makeItem(
            videoId: idB,
            qualityScore: 91.5,
            qualityStatus: "pass",
            annotationStatus: "human_review_pending",
            durationSeconds: 22.3,
            processedResolution: "1280x720"
        )
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [itemA, itemB], mock: mock)

        await vm.deleteVideo(videoId: idA)

        guard case .loaded(let items) = vm.listState else {
            return XCTFail("Expected .loaded with 1 remaining item")
        }
        let b = items.first { $0.videoId == idB }
        XCTAssertEqual(b?.qualityScore, 91.5, "Sibling qualityScore must be unchanged")
        XCTAssertEqual(b?.qualityStatus, "pass", "Sibling qualityStatus must be unchanged")
        XCTAssertEqual(b?.annotationStatus, "human_review_pending", "Sibling annotationStatus must be unchanged")
        XCTAssertEqual(b?.durationSeconds, 22.3, "Sibling durationSeconds must be unchanged")
        XCTAssertEqual(b?.processedResolution, "1280x720", "Sibling processedResolution must be unchanged")
    }

    // MARK: — JMD-09: thumbnail cache evicted on success

    func test_JMD09_thumbnail_cache_evicted_on_success() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id)
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [item], mock: mock)
        // Seed a thumbnail in the cache
        vm.thumbnails[id] = UIImage()

        await vm.deleteVideo(videoId: id)

        XCTAssertNil(vm.thumbnails[id], "Thumbnail cache must be evicted after successful delete")
    }

    // MARK: — JMD-10: deleting state cleared after success and failure

    func test_JMD10_deleting_state_cleared_after_success() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id)
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        XCTAssertFalse(vm.deletingVideoIds.contains(id), "deletingVideoIds must be cleared on success")
    }

    func test_JMD10b_deleting_state_cleared_after_failure() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id)
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .failure(VideoDeleteError.notFound)
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        XCTAssertFalse(vm.deletingVideoIds.contains(id), "deletingVideoIds must be cleared on failure")
    }

    // MARK: — JMD-11: successful delete removes item from list (multi-item, item still absent after)

    func test_JMD11_deleteVideo_removesItemFromList() async {
        let idA = UUID().uuidString
        let idB = UUID().uuidString
        let itemA = makeItem(videoId: idA, status: "analyzed")
        let itemB = makeItem(videoId: idB, status: "analyzed")
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [itemA, itemB], mock: mock)

        await vm.deleteVideo(videoId: idA)

        guard case .loaded(let items) = vm.listState else {
            return XCTFail("Expected .loaded with 1 item remaining")
        }
        XCTAssertEqual(items.count, 1, "List must contain exactly 1 item after delete")
        XCTAssertNil(items.first { $0.videoId == idA }, "Deleted video must not appear in list")
        XCTAssertNotNil(items.first { $0.videoId == idB }, "Non-deleted video must remain in list")
    }

    // MARK: — JMD-12: deleting last item transitions list to .empty

    func test_JMD12_deletingLastItem_transitionsToEmptyState() async {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = makeVM(items: [item], mock: mock)

        await vm.deleteVideo(videoId: id)

        guard case .empty = vm.listState else {
            return XCTFail("Expected .empty after deleting last item, got \(vm.listState)")
        }
        XCTAssertNil(vm.thumbnails[id], "Thumbnail cache must be evicted for last deleted item")
        XCTAssertNil(vm.errorMessage, "No error message expected on success")
    }
}

// MARK: — Test support

// CountingMockDeleteClient: lets tests verify call count without MainActor constraints.
@MainActor
private final class CountingMockDeleteClient: JugglingAnnotationAPIClientProtocol {

    private let onDelete: () -> Void
    private let result: Result<Void, Error>

    init(onDelete: @escaping () -> Void, result: Result<Void, Error>) {
        self.onDelete = onDelete
        self.result = result
    }

    func deleteVideo(videoId: String) async throws {
        onDelete()
        try result.get()
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
