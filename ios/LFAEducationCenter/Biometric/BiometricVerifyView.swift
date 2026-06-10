import SwiftUI

// Verify result screen.
//
// Displays one of three outcomes: verified / manual_review_required / rejected.
// face_match_score is never requested, stored, displayed, or logged — not even in debug.
// Only result.result (a classified string) is shown.
struct BiometricVerifyView: View {

    @EnvironmentObject private var authManager: AuthManager
    @StateObject private var vm: BiometricVerifyViewModel

    private let photoFilename: String?
    private let onDismiss:     () -> Void

    init(service: BiometricService, photoFilename: String?, onDismiss: @escaping () -> Void) {
        _vm = StateObject(wrappedValue: BiometricVerifyViewModel(service: service))
        self.photoFilename = photoFilename
        self.onDismiss     = onDismiss
    }

    var body: some View {
        ZStack {
            Color(UIColor.systemBackground).ignoresSafeArea()
            content
            if vm.isLoading { loadingOverlay }
        }
        .navigationTitle("Ellenőrzés")
        .navigationBarTitleDisplayMode(.inline)
        .alert(item: $vm.error) { err in
            Alert(
                title: Text("Hiba"),
                message: Text(err.userFacingMessage),
                dismissButton: .default(Text("OK"), action: onDismiss)
            )
        }
        .onAppear { Task { await vm.verify(photoFilename: photoFilename) } }
    }

    // MARK: — Content

    @ViewBuilder
    private var content: some View {
        if let result = vm.result {
            resultView(for: result.result)
        } else if !vm.isLoading {
            // verify() returned nil without error — treat as loading.
            ProgressView()
        }
    }

    @ViewBuilder
    private func resultView(for outcome: String) -> some View {
        switch outcome {
        case "verified":
            outcomeView(
                icon: "checkmark.circle.fill",
                color: Theme.Color.primary,
                title: "Biometrikus ellenőrzés sikeres.",
                subtitle: nil,
                showRetry: false
            )
        case "manual_review_required":
            outcomeView(
                icon: "clock.fill",
                color: Theme.Color.warning,
                title: "Ellenőrzés kézi felülvizsgálatra vár.",
                subtitle: "Az adminisztrátor hamarosan felülvizsgálja a kérelmet.",
                showRetry: false
            )
        case "rejected":
            outcomeView(
                icon: "xmark.circle.fill",
                color: Theme.Color.error,
                title: "Az ellenőrzés nem sikerült.",
                subtitle: "Kérjük, próbáld meg újra a liveness folyamatot.",
                showRetry: true
            )
        default:
            outcomeView(
                icon: "questionmark.circle.fill",
                color: Theme.Color.muted,
                title: "Ismeretlen eredmény.",
                subtitle: nil,
                showRetry: false
            )
        }
        // face_match_score: not requested, not stored, not displayed — structural guarantee.
    }

    private func outcomeView(
        icon: String,
        color: Color,
        title: String,
        subtitle: String?,
        showRetry: Bool
    ) -> some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: icon)
                .font(.system(size: 72))
                .foregroundColor(color)

            Text(title)
                .font(.system(size: Theme.FontSize.title3, weight: .semibold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)

            if let subtitle = subtitle {
                Text(subtitle)
                    .font(.system(size: Theme.FontSize.body))
                    .foregroundColor(Theme.Color.muted)
                    .multilineTextAlignment(.center)
            }

            if showRetry {
                Button {
                    onDismiss()   // pop back to liveness entry point
                } label: {
                    Text("Újra")
                        .font(.system(size: Theme.FontSize.body, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, Theme.Spacing.sm)
                        .background(Theme.Color.primary)
                        .cornerRadius(Theme.Radius.sm)
                }
                .padding(.horizontal, Theme.Spacing.md)
            }

            Button(action: onDismiss) {
                Text("Bezárás")
                    .font(.system(size: Theme.FontSize.body))
                    .foregroundColor(Theme.Color.muted)
            }
        }
        .padding(Theme.Spacing.lg)
    }

    // MARK: — Helpers

    private var loadingOverlay: some View {
        Color.black.opacity(0.15)
            .ignoresSafeArea()
            .overlay(ProgressView())
    }
}
