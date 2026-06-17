import SwiftUI
import PhotosUI

// MARK: — JugglingVideoPHPicker
//
// iOS 14-compatible PHPickerViewController wrapper. Filters to videos only.
// The coordinator copies the selected video to a controlled temp URL and
// determines MIME from the file extension before dispatching onPick on the main queue.
// The caller (onPick closure) receives ownership of the temp file and is responsible
// for cleaning it up — typically by passing it to JugglingVideoUploadViewModel.

struct JugglingVideoPHPicker: UIViewControllerRepresentable {

    let onPick: (URL, String) -> Void
    let onCancel: () -> Void

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var config = PHPickerConfiguration()
        config.filter = .videos
        config.selectionLimit = 1
        let picker = PHPickerViewController(configuration: config)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: PHPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onPick: onPick, onCancel: onCancel)
    }

    // MARK: — Coordinator

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        private let onPick: (URL, String) -> Void
        private let onCancel: () -> Void

        init(onPick: @escaping (URL, String) -> Void, onCancel: @escaping () -> Void) {
            self.onPick = onPick
            self.onCancel = onCancel
        }

        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            #if DEBUG
            print("[B3-DIAG][Picker] selection received — results.count=\(results.count), typeIdentifiers=\(results.first?.itemProvider.registeredTypeIdentifiers ?? [])")
            #endif
            // Do NOT call picker.dismiss() here — the picker is presented via
            // .fullScreenCover(isPresented:), which is driven exclusively by
            // viewModel.state. Calling dismiss() synchronously (before the async
            // loadFileRepresentation completion below) triggers SwiftUI's binding
            // reconciliation early, firing pickerCancelled() while state is still
            // .selecting — and the real selection then arrives too late, silently
            // ignored. Once onPick/onCancel update viewModel.state away from
            // .selecting, the fullScreenCover dismisses itself.
            guard let provider = results.first?.itemProvider,
                  provider.hasItemConformingToTypeIdentifier("public.movie") else {
                #if DEBUG
                print("[B3-DIAG][Picker] no public.movie-conforming provider — calling onCancel")
                #endif
                onCancel()
                return
            }
            provider.loadFileRepresentation(forTypeIdentifier: "public.movie") { [weak self] url, error in
                guard let strongSelf = self else { return }
                #if DEBUG
                print("[B3-DIAG][Picker] loadFileRepresentation completion — sourceURL=\(url?.lastPathComponent ?? "nil"), error=\(error?.localizedDescription ?? "nil")")
                #endif
                guard let sourceURL = url else {
                    #if DEBUG
                    print("[B3-DIAG][Picker] sourceURL is nil — calling onCancel")
                    #endif
                    DispatchQueue.main.async { strongSelf.onCancel() }
                    return
                }
                let ext = sourceURL.pathExtension.lowercased()
                let mimeType: String
                switch ext {
                case "mov":  mimeType = "video/quicktime"
                case "m4v":  mimeType = "video/x-m4v"
                default:     mimeType = "video/mp4"
                }
                #if DEBUG
                print("[B3-DIAG][Picker] resolved ext=\(ext) -> mime=\(mimeType)")
                #endif
                let tempURL = FileManager.default.temporaryDirectory
                    .appendingPathComponent(UUID().uuidString)
                    .appendingPathExtension(ext.isEmpty ? "mp4" : ext)
                do {
                    try FileManager.default.copyItem(at: sourceURL, to: tempURL)
                    #if DEBUG
                    let size = (try? FileManager.default.attributesOfItem(atPath: tempURL.path)[.size] as? Int64) ?? nil
                    print("[B3-DIAG][Picker] temp copy SUCCESS — tempFile=\(tempURL.lastPathComponent), size=\(size ?? -1) bytes")
                    #endif
                    DispatchQueue.main.async {
                        #if DEBUG
                        print("[B3-DIAG][Picker] dispatching onPick(tempFile=\(tempURL.lastPathComponent), mime=\(mimeType))")
                        #endif
                        strongSelf.onPick(tempURL, mimeType)
                    }
                } catch {
                    #if DEBUG
                    print("[B3-DIAG][Picker] temp copy FAILED — error=\(error.localizedDescription) — calling onCancel")
                    #endif
                    DispatchQueue.main.async { strongSelf.onCancel() }
                }
            }
        }
    }
}
