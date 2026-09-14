import AVFoundation
import Combine

// MARK: — PlaybackRate

enum PlaybackRate: Float, CaseIterable, Identifiable {
    case quarter        = 0.25
    case half           = 0.5
    case threeQuarters  = 0.75
    case normal         = 1.0
    case oneAndQuarter  = 1.25
    case oneAndHalf     = 1.5
    case double         = 2.0
    case triple         = 3.0

    var id: Float { rawValue }

    var label: String {
        switch self {
        case .quarter:       return "0.25×"
        case .half:          return "0.5×"
        case .threeQuarters: return "0.75×"
        case .normal:        return "1×"
        case .oneAndQuarter: return "1.25×"
        case .oneAndHalf:    return "1.5×"
        case .double:        return "2×"
        case .triple:        return "3×"
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
    @Published private(set) var videoNaturalSize:   CGSize?      = nil
    @Published private(set) var userRotation:       Int          = 0   // 0/90/180/270
    @Published private(set) var isReady:            Bool         = false
    @Published private(set) var isPreparing:        Bool         = false
    @Published private(set) var playbackError:      String?      = nil
    @Published var duration: CMTime = .zero

    // Set once when the asset loads; var (not private(set)) so tests can inject
    // a specific frame duration without going through AVAsset loading.
    var frameDuration: CMTime = CMTime(value: 1, timescale: 30)

    private let player: PlayerSeekable
    private var timeObserver: Any?
    private var itemStatusObservation: NSKeyValueObservation?
    private var assetPreparationTask: Task<Void, Never>?
    private var didHonorAutoplay = false

    /// Safe read-only access to the underlying AVPlayer for layer-based rendering.
    /// Returns nil when the controller is initialised with a non-AVPlayer mock (tests).
    var avPlayer: AVPlayer? { player as? AVPlayer }

    // Production init: uses a real AVPlayer.
    // initialRotation is restored from the server-persisted value; ignored if not in {0,90,180,270}.
    init(player: PlayerSeekable = AVPlayer(), initialRotation: Int = 0) {
        self.player = player
        self.userRotation = [0, 90, 180, 270].contains(initialRotation) ? initialRotation : 0
        // Unit-test players have no AVPlayerItem lifecycle. They remain ready so
        // the existing transport-control tests continue to exercise the controls.
        self.isReady = !(player is AVPlayer)
        if let avp = player as? AVPlayer {
            // The annotation asset is a complete local file. Waiting for a
            // streaming buffer can leave physical devices at 0:00 indefinitely.
            avp.automaticallyWaitsToMinimizeStalling = false
            setupPeriodicObserver(avp)
        }
    }

    deinit {
        assetPreparationTask?.cancel()
        itemStatusObservation?.invalidate()
        if let avp = player as? AVPlayer, let obs = timeObserver {
            avp.removeTimeObserver(obs)
        }
    }

    // MARK: — Asset loading

    // Call once the authenticated temp-file URL is ready (after download).
    // AVAsset properties are loaded asynchronously before the item is attached.
    // Autoplay is honored only after AVPlayerItem reaches .readyToPlay; this keeps
    // the UI from showing Pause while a physical device is still black at 0:00.
    func loadAsset(_ asset: AVAsset, autoplay: Bool = false) {
        assetPreparationTask?.cancel()
        itemStatusObservation?.invalidate()
        itemStatusObservation = nil
        didHonorAutoplay = false
        isReady = false
        isPreparing = true
        isPlaying = false
        playbackError = nil
        videoNaturalSize = nil
        duration = .zero

        assetPreparationTask = Task { [weak self] in
            guard let self else { return }
            do {
                let metadata = try await Self.prepareMetadata(for: asset)
                guard !Task.isCancelled else { return }

                self.frameDuration = metadata.frameDuration
                self.videoNaturalSize = metadata.displaySize
                self.duration = metadata.duration

                guard let avp = self.player as? AVPlayer else {
                    self.isPreparing = false
                    self.isReady = true
                    if autoplay { self.play() }
                    return
                }

                let item = AVPlayerItem(asset: asset)
                self.itemStatusObservation = item.observe(\.status, options: [.initial, .new]) {
                    [weak self, weak item] observedItem, _ in
                    let safeError = item?.error == nil ? nil : "Video cannot be played."
                    Task { @MainActor [weak self] in
                        self?.applyItemStatus(
                            observedItem.status,
                            autoplayRequested: autoplay,
                            errorDescription: safeError
                        )
                    }
                }
                avp.replaceCurrentItem(with: item)
            } catch {
                guard !Task.isCancelled else { return }
                self.applyItemStatus(
                    .failed,
                    autoplayRequested: autoplay,
                    errorDescription: "Video cannot be played."
                )
            }
        }
    }

    private struct PreparedMetadata {
        let frameDuration: CMTime
        let displaySize: CGSize
        let duration: CMTime
    }

    private enum AssetPreparationError: Error {
        case unplayable
        case missingVideoTrack
        case invalidDuration
    }

    private static func prepareMetadata(for asset: AVAsset) async throws -> PreparedMetadata {
        let playable = try await asset.load(.isPlayable)
        guard playable else { throw AssetPreparationError.unplayable }

        let duration = try await asset.load(.duration)
        guard duration.isValid, duration > .zero else {
            throw AssetPreparationError.invalidDuration
        }

        let tracks = try await asset.loadTracks(withMediaType: .video)
        guard let track = tracks.first else {
            throw AssetPreparationError.missingVideoTrack
        }

        let naturalSize = try await track.load(.naturalSize)
        let preferredTransform = try await track.load(.preferredTransform)
        let nominalFPS = try await track.load(.nominalFrameRate)
        let minFrameDuration = try await track.load(.minFrameDuration)

        return PreparedMetadata(
            frameDuration: frameDuration(
                nominalFPS: nominalFPS,
                minFrameDuration: minFrameDuration.isValid && minFrameDuration > .zero
                    ? minFrameDuration
                    : nil
            ),
            displaySize: displaySize(
                naturalSize: naturalSize,
                preferredTransform: preferredTransform
            ),
            duration: duration
        )
    }

    // Internal so the readiness behavior is regression-testable with a mock
    // player and does not require an AVFoundation decoder in unit tests.
    func applyItemStatus(
        _ status: AVPlayerItem.Status,
        autoplayRequested: Bool,
        errorDescription: String? = nil
    ) {
        switch status {
        case .unknown:
            isReady = false
            isPreparing = true
            isPlaying = false
        case .readyToPlay:
            isReady = true
            isPreparing = false
            playbackError = nil
            if autoplayRequested && !didHonorAutoplay {
                didHonorAutoplay = true
                play()
            }
        case .failed:
            player.pause()
            isReady = false
            isPreparing = false
            isPlaying = false
            playbackError = errorDescription ?? "Video cannot be played."
        @unknown default:
            player.pause()
            isReady = false
            isPreparing = false
            isPlaying = false
            playbackError = "Video cannot be played."
        }
    }

    // Returns the display size after applying preferredTransform.
    // Abs() handles mirrored transforms where width or height may be negative.
    // Exposed as static for direct unit-test coverage without an AVAsset.
    static func displaySize(naturalSize: CGSize, preferredTransform: CGAffineTransform) -> CGSize {
        let transformed = naturalSize.applying(preferredTransform)
        return CGSize(width: abs(transformed.width), height: abs(transformed.height))
    }

    // MARK: — Transport controls

    func play() {
        guard isReady else { return }
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
        // AVPlayer does not expose a per-rate capability check API on iOS 15.
        // Setting player.rate to an unsupported value (e.g. 3× on slow hardware)
        // causes AVPlayer to silently reduce to the nearest supported rate —
        // it does not crash or throw. selectedRate is still updated so the UI
        // reflects what the user requested; if the device cannot sustain 3×,
        // the playback will stutter visibly without any additional error handling.
        selectedRate = rate
        if isPlaying {
            player.rate = rate.rawValue
        }
    }

    // Cycles userRotation clockwise: 0 → 90 → 180 → 270 → 0.
    // Caller is responsible for persisting the new value via PATCH /rotation.
    func rotateClockwise() {
        userRotation = (userRotation + 90) % 360
    }

    // MARK: — Video render size (static — directly unit-testable without AVAsset)

    // Returns the frame size (in points) to assign to AVPlayerLayerView before
    // applying .rotationEffect(.degrees(userRotation)).
    //
    // The key invariant: the *visual footprint* after rotation must fit entirely
    // within the container with no clipping:
    //   - At 0°/180°: visual footprint = renderW × renderH  (fits vW:vH into W:H)
    //   - At 90°/270°: visual footprint = renderH × renderW (fits vH:vW into W:H,
    //     because the axes swap after rotation)
    //
    // Choosing the render frame instead of relying on .scaledToFit() avoids the
    // SwiftUI layout trap where rotationEffect fires after frame is already locked.
    static func computeVideoRenderSize(
        videoSize:    CGSize,
        container:    CGSize,
        userRotation: Int
    ) -> CGSize {
        let vW = videoSize.width,  vH = videoSize.height
        let W  = container.width,  H  = container.height
        guard vW > 0, vH > 0, W > 0, H > 0 else { return container }
        let scale: CGFloat
        switch userRotation % 360 {
        case 90, 270: scale = min(W / vH, H / vW)
        default:      scale = min(W / vW, H / vH)
        }
        return CGSize(width: vW * scale, height: vH * scale)
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
