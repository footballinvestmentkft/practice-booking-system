import ARKit
import SceneKit
import SwiftUI

// UIViewRepresentable wrapping ARSCNView for TrueDepth face tracking.
//
// Device support:
//   Supported:    iPhone X+ (A11 Bionic + TrueDepth camera), iOS 12+
//   Not supported: iPhone SE (1st/2nd gen), iPad without TrueDepth
//
// When ARFaceTrackingConfiguration.isSupported == false, the view shows
// a fallback message and does NOT attempt to start an ARSession.
// The caller (SpikeLivenessView) routes to the legacy manual flow.
//
// Privacy rules:
//   - The ARSession runs entirely on-device; no data leaves the device.
//   - No frame images, landmarks, or blendshape values are stored anywhere.
//   - ViewModel.update(with:) processes each anchor ephemerally in memory only.
struct ARFaceTrackingView: UIViewRepresentable {

    @ObservedObject var viewModel: SpikeLivenessViewModel

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.delegate              = context.coordinator
        view.session.delegate      = context.coordinator
        view.showsStatistics       = false
        view.automaticallyUpdatesLighting = false
        view.rendersCameraGrain    = false
        view.rendersMotionBlur     = false

        if ARFaceTrackingConfiguration.isSupported {
            let config = ARFaceTrackingConfiguration()
            config.isLightEstimationEnabled = false
            config.maximumNumberOfTrackedFaces = 1   // single face; multi-face = unsupported
            view.session.run(config, options: [.resetTracking, .removeExistingAnchors])
        }
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}

    static func dismantleUIView(_ uiView: ARSCNView, coordinator: Coordinator) {
        uiView.session.pause()
    }

    func makeCoordinator() -> Coordinator { Coordinator(viewModel: viewModel) }

    // MARK: — Coordinator

    final class Coordinator: NSObject, ARSCNViewDelegate, ARSessionDelegate {

        private let viewModel: SpikeLivenessViewModel
        private var hasFace = false

        init(viewModel: SpikeLivenessViewModel) {
            self.viewModel = viewModel
        }

        // ARSCNViewDelegate — face anchor added/updated/removed
        func renderer(_ renderer: SCNSceneRenderer, didAdd node: SCNNode, for anchor: ARAnchor) {
            guard anchor is ARFaceAnchor else { return }
            hasFace = true
        }

        func renderer(_ renderer: SCNSceneRenderer,
                      didUpdate node: SCNNode,
                      for anchor: ARAnchor) {
            guard let face = anchor as? ARFaceAnchor else { return }
            hasFace = true
            Task { @MainActor [weak viewModel] in
                viewModel?.update(with: face)
            }
        }

        func renderer(_ renderer: SCNSceneRenderer,
                      didRemove node: SCNNode,
                      for anchor: ARAnchor) {
            guard anchor is ARFaceAnchor else { return }
            hasFace = false
            Task { @MainActor [weak viewModel] in
                viewModel?.faceTrackingLost()
            }
        }

        // ARSessionDelegate — session interruption (call/lock screen)
        func sessionWasInterrupted(_ session: ARSession) {
            Task { @MainActor [weak viewModel] in
                viewModel?.faceTrackingLost()
            }
        }
    }
}

// MARK: — Device support check

extension ARFaceTrackingView {

    /// True when the current device has a TrueDepth camera and can run face tracking.
    static var isDeviceSupported: Bool {
        ARFaceTrackingConfiguration.isSupported
    }
}
