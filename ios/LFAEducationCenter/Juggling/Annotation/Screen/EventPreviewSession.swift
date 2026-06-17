import AVFoundation
import Combine

// MARK: — EventPreviewSession (AN-3B2A P2C-1)
//
// Manages an isolated, muted AVQueuePlayer + AVPlayerLooper that loops a
// ±0.5 s window around an annotated event timestamp.
//
// Completely independent from the main PlaybackController — seeking, pausing
// or releasing this session has zero effect on the main player's position or
// audio session. isMuted is set at init and never changed.
//
// Lifecycle:
//   start(url:timestampMs:)   — loads asset duration, builds looper, plays
//   restart(url:timestampMs:) — stop() + start() (used on event navigation)
//   stop()                    — pauses player, disables looper, cancels load
//   togglePlayPause()         — flips isPlaying; no-op while loading / error
//   replay()                  — seeks to loopStart, resumes play
//
// Duration loading uses AVURLAsset.loadValuesAsynchronously so the code is
// safe on iOS 14. The continuation always resumes back onto the MainActor
// through the Task isolation inherited at creation time.
//
// Retain-cycle safety:
//   • async callbacks use [weak self]
//   • deinit cancels the load Task; AVFoundation cleans up player + looper
//   • AVPlayerLooper retains its AVQueuePlayer internally — this is a
//     documented one-way ownership; EventPreviewSession does NOT create a
//     cycle because the looper never holds a reference back to this class.

@MainActor
final class EventPreviewSession: ObservableObject {

    // MARK: — Published state

    @Published private(set) var isLoading: Bool   = false
    @Published private(set) var isPlaying: Bool   = false
    @Published private(set) var hasError:  Bool   = false
    @Published private(set) var loopStart: Double = 0
    @Published private(set) var loopEnd:   Double = 0

    // MARK: — Player (exposed so AVPlayerLayerView can bind to it)

    let player: AVQueuePlayer

    // MARK: — Private

    private var looper:          AVPlayerLooper?
    private var currentLoadTask: Task<Void, Never>?

    // MARK: — Init / deinit

    init() {
        player = AVQueuePlayer()
        player.isMuted = true
    }

    deinit {
        // Cancel the in-flight load Task. The player/looper are released by
        // ARC; AVFoundation handles internal teardown asynchronously.
        currentLoadTask?.cancel()
    }

    // MARK: — Public API

    /// Loads `url`, builds a ±0.5 s loop around `timestampMs`, and starts
    /// playing. Any previously running loop is stopped first.
    func start(url: URL, timestampMs: Int) {
        stopInternal()
        isLoading = true
        isPlaying = false
        hasError  = false
        loopStart = 0
        loopEnd   = 0

        let asset = AVURLAsset(url: url)
        currentLoadTask = Task { [weak self] in
            await self?.loadAndBuild(asset: asset, timestampMs: timestampMs)
        }
    }

    /// Stops the current loop and starts a fresh one for a different event.
    /// Equivalent to stop() followed by start().
    func restart(url: URL, timestampMs: Int) {
        start(url: url, timestampMs: timestampMs)
    }

    /// Pauses the player and tears down the looper. Safe to call at any time,
    /// including before the asset finishes loading.
    func stop() {
        stopInternal()
    }

    /// Toggles between play and pause. No-op while isLoading or hasError.
    func togglePlayPause() {
        guard !isLoading, !hasError else { return }
        if isPlaying {
            player.pause()
            isPlaying = false
        } else {
            player.play()
            isPlaying = true
        }
    }

    /// Seeks to the start of the current loop and resumes playback. No-op if
    /// no loop is active (loading, error, or stop() was called).
    func replay() {
        guard !hasError, !isLoading, looper != nil else { return }
        let target = CMTime(seconds: loopStart, preferredTimescale: 600)
        player.seek(to: target, toleranceBefore: .zero,
                    toleranceAfter: CMTime(seconds: 0.05, preferredTimescale: 600))
        player.play()
        isPlaying = true
    }

    // MARK: — Clamping (static for unit-testability)

    /// Returns (loopStart, loopEnd) clamped so that:
    ///   loopStart = max(0,           center - 0.5 s)
    ///   loopEnd   = min(durationSec, center + 0.5 s)
    /// Both are in seconds. The caller should verify loopEnd > loopStart
    /// before using the result (degenerate for very short assets).
    static func clampRange(timestampMs: Int, durationSec: Double) -> (start: Double, end: Double) {
        let center = Double(timestampMs) / 1000.0
        let start  = max(0.0, center - 0.5)
        let end    = min(durationSec, center + 0.5)
        return (start, end)
    }

    // MARK: — Private internals

    private func stopInternal() {
        currentLoadTask?.cancel()
        currentLoadTask = nil
        looper?.disableLooping()
        looper = nil
        player.pause()
        player.removeAllItems()
        isPlaying = false
        isLoading = false
    }

    private func loadAndBuild(asset: AVURLAsset, timestampMs: Int) async {
        // Suspend until the duration key is loaded.
        // loadValuesAsynchronously fires its callback on a background thread;
        // the CheckedContinuation resume is safe from any thread.
        // After resuming, execution returns here on the MainActor (Task inherits
        // the @MainActor isolation from the EventPreviewSession context).
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            asset.loadValuesAsynchronously(forKeys: ["duration"]) {
                cont.resume()
            }
        }

        guard !Task.isCancelled else { return }

        var statusError: NSError?
        let status = asset.statusOfValue(forKey: "duration", error: &statusError)

        guard status == .loaded else {
            hasError  = true
            isLoading = false
            return
        }

        let durationSec = asset.duration.seconds
        guard durationSec > 0, durationSec.isFinite else {
            hasError  = true
            isLoading = false
            return
        }

        buildLooper(asset: asset, timestampMs: timestampMs, durationSec: durationSec)
    }

    private func buildLooper(asset: AVURLAsset, timestampMs: Int, durationSec: Double) {
        let (start, end) = Self.clampRange(timestampMs: timestampMs, durationSec: durationSec)

        // A degenerate window (< 50 ms) means the asset is too short to loop
        // meaningfully. Surface as idle (no error — asset loaded fine).
        guard end - start >= 0.05 else {
            isLoading = false
            return
        }

        loopStart = start
        loopEnd   = end

        let item  = AVPlayerItem(asset: asset)
        let range = CMTimeRange(
            start: CMTime(seconds: start, preferredTimescale: 600),
            end:   CMTime(seconds: end,   preferredTimescale: 600)
        )

        // Clear any leftover items before attaching the new looper.
        player.removeAllItems()
        looper    = AVPlayerLooper(player: player, templateItem: item, timeRange: range)
        isLoading = false
        player.play()
        isPlaying = true
    }
}
