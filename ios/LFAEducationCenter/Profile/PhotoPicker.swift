import PhotosUI
import SwiftUI
import UIKit

// UIViewControllerRepresentable wrapping PHPickerViewController.
// Single-image selection, no crop, no camera — MVP scope.
//
// IMPORTANT — dismiss strategy:
//   Do NOT call picker.dismiss(animated:) from the delegate. Doing so triggers
//   a UIKit dismissal that, in iOS 14/15, cascades through the SwiftUI modal
//   stack and also closes the parent .fullScreenCover (MoodPhotosView).
//   Instead, the caller sets its @State binding to nil/false inside the
//   onPickerFinished callback, which causes SwiftUI to dismiss the .sheet
//   through its own system — no cascade occurs.
//
//   onPickerFinished(UIImage) — user selected a photo
//   onPickerFinished(nil)     — user cancelled (no selection)
//
// Named ProfilePhotoPicker to avoid collision with the private PhotoPicker in RegisterView.
struct ProfilePhotoPicker: UIViewControllerRepresentable {

    let onPickerFinished: (UIImage?) -> Void

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var config            = PHPickerConfiguration(photoLibrary: .shared())
        config.filter         = .images
        config.selectionLimit = 1
        let picker            = PHPickerViewController(configuration: config)
        picker.delegate       = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: PHPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onPickerFinished: onPickerFinished) }

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        private let onPickerFinished: (UIImage?) -> Void

        init(onPickerFinished: @escaping (UIImage?) -> Void) {
            self.onPickerFinished = onPickerFinished
        }

        func picker(_ picker: PHPickerViewController,
                    didFinishPicking results: [PHPickerResult]) {
            // Do NOT call picker.dismiss(animated:) — the caller dismisses via SwiftUI
            // binding to avoid the iOS 14/15 modal-cascade bug.
            guard let result = results.first,
                  result.itemProvider.canLoadObject(ofClass: UIImage.self) else {
                DispatchQueue.main.async { self.onPickerFinished(nil) }  // cancelled
                return
            }
            result.itemProvider.loadObject(ofClass: UIImage.self) { [weak self] object, _ in
                DispatchQueue.main.async { self?.onPickerFinished(object as? UIImage) }
            }
        }
    }
}

// UIImagePickerController wrapper for camera capture — mood photos only.
//
// IMPORTANT — same dismiss rule as ProfilePhotoPicker:
//   Do NOT call picker.dismiss(animated:) from the delegate. Doing so triggers
//   the same UIKit modal-cascade bug that closes the parent MoodPhotosView.
//   The caller sets showingCamera = false, which drives SwiftUI-native sheet close.
struct CameraImagePicker: UIViewControllerRepresentable {

    let onPickerFinished: (UIImage?) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker           = UIImagePickerController()
        picker.sourceType    = .camera
        picker.allowsEditing = false
        picker.delegate      = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onPickerFinished: onPickerFinished) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        private let onPickerFinished: (UIImage?) -> Void

        init(onPickerFinished: @escaping (UIImage?) -> Void) {
            self.onPickerFinished = onPickerFinished
        }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            // Do NOT call picker.dismiss(animated:) — caller closes via SwiftUI binding.
            let image = info[.originalImage] as? UIImage
            DispatchQueue.main.async { self.onPickerFinished(image) }
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            // Do NOT call picker.dismiss(animated:)
            DispatchQueue.main.async { self.onPickerFinished(nil) }
        }
    }
}
