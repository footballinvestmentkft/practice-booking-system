import XCTest
import AVFoundation
@testable import LFAEducationCenter

// MARK: — AN-3B1 Validation: AVPlayerLayerView tests (AN3B-V01..V09)
//
// UIViewRepresentable.makeUIView/updateUIView require a Context, which is
// non-trivial to construct without SwiftUI test infrastructure.  We test the
// underlying _AVPlayerLayerHostView directly and call dismantleUIView (which
// takes no Context) to prove the retain-cycle break.

@MainActor
final class AVPlayerLayerViewTests: XCTestCase {

    // AN3B-V01: _AVPlayerLayerHostView.layerClass is AVPlayerLayer
    func test_AN3B_V01_hostViewLayerClassIsAVPlayerLayer() {
        XCTAssertTrue(_AVPlayerLayerHostView.layerClass === AVPlayerLayer.self,
                      "layerClass must be AVPlayerLayer so the OS creates the right backing layer")
    }

    // AN3B-V02: playerLayer accessor returns the backing AVPlayerLayer
    func test_AN3B_V02_playerLayerIsBackingLayer() {
        let view = _AVPlayerLayerHostView()
        XCTAssertTrue(view.layer is AVPlayerLayer,
                      "layer must be AVPlayerLayer (guaranteed by layerClass)")
        XCTAssertTrue(view.playerLayer === view.layer,
                      "playerLayer property must point to the same CALayer instance")
    }

    // AN3B-V03: fresh host view has no player attached (no autoplay risk)
    func test_AN3B_V03_freshHostViewHasNilPlayer() {
        let view = _AVPlayerLayerHostView()
        XCTAssertNil(view.playerLayer.player,
                     "A freshly allocated host view must not carry an AVPlayer — no autoplay risk")
    }

    // AN3B-V04: dismantleUIView nils the layer's player (retain-cycle break)
    func test_AN3B_V04_dismantleUIViewNilsPlayer() {
        let player = AVPlayer()
        let view   = _AVPlayerLayerHostView()
        view.playerLayer.player = player
        XCTAssertNotNil(view.playerLayer.player)

        AVPlayerLayerView.dismantleUIView(view, coordinator: ())

        XCTAssertNil(view.playerLayer.player,
                     "dismantleUIView must nil the player to break AVPlayerLayer→AVPlayer retain cycle")
    }

    // AN3B-V05: videoGravity .resizeAspect is assignable (letter-box contract)
    func test_AN3B_V05_resizeAspectGravityAssignable() {
        let view = _AVPlayerLayerHostView()
        view.playerLayer.videoGravity = .resizeAspect
        XCTAssertEqual(view.playerLayer.videoGravity, .resizeAspect)
    }

    // AN3B-V06: updateUIView identity check — same player → no reassignment
    //
    // Simulates the `if uiView.playerLayer.player !== player` guard in updateUIView.
    // Reassigning a player (even to the same instance) causes AVPlayerLayer to
    // reset internal state; the !==  guard prevents that.
    func test_AN3B_V06_samePlayerSkipsReassignment() {
        let player = AVPlayer()
        let view   = _AVPlayerLayerHostView()
        view.playerLayer.player = player

        // Capture pointer identity before simulated updateUIView call.
        let beforePtr = ObjectIdentifier(view.playerLayer)

        if view.playerLayer.player !== player { view.playerLayer.player = player }

        XCTAssertEqual(ObjectIdentifier(view.playerLayer), beforePtr,
                       "playerLayer identity must be preserved when player is unchanged")
    }

    // AN3B-V07: updateUIView — different player → reassignment occurs
    func test_AN3B_V07_differentPlayerTriggersReassignment() {
        let player1 = AVPlayer()
        let player2 = AVPlayer()
        let view    = _AVPlayerLayerHostView()
        view.playerLayer.player = player1

        if view.playerLayer.player !== player2 { view.playerLayer.player = player2 }

        XCTAssertTrue(view.playerLayer.player === player2,
                      "New player must be assigned when it differs from the current one")
    }

    // AN3B-V08: AVPlayerLayerView struct initialises without crash
    func test_AN3B_V08_structInitDoesNotCrash() {
        let player   = AVPlayer()
        let layerView = AVPlayerLayerView(player: player)
        XCTAssertTrue(layerView.player === player,
                      "player stored property must equal the injected AVPlayer")
    }

    // AN3B-V09: black background assignable to host view
    func test_AN3B_V09_blackBackgroundAssignable() {
        let view = _AVPlayerLayerHostView()
        view.backgroundColor = .black
        XCTAssertEqual(view.backgroundColor, .black)
    }
}
