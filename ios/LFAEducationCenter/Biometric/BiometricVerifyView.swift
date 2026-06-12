import SwiftUI

// Verify result screen.
//
// Navigation contract (two separate closures — white screen fix):
//   onPopToLiveness  — resets navigateToVerify = false in BiometricLivenessView;
//                      used for transient errors (referenceNotFound, non-fatal errors).
//                      Does NOT close the biometric fullScreenCover.
//   onDismiss        — closes the entire biometric fullScreenCover;
//                      only called on explicit "Bezárás" user action.
//
// Auto-retry: BiometricVerifyViewModel silently retries referenceNotFound
// up to kMaxRetries times. The alert only appears after retries are exhausted.
//
// face_match_score: never requested, stored, displayed, or logged — not even in debug.
struct BiometricVerifyView: View {

    @EnvironmentObject private var authManager: AuthManager
    @StateObject private var vm: BiometricVerifyViewModel

    private let photoFilename:   String?
    private let onPopToLiveness: () -> Void   // pop NavigationLink only — flow stays open
    private let onDismiss:       () -> Void   // close entire biometric flow

    init(
        service:         BiometricService,
        photoFilename:   String?,
        onPopToLiveness: @escaping () -> Void,
        onDismiss:       @escaping () -> Void
    ) {
        _vm              = StateObject(wrappedValue: BiometricVerifyViewModel(service: service))
        self.photoFilename   = photoFilename
        self.onPopToLiveness = onPopToLiveness
        self.onDismiss       = onDismiss
    }

    var body: some View {
        ZStack {
            Color(UIColor.systemBackground).ignoresSafeArea()
            content
            if vm.isLoading { loadingOverlay }
        }
        .navigationTitle("Verification")
        .navigationBarTitleDisplayMode(.inline)
        .alert(item: $vm.error) { err in
            errorAlert(for: err)
        }
        .onAppear { Task { await vm.verify(photoFilename: photoFilename) } }
    }

    // MARK: — Alert

    private func errorAlert(for err: BiometricClientError) -> Alert {
        switch err {
        case .referenceNotFound:
            // Retries exhausted — offer pop-back or full dismiss.
            return Alert(
                title: Text("Reference photo unavailable"),
                message: Text("Face data generation did not complete in time. Please retry the liveness flow."),
                primaryButton: .default(Text("Go back")) {
                    // Reset NavigationLink first, then no dismiss — flow stays open.
                    onPopToLiveness()
                },
                secondaryButton: .cancel(Text("Close")) {
                    // Reset NavigationLink, then close entire flow.
                    onPopToLiveness()
                    onDismiss()
                }
            )
        default:
            // Non-fatal errors: pop back to liveness without closing the full flow.
            return Alert(
                title: Text("Error"),
                message: Text(err.userFacingMessage),
                dismissButton: .default(Text("Go back")) {
                    onPopToLiveness()
                }
            )
        }
    }

    // MARK: — Content

    @ViewBuilder
    private var content: some View {
        if let result = vm.result {
            resultView(for: result.result)
        } else if !vm.isLoading {
            // Loading overlay handles the in-progress state.
            // This branch is only reached if loading has stopped without a result
            // (i.e., error is set) — the alert handles display in that case.
            // Show a minimal spinner so content is never fully blank.
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
                title: "Biometric verification successful.",
                subtitle: nil,
                showRetry: false
            )
        case "manual_review_required":
            outcomeView(
                icon: "clock.fill",
                color: Theme.Color.warning,
                title: "Verification pending manual review.",
                subtitle: "An administrator will review your request shortly.",
                showRetry: false
            )
        case "rejected":
            outcomeView(
                icon: "xmark.circle.fill",
                color: Theme.Color.error,
                title: "Verification failed.",
                subtitle: "Please retry the liveness flow.",
                showRetry: true
            )
        default:
            outcomeView(
                icon: "questionmark.circle.fill",
                color: Theme.Color.muted,
                title: "Unknown result.",
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
                    // Pop back to liveness — flow stays open for another attempt.
                    onPopToLiveness()
                } label: {
                    Text("Retry")
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
                Text("Close")
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
