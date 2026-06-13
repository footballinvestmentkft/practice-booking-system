import AVFoundation
import Combine

// MARK: — PlaybackRate

enum PlaybackRate: Float, CaseIterable, Identifiable {
    case quarter = 0.25
    case half    = 0.5
    case normal  = 1.0

    var id: Float { rawValue }

    var label: String {
        switch self {
        case .quarter: return "0.25×"
        case .half:    return "0.5×"
        case .normal:  return "1×"
        }
    }
}

// MARK: — PlayerSeekable (testability seam; AVPlayer conforms via extension below)

protocol PlayerSeekable: AnyObject {
    func currentTime() -> CMTime  // matches AVPlayer's existing method signature
    var rate: Float { get set }
    func seek(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime)
    func play()
    func pause()
}

extension AVPlayer: PlayerSeekable {}

// MARK: — PlaybackController

// Owns one AVPlayer (or mock PlayerSeekable in tests) and exposes
// transport controls needed by the annotation screen:
//   - play / pause / togglePlayPause
//   - rate selection (0.25× / 0.5× / 1×)
//   - frame-accurate step (forward / backward)
//   - seek to a millisecond timestamp (for tap-to-seek from the event timeline)
//   - periodic currentTimestampMs for the timestamp readout
//
// No rendering — pair with AVPlayerLayerView (UIViewRepresentable, added in AN-3B)
// to display the video on screen.

@MainActor
final class PlaybackController: ObservableObject {

    @Published private(set) var isPlaying:          Bool         = false
    @Published private(set) var currentTimestampMs: Int          = 0
    @Published private(set) var selectedRate:       PlaybackRate = .normal
    @Published var duration: CMTime = .zero

    // Set once when the asset loads; var (not private(set)) so tests can inject
    // a specific frame duration without going through AVAsset loading.
    var frameDuration: CMTime = CMTime(value: 1, timescale: 30)

    private let player: PlayerSeekable
    private var timeObserver: Any?

    // Production init: uses a real AVPlayer.
    init(player: PlayerSeekable = AVPlayer()) {
        self.player = player
        if let avp = player as? AVPlayer {
            setupPeriodicObserver(avp)
        }
    }

    deinit {
        if let avp = player as? AVPlayer, let obs = timeObserver {
            avp.removeTimeObserver(obs)
        }
    }

    // MARK: — Asset loading

    // Call once the authenticated temp-file URL is ready (after download).
    // Reads nominalFrameRate / minFrameDuration from the asset before playback starts.
    func loadAsset(_ asset: AVAsset) {
        frameDuration = Self.effectiveFrameDuration(for: asset)
        if let avp = player as? AVPlayer {
            let item = AVPlayerItem(asset: asset)
            avp.replaceCurrentItem(with: item)
        }
        let dur = asset.duration
        if dur.isValid && dur > .zero {
            duration = dur
        }
    }

    // MARK: — Transport controls

    func play() {
        player.play()
        player.rate = selectedRate.rawValue
        isPlaying   = true
    }

    func pause() {
        player.pause()
        isPlaying = false
    }

    func togglePlayPause() {
        isPlaying ? pause() : play()
    }

    func setRate(_ rate: PlaybackRate) {
        selectedRate = rate
        if isPlaying {
            player.rate = rate.rawValue
        }
    }

    // Frame-accurate step — always pauses first. Clamps to [0, duration].
    func stepForward() {
        pause()
        let stepped = player.currentTime() + frameDuration
        let clamped = duration > .zero ? min(stepped, duration) : stepped
        player.seek(to: clamped, toleranceBefore: .zero, toleranceAfter: .zero)
        currentTimestampMs = clamped.asMilliseconds
    }

    func stepBackward() {
        pause()
        let stepped = player.currentTime() - frameDuration
        let clamped = max(.zero, stepped)
        player.seek(to: clamped, toleranceBefore: .zero, toleranceAfter: .zero)
        currentTimestampMs = clamped.asMilliseconds
    }

    // Coarser timeline-driven seek (tap-to-seek); uses default tolerance for
    // performance — precision is less critical than frame-step.
    func seek(toTimestampMs ms: Int) {
        let time = CMTime(value: CMTimeValue(ms), timescale: 1000)
        player.seek(
            to:              time,
            toleranceBefore: .positiveInfinity,
            toleranceAfter:  .positiveInfinity
        )
        currentTimestampMs = ms
    }

    // MARK: — Frame duration (static — directly unit-testable without AVAsset)

    // Given raw track metadata values, returns the CMTime to seek per frame-step.
    // Used by effectiveFrameDuration(for:) after the async AVAsset load, and
    // by tests directly.
    static func frameDuration(nominalFPS: Float, minFrameDuration: CMTime?) -> CMTime {
        if nominalFPS > 0 {
            return CMTime(seconds: 1.0 / Double(nominalFPS), preferredTimescale: 600)
        }
        if let min = minFrameDuration, min.isValid, min > .zero {
            return min
        }
        return CMTime(value: 1, timescale: 30)
    }

    // Synchronous version used for production asset loading (iOS 14 compatible).
    static func effectiveFrameDuration(for asset: AVAsset) -> CMTime {
        guard let track = asset.tracks(withMediaType: .video).first else {
            return CMTime(value: 1, timescale: 30)
        }
        let fps    = track.nominalFrameRate
        let minDur = track.minFrameDuration
        return frameDuration(nominalFPS: fps, minFrameDuration: minDur.isValid && minDur > .zero ? minDur : nil)
    }

    // MARK: — Private

    private func setupPeriodicObserver(_ avPlayer: AVPlayer) {
        let interval = CMTime(value: 1, timescale: 30)
        timeObserver = avPlayer.addPeriodicTimeObserver(
            forInterval: interval,
            queue:        .main
        ) { [weak self] time in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.currentTimestampMs = time.asMilliseconds
                self.isPlaying          = self.player.rate != 0
            }
        }
    }
}

// MARK: — CMTime helpers

extension CMTime {
    var asMilliseconds: Int {
        guard isValid, !isIndefinite, !isNegativeInfinity, !isPositiveInfinity else { return 0 }
        return Int(seconds * 1000)
    }
}
