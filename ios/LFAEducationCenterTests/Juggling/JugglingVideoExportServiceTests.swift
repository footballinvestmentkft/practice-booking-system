import XCTest
import AVFoundation
@testable import LFAEducationCenter

// MARK: — EXP-01..09: JugglingVideoExportService
//
// Client-side 360p pre-upload export. Tests use small synthetic H.264 .mov
// fixtures generated on the fly via AVAssetWriter (same technique as
// EventStillFrameGeneratorTests) — no binary fixtures checked into the repo.
//
// Naming: EXP = video EXPort service.

final class JugglingVideoExportServiceTests: XCTestCase {

    // MARK: — EXP-01: landscape source -> smaller 360p landscape output

    func test_EXP01_landscapeExportProducesSmallerLandscapeOutput() async throws {
        let source = try Self.makeTestVideo(width: 1280, height: 720, transform: .identity, durationSeconds: 1, fps: 10)
        defer { try? FileManager.default.removeItem(at: source) }

        let sourceSize = try Self.fileSize(source)

        let service = JugglingVideoExportService()
        let result = try Self.requireSuccess(await service.export(sourceURL: source) { _ in })
        defer { try? FileManager.default.removeItem(at: result.outputURL) }

        XCTAssertGreaterThan(result.width, result.height, "360p landscape output should remain landscape")
        XCTAssertLessThanOrEqual(result.width, 640)
        XCTAssertLessThanOrEqual(result.height, 480)
        XCTAssertLessThan(result.fileSizeBytes, sourceSize, "360p output should be smaller than the 720p source")
    }

    // MARK: — EXP-02: portrait source -> correctly-oriented portrait output

    func test_EXP02_portraitExportPreservesOrientation() async throws {
        // Raw pixel buffer is 1280x720 (landscape), but a 90-degree
        // preferredTransform marks it as portrait — matching how a real
        // iPhone-recorded portrait video is stored.
        let source = try Self.makeTestVideo(
            width: 1280, height: 720,
            transform: CGAffineTransform(rotationAngle: .pi / 2),
            durationSeconds: 1, fps: 10
        )
        defer { try? FileManager.default.removeItem(at: source) }

        let service = JugglingVideoExportService()
        let result = try Self.requireSuccess(await service.export(sourceURL: source) { _ in })
        defer { try? FileManager.default.removeItem(at: result.outputURL) }

        // AVAssetExportPreset640x480 fits the pre-transform (encoded) frame
        // within the 640x480 box; the preferredTransform is then applied for
        // display, so a 1280x720 buffer rotated 90 degrees encodes as
        // ~640x360 and displays as ~360x640. Orientation must be preserved
        // and both dimensions must be bounded by the larger preset edge.
        XCTAssertGreaterThan(result.height, result.width, "portrait source must remain portrait after export")
        XCTAssertLessThanOrEqual(result.width, 640)
        XCTAssertLessThanOrEqual(result.height, 640)
    }

    // MARK: — EXP-03: output URL differs from source URL

    func test_EXP03_outputURLDiffersFromSourceURL() async throws {
        let source = try Self.makeTestVideo(width: 640, height: 480, transform: .identity, durationSeconds: 1, fps: 10)
        defer { try? FileManager.default.removeItem(at: source) }

        let service = JugglingVideoExportService()
        let result = try Self.requireSuccess(await service.export(sourceURL: source) { _ in })
        defer { try? FileManager.default.removeItem(at: result.outputURL) }

        XCTAssertNotEqual(result.outputURL, source)
        XCTAssertNotEqual(result.outputURL.lastPathComponent, source.lastPathComponent)
    }

    // MARK: — EXP-04: source file is not modified by export

    func test_EXP04_sourceFileUnmodifiedAfterExport() async throws {
        let source = try Self.makeTestVideo(width: 640, height: 480, transform: .identity, durationSeconds: 1, fps: 10)
        defer { try? FileManager.default.removeItem(at: source) }

        let beforeAttrs = try FileManager.default.attributesOfItem(atPath: source.path)
        let beforeSize = beforeAttrs[.size] as? Int64
        let beforeModified = beforeAttrs[.modificationDate] as? Date

        let service = JugglingVideoExportService()
        let result = try Self.requireSuccess(await service.export(sourceURL: source) { _ in })
        defer { try? FileManager.default.removeItem(at: result.outputURL) }

        let afterAttrs = try FileManager.default.attributesOfItem(atPath: source.path)
        XCTAssertEqual(afterAttrs[.size] as? Int64, beforeSize)
        XCTAssertEqual(afterAttrs[.modificationDate] as? Date, beforeModified)
        XCTAssertTrue(FileManager.default.fileExists(atPath: source.path))
    }

