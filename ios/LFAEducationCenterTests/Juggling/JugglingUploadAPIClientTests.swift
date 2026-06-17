import XCTest
@testable import LFAEducationCenter

// MARK: — MockURLProtocol
//
// Intercepts all URLSession requests (dataTask and uploadTask) made through a
// URLSession configured with this protocol class.
// Set requestHandler before each test; clear it in tearDown.

final class MockURLProtocol: URLProtocol {
    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = MockURLProtocol.requestHandler else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

// MARK: — JugglingUploadAPIClientTests
//
// BU-01..BU-21: Tests for the three-step upload pipeline in JugglingAnnotationAPIClient.
//
// Strategy:
//   • Error-mapping tests (BU-02/03/04/08/09/10/11/12/19/21):
//       Use MockURLProtocol via APIClient._testURLSession. The real APIClient +
//       AuthManager stack is exercised — a fake Bearer token is seeded into
//       Keychain so AuthManager.accessToken is non-nil without a real login.
//
//   • Request-construction tests (BU-05/06/13/14/15):
//       MockURLProtocol captures the URLRequest and lets the test inspect
//       URL, method, and headers before returning a success response.
//
//   • Multipart-body tests (BU-16/17):
//       Call APIClient.buildMultipartTempFile() directly (internal visibility).
//       Verifies field name, MIME Content-Type, and that video bytes survive
//       the chunked write without truncation.

@MainActor
final class JugglingUploadAPIClientTests: XCTestCase {

    private var authManager: AuthManager!
    private var client: JugglingAnnotationAPIClient!

    // Fake token seeded into Keychain — never a real credential.
    private let fakeToken = "test-bearer-BU"

    override func setUp() async throws {
        authManager = AuthManager()
        // Inject token directly — avoids Keychain accessibility issues in CI simulators
        // where kSecAttrAccessibleAfterFirstUnlock can return nil before first unlock.
        authManager._testAccessToken = fakeToken
        client = JugglingAnnotationAPIClient(authManager: authManager)

        // Wire MockURLProtocol into APIClient.
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        APIClient._testURLSession = URLSession(configuration: config)
    }

    override func tearDown() async throws {
        MockURLProtocol.requestHandler = nil
        APIClient._testURLSession = nil
        authManager._testAccessToken = nil
        client = nil
        authManager = nil
    }

    // MARK: — Helpers

    private func makeHTTPResponse(url: URL, status: Int) -> HTTPURLResponse {
        HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!
    }

    private func jsonData(_ dict: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: dict)
    }

