import XCTest
@testable import LFAEducationCenter

// MARK: — AN2-T05..T10: LocalAnnotationStore persistence, isolation, recovery

@MainActor
final class LocalAnnotationStoreTests: XCTestCase {

    private var tempDir: URL!
    private var store: LocalAnnotationStore!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("an2_local_store_tests_\(UUID().uuidString)", isDirectory: true)
        store = LocalAnnotationStore(baseDirectory: tempDir)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        store = nil
        tempDir = nil
        super.tearDown()
    }

    // AN2-T05: no file on disk → .empty
    func test_AN2_T05_loadWithNoFileReturnsEmpty() {
        switch store.load(userId: 1, videoId: "vid-1") {
        case .empty: break
        default: XCTFail("Expected .empty")
        }
    }

    // AN2-T06: save then load round-trips drafts including computed checksum.
    func test_AN2_T06_saveAndLoadRoundTrip() throws {
        var session = store.emptySession(userId: 1, videoId: "vid-1")
        let draft = ContactEventDraft.new(
            timestampMs: 1234, contactType: "right_instep",
            side: "right", annotationConfidence: "certain"
        )
        session.drafts.append(draft)
        try store.save(session: &session)
        XCTAssertFalse(session.checksum.isEmpty)

        switch store.load(userId: 1, videoId: "vid-1") {
        case .loaded(let loaded):
            XCTAssertEqual(loaded.drafts.count, 1)
            XCTAssertEqual(loaded.drafts[0].deviceEventId, draft.deviceEventId)
            XCTAssertEqual(loaded.drafts[0].contactType, "right_instep")
            XCTAssertEqual(loaded.checksum, session.checksum)
        default:
            XCTFail("Expected .loaded")
        }
    }

    // AN2-T07: userId is part of the storage key — user A never sees user B's drafts.
    func test_AN2_T07_userIsolation() throws {
        var sessionUser1 = store.emptySession(userId: 1, videoId: "vid-shared")
        sessionUser1.drafts.append(.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain"))
        try store.save(session: &sessionUser1)

        // User 2 with the same videoId sees no session.
        switch store.load(userId: 2, videoId: "vid-shared") {
        case .empty: break
        default: XCTFail("Expected .empty for a different userId with the same videoId")
        }

        // User 1's session is unaffected.
        switch store.load(userId: 1, videoId: "vid-shared") {
        case .loaded(let loaded): XCTAssertEqual(loaded.drafts.count, 1)
        default: XCTFail("Expected .loaded for user 1")
        }
    }

    // AN2-T08: undecodable file → quarantined, original bytes preserved.
    func test_AN2_T08_corruptFileIsQuarantinedNotDeleted() throws {
        let fileURL = tempDir
            .appendingPathComponent("3", isDirectory: true)
            .appendingPathComponent("vid-corrupt.json")
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let garbage = Data("{ not valid json".utf8)
        try garbage.write(to: fileURL)

        switch store.load(userId: 3, videoId: "vid-corrupt") {
        case .quarantined(let quarantineURL, _):
            XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path), "original path should be vacated")
            XCTAssertTrue(FileManager.default.fileExists(atPath: quarantineURL.path), "quarantine copy must exist")
            let preserved = try Data(contentsOf: quarantineURL)
            XCTAssertEqual(preserved, garbage, "quarantine must preserve original bytes")
        default:
            XCTFail("Expected .quarantined")
        }
    }

    // AN2-T09: checksum mismatch (tampered drafts) → quarantined.
    func test_AN2_T09_checksumMismatchIsQuarantined() throws {
        var session = store.emptySession(userId: 4, videoId: "vid-tamper")
        session.drafts.append(.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain"))
        try store.save(session: &session)

        // Tamper with the on-disk file: corrupt the checksum field only.
        let fileURL = tempDir
            .appendingPathComponent("4", isDirectory: true)
            .appendingPathComponent("vid-tamper.json")
        var raw = try JSONSerialization.jsonObject(with: try Data(contentsOf: fileURL)) as! [String: Any]
        raw["checksum"] = "0000000000000000000000000000000000000000000000000000000000000000"
        try JSONSerialization.data(withJSONObject: raw).write(to: fileURL)

        switch store.load(userId: 4, videoId: "vid-tamper") {
        case .quarantined: break
        default: XCTFail("Expected .quarantined on checksum mismatch")
        }
    }

    // AN2-T10: delete removes the session file; subsequent load is .empty.
    func test_AN2_T10_deleteRemovesFile() throws {
        var session = store.emptySession(userId: 5, videoId: "vid-finish")
        session.drafts.append(.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain"))
        try store.save(session: &session)

        store.delete(userId: 5, videoId: "vid-finish")

        switch store.load(userId: 5, videoId: "vid-finish") {
        case .empty: break
        default: XCTFail("Expected .empty after delete")
        }
    }
}
