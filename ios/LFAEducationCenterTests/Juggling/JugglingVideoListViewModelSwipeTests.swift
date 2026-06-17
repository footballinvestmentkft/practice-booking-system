import XCTest
@testable import LFAEducationCenter

// MARK: — JSW-01..08: resolveDeleteCandidate + I-2 swipe-to-delete behaviour
//
// Tests that the swipe-to-delete flow captures a stable videoId at swipe time
// and correctly guards no-op cases (media_deleted, in-flight, out-of-bounds).
//
// Naming: JSW = Juggling Swipe

@MainActor
final class JugglingVideoListViewModelSwipeTests: XCTestCase {

    // MARK: — Helpers

    private func makeItem(
        videoId: String = UUID().uuidString,
        status: String = "analyzed"
    ) -> JugglingVideoItem {
        let json = """
        {
          "video_id": "\(videoId)",
          "status": "\(status)",
          "transcode_status": "done",
          "quality_status": "pass",
          "quality_score": 85.0,
          "created_at": "2026-06-14T10:00:00Z",
          "updated_at": "2026-06-14T10:00:00Z",
          "duration_seconds": 12.0,
          "processed_resolution": "1920x1080",
          "processed_fps": 30.0,
          "processed_file_size_bytes": 9000000,
          "has_thumbnail": true,
          "has_media": true,
          "upload_source": "gallery",
          "source_type": "uploaded_video",
          "annotation_status": null
        }
        """.data(using: .utf8)!
        return try! JSONDecoder().decode(JugglingVideoItem.self, from: json)
    }

    private func makeVM(items: [JugglingVideoItem]) -> JugglingVideoListViewModel {
        let mock = MockAnnotationAPIClient()
        mock.deleteVideoResult = .success(())
        let vm = JugglingVideoListViewModel(deleteClient: mock)
        vm.setLoadedForTests(items)
        return vm
    }

    // MARK: — JSW-01: resolveDeleteCandidate returns the correct videoId for a normal video

    func test_JSW01_resolve_returns_videoId_for_normal_video() {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let vm = makeVM(items: [item])

        let result = vm.resolveDeleteCandidate(at: IndexSet(integer: 0), in: [item])

        XCTAssertEqual(result, id, "Should return the videoId of the video at index 0")
    }

    // MARK: — JSW-02: resolveDeleteCandidate returns nil for a media_deleted row

    func test_JSW02_resolve_returns_nil_for_media_deleted_row() {
        let item = makeItem(status: "media_deleted")
        let vm = makeVM(items: [item])

        let result = vm.resolveDeleteCandidate(at: IndexSet(integer: 0), in: [item])

        XCTAssertNil(result, "media_deleted rows must not trigger a delete — archive row is display-only")
    }

    // MARK: — JSW-03: resolveDeleteCandidate returns nil when a delete is already in flight

    func test_JSW03_resolve_returns_nil_when_delete_in_flight() {
        let id = UUID().uuidString
        let item = makeItem(videoId: id, status: "analyzed")
        let vm = makeVM(items: [item])
        vm.simulateInFlightDelete(videoId: id)

        let result = vm.resolveDeleteCandidate(at: IndexSet(integer: 0), in: [item])

        XCTAssertNil(result, "A second swipe while a delete is in flight must be ignored")
    }

    // MARK: — JSW-04: resolveDeleteCandidate returns nil for an out-of-bounds index

    func test_JSW04_resolve_returns_nil_for_out_of_bounds_index() {
        let item = makeItem(status: "analyzed")
        let vm = makeVM(items: [item])

        let result = vm.resolveDeleteCandidate(at: IndexSet(integer: 5), in: [item])

        XCTAssertNil(result, "Out-of-bounds index must not crash and must return nil")
    }

    // MARK: — JSW-05: stable videoId capture — index resolves at call time, not later

    // Proves that resolveDeleteCandidate captures the videoId of the video at the
    // given index in the GIVEN videos array. Even if the list is subsequently reordered
    // (videos array changes), the already-captured id is unaffected because we store
    // the String, not the IndexSet.

    func test_JSW05_stable_id_capture_not_affected_by_later_reorder() {
        let idA = UUID().uuidString
        let idB = UUID().uuidString
        let itemA = makeItem(videoId: idA)
        let itemB = makeItem(videoId: idB)
        let vm = makeVM(items: [itemA, itemB])

        // Capture id at swipe time: index 0 → itemA
        let captured = vm.resolveDeleteCandidate(at: IndexSet(integer: 0), in: [itemA, itemB])

        // Simulate a reorder: B now appears first
        let reorderedVideos = [itemB, itemA]

        // Re-resolve the SAME IndexSet in the reordered array → would give idB
        let wouldBeWrong = vm.resolveDeleteCandidate(at: IndexSet(integer: 0), in: reorderedVideos)

        // The CAPTURED id is idA (stable). The re-resolution gives idB (wrong).
        XCTAssertEqual(captured, idA, "Captured id must equal the video at index 0 at swipe time")
        XCTAssertEqual(wouldBeWrong, idB, "Resolving after reorder gives a different id — proves stable capture is necessary")
        XCTAssertNotEqual(captured, wouldBeWrong, "Stable capture differs from post-reorder resolution")
    }

    // MARK: — JSW-06: resolveDeleteCandidate accepts non-deleted non-analyzed statuses

    func test_JSW06_resolve_accepts_processing_and_uploaded_statuses() {
        for status in ["processing", "failed", "rejected", "uploaded"] {
            let id = UUID().uuidString
            let item = makeItem(videoId: id, status: status)
            let vm = makeVM(items: [item])

            let result = vm.resolveDeleteCandidate(at: IndexSet(integer: 0), in: [item])

            XCTAssertEqual(result, id, "Status '\(status)' should be deletable (only media_deleted is excluded)")
        }
    }

    // MARK: — JSW-07: resolveDeleteCandidate uses first index from a multi-index IndexSet

    func test_JSW07_resolve_uses_first_index_from_multi_index_set() {
        let idA = UUID().uuidString
        let idB = UUID().uuidString
        let itemA = makeItem(videoId: idA)
        let itemB = makeItem(videoId: idB)
        let vm = makeVM(items: [itemA, itemB])

        // IndexSet with two indices: 0 and 1
        let indexSet = IndexSet([0, 1])
        let result = vm.resolveDeleteCandidate(at: indexSet, in: [itemA, itemB])

        // first index is 0 → idA
        XCTAssertEqual(result, idA, "Should use indexSet.first (lowest index)")
    }

    // MARK: — JSW-08: deleteVideo clears a stale errorMessage before the new attempt

    func test_JSW08_error_message_cleared_at_start_of_new_delete() async {
        let idA = UUID().uuidString
        let idB = UUID().uuidString
        let itemA = makeItem(videoId: idA)
        let itemB = makeItem(videoId: idB)
        let mock = MockAnnotationAPIClient()

        // First delete: fails → errorMessage is set
        mock.deleteVideoResult = .failure(VideoDeleteError.notFound)
        let vm = JugglingVideoListViewModel(deleteClient: mock)
        vm.setLoadedForTests([itemA, itemB])
        await vm.deleteVideo(videoId: idA)
        XCTAssertNotNil(vm.errorMessage, "Precondition: errorMessage set after failed delete")

        // Second delete: succeeds → errorMessage must be nil by the time it completes
        mock.deleteVideoResult = .success(())
        await vm.deleteVideo(videoId: idB)

        XCTAssertNil(vm.errorMessage,
                     "errorMessage must be cleared at the start of a new deleteVideo call, " +
                     "even if the previous call left an error")
    }
}