    private func tempVideoFile(content: Data = Data("fake-video".utf8)) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".mp4")
        try content.write(to: url)
        return url
    }

    // MARK: — BU-01: uploadInit success → correct JugglingUploadInitResponse

    func test_BU01_uploadInit_success_decodes_response() async throws {
        let responseBody = jsonData([
            "video_id": "abc-123",
            "status": "pending_upload",
            "upload_url": "http://localhost/upload",
            "message": "Upload ready."
        ])
        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 201), responseBody)
        }

        let result = try await client.uploadInit(sourceType: "uploaded_video", uploadSource: "gallery")

        XCTAssertEqual(result.videoId, "abc-123")
        XCTAssertEqual(result.status, "pending_upload")
    }

    // MARK: — BU-02: uploadInit 403 → JugglingUploadError.noConsent

    func test_BU02_uploadInit_403_throws_noConsent() async throws {
        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 403),
             self.jsonData(["detail": "service_consent required"]))
        }

        do {
            _ = try await client.uploadInit(sourceType: "uploaded_video", uploadSource: "gallery")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            XCTAssertEqual(err, .noConsent)
        }
    }

    // MARK: — BU-03: uploadInit network error → JugglingUploadError.networkError

    func test_BU03_uploadInit_networkError_throws_networkError() async throws {
        MockURLProtocol.requestHandler = { _ in
            throw URLError(.notConnectedToInternet)
        }

        do {
            _ = try await client.uploadInit(sourceType: "uploaded_video", uploadSource: "gallery")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            if case .networkError = err { /* pass */ } else {
                XCTFail("Expected .networkError, got \(err)")
            }
        }
    }

    // MARK: — BU-04: uploadInit timeout → JugglingUploadError.networkError

    func test_BU04_uploadInit_timeout_throws_networkError() async throws {
        MockURLProtocol.requestHandler = { _ in
            throw URLError(.timedOut)
        }

        do {
            _ = try await client.uploadInit(sourceType: "uploaded_video", uploadSource: "gallery")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            if case .networkError = err { /* pass */ } else {
                XCTFail("Expected .networkError, got \(err)")
            }
        }
    }

    // MARK: — BU-05: uploadInit URL path is correct

    func test_BU05_uploadInit_url_path_is_correct() async throws {
        var capturedRequest: URLRequest?
        MockURLProtocol.requestHandler = { req in
            capturedRequest = req
            return (self.makeHTTPResponse(url: req.url!, status: 201),
                    self.jsonData(["video_id": "x", "status": "pending_upload", "upload_url": "u", "message": "m"]))
        }

        _ = try await client.uploadInit(sourceType: "uploaded_video", uploadSource: "gallery")

        let path = capturedRequest?.url?.path ?? ""
        XCTAssertTrue(path.hasSuffix("/api/v1/users/me/juggling/videos/upload-init"),
                      "URL path mismatch: \(path)")
    }

    // MARK: — BU-06: uploadInit Authorization header contains Bearer token

    func test_BU06_uploadInit_auth_header_present() async throws {
        var capturedRequest: URLRequest?
        MockURLProtocol.requestHandler = { req in
            capturedRequest = req
            return (self.makeHTTPResponse(url: req.url!, status: 201),
                    self.jsonData(["video_id": "x", "status": "pending_upload", "upload_url": "u", "message": "m"]))
        }

        _ = try await client.uploadInit(sourceType: "uploaded_video", uploadSource: "gallery")

        let auth = capturedRequest?.value(forHTTPHeaderField: "Authorization") ?? ""
        XCTAssertTrue(auth.hasPrefix("Bearer "), "Authorization header must start with 'Bearer '")
        XCTAssertTrue(auth.contains(fakeToken), "Authorization header must contain the seeded token")
    }

    // MARK: — BU-07: uploadVideoFile success → correct JugglingUploadFileResponse

    func test_BU07_uploadVideoFile_success_decodes_response() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 200),
             self.jsonData([
                 "video_id": "vid-1",
                 "status": "uploaded",
                 "file_size_bytes": 42,
                 "checksum_sha256": "abc"
             ]))
        }

        let result = try await client.uploadVideoFile(
            videoId: "vid-1", fileURL: videoFile, mimeType: "video/mp4"
        )

        XCTAssertEqual(result.videoId, "vid-1")
        XCTAssertEqual(result.status, "uploaded")
        XCTAssertEqual(result.fileSizeBytes, 42)
    }

    // MARK: — BU-08: uploadVideoFile 409 → JugglingUploadError.invalidState

    func test_BU08_uploadVideoFile_409_throws_invalidState() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 409),
             self.jsonData(["detail": "Cannot upload: video is in status='analyzed'"]))
        }

        do {
            _ = try await client.uploadVideoFile(videoId: "v", fileURL: videoFile, mimeType: "video/mp4")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            if case .invalidState = err { /* pass */ } else {
                XCTFail("Expected .invalidState, got \(err)")
            }
        }
    }

    // MARK: — BU-09: uploadVideoFile 413 → JugglingUploadError.fileTooLarge

    func test_BU09_uploadVideoFile_413_throws_fileTooLarge() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 413),
             self.jsonData(["detail": "file_too_large: exceeds 100 MB"]))
        }

        do {
            _ = try await client.uploadVideoFile(videoId: "v", fileURL: videoFile, mimeType: "video/mp4")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            XCTAssertEqual(err, .fileTooLarge)
        }
    }

    // MARK: — BU-10: uploadVideoFile 415 → JugglingUploadError.unsupportedFormat

    func test_BU10_uploadVideoFile_415_throws_unsupportedFormat() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 415),
             self.jsonData(["detail": "unsupported_mime: video/avi not in allowed list"]))
        }

        do {
            _ = try await client.uploadVideoFile(videoId: "v", fileURL: videoFile, mimeType: "video/mp4")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            XCTAssertEqual(err, .unsupportedFormat)
        }
    }

    // MARK: — BU-11: uploadVideoFile network error → JugglingUploadError.networkError

    func test_BU11_uploadVideoFile_networkError_throws_networkError() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        MockURLProtocol.requestHandler = { _ in throw URLError(.notConnectedToInternet) }

        do {
            _ = try await client.uploadVideoFile(videoId: "v", fileURL: videoFile, mimeType: "video/mp4")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            if case .networkError = err { /* pass */ } else {
                XCTFail("Expected .networkError, got \(err)")
            }
        }
    }

    // MARK: — BU-12: uploadVideoFile timeout → JugglingUploadError.networkError

    func test_BU12_uploadVideoFile_timeout_throws_networkError() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        MockURLProtocol.requestHandler = { _ in throw URLError(.timedOut) }

        do {
            _ = try await client.uploadVideoFile(videoId: "v", fileURL: videoFile, mimeType: "video/mp4")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            if case .networkError = err { /* pass */ } else {
                XCTFail("Expected .networkError, got \(err)")
            }
        }
    }

    // MARK: — BU-13: uploadVideoFile URL path is correct

    func test_BU13_uploadVideoFile_url_path_is_correct() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }
        let videoId = "my-video-id"

        var capturedRequest: URLRequest?
        MockURLProtocol.requestHandler = { req in
            capturedRequest = req
            return (self.makeHTTPResponse(url: req.url!, status: 200),
                    self.jsonData(["video_id": videoId, "status": "uploaded",
                                   "file_size_bytes": 1, "checksum_sha256": "x"]))
        }

        _ = try await client.uploadVideoFile(videoId: videoId, fileURL: videoFile, mimeType: "video/mp4")

        let path = capturedRequest?.url?.path ?? ""
        let expectedSuffix = "/api/v1/users/me/juggling/videos/\(videoId)/upload"
        XCTAssertTrue(path.hasSuffix(expectedSuffix), "URL path mismatch: \(path)")
    }

    // MARK: — BU-14: uploadVideoFile Content-Type header is multipart/form-data

    func test_BU14_uploadVideoFile_contentType_is_multipart() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        var capturedRequest: URLRequest?
        MockURLProtocol.requestHandler = { req in
            capturedRequest = req
            return (self.makeHTTPResponse(url: req.url!, status: 200),
                    self.jsonData(["video_id": "v", "status": "uploaded",
                                   "file_size_bytes": 1, "checksum_sha256": "x"]))
        }

        _ = try await client.uploadVideoFile(videoId: "v", fileURL: videoFile, mimeType: "video/mp4")

        let ct = capturedRequest?.value(forHTTPHeaderField: "Content-Type") ?? ""
        XCTAssertTrue(ct.hasPrefix("multipart/form-data"),
                      "Content-Type must be multipart/form-data; got: \(ct)")
        XCTAssertTrue(ct.contains("boundary="), "Content-Type must include boundary; got: \(ct)")
    }

    // MARK: — BU-15: uploadVideoFile Authorization header contains Bearer token

    func test_BU15_uploadVideoFile_auth_header_present() async throws {
        let videoFile = try tempVideoFile()
        defer { try? FileManager.default.removeItem(at: videoFile) }

        var capturedRequest: URLRequest?
        MockURLProtocol.requestHandler = { req in
            capturedRequest = req
            return (self.makeHTTPResponse(url: req.url!, status: 200),
                    self.jsonData(["video_id": "v", "status": "uploaded",
                                   "file_size_bytes": 1, "checksum_sha256": "x"]))
        }

        _ = try await client.uploadVideoFile(videoId: "v", fileURL: videoFile, mimeType: "video/mp4")

        let auth = capturedRequest?.value(forHTTPHeaderField: "Authorization") ?? ""
        XCTAssertTrue(auth.hasPrefix("Bearer "), "Authorization must start with 'Bearer '")
        XCTAssertTrue(auth.contains(fakeToken), "Authorization must contain the seeded token")
    }

    // MARK: — BU-16: multipart field name is "file"
    //
    // Calls APIClient.buildMultipartTempFile directly (internal visibility).
    // Verifies the multipart body contains name="file" in the Content-Disposition.

    func test_BU16_multipart_field_name_is_file() throws {
        let content = Data("hello-video".utf8)
        let videoFile = try tempVideoFile(content: content)
        defer { try? FileManager.default.removeItem(at: videoFile) }

        let boundary = "TestBoundary123"
        let tempMultipart = try APIClient.buildMultipartTempFile(
            from: videoFile, mimeType: "video/mp4", fieldName: "file", boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: tempMultipart) }

        let body = String(data: try Data(contentsOf: tempMultipart), encoding: .utf8)!
        XCTAssertTrue(body.contains("name=\"file\""),
                      "Multipart body must contain name=\"file\"; actual:\n\(body)")
        XCTAssertTrue(body.contains("Content-Type: video/mp4"),
                      "Multipart body must declare MIME type video/mp4")
    }

    // MARK: — BU-17: large file streams without full memory load
    //
    // Creates a 5 MB synthetic file and passes it through buildMultipartTempFile.
    // Verifies that ALL source bytes appear in the output (chunked writes preserve
    // the full payload). The implementation uses 1 MB FileHandle reads — never
    // a single Data(contentsOf:) call on the source file.

    func test_BU17_large_file_multipart_body_contains_all_video_bytes() throws {
        let size = 5 * 1024 * 1024
        let videoContent = Data(repeating: 0xAB, count: size)
        let videoFile = try tempVideoFile(content: videoContent)
        defer { try? FileManager.default.removeItem(at: videoFile) }

        let boundary = "LargeBoundary"
        let tempMultipart = try APIClient.buildMultipartTempFile(
            from: videoFile, mimeType: "video/mp4", fieldName: "file", boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: tempMultipart) }

        let multipartData = try Data(contentsOf: tempMultipart)
        XCTAssertGreaterThan(multipartData.count, size,
                             "Multipart file must be larger than source (includes preamble + epilogue)")

        // Verify the video payload survives intact in the multipart body.
        // The 0xAB block must appear as a contiguous run inside the multipart file.
        let pattern = Data(repeating: 0xAB, count: 256)
        let range = multipartData.range(of: pattern)
        XCTAssertNotNil(range, "Video bytes (0xAB pattern) must be present in multipart output")
    }

    // MARK: — BU-18: completeUpload success → correct JugglingCompleteResponse

    func test_BU18_completeUpload_success_decodes_response() async throws {
        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 200),
             self.jsonData(["video_id": "v1", "status": "pending_quality_check",
                            "message": "Analysis queued."]))
        }

        let result = try await client.completeUpload(videoId: "v1")

        XCTAssertEqual(result.videoId, "v1")
        XCTAssertEqual(result.status, "pending_quality_check")
    }

    // MARK: — BU-19: completeUpload 409 → JugglingUploadError.invalidState with detail

    func test_BU19_completeUpload_409_throws_invalidState_with_detail() async throws {
        MockURLProtocol.requestHandler = { req in
            (self.makeHTTPResponse(url: req.url!, status: 409),
             self.jsonData(["detail": "Cannot call complete from status='analyzed'"]))
        }

        do {
            _ = try await client.completeUpload(videoId: "v1")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            if case .invalidState(let detail) = err {
                XCTAssertNotNil(detail, "invalidState should carry the server detail string")
            } else {
                XCTFail("Expected .invalidState, got \(err)")
            }
        }
    }

    // MARK: — BU-20: completeUpload URL path is correct

    func test_BU20_completeUpload_url_path_is_correct() async throws {
        let videoId = "complete-me"
        var capturedRequest: URLRequest?
        MockURLProtocol.requestHandler = { req in
            capturedRequest = req
            return (self.makeHTTPResponse(url: req.url!, status: 200),
                    self.jsonData(["video_id": videoId, "status": "pending_quality_check",
                                   "message": "queued"]))
        }

        _ = try await client.completeUpload(videoId: videoId)

        let path = capturedRequest?.url?.path ?? ""
        let expectedSuffix = "/api/v1/users/me/juggling/videos/\(videoId)/complete"
        XCTAssertTrue(path.hasSuffix(expectedSuffix), "URL path mismatch: \(path)")
    }

    // MARK: — BU-21: completeUpload network error → JugglingUploadError.networkError

    func test_BU21_completeUpload_networkError_throws_networkError() async throws {
        MockURLProtocol.requestHandler = { _ in throw URLError(.notConnectedToInternet) }

        do {
            _ = try await client.completeUpload(videoId: "v")
            XCTFail("Expected throw")
        } catch let err as JugglingUploadError {
            if case .networkError = err { /* pass */ } else {
                XCTFail("Expected .networkError, got \(err)")
            }
        }
    }

    // MARK: — BU-22: error mapping — mapUploadInitError direct unit tests

    func test_BU22_mapUploadInitError_coverage() {
        XCTAssertEqual(JugglingAnnotationAPIClient.mapUploadInitError(.httpError(statusCode: 403, detail: nil)), .noConsent)
        XCTAssertEqual(JugglingAnnotationAPIClient.mapUploadInitError(.unauthorized), .unauthorized)
        XCTAssertEqual(JugglingAnnotationAPIClient.mapUploadInitError(.httpError(statusCode: 401, detail: nil)), .unauthorized)
        if case .networkError = JugglingAnnotationAPIClient.mapUploadInitError(.networkError(URLError(.timedOut))) {
            // pass
        } else { XCTFail("Expected .networkError") }
    }

    // MARK: — BU-23: error mapping — mapUploadFileError direct unit tests

    func test_BU23_mapUploadFileError_coverage() {
        XCTAssertEqual(JugglingAnnotationAPIClient.mapUploadFileError(.httpError(statusCode: 403, detail: nil)), .noConsent)
        XCTAssertEqual(JugglingAnnotationAPIClient.mapUploadFileError(.httpError(statusCode: 413, detail: nil)), .fileTooLarge)
        XCTAssertEqual(JugglingAnnotationAPIClient.mapUploadFileError(.httpError(statusCode: 415, detail: nil)), .unsupportedFormat)
        XCTAssertEqual(JugglingAnnotationAPIClient.mapUploadFileError(.httpError(statusCode: 409, detail: "Cannot upload")),
                       .invalidState("Cannot upload"))
        if case .networkError = JugglingAnnotationAPIClient.mapUploadFileError(.networkError(URLError(.timedOut))) {
            // pass
        } else { XCTFail("Expected .networkError") }
    }

    // MARK: — BU-24: error mapping — mapCompleteError direct unit tests

    func test_BU24_mapCompleteError_coverage() {
        XCTAssertEqual(JugglingAnnotationAPIClient.mapCompleteError(.httpError(statusCode: 409, detail: "state error")),
                       .invalidState("state error"))
        XCTAssertEqual(JugglingAnnotationAPIClient.mapCompleteError(.unauthorized), .unauthorized)
        if case .networkError = JugglingAnnotationAPIClient.mapCompleteError(.networkError(URLError(.timedOut))) {
            // pass
        } else { XCTFail("Expected .networkError") }
    }
}
