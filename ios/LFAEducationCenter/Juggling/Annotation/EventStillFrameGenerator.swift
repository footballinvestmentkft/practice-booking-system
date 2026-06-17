import AVFoundation
import UIKit

// MARK: — EventStillFrameGenerator (AN-3B2A P2B-2)
//
// Generates a still frame (UIImage) for a given event timestamp, for the
// planned per-event preview (P2B-3, not yet implemented). No UI here.
//
// - Time is clamped to [0, asset.duration] before generation, so a
//   negative or out-of-range timestampMs never produces a failed lookup.
// - appliesPreferredTrackTransform = true handles portrait/landscape
//   orientation (preferredTransform) automatically, matching
//   PlaybackController.displaySize's mixed-orientation handling.
// - generateCGImagesAsynchronously + cancelAllCGImageGeneration is the
//   iOS14-compatible async/cancellable API (the structured `image(at:)`
//   API is iOS16+, not usable here).
// - Cache is session-scoped, in-memory only, keyed by (videoId, timestampMs),
//   bounded (LRU-by-insertion eviction), and cleared by clearCache() when
//   the labeling screen closes. Never written to disk.

@MainActor
final class EventStillFrameGenerator {

    struct CacheKey: Hashable {
        let videoId:     String
        let timestampMs: Int
    }

    private var cache:      [CacheKey: UIImage] = [:]
    private var cacheOrder: [CacheKey] = []
    private let maxCacheSize: Int

    // Tracks in-flight generators so a repeated/changed request for the same
    // key can cancel the previous one before starting a new generation.
    private var activeGenerators: [CacheKey: AVAssetImageGenerator] = [:]

    init(maxCacheSize: Int = 8) {
        self.maxCacheSize = maxCacheSize
    }

    // MARK: — Public API

    // Returns a cached image without generating, or nil if not cached.
    func cachedImage(videoId: String, timestampMs: Int) -> UIImage? {
        cache[CacheKey(videoId: videoId, timestampMs: timestampMs)]
    }

    // Returns the cached image if present, otherwise generates (and caches)
    // a still frame from `asset` at `timestampMs`, clamped to the asset's
    // duration. Returns nil on failure or cancellation — callers should show
    // a placeholder in that case.
    @discardableResult
    func image(for asset: AVAsset, videoId: String, timestampMs: Int) async -> UIImage? {
        let key = CacheKey(videoId: videoId, timestampMs: timestampMs)
        if let cached = cache[key] { return cached }

        cancelGeneration(for: key)

        let time = Self.clampedTime(forMs: timestampMs, duration: asset.duration)

        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter  = .zero
        activeGenerators[key] = generator

        let image = await Self.generateImage(using: generator, at: time)

        // Only store/clear if this generator is still the active one for
        // the key (i.e. it wasn't cancelled and replaced meanwhile).
        guard activeGenerators[key] === generator else { return nil }
        activeGenerators.removeValue(forKey: key)

        if let image {
            store(image, for: key)
        }
        return image
    }

    // Cancels any in-flight generation for the given key. Safe to call even
    // if nothing is in flight.
    func cancelGeneration(videoId: String, timestampMs: Int) {
        cancelGeneration(for: CacheKey(videoId: videoId, timestampMs: timestampMs))
    }

    // Cancels all in-flight generation and drops every cached frame. Call
    // when the labeling screen closes.
    func clearCache() {
        for generator in activeGenerators.values {
            generator.cancelAllCGImageGeneration()
        }
        activeGenerators.removeAll()
        cache.removeAll()
        cacheOrder.removeAll()
    }

    // MARK: — Time clamping (pure, directly testable)

    // Clamps a requested millisecond offset to [0, duration]. A negative
    // timestamp clamps to 0; a timestamp past the end clamps to duration.
    // If duration is invalid/zero (e.g. asset not yet loaded), only the
    // lower bound is applied.
    nonisolated static func clampedTime(forMs timestampMs: Int, duration: CMTime) -> CMTime {
        let requested = CMTime(value: CMTimeValue(max(0, timestampMs)), timescale: 1000)
        guard duration.isValid, duration > .zero else { return requested }
        return min(requested, duration)
    }

    // MARK: — Private

    private func cancelGeneration(for key: CacheKey) {
        activeGenerators[key]?.cancelAllCGImageGeneration()
        activeGenerators.removeValue(forKey: key)
    }

    private func store(_ image: UIImage, for key: CacheKey) {
        if cache[key] == nil {
            cacheOrder.append(key)
        }
        cache[key] = image
        while cacheOrder.count > maxCacheSize {
            let evicted = cacheOrder.removeFirst()
            cache.removeValue(forKey: evicted)
        }
    }

    // Wraps the completion-handler based generateCGImagesAsynchronously in
    // an awaitable call. Resolves to nil on .cancelled/.failed or a missing
    // image — never throws, so callers always have a placeholder path.
    private static func generateImage(using generator: AVAssetImageGenerator, at time: CMTime) async -> UIImage? {
        await withCheckedContinuation { continuation in
            generator.generateCGImagesAsynchronously(forTimes: [NSValue(time: time)]) { _, cgImage, _, result, _ in
                switch result {
                case .succeeded:
                    if let cgImage {
                        continuation.resume(returning: UIImage(cgImage: cgImage))
                    } else {
                        continuation.resume(returning: nil)
                    }
                case .cancelled, .failed:
                    continuation.resume(returning: nil)
                @unknown default:
                    continuation.resume(returning: nil)
                }
            }
        }
    }
}
