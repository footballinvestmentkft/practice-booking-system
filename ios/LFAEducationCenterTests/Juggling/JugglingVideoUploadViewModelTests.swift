import XCTest
import Combine
@testable import LFAEducationCenter

// MARK: — BV-01..22: JugglingVideoUploadViewModel
//         BV-EXP-01..14: export-integration tests (Commit 2)
//
// All tests run on @MainActor. No network; mocks inject via protocols.
// Temp files are real files in /tmp so we can verify cleanup.
//
// Naming: BV = B-phase Video upload ViewModel
//         BV-EXP = export integration tests

@MainActor
final class JugglingVideoUploadViewModelTests: XCTestCase {

    // MARK: — Helpers

    private func makeTempVideoFile(size: Int = 1024) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("mp4")
        try Data(repeating: 0xFF, count: size).write(to: url)
        return url
    }

    private func makeSuccessMock(videoId: String = "v1") -> MockUploadClient {
        let mock = MockUploadClient()
        mock.uploadInitResult = .success(
            JugglingUploadInitResponse(videoId: videoId, status: "pending_upload", uploadUrl: "/upload")
        )
        mock.uploadVideoFileResult = .success(
            JugglingUploadFileResponse(videoId: videoId, status: "uploaded", fileSizeBytes: 1024, checksumSha256: "abc123")
        )
        mock.completeUploadResult = .success(
            JugglingCompleteResponse(videoId: videoId, status: "transcoding", message: "queued")
        )
        return mock
    }

    private func makeVM(
        mock: MockUploadClient,
        exportService: MockExportService? = nil,
        maxSize: Int64 = 100 * 1024 * 1024
    ) -> JugglingVideoUploadViewModel {
        JugglingVideoUploadViewModel(
            apiClient: mock,
            exportService: exportService ?? MockExportService(),
            maxFileSizeBytes: maxSize
        )
    }

    // Runs the full picker → export → upload flow and awaits completion.
    private func runUpload(
        vm: JugglingVideoUploadViewModel,
        tempURL: URL,
        mimeType: String = "video/mp4"
    ) async {
        vm.startPicker()
        vm.pickerDidSelect(tempURL: tempURL, mimeType: mimeType)
        let task = vm.uploadTask
        await task?.value
    }

    // MARK: — BV-01: full success state transitions (now includes .exporting)

    func test_BV01_fullSuccessStateTransitions() async throws {
        let mock = makeSuccessMock()
        let vm = makeVM(mock: mock)
        var states: [JugglingVideoUploadViewModel.UploadState] = []
        let cancellable = vm.$state.sink { states.append($0) }

        let url = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: url)

        // MockExportService fires no progress updates, so the exact sequence is deterministic.
        let expected: [JugglingVideoUploadViewModel.UploadState] = [
            .idle, .selecting, .preparing, .exporting(progress: 0),
            .uploading(progress: 0), .completing, .success
        ]
        XCTAssertEqual(states, expected)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        cancellable.cancel()
    }

    // MARK: — BV-02: picker cancel → idle

    func test_BV02_pickerCancelReturnsIdle() {
        let vm = makeVM(mock: MockUploadClient())
        vm.startPicker()
        XCTAssertEqual(vm.state, .selecting)
        vm.pickerCancelled()
        XCTAssertEqual(vm.state, .idle)
        XCTAssertNil(vm.uploadTask)
    }

    // MARK: — BV-03: exported output too large → .fileTooLarge, no network call
    // The 100 MB cap now applies to the EXPORTED output, not the picker source.

    func test_BV03_exportedOutputTooLargeBlocksNetworkCall() async throws {
        let exportMock = MockExportService()
        exportMock.outputFileSizeBytes = 1025          // exceeds 1024-byte limit
        let uploadMock = MockUploadClient()
        let vm = makeVM(mock: uploadMock, exportService: exportMock, maxSize: 1024)
        let url = try makeTempVideoFile(size: 128)     // source size is irrelevant now

        await runUpload(vm: vm, tempURL: url)

        XCTAssertEqual(vm.state, .failure(.fileTooLarge))
        XCTAssertFalse(uploadMock.uploadInitCalled, "Network must not start for oversized exported output")
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path), "source deleted after export success")
    }

    // MARK: — BV-04: export unsupported → .exportUnsupported, no network call

    func test_BV04_exportUnsupportedBlocksNetworkCall() async throws {
        let exportMock = MockExportService()
        exportMock.shouldSucceed = false
        exportMock.failureError = .exportUnsupported
        let uploadMock = MockUploadClient()
        let vm = makeVM(mock: uploadMock, exportService: exportMock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        XCTAssertEqual(vm.state, .failure(.exportUnsupported))
        XCTAssertFalse(uploadMock.uploadInitCalled)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path), "source deleted on export failure")
    }

    // MARK: — BV-05: no consent (403 from uploadInit)

    func test_BV05_noConsentFromInit() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .failure(JugglingUploadError.noConsent)
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        XCTAssertEqual(vm.state, .failure(.noConsent))
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path), "source deleted after export success")
    }

    // MARK: — BV-06: invalid state (409 from uploadVideoFile)

    func test_BV06_invalidStateFromFileUpload() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .success(
            JugglingUploadInitResponse(videoId: "v1", status: "pending_upload", uploadUrl: "/upload")
        )
        mock.uploadVideoFileResult = .failure(JugglingUploadError.invalidState("already uploaded"))
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        XCTAssertEqual(vm.state, .failure(.invalidState("already uploaded")))
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    // MARK: — BV-07: unauthorized (401 from uploadInit)

    func test_BV07_unauthorizedFromInit() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .failure(JugglingUploadError.unauthorized)
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        XCTAssertEqual(vm.state, .failure(.unauthorized))
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    // MARK: — BV-08: network error from init

    func test_BV08_networkErrorFromInit() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .failure(
            JugglingUploadError.networkError(URLError(.notConnectedToInternet))
        )
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        guard case .failure(.networkError) = vm.state else {
            return XCTFail("Expected .failure(.networkError), got \(vm.state)")
        }
    }

    // MARK: — BV-09: timeout from file upload

    func test_BV09_timeoutFromFileUpload() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .success(
            JugglingUploadInitResponse(videoId: "v1", status: "pending_upload", uploadUrl: "/upload")
        )
        mock.uploadVideoFileResult = .failure(
            JugglingUploadError.networkError(URLError(.timedOut))
        )
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        guard case .failure(.networkError) = vm.state else {
            return XCTFail("Expected .failure(.networkError), got \(vm.state)")
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path), "source deleted after export success")
    }

    // MARK: — BV-10: retry after UPLOAD failure reuses exported output — no re-export

    func test_BV10_retryAfterUploadFailureReusesExportedOutputWithoutReExporting() async throws {
        let exportMock = MockExportService()
        let uploadMock = MockUploadClient()
        uploadMock.uploadInitResult = .failure(JugglingUploadError.networkError(URLError(.notConnectedToInternet)))
        let vm = makeVM(mock: uploadMock, exportService: exportMock)

        let sourceURL = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: sourceURL)
        guard case .failure(.networkError) = vm.state else { return XCTFail("Expected network failure on first attempt") }
        XCTAssertEqual(exportMock.exportCallCount, 1, "export called exactly once")

        // Source is gone; exported output survives network failure.
        XCTAssertFalse(FileManager.default.fileExists(atPath: sourceURL.path))
        let exportedURL = try XCTUnwrap(exportMock.lastOutputURL)
        XCTAssertTrue(FileManager.default.fileExists(atPath: exportedURL.path),
                      "exported output must survive upload network failure")

        // Fix mock and retry — no new picker selection, no new export.
        let successMock = makeSuccessMock()
        uploadMock.uploadInitResult = successMock.uploadInitResult
        uploadMock.uploadVideoFileResult = successMock.uploadVideoFileResult
        uploadMock.completeUploadResult = successMock.completeUploadResult
        vm.retry()
        // retry() → state = .preparing (not .idle); uses existing exported file.
        await vm.uploadTask?.value

        XCTAssertEqual(vm.state, .success)
        XCTAssertEqual(exportMock.exportCallCount, 1, "export must NOT be called again on retry")
        XCTAssertFalse(FileManager.default.fileExists(atPath: exportedURL.path),
                       "exported output deleted after upload success")
    }

    // MARK: — BV-11: duplicate start blocked

    func test_BV11_startPickerBlockedUnlessIdle() {
        let vm = makeVM(mock: MockUploadClient())
        vm.startPicker()
        XCTAssertEqual(vm.state, .selecting)
        vm.startPicker()
        XCTAssertEqual(vm.state, .selecting)
        XCTAssertNil(vm.uploadTask, "No task must be created by a blocked startPicker")
    }

    // MARK: — BV-12: completeUpload not called if file upload fails

    func test_BV12_completeNotCalledIfFileUploadFails() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .success(
            JugglingUploadInitResponse(videoId: "v1", status: "pending_upload", uploadUrl: "/upload")
        )
        mock.uploadVideoFileResult = .failure(JugglingUploadError.fileTooLarge)
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        XCTAssertFalse(mock.completeUploadCalled, "completeUpload must not be called if file upload failed")
        guard case .failure(.fileTooLarge) = vm.state else {
            return XCTFail("Expected .failure(.fileTooLarge)")
        }
    }

    // MARK: — BV-13: completion callback fired only on full success

    func test_BV13_completionCallbackFiredOnlyOnSuccess() async throws {
        let failMock = MockUploadClient()
        failMock.uploadInitResult = .failure(JugglingUploadError.noConsent)
        let failVM = makeVM(mock: failMock)
        var failCallbackFired = false
        failVM.onSuccess = { failCallbackFired = true }

        let url1 = try makeTempVideoFile()
        await runUpload(vm: failVM, tempURL: url1)
        XCTAssertFalse(failCallbackFired, "Callback must not fire on failure")

        let successVM = makeVM(mock: makeSuccessMock())
        var successCallbackFired = false
        successVM.onSuccess = { successCallbackFired = true }

        let url2 = try makeTempVideoFile()
        await runUpload(vm: successVM, tempURL: url2)
        XCTAssertTrue(successCallbackFired, "Callback must fire on full success")
    }

    // MARK: — BV-14: ALL temp files (source + exported output) deleted on success

    func test_BV14_allTempFilesDeletedOnSuccess() async throws {
        let exportMock = MockExportService()
        let vm = makeVM(mock: makeSuccessMock(), exportService: exportMock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        XCTAssertEqual(vm.state, .success)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path), "source deleted after export")
        let exportedURL = try XCTUnwrap(exportMock.lastOutputURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: exportedURL.path),
                       "exported output deleted after success")
    }

    // MARK: — BV-15: source temp always deleted (even on upload failure)

    func test_BV15_sourceTempDeletedOnFailure() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .failure(JugglingUploadError.noConsent)
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path),
                       "source deleted immediately after export, regardless of upload outcome")
    }

    // MARK: — BV-16: source deleted on cancel (before export runs)

    func test_BV16_sourceTempDeletedOnCancel() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .success(
            JugglingUploadInitResponse(videoId: "v1", status: "pending_upload", uploadUrl: "/upload")
        )
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        vm.startPicker()
        vm.pickerDidSelect(tempURL: url, mimeType: "video/mp4")
        let task = vm.uploadTask
        vm.cancel()
        await task?.value

        XCTAssertEqual(vm.state, .idle)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path), "source deleted on cancel")
    }

    // MARK: — BV-17: retry after EXPORT failure resets to .idle — new picker required

    func test_BV17_retryAfterExportFailureResetsToIdle() async throws {
        let exportMock = MockExportService()
        exportMock.shouldSucceed = false
        exportMock.failureError = .exportFailed("mock disk write error")
        let uploadMock = MockUploadClient()
        let vm = makeVM(mock: uploadMock, exportService: exportMock)

        let url = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: url)

        guard case .failure(.exportFailed) = vm.state else {
            return XCTFail("Expected .failure(.exportFailed), got \(vm.state)")
        }
        XCTAssertFalse(uploadMock.uploadInitCalled, "no upload ever attempted after export failure")

        vm.retry()
        XCTAssertEqual(vm.state, .idle, "export failure → retry resets to .idle, requires new picker selection")
        XCTAssertNil(vm.uploadTask)
    }

    // MARK: — BV-18: state not stuck after completeUpload error

    func test_BV18_stateNotStuckAfterCompleteUploadError() async throws {
        let mock = MockUploadClient()
        mock.uploadInitResult = .success(
            JugglingUploadInitResponse(videoId: "v1", status: "pending_upload", uploadUrl: "/upload")
        )
        mock.uploadVideoFileResult = .success(
            JugglingUploadFileResponse(videoId: "v1", status: "uploaded", fileSizeBytes: 1024, checksumSha256: "abc")
        )
        mock.completeUploadResult = .failure(JugglingUploadError.invalidState("not in uploaded state"))
        let vm = makeVM(mock: mock)
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)

        guard case .failure(let err) = vm.state else {
            return XCTFail("Expected .failure, got \(vm.state)")
        }
        XCTAssertEqual(err, .invalidState("not in uploaded state"),
                       "State must not remain .completing after completeUpload error")
    }

    // MARK: — BV-19: late pickerDidSelect is a no-op AND deletes the temp file

    func test_BV19_latePickerDidSelectIsNoOpAndDeletesTempFile() throws {
        let vm = makeVM(mock: MockUploadClient())
        let url = try makeTempVideoFile()

        vm.startPicker()
        vm.pickerCancelled()
        vm.pickerDidSelect(tempURL: url, mimeType: "video/mp4")

        XCTAssertEqual(vm.state, .idle)
        XCTAssertNil(vm.uploadTask)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    // MARK: — BV-20: pickerCancelled() ignored once upload has progressed past .selecting

    func test_BV20_pickerCancelledIgnoredOnceUploadStarted() async throws {
        let vm = makeVM(mock: makeSuccessMock())
        let url = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: url)
        XCTAssertEqual(vm.state, .success)

        vm.pickerCancelled()
        XCTAssertEqual(vm.state, .success)
    }

    // MARK: — BV-21: normal selection → .preparing, no intermediate .idle

    func test_BV21_normalSelectionGoesToPreparingNotIdle() throws {
        let vm = makeVM(mock: MockUploadClient())
        let url = try makeTempVideoFile()
        var states: [JugglingVideoUploadViewModel.UploadState] = []
        let cancellable = vm.$state.sink { states.append($0) }

        vm.startPicker()
        vm.pickerDidSelect(tempURL: url, mimeType: "video/mp4")

        XCTAssertEqual(vm.state, .preparing)
        XCTAssertEqual(states, [.idle, .selecting, .preparing],
                       "No intermediate .idle must appear between .selecting and .preparing")

        vm.cancel()
        cancellable.cancel()
    }

    // MARK: — BV-22: picker cancel still resets to .idle (legitimate path)

    func test_BV22_pickerCancelStillResetsToIdle() {
        let vm = makeVM(mock: MockUploadClient())
        vm.startPicker()
        vm.pickerCancelled()
        XCTAssertEqual(vm.state, .idle)
        XCTAssertNil(vm.uploadTask)
    }

    // ─────────────────────────────────────────────────────────────────
    // MARK: — BV-EXP: Export integration (Commit 2 required tests)
    // ─────────────────────────────────────────────────────────────────

    // MARK: — BV-EXP-01: picker selection → .exporting state follows .preparing

    func test_BVEXP01_pickerSelectionTransitionsThroughExporting() async throws {
        let exportMock = MockExportService()
        exportMock.shouldHold = true
        let vm = makeVM(mock: makeSuccessMock(), exportService: exportMock)
        var states: [JugglingVideoUploadViewModel.UploadState] = []
        let cancellable = vm.$state.sink { states.append($0) }

        let sourceURL = try makeTempVideoFile()
        vm.startPicker()
        vm.pickerDidSelect(tempURL: sourceURL, mimeType: "video/mp4")
        await exportMock.waitUntilExportStarted()

        XCTAssertTrue(states.contains(.preparing), "must pass through .preparing")
        XCTAssertEqual(vm.state, .exporting(progress: 0), "must reach .exporting while export is held")

        exportMock.resumeExport()
        await vm.uploadTask?.value
        cancellable.cancel()
    }

    // MARK: — BV-EXP-02: export success → upload uses exported URL and MIME (not source)

    func test_BVEXP02_uploadStartsWithExportedURLAndMIME() async throws {
        let exportMock = MockExportService()
        let uploadMock = makeSuccessMock()
        let vm = makeVM(mock: uploadMock, exportService: exportMock)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        XCTAssertEqual(vm.state, .success)
        let uploadedURL = try XCTUnwrap(uploadMock.lastUploadVideoFileURL,
                                        "uploadVideoFile must be called")
        let exportedURL = try XCTUnwrap(exportMock.lastOutputURL,
                                        "export must produce an output URL")
        XCTAssertEqual(uploadedURL, exportedURL,
                       "upload must use export output URL — not the source URL")
        XCTAssertEqual(uploadMock.lastUploadVideoFileMimeType, "video/mp4",
                       "upload MIME must match export output MIME, not original source MIME")
    }

    // MARK: — BV-EXP-03: source URL never reaches the API client

    func test_BVEXP03_sourceURLNeverReachesAPIClient() async throws {
        let exportMock = MockExportService()
        let uploadMock = makeSuccessMock()
        let vm = makeVM(mock: uploadMock, exportService: exportMock)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        let uploadedURL = try XCTUnwrap(uploadMock.lastUploadVideoFileURL)
        XCTAssertNotEqual(uploadedURL, sourceURL,
                          "API client must NEVER receive the picker's original source URL")
        XCTAssertNotEqual(uploadedURL.lastPathComponent, sourceURL.lastPathComponent)
    }

    // MARK: — BV-EXP-04: exported output within limit → upload starts

    func test_BVEXP04_exportedOutputWithinLimitStartsUpload() async throws {
        let exportMock = MockExportService()
        exportMock.outputFileSizeBytes = 512           // within 1024-byte limit
        let uploadMock = makeSuccessMock()
        let vm = makeVM(mock: uploadMock, exportService: exportMock, maxSize: 1024)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        XCTAssertEqual(vm.state, .success)
        XCTAssertTrue(uploadMock.uploadInitCalled, "upload must start for output within the size limit")
    }

    // MARK: — BV-EXP-05: exported output > limit → .fileTooLarge, no network call

    func test_BVEXP05_exportedOutputTooLargeYieldsFileTooLargeNoNetwork() async throws {
        let exportMock = MockExportService()
        exportMock.outputFileSizeBytes = 1025          // exceeds 1024-byte limit
        let uploadMock = MockUploadClient()
        let vm = makeVM(mock: uploadMock, exportService: exportMock, maxSize: 1024)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        XCTAssertEqual(vm.state, .failure(.fileTooLarge))
        XCTAssertFalse(uploadMock.uploadInitCalled, "no network call for oversized exported output")
    }

    // MARK: — BV-EXP-06: unsupported export → .exportUnsupported error

    func test_BVEXP06_exportUnsupportedYieldsStructuredError() async throws {
        let exportMock = MockExportService()
        exportMock.shouldSucceed = false
        exportMock.failureError = .exportUnsupported
        let uploadMock = MockUploadClient()
        let vm = makeVM(mock: uploadMock, exportService: exportMock)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        XCTAssertEqual(vm.state, .failure(.exportUnsupported))
        XCTAssertFalse(uploadMock.uploadInitCalled)
    }

    // MARK: — BV-EXP-07: export failure → .exportFailed, source deleted, no upload

    func test_BVEXP07_exportFailureCleansSourceAndBlocksUpload() async throws {
        let exportMock = MockExportService()
        exportMock.shouldSucceed = false
        exportMock.failureError = .exportFailed("disk write failed")
        let uploadMock = MockUploadClient()
        let vm = makeVM(mock: uploadMock, exportService: exportMock)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        guard case .failure(.exportFailed) = vm.state else {
            return XCTFail("Expected .failure(.exportFailed), got \(vm.state)")
        }
        XCTAssertFalse(uploadMock.uploadInitCalled, "no upload after export failure")
        XCTAssertFalse(FileManager.default.fileExists(atPath: sourceURL.path),
                       "source deleted on export failure")
    }

    // MARK: — BV-EXP-08: cancel during export → .idle, all files cleaned

    func test_BVEXP08_cancelDuringExportResetsToIdleAndCleansFiles() async throws {
        let exportMock = MockExportService()
        exportMock.shouldHold = true
        let vm = makeVM(mock: makeSuccessMock(), exportService: exportMock)

        let sourceURL = try makeTempVideoFile()
        vm.startPicker()
        vm.pickerDidSelect(tempURL: sourceURL, mimeType: "video/mp4")
        await exportMock.waitUntilExportStarted()
        XCTAssertEqual(vm.state, .exporting(progress: 0))

        let task = vm.uploadTask
        vm.cancel()
        XCTAssertTrue(exportMock.cancelExportCalled, "cancel() must call exportService.cancelExport()")
        exportMock.resumeExport()   // unblock the held export so task can finish
        await task?.value

        XCTAssertEqual(vm.state, .idle)
        XCTAssertFalse(FileManager.default.fileExists(atPath: sourceURL.path), "source deleted on cancel")
    }

    // MARK: — BV-EXP-09: invalid output metadata → .invalidExportOutput, no upload

    func test_BVEXP09_invalidExportOutputMetadataBlocksUpload() async throws {
        let exportMock = MockExportService()
        exportMock.invalidateCodec = true   // codec = "unknown" → validation fails
        let uploadMock = MockUploadClient()
        let vm = makeVM(mock: uploadMock, exportService: exportMock)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        XCTAssertEqual(vm.state, .failure(.invalidExportOutput))
        XCTAssertFalse(uploadMock.uploadInitCalled, "no upload after invalid output metadata")
        XCTAssertFalse(FileManager.default.fileExists(atPath: sourceURL.path),
                       "source deleted even when output is invalid")
    }

    // MARK: — BV-EXP-10: success → all temp files (source + exported output) deleted

    func test_BVEXP10_successDeletesAllTempFiles() async throws {
        let exportMock = MockExportService()
        let vm = makeVM(mock: makeSuccessMock(), exportService: exportMock)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        XCTAssertEqual(vm.state, .success)
        XCTAssertFalse(FileManager.default.fileExists(atPath: sourceURL.path), "source deleted")
        let exportedURL = try XCTUnwrap(exportMock.lastOutputURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: exportedURL.path),
                       "exported output deleted on success")
    }

    // MARK: — BV-EXP-11: upload network failure → exported output remains for retry

    func test_BVEXP11_uploadNetworkFailureKeepsExportedOutputForRetry() async throws {
        let exportMock = MockExportService()
        let uploadMock = MockUploadClient()
        uploadMock.uploadInitResult = .failure(JugglingUploadError.networkError(URLError(.timedOut)))
        let vm = makeVM(mock: uploadMock, exportService: exportMock)
        let sourceURL = try makeTempVideoFile()

        await runUpload(vm: vm, tempURL: sourceURL)

        guard case .failure(.networkError) = vm.state else { return XCTFail("Expected network failure") }

        let exportedURL = try XCTUnwrap(exportMock.lastOutputURL)
        XCTAssertTrue(FileManager.default.fileExists(atPath: exportedURL.path),
                      "exported output must survive upload network failure for retry")
        XCTAssertFalse(FileManager.default.fileExists(atPath: sourceURL.path),
                       "source already deleted after export success")

        try? FileManager.default.removeItem(at: exportedURL)   // test cleanup
    }

    // MARK: — BV-EXP-12: retry after upload failure does NOT trigger a new export

    func test_BVEXP12_retryAfterUploadFailureDoesNotReExport() async throws {
        let exportMock = MockExportService()
        let uploadMock = MockUploadClient()
        uploadMock.uploadInitResult = .failure(JugglingUploadError.networkError(URLError(.timedOut)))
        let vm = makeVM(mock: uploadMock, exportService: exportMock)

        let sourceURL = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: sourceURL)
        guard case .failure = vm.state else { return XCTFail("Expected failure") }
        XCTAssertEqual(exportMock.exportCallCount, 1, "export called once on first attempt")

        let successMock = makeSuccessMock()
        uploadMock.uploadInitResult = successMock.uploadInitResult
        uploadMock.uploadVideoFileResult = successMock.uploadVideoFileResult
        uploadMock.completeUploadResult = successMock.completeUploadResult
        vm.retry()
        await vm.uploadTask?.value

        XCTAssertEqual(vm.state, .success)
        XCTAssertEqual(exportMock.exportCallCount, 1, "export must NOT be invoked again on retry")
    }

    // MARK: — BV-EXP-13: cancel during upload → exported output deleted

    func test_BVEXP13_cancelDuringUploadDeletesExportedOutput() async throws {
        let exportMock = MockExportService()
        let uploadMock = makeSuccessMock()
        uploadMock.holdUploadVideoFile = true
        let vm = makeVM(mock: uploadMock, exportService: exportMock)

        let sourceURL = try makeTempVideoFile()
        vm.startPicker()
        vm.pickerDidSelect(tempURL: sourceURL, mimeType: "video/mp4")
        await uploadMock.waitUntilUploadVideoFileStarted()

        XCTAssertEqual(vm.state, .uploading(progress: 0))
        let exportedURL = try XCTUnwrap(exportMock.lastOutputURL)
        XCTAssertTrue(FileManager.default.fileExists(atPath: exportedURL.path),
                      "exported output exists before cancel")

        let task = vm.uploadTask
        vm.cancel()
        uploadMock.resumeUpload()
        await task?.value

        XCTAssertEqual(vm.state, .idle)
        XCTAssertFalse(FileManager.default.fileExists(atPath: exportedURL.path),
                       "exported output deleted when cancel is called during upload")
    }

    // MARK: — BV-EXP-14: state never stuck in .exporting after export finishes

    func test_BVEXP14_stateNeverStuckInExportingAfterExportCompletes() async throws {
        let exportMock = MockExportService()
        exportMock.shouldSucceed = false
        exportMock.failureError = .exportFailed("forced failure")
        let vm = makeVM(mock: MockUploadClient(), exportService: exportMock)

        let sourceURL = try makeTempVideoFile()
        await runUpload(vm: vm, tempURL: sourceURL)

        if case .exporting = vm.state {
            XCTFail("state must never remain .exporting after export finishes")
        }
        guard case .failure = vm.state else {
            return XCTFail("Expected .failure after export failure, got \(vm.state)")
        }
    }
}

