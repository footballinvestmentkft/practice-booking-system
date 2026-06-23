import SwiftUI
import AVFoundation
import UIKit

#if DEBUG
struct CapturePreviewView: UIViewRepresentable {
    let captureSession: AVCaptureSession
    let interfaceOrientation: UIInterfaceOrientation

    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        view.previewLayer.session = captureSession
        view.previewLayer.videoGravity = .resizeAspectFill
        applyOrientation(to: view)
        return view
    }

    func updateUIView(_ uiView: PreviewUIView, context: Context) {
        applyOrientation(to: uiView)
    }

    static func dismantleUIView(_ uiView: PreviewUIView, coordinator: ()) {
        // Break AVCaptureVideoPreviewLayer → AVCaptureSession retain cycle
        uiView.previewLayer.session = nil
    }

    private func applyOrientation(to view: PreviewUIView) {
        guard let conn = view.previewLayer.connection, conn.isVideoOrientationSupported else { return }
        conn.videoOrientation = CaptureOrientationHelper.avCaptureOrientation(for: interfaceOrientation)
    }
}

final class PreviewUIView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
}
#endif
