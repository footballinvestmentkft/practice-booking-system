import XCTest
import AVFoundation
@testable import LFAEducationCenter

// MARK: — AN-3B2A P2B-2: EventStillFrameGenerator

@MainActor
final class EventStillFrameGeneratorTests: XCTestCase {

    private var assetURL: URL!
    private var asset: AVAsset!

    // 1 second, 10 fps, 64x64 — small enough to write quickly in setUp.
    override func setUpWithError() throws {
        try super.setUpWithError()
        assetURL = try Self.makeTestVideoAsset()
        asset = AVAsset(url: assetURL)
    }

    override func tearDown() {
        if let assetURL { try? FileManager.default.removeItem(at: assetURL) }
        assetURL = nil
        asset = nil
        super.tearDown()
    }

    // MARK: — clampedTime (pure, no asset I/O)

    func test_clampedTime_negativeTimestamp_clampsToZero() {
        let duration = CMTime(seconds: 5, preferredTimescale: 600)
        let time = EventStillFrameGenerator.clampedTime(forMs: -500, duration: duration)
        XCTAssertEqual(time, .zero)
    }

    func test_clampedTime_beyondDuration_clampsToDuration() {
        let duration = CMTime(seconds: 2, preferredTimescale: 600)
        let time = EventStillFrameGenerator.clampedTime(forMs: 5000, duration: duration)
        XCTAssertEqual(time, duration)
    }

    func test_clampedTime_withinRange_returnsRequestedMs() {
        let duration = CMTime(seconds: 5, preferredTimescale: 600)
        let time = EventStillFrameGenerator.clampedTime(forMs: 1500, duration: duration)
        XCTAssertEqual(time, CMTime(value: 1500, timescale: 1000))
    }

    func test_clampedTime_invalidDuration_onlyClampsLowerBound() {
        let time = EventStillFrameGenerator.clampedTime(forMs: -100, duration: .invalid)
        XCTAssertEqual(time, .zero)

        let time2 = EventStillFrameGenerator.clampedTime(forMs: 1500, duration: .invalid)
        XCTAssertEqual(time2, CMTime(value: 1500, timescale: 1000))
    }

    // MARK: — image generation + cache

    func test_image_generatesFrameForRealAsset() async {
        let generator = EventStillFrameGenerator()
        let image = await generator.image(for: asset, videoId: "vid-1", timestampMs: 200)
        XCTAssertNotNil(image)
    }

    func test_image_negativeTimestamp_stillGeneratesClampedFrame() async {
        let generator = EventStillFrameGenerator()
        let image = await generator.image(for: asset, videoId: "vid-1", timestampMs: -500)
        XCTAssertNotNil(image)
    }

    func test_image_beyondDuration_stillGeneratesClampedFrame() async {
        let generator = EventStillFrameGenerator()
        let image = await generator.image(for: asset, videoId: "vid-1", timestampMs: 999_999)
        XCTAssertNotNil(image)
    }

    func test_image_invalidAsset_returnsNilWithoutThrowing() async {
        let generator = EventStillFrameGenerator()
        let badAsset = AVAsset(url: URL(fileURLWithPath: "/tmp/does_not_exist_\(UUID().uuidString).mov"))
        let image = await generator.image(for: badAsset, videoId: "vid-1", timestampMs: 100)
        XCTAssertNil(image)
    }

    func test_cachedImage_returnsNilBeforeGeneration() {
        let generator = EventStillFrameGenerator()
        XCTAssertNil(generator.cachedImage(videoId: "vid-1", timestampMs: 200))
    }

    func test_image_secondCallForSameKey_returnsCachedImage() async {
        let generator = EventStillFrameGenerator()
        let first = await generator.image(for: asset, videoId: "vid-1", timestampMs: 200)
        XCTAssertNotNil(first)

        XCTAssertNotNil(generator.cachedImage(videoId: "vid-1", timestampMs: 200))

        // A second request for the same key returns the cached instance
        // without needing the asset again (pass the same asset — identity
        // not checked, but no crash/hang confirms the cache path).
        let second = await generator.image(for: asset, videoId: "vid-1", timestampMs: 200)
        XCTAssertNotNil(second)
        XCTAssertTrue(first === second)
    }

    // P2B-2: cache key includes videoId — same timestamp, different video,
    // is a cache miss.
    func test_cacheKey_differsByVideoId() async {
        let generator = EventStillFrameGenerator()
        _ = await generator.image(for: asset, videoId: "vid-1", timestampMs: 200)
        XCTAssertNil(generator.cachedImage(videoId: "vid-2", timestampMs: 200))
    }

    // P2B-2: bounded cache — exceeding maxCacheSize evicts the oldest entry.
    func test_cache_evictsOldestEntryWhenExceedingMaxSize() async {
        let generator = EventStillFrameGenerator(maxCacheSize: 2)
        _ = await generator.image(for: asset, videoId: "vid-1", timestampMs: 0)
        _ = await generator.image(for: asset, videoId: "vid-1", timestampMs: 100)
        _ = await generator.image(for: asset, videoId: "vid-1", timestampMs: 200)

        XCTAssertNil(generator.cachedImage(videoId: "vid-1", timestampMs: 0), "oldest entry should be evicted")
        XCTAssertNotNil(generator.cachedImage(videoId: "vid-1", timestampMs: 100))
        XCTAssertNotNil(generator.cachedImage(videoId: "vid-1", timestampMs: 200))
    }

    // P2B-2: clearCache() drops every cached frame (called on labeling screen close).
    func test_clearCache_removesAllCachedImages() async {
        let generator = EventStillFrameGenerator()
        _ = await generator.image(for: asset, videoId: "vid-1", timestampMs: 0)
        _ = await generator.image(for: asset, videoId: "vid-1", timestampMs: 100)
        XCTAssertNotNil(generator.cachedImage(videoId: "vid-1", timestampMs: 0))

        generator.clearCache()

        XCTAssertNil(generator.cachedImage(videoId: "vid-1", timestampMs: 0))
        XCTAssertNil(generator.cachedImage(videoId: "vid-1", timestampMs: 100))
    }

    // P2B-2: cancelGeneration is safe to call even when nothing is in flight.
    func test_cancelGeneration_withoutInFlightRequest_doesNotCrash() {
        let generator = EventStillFrameGenerator()
        generator.cancelGeneration(videoId: "vid-1", timestampMs: 200)
    }

    // MARK: — Test asset generation

    // Writes a tiny (1s, 10fps, 64x64) solid-color H.264 .mov to a temp file
    // and returns its URL. Synchronous — call from setUpWithError, not from
    // an async test, to avoid blocking the cooperative thread pool.
    private static func makeTestVideoAsset() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("stillframe_test_\(UUID().uuidString).mov")

        let size: CGFloat = 64
        let fps: Int32 = 10
        let frameCount = 10

        let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        let videoSettings: [String: Any] = [
            AVVideoCodecKey:  AVVideoCodecType.h264,
            AVVideoWidthKey:  Int(size),
            AVVideoHeightKey: Int(size)
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
        input.expectsMediaDataInRealTime = false

        let adaptorAttributes: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
            kCVPixelBufferWidthKey as String:  Int(size),
            kCVPixelBufferHeightKey as String: Int(size)
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
        input.requestMediaDataWhenReady(on: DispatchQueue(label: "EventStillFrameGeneratorTests.writer")) {
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

        let finishGroup = DispatchGroup()
        finishGroup.enter()
        writer.finishWriting { finishGroup.leave() }
        finishGroup.wait()

        return url
    }
}
