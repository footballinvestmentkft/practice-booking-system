import XCTest
@testable import LFAEducationCenter

// MARK: — PoseSnapshotFetchOnAppearTests
//
// Verifies the concurrent pose-snapshot fetch introduced in the onAppear fix:
//
//   PSF-01  Guard path: fetchPoseSnapshots() with MockAnnotationAPIClient returns []
//           without crashing. The ViewModel's guard-cast (apiClient as?
//           JugglingAnnotationAPIClient) returns nil for any mock, so []
//           is always the correct result in unit tests. This documents the
//           contract: the onAppear Task is fire-and-safe even if the API
//           client isn't the real concrete type.
//
//   PSF-02  Concurrent safety: onAppear() and fetchPoseSnapshots() launched
//           concurrently (as the new .onAppear modifier does) do not deadlock,
//           crash, or leave the ViewModel in a corrupt state.
//
//   PSF-03  Idempotency: calling fetchPoseSnapshots() twice in sequence returns
//           the same [] result both times. The second call (from
//           onChange(of: loader.state → .ready)) correctly overwrites the first
//           without side-effects.

@MainActor
final class PoseSnapshotFetchOnAppearTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pose_fetch_on_appear_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    // MARK: — Helpers

    private func makeViewModel() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId:        1,
            videoId:       "vid-psf-test",
            apiClient:     MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore:    LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // PSF-01: fetchPoseSnapshots() with a mock client returns [] without crashing.
    //
    // The ViewModel guards against the mock via:
    //   guard let client = apiClient as? JugglingAnnotationAPIClient else { return [] }
    // This means the onAppear concurrent Task is always a safe no-op in tests.
    func test_PSF_01_fetchPoseSnapshotsWithMockReturnsEmptySafely() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let snapshots = await vm.fetchPoseSnapshots()

        XCTAssertEqual(snapshots, [],
                       "fetchPoseSnapshots() must return [] for MockAnnotationAPIClient — " +
                       "the guard cast returns nil and the function exits early without crashing")
    }

    // PSF-02: onAppear() and fetchPoseSnapshots() run concurrently without
    // deadlock, crash, or ViewModel corruption.
    //
    // This mirrors the new .onAppear modifier:
    //   Task { await onAppear() }
    //   Task { poseSnapshots = await vm.fetchPoseSnapshots() }
    func test_PSF_02_concurrentOnAppearAndFetchPoseSnapshots_isRaceFree() async {
        let vm = makeViewModel()

        // Launch both tasks concurrently (mirrors the new .onAppear modifier)
        async let onAppearTask: Void    = vm.onAppear()
        async let fetchTask: [PoseSnapshotOut] = vm.fetchPoseSnapshots()

        let (_, snapshots) = await (onAppearTask, fetchTask)

        // The VM must be in a usable state after both complete
        XCTAssertEqual(snapshots, [],
                       "fetchPoseSnapshots() concurrent with onAppear() must still return [] " +
                       "with a mock — no crash or deadlock")
        // The session should be initialised (onAppear completed normally)
        XCTAssertNotNil(vm.taxonomy,
                        "onAppear() must complete normally when run concurrently with fetchPoseSnapshots()")
    }

    // PSF-03: two sequential fetchPoseSnapshots() calls return the same result.
    //
    // Defense-in-depth: onChange(of: loader.state → .ready) fires a second fetch
    // after the onAppear Task completes. The second result must overwrite the
    // first without altering the value (idempotent assign).
    func test_PSF_03_twoSequentialFetches_areIdempotent() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let first  = await vm.fetchPoseSnapshots()
        let second = await vm.fetchPoseSnapshots()

        XCTAssertEqual(first, second,
                       "fetchPoseSnapshots() called twice must return identical results — " +
                       "the onAppear + onChange(loader.state) double-fetch is safe to assign")
    }
}
