import SwiftUI

// MARK: — JugglingVideoUploadView
//
// Sheet content for B3 (video upload list integration). Presented from
// JugglingVideoListView via .sheet(isPresented:onDismiss:).
//
// Picker lifecycle: the PHPicker is shown via a nested .fullScreenCover bound
// to viewModel.state == .selecting (iOS 14: only one .sheet reliably presents
// at a time, so the picker uses fullScreenCover instead of a second sheet).
//
// Dismiss lifecycle: the "Mégse" toolbar button and swipe-to-dismiss both
// route through the parent's .sheet(onDismiss:), which calls
// viewModel.cancel() if an upload is active — no orphan upload tasks.

struct JugglingVideoUploadView: View {
    @ObservedObject var viewModel: JugglingVideoUploadViewModel
    @Binding var isPresented: Bool

    var body: some View {
        NavigationView {
            VStack(spacing: Theme.Spacing.lg) {
                Spacer()
                content
                Spacer()
            }
            .padding(Theme.Spacing.xl)
            .navigationTitle("Videó feltöltése")
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Mégse") {
                        isPresented = false
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
        .fullScreenCover(isPresented: pickerBinding) {
            JugglingVideoPHPicker(
                onPick: { url, mimeType in viewModel.pickerDidSelect(tempURL: url, mimeType: mimeType) },
                onCancel: { viewModel.pickerCancelled() }
            )
        }
    }

    // Bridges viewModel.state == .selecting to the fullScreenCover presentation.
    // Dismissal is driven exclusively by viewModel.state: pickerDidSelect/
    // pickerCancelled (called from JugglingVideoPHPicker's onPick/onCancel)
    // move state away from .selecting, which flips `get` to false and lets
    // SwiftUI dismiss the cover. The setter is intentionally a no-op — it only
    // exists because Binding requires one, and must NOT call pickerCancelled()
    // here: SwiftUI invokes it as part of its own reconciliation, which can
    // fire before the picker's async loadFileRepresentation completion
    // delivers the real selection (see B3-DIAG investigation).
    private var pickerBinding: Binding<Bool> {
        Binding(
            get: { viewModel.state == .selecting },
            set: { newValue in
                #if DEBUG
                print("[B3-DIAG][View] pickerBinding.set(\(newValue)) — currentState=\(viewModel.state) (no-op)")
                #endif
            }
        )
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .idle:
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "video.badge.plus")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.primary)
                Text("Válassz videót a galériából a feltöltéshez.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.lg)
                Button(action: { viewModel.startPicker() }) {
                    Text("Videó kiválasztása")
                        .font(.body.weight(.semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, Theme.Spacing.lg)
                        .padding(.vertical, Theme.Spacing.sm)
                        .background(Theme.Color.primary)
                        .cornerRadius(Theme.Radius.md)
                }
            }

        case .selecting:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                Text("Videó kiválasztása…")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }

        case .preparing, .exporting, .uploading:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                Text("Feltöltés…")
                    .font(.body.weight(.medium))
                    .foregroundColor(Theme.Color.onSurface)
            }

        case .completing:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                Text("Feldolgozás indítása…")
                    .font(.body.weight(.medium))
                    .foregroundColor(Theme.Color.onSurface)
            }

        case .success:
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.primary)
                Text("Feltöltés sikeres")
                    .font(.body.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
            }

        case .failure(let err):
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.error)
                Text(err.errorDescription ?? "Hiba történt a feltöltés során.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.lg)
                Button {
                    viewModel.retry()
                    viewModel.startPicker()
                } label: {
                    Text("Újrapróbálás")
                        .font(.body.weight(.semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, Theme.Spacing.lg)
                        .padding(.vertical, Theme.Spacing.sm)
                        .background(Theme.Color.primary)
                        .cornerRadius(Theme.Radius.md)
                }
            }
        }
    }
}
