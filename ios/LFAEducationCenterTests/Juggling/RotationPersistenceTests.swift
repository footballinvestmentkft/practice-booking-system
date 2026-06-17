import XCTest
import AVFoundation
@testable import LFAEducationCenter

// MARK: — JRI (Juggling Rotation Integration) tests — JRI-01..JRI-05

final class RotationPersistenceTests: XCTestCase {

    // MARK: — JRI-01: JugglingVideoItem decodes user_rotation_degrees from JSON

    func test_JRI_01_item_decodes_user_rotation_degrees() throws {
        let json = """
        {
          "video_id": "abc123", "status": "analyzed",
          "transcode_status": "done", "quality_status": "passed",
          "quality_score": 0.8, "created_at": "2026-06-17T00:00:00Z",
          "updated_at": "2026-06-17T00:00:00Z", "duration_seconds": 10.0,
          "processed_resolution": "640x628", "processed_fps": 30.0,
          "processed_file_size_bytes": 1024,
          "has_thumbnail": true, "has_media": true,
          "upload_source": "gallery", "source_type": "uploaded_video",
          "annotation_status": null,
          "user_rotation_degrees": 90
        }
        """.data(using: .utf8)!
        let item = try JSONDecoder().decode(JugglingVideoItem.self, from: json)
        XCTAssertEqual(item.userRotationDegrees, 90)
    }

    // MARK: — JRI-02: JugglingVideoItem falls back to nil when field absent

    func test_JRI_02_item_missing_user_rotation_degrees_is_nil() throws {
        let json = """
        {
          "video_id": "abc123", "status": "analyzed",
          "transcode_status": "done", "quality_status": "passed",
          "quality_score": 0.8, "created_at": "2026-06-17T00:00:00Z",
          "updated_at": "2026-06-17T00:00:00Z", "duration_seconds": null,
          "processed_resolution": null, "processed_fps": null,
          "processed_file_size_bytes": null,
          "has_thumbnail": false, "has_media": false,
          "upload_source": "gallery", "source_type": "uploaded_video",
          "annotation_status": null
        }
        """.data(using: .utf8)!
        let item = try JSONDecoder().decode(JugglingVideoItem.self, from: json)
        XCTAssertNil(item.userRotationDegrees,
                     "Missing user_rotation_degrees should decode as nil")
    }

    // MARK: — JRI-03: PlaybackController initialises with saved rotation

    @MainActor
    func test_JRI_03_playback_controller_initialises_with_rotation() {
        let controller = PlaybackController(player: MockPlayerJRI(), initialRotation: 180)
        XCTAssertEqual(controller.userRotation, 180)
    }

    // MARK: — JRI-04: PlaybackController clamps invalid initialRotation to 0

    @MainActor
    func test_JRI_04_playback_controller_invalid_rotation_clamps_to_zero() {
        let controller = PlaybackController(player: MockPlayerJRI(), initialRotation: 45)
        XCTAssertEqual(controller.userRotation, 0,
                       "Invalid rotation (45) must clamp to 0, not be stored")
    }

    // MARK: — JRI-05: rotateClockwise publishes incremented value

    @MainActor
    func test_JRI_05_rotate_clockwise_publishes_new_value() {
        let controller = PlaybackController(player: MockPlayerJRI(), initialRotation: 90)
        controller.rotateClockwise()
        XCTAssertEqual(controller.userRotation, 180)
    }

    // MARK: — JRI-06: rotateClockwise wraps 270 → 0

    @MainActor
    func test_JRI_06_rotate_clockwise_wraps_at_360() {
        let controller = PlaybackController(player: MockPlayerJRI(), initialRotation: 270)
        controller.rotateClockwise()
        XCTAssertEqual(controller.userRotation, 0,
                       "270 + 90 should wrap to 0, not 360")
    }

    // MARK: — JRI-07: cachedRotation returns UserDefaults value when present, not the server value

    func test_JRI_07_cached_rotation_prefers_local_cache_over_server_value() throws {
        let item = try makeItem(userRotationDegrees: 0)
        let key = "juggling_rotation_\(item.videoId)"
        UserDefaults.standard.set(180, forKey: key)
        defer { UserDefaults.standard.removeObject(forKey: key) }

        let rotation = JugglingAnnotationScreen.cachedRotation(for: item)
        XCTAssertEqual(rotation, 180,
                       "Local UserDefaults (180) should win over server value (0)")
    }

    // MARK: — Helpers

    private func makeItem(userRotationDegrees: Int?) throws -> JugglingVideoItem {
        var dict: [String: Any] = [
            "video_id": "test-video-\(Int.random(in: 10000...99999))",
            "status": "analyzed", "transcode_status": "done",
            "quality_status": "passed", "quality_score": 0.8,
            "created_at": "2026-06-17T00:00:00Z", "updated_at": "2026-06-17T00:00:00Z",
            "duration_seconds": NSNull(), "processed_resolution": NSNull(),
            "processed_fps": NSNull(), "processed_file_size_bytes": NSNull(),
            "has_thumbnail": false, "has_media": true,
            "upload_source": "gallery", "source_type": "uploaded_video",
            "annotation_status": NSNull()
        ]
        if let deg = userRotationDegrees {
            dict["user_rotation_degrees"] = deg
        }
        let data = try JSONSerialization.data(withJSONObject: dict)
        return try JSONDecoder().decode(JugglingVideoItem.self, from: data)
    }
}

// MARK: — MockPlayerJRI (minimal PlayerSeekable for controller init in tests)

private final class MockPlayerJRI: PlayerSeekable {
    var rate: Float = 0
    func currentTime() -> CMTime { .zero }
    func seek(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime) {}
    func play() {}
    func pause() {}
}