// MARK: — MockExportService

@MainActor
final class MockExportService: JugglingVideoExportServiceProtocol {

    var shouldSucceed = true
    var failureError: JugglingVideoExportError = .exportFailed("mock failure")

    // Reported fileSizeBytes in the result (the actual file written is 1 byte — the
    // ViewModel validates fileSizeBytes from the struct, not the real file size).
    var outputFileSizeBytes: Int = 1024

    // When true, sets codec = "unknown" to trigger isValidExportResult failure.
    var invalidateCodec = false

    // When true, export() suspends at the start until resumeExport() is called.
    var shouldHold = false
    private var holdContinuation: CheckedContinuation<Void, Never>?
    private var startedContinuation: CheckedContinuation<Void, Never>?

    private(set) var exportCallCount = 0
    private(set) var cancelExportCalled = false
    private(set) var lastOutputURL: URL?

    // Await until export() has been entered (for mid-export state assertions).
    func waitUntilExportStarted() async {
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            startedContinuation = cont
        }
    }

    // Unblock a held export.
    func resumeExport() {
        holdContinuation?.resume()
        holdContinuation = nil
    }

    func export(
        sourceURL: URL,
        progressHandler: @escaping (Double) -> Void
    ) async -> Result<JugglingVideoExportResult, JugglingVideoExportError> {
        exportCallCount += 1
        startedContinuation?.resume()
        startedContinuation = nil

        if shouldHold {
            await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                holdContinuation = cont
            }
        }

        guard shouldSucceed else { return .failure(failureError) }

        // Write a 1-byte sentinel so the ViewModel's fileExists check passes.
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("mock_export_\(UUID().uuidString).mp4")
        try? Data([0xAB]).write(to: url)

        let codec = invalidateCodec ? "unknown" : "avc1"
        let result = JugglingVideoExportResult(
            outputURL: url,
            fileSizeBytes: Int64(outputFileSizeBytes),
            width: 640, height: 360,
            codec: codec,
            fileType: "mp4",
            mimeType: "video/mp4"
        )
        lastOutputURL = url
        return .success(result)
    }

    func cancelExport() { cancelExportCalled = true }
}