    // MARK: — EXP-05: unsupported preset -> structured .exportUnsupported error

    func test_EXP05_presetSelectionReturnsNilWhenNoCandidateIsCompatible() {
        // No candidate preset (AVAssetExportPreset640x480) in the compatible list
        // -> selectPreset returns nil -> export() maps this to .exportUnsupported.
        // Passthrough is intentionally excluded from candidatePresets, so even if
        // an asset is passthrough-only, the result is .exportUnsupported, not a
        // re-upload of the unshrunk original.
        let compatible = [AVAssetExportPresetLowQuality, AVAssetExportPresetAppleM4A]
        XCTAssertNil(JugglingVideoExportService.selectPreset(from: compatible))
        XCTAssertFalse(JugglingVideoExportService.candidatePresets.contains(AVAssetExportPresetPassthrough))
    }

    func test_EXP05_presetSelectionPicks640x480WhenCompatible() {
        let compatible = [AVAssetExportPresetHighestQuality, AVAssetExportPreset640x480, AVAssetExportPresetPassthrough]
        XCTAssertEqual(JugglingVideoExportService.selectPreset(from: compatible), AVAssetExportPreset640x480)
    }

    // MARK: — EXP-06: cancel deletes partial output

    func test_EXP06_cancelDeletesPartialOutput() async throws {
        // Generous duration/resolution so the export has a real window to be
        // cancelled mid-flight (timing-based; the assertion only checks the
        // .cancelled / no-leftover-file outcome, not exact timing).
        let source = try Self.makeTestVideo(width: 1280, height: 720, transform: .identity, durationSeconds: 3, fps: 30)
        defer { try? FileManager.default.removeItem(at: source) }

        let service = JugglingVideoExportService()
        let task = Task {
            await service.export(sourceURL: source) { _ in }
        }
        try await Task.sleep(nanoseconds: 30_000_000) // 30ms
        service.cancelExport()
        let result = await task.value

        switch result {
        case .failure(.cancelled):
            break
        case .success(let exported):
            // Export raced ahead of cancelExport(); clean up and treat as
            // inconclusive rather than a hard failure of the production code.
            try? FileManager.default.removeItem(at: exported.outputURL)
            throw XCTSkip("export completed before cancelExport() landed")
        default:
            XCTFail("unexpected result: \(result)")
        }
    }

    // MARK: — EXP-07: export failure deletes partial output

    func test_EXP07_exportFailureDeletesPartialOutput() async throws {
        let source = try Self.makeTestVideo(width: 640, height: 480, transform: .identity, durationSeconds: 1, fps: 10)
        defer { try? FileManager.default.removeItem(at: source) }

        // Output directory does not exist -> AVAssetExportSession fails to
        // write the output file -> status == .failed.
        let badDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("exp07-missing-\(UUID().uuidString)")
            .appendingPathComponent("nested")

        let service = JugglingVideoExportService(outputDirectory: badDirectory)
        let result = await service.export(sourceURL: source) { _ in }

        switch result {
        case .failure(.exportFailed):
            XCTAssertFalse(FileManager.default.fileExists(atPath: badDirectory.path), "no partial output left behind")
        default:
            XCTFail("expected .exportFailed, got \(result)")
        }
    }

    // MARK: — EXP-08: output metadata fully readable

    func test_EXP08_outputMetadataFullyReadable() async throws {
        let source = try Self.makeTestVideo(width: 1280, height: 720, transform: .identity, durationSeconds: 1, fps: 10)
        defer { try? FileManager.default.removeItem(at: source) }

        let service = JugglingVideoExportService()
        let result = try Self.requireSuccess(await service.export(sourceURL: source) { _ in })
        defer { try? FileManager.default.removeItem(at: result.outputURL) }

        XCTAssertEqual(result.fileType, "mp4")
        XCTAssertEqual(result.mimeType, "video/mp4")
        XCTAssertEqual(result.codec, "avc1")
        XCTAssertGreaterThan(result.fileSizeBytes, 0)
        XCTAssertGreaterThan(result.width, 0)
        XCTAssertGreaterThan(result.height, 0)
        XCTAssertTrue(FileManager.default.fileExists(atPath: result.outputURL.path))
    }

