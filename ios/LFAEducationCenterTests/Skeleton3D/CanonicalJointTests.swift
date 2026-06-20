import XCTest
@testable import LFAEducationCenter

final class CanonicalJointTests: XCTestCase {

    // CJ-01: 19 enum members
    func test_CJ_01_19_enum_members() {
        XCTAssertEqual(CanonicalJoint.allCases.count, 19)
    }

    // CJ-02: rawValue parity with Python (wire values)
    func test_CJ_02_rawValue_parity() {
        let expected: [String] = [
            "nose", "left_eye", "right_eye", "left_ear", "right_ear",
            "neck", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
            "left_wrist", "right_wrist", "root", "left_hip", "right_hip",
            "left_knee", "right_knee", "left_ankle", "right_ankle"
        ]
        let actual = CanonicalJoint.allCases.map(\.rawValue)
        XCTAssertEqual(Set(actual), Set(expected))
        XCTAssertEqual(actual.count, expected.count)
    }

    // CJ-03: Apple Vision mapping covers all 19
    func test_CJ_03_apple_vision_mapping_19() {
        XCTAssertEqual(AppleVisionJointMapping.map.count, 19)
        let canonicals = Set(AppleVisionJointMapping.map.map(\.canonical))
        XCTAssertEqual(canonicals, Set(CanonicalJoint.allCases))
    }

    // CJ-04: JointMapper produces 19 joints from full body
    func test_CJ_04_mapper_full_body() {
        let body = AppleVisionJointMapping.map.map { entry in
            BodyLandmarkDTO(name: entry.visionName, x: 0.5, y: 0.5, confidence: 0.9)
        }
        let joints = AppleVisionJointMapper.mapToCanonical(body: body)
        XCTAssertEqual(joints.count, 19)
    }

    // CJ-05: source_model preserved
    func test_CJ_05_source_model_preserved() {
        let body = [BodyLandmarkDTO(name: "nose", x: 0.5, y: 0.2, confidence: 0.95)]
        let joints = AppleVisionJointMapper.mapToCanonical(body: body)
        XCTAssertEqual(joints.first?.sourceModel, "apple_vision_body_pose_v1")
    }

    // CJ-06: source_confidence passthrough
    func test_CJ_06_source_confidence_passthrough() {
        let body = [BodyLandmarkDTO(name: "left_shoulder", x: 0.4, y: 0.3, confidence: 0.77)]
        let joints = AppleVisionJointMapper.mapToCanonical(body: body)
        XCTAssertEqual(joints.first?.sourceConfidence ?? -1, 0.77, accuracy: 0.0001)
        XCTAssertEqual(joints.first?.imageConfidence ?? -1, 0.77, accuracy: 0.0001)
    }

    // CJ-07: TriangulationStatus wire values match Python
    func test_CJ_07_triangulation_status_wire_values() {
        XCTAssertEqual(TriangulationStatus.triangulated.rawValue, "triangulated")
        XCTAssertEqual(TriangulationStatus.singleViewOnly.rawValue, "single_view_only")
        XCTAssertEqual(TriangulationStatus.belowConfidence.rawValue, "below_confidence")
        XCTAssertEqual(TriangulationStatus.jointMissing.rawValue, "joint_missing")
    }

    // CJ-08: Decode frame_v2_full.json fixture
    func test_CJ_08_decode_v2_full_fixture() throws {
        let data = try fixtureData("frame_v2_full")
        let frame = try JSONDecoder().decode(Skeleton3DFrame.self, from: data)
        XCTAssertEqual(frame.schemaVersion, "2")
        XCTAssertEqual(frame.joints.count, 5)
        XCTAssertEqual(frame.joints[0].canonicalJointName, "nose")
        XCTAssertEqual(frame.joints[0].triangulationStatus, "triangulated")
        XCTAssertNotNil(frame.joints[0].worldX)
    }

    // CJ-09: Decode frame_v2_2d_only.json fixture
    func test_CJ_09_decode_v2_2d_only_fixture() throws {
        let data = try fixtureData("frame_v2_2d_only")
        let frame = try JSONDecoder().decode(Skeleton3DFrame.self, from: data)
        XCTAssertNil(frame.joints[0].worldX)
        XCTAssertNil(frame.calibrationId)
        XCTAssertEqual(frame.joints[0].triangulationStatus, "single_view_only")
    }

    // CJ-10: SyncMethod and SyncQuality wire values
    func test_CJ_10_sync_enum_wire_values() {
        XCTAssertEqual(SyncMethod.audioClap.rawValue, "audio_clap")
        XCTAssertEqual(SyncMethod.softwareStart.rawValue, "software_start")
        XCTAssertEqual(SyncMethod.manual.rawValue, "manual")
        XCTAssertEqual(SyncQuality.high.rawValue, "high")
        XCTAssertEqual(SyncQuality.acceptable.rawValue, "acceptable")
        XCTAssertEqual(SyncQuality.degraded.rawValue, "degraded")
        XCTAssertEqual(SyncQuality.failed.rawValue, "failed")
    }

    // CJ-11: Int64 nanosecond timestamp lossless
    func test_CJ_11_int64_timestamp_lossless() throws {
        let data = try fixtureData("frame_v2_full")
        let frame = try JSONDecoder().decode(Skeleton3DFrame.self, from: data)
        XCTAssertEqual(frame.sourceTimestampNs, 1719000000000000000)
    }

    // CJ-12: UUID round-trip
    func test_CJ_12_uuid_roundtrip() throws {
        let data = try fixtureData("frame_v2_full")
        let frame = try JSONDecoder().decode(Skeleton3DFrame.self, from: data)
        XCTAssertEqual(frame.sessionId, "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    }

    // MARK: — Fixture helper

    private func fixtureData(_ name: String) throws -> Data {
        let bundle = Bundle(for: type(of: self))
        guard let url = bundle.url(forResource: name, withExtension: "json") else {
            throw NSError(domain: "test", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Fixture \(name).json not found in test bundle"])
        }
        return try Data(contentsOf: url)
    }
}