// MARK: — MockUploadClient

@MainActor
final class MockUploadClient: JugglingAnnotationAPIClientProtocol {

    var uploadInitResult: Result<JugglingUploadInitResponse, Error> = .failure(JugglingUploadError.unauthorized)
    var uploadVideoFileResult: Result<JugglingUploadFileResponse, Error> = .failure(JugglingUploadError.unauthorized)
    var completeUploadResult: Result<JugglingCompleteResponse, Error> = .failure(JugglingUploadError.unauthorized)

    private(set) var uploadInitCalled = false
    private(set) var uploadVideoFileCalled = false
    private(set) var completeUploadCalled = false

    // Captured to verify URL/MIME passed to uploadVideoFile.
    private(set) var lastUploadVideoFileURL: URL?
    private(set) var lastUploadVideoFileMimeType: String?

    // When true, uploadVideoFile suspends until resumeUpload() is called.
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
        uploadInitCalled = true
        return try uploadInitResult.get()
    }

    func uploadVideoFile(videoId: String, fileURL: URL, mimeType: String) async throws -> JugglingUploadFileResponse {
        uploadVideoFileCalled = true
        lastUploadVideoFileURL = fileURL
        lastUploadVideoFileMimeType = mimeType
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
        completeUploadCalled = true
        return try completeUploadResult.get()
    }

    // — Unused protocol requirements (stub only)

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
}