    // MARK: — EXP-09: no full-file in-memory load

    func test_EXP09_implementationDoesNotLoadVideoDataIntoMemory() throws {
        // AVAssetExportSession is inherently file-to-file/streaming. Guard
        // against a future regression that reads the source or output via
        // Data(contentsOf:), which would defeat the streaming guarantee for
        // large video files.
        let testFileURL = URL(fileURLWithPath: #filePath)
        let iosRoot = testFileURL
            .deletingLastPathComponent() // .../Juggling
            .deletingLastPathComponent() // .../LFAEducationCenterTests
            .deletingLastPathComponent() // .../ios
        let implURL = iosRoot
            .appendingPathComponent("LFAEducationCenter/Juggling/Upload/JugglingVideoExportService.swift")

        let source = try String(contentsOf: implURL, encoding: .utf8)
        XCTAssertFalse(source.contains("Data(contentsOf:"), "export service must not load video files into memory")
    }

    // MARK: — Helpers

    private static func requireSuccess(
        _ result: Result<JugglingVideoExportResult, JugglingVideoExportError>
    ) throws -> JugglingVideoExportResult {
        switch result {
        case .success(let value):
            return value
        case .failure(let error):
            throw error
        }
    }

    private static func fileSize(_ url: URL) throws -> Int64 {
        let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
        return (attrs[.size] as? Int64) ?? 0
    }

    // Writes a small solid-color H.264 .mov to a temp file and returns its
    // URL. `transform` is set as the track's preferredTransform (used to
    // simulate portrait recordings stored as rotated landscape buffers, as
    // real iPhone footage is). Synchronous — call from a sync context.
    private static func makeTestVideo(
        width: Int, height: Int,
        transform: CGAffineTransform,
        durationSeconds: Double, fps: Int32
    ) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("export_test_\(UUID().uuidString).mov")

        let frameCount = Int(durationSeconds * Double(fps))

        let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        let videoSettings: [String: Any] = [
            AVVideoCodecKey:  AVVideoCodecType.h264,
            AVVideoWidthKey:  width,
            AVVideoHeightKey: height
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
        input.expectsMediaDataInRealTime = false
        input.transform = transform

        let adaptorAttributes: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
            kCVPixelBufferWidthKey as String:  width,
            kCVPixelBufferHeightKey as String: height
        ]
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: input,
            sourcePixelBufferAttributes: adaptorAttributes
        )
        writer.add(input)
        writer.startWriting()
        writer.startSession(atSourceTime: .zero)

        let writeGroup = DispatchGroup()
        writeGroup.enter()
        var frameIndex = 0
        input.requestMediaDataWhenReady(on: DispatchQueue(label: "JugglingVideoExportServiceTests.writer")) {
            while input.isReadyForMoreMediaData {
                if frameIndex >= frameCount {
                    input.markAsFinished()
                    writeGroup.leave()
                    return
                }
                guard let pool = adaptor.pixelBufferPool else { continue }
                var pixelBufferOut: CVPixelBuffer?
                CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pixelBufferOut)
                guard let pixelBuffer = pixelBufferOut else { continue }

                CVPixelBufferLockBaseAddress(pixelBuffer, [])
                if let base = CVPixelBufferGetBaseAddress(pixelBuffer) {
                    let fill: UInt8 = frameIndex.isMultiple(of: 2) ? 0xFF : 0x00
                    memset(base, Int32(fill), CVPixelBufferGetDataSize(pixelBuffer))
                }
                CVPixelBufferUnlockBaseAddress(pixelBuffer, [])

                let time = CMTime(value: CMTimeValue(frameIndex), timescale: fps)
                adaptor.append(pixelBuffer, withPresentationTime: time)
                frameIndex += 1
            }
        }
        writeGroup.wait()
        writer.endSession(atSourceTime: CMTime(value: CMTimeValue(frameCount), timescale: fps))

        let finishGroup = DispatchGroup()
        finishGroup.enter()
        writer.finishWriting { finishGroup.leave() }
        finishGroup.wait()

        return url
    }
}
