import SwiftUI

// Disclosure + consent flow — two sequential steps gated by backend flags.
//
// Spike bypass: when kBiometricAutoCaptureSpikeEnabled == true, the entire backend
// disclosure/consent flow is skipped and SpikeLivenessView is shown directly.
// This prevents a backend 503 (feature flag off in dev) from blocking the spike.
// kBiometricAutoCaptureSpikeEnabled is false by default; false in all Release builds.
//
// Disclosure text is a dev/test placeholder (v1.0).
// Legal review of the final disclosure text is required before production use.
struct BiometricDisclosureView: View {

    @EnvironmentObject private var authManager: AuthManager
    @StateObject private var vm: BiometricDisclosureViewModel

    @State private var showLiveness = false
    private let onDismiss: () -> Void

    init(service: BiometricService, onDismiss: @escaping () -> Void) {
        _vm = StateObject(wrappedValue: BiometricDisclosureViewModel(service: service))
        self.onDismiss = onDismiss
    }

    var body: some View {
        // Spike short-circuit: bypass backend disclosure/consent entirely.
        // The spike is local-only — no backend disclosure state is needed.
        if kBiometricAutoCaptureSpikeEnabled {
            SpikeLivenessView(onDismiss: onDismiss)
                .environmentObject(authManager)
        } else {
            productionFlow
        }
    }

    // MARK: — Production flow (kBiometricAutoCaptureSpikeEnabled == false)

    private var productionFlow: some View {
        NavigationView {
            ZStack {
                Color(UIColor.systemBackground).ignoresSafeArea()
                content
                if vm.isLoading { loadingOverlay }
            }
            .navigationTitle(navigationTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { closeButton }
            .alert(item: $vm.error) { err in
                Alert(
                    title: Text("Error"),
                    message: Text(err.userFacingMessage),
                    dismissButton: .default(Text("OK"))
                )
            }
            .fullScreenCover(isPresented: $showLiveness) {
                BiometricLivenessView(
                    service: BiometricService(auth: authManager),
                    onDismiss: { showLiveness = false }
                )
                .environmentObject(authManager)
            }
        }
        .onAppear {
            vm.onReadyForLiveness = { showLiveness = true }
            Task { await vm.load() }
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Phase rendering

    @ViewBuilder
    private var content: some View {
        switch vm.phase {
        case .loading:
            ProgressView()
        case .unavailable(let message):
            unavailableView(message: message)
        case .disclosure:
            disclosureStep
        case .consent:
            consentStep
        case .done:
            doneView
        }
    }

    // MARK: — Disclosure step

    private var disclosureStep: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                Text("Biometric Disclosure")
                    .font(.system(size: Theme.FontSize.title3, weight: .bold))
                    .foregroundColor(Theme.Color.onSurface)

                // DEV/TEST PLACEHOLDER — not legally approved production text.
                // Legal review of the disclosure is required before production.
                Text(disclosureBodyText)
                    .font(.system(size: Theme.FontSize.body))
                    .foregroundColor(Theme.Color.onSurface)
                    .lineSpacing(4)

                Text("Version: \(kBiometricDisclosureVersion)")
                    .font(.system(size: Theme.FontSize.caption))
                    .foregroundColor(Theme.Color.muted)

                actionButton(
                    label: "I Accept",
                    color: Theme.Color.primary
                ) { await vm.acceptDisclosure() }

                Button(action: onDismiss) {
                    Text("Decline / Later")
                        .font(.system(size: Theme.FontSize.body))
                        .foregroundColor(Theme.Color.muted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, Theme.Spacing.sm)
                }
            }
            .padding(Theme.Spacing.md)
        }
    }

    // DEV/TEST placeholder disclosure text — not legally reviewed.
    private var disclosureBodyText: String {
        """
        LFA Football Academy uses biometric face recognition to verify \
        attendance and authenticate Academy ID.

        Face recognition data (face embedding vector) is stored encrypted. \
        This data is used exclusively for internal identification purposes \
        and qualifies as a special category of personal data under GDPR Article 9.

        Consent can be withdrawn at any time via Profile → Biometric settings. \
        Upon withdrawal, face embedding data will be deleted within 30 days.

        This disclosure applies to the developer/test environment. \
        The final legal text is subject to separate approval.
        """
    }

    // MARK: — Consent step

    private var consentStep: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                Text("Biometric Consent")
                    .font(.system(size: Theme.FontSize.title3, weight: .bold))
                    .foregroundColor(Theme.Color.onSurface)

                Text(
                    "You have accepted the disclosure. To activate biometric verification, " +
                    "explicit consent under GDPR Art. 9(2)(a) is also required."
                )
                .font(.system(size: Theme.FontSize.body))
                .foregroundColor(Theme.Color.onSurface)
                .lineSpacing(4)

                Text("Consent version: \(kBiometricConsentVersion)")
                    .font(.system(size: Theme.FontSize.caption))
                    .foregroundColor(Theme.Color.muted)

                actionButton(
                    label: "I Consent",
                    color: Theme.Color.primary
                ) { await vm.grantConsent() }

                Button(action: onDismiss) {
                    Text("Cancel")
                        .font(.system(size: Theme.FontSize.body))
                        .foregroundColor(Theme.Color.muted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, Theme.Spacing.sm)
                }
            }
            .padding(Theme.Spacing.md)
        }
    }

    // MARK: — Done

    private var doneView: some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 64))
                .foregroundColor(Theme.Color.primary)
            Text("Disclosure and consent accepted.")
                .font(.system(size: Theme.FontSize.title3, weight: .semibold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)

            actionButton(label: "Continue to liveness test", color: Theme.Color.primary) {
                showLiveness = true
            }
            Button(action: onDismiss) {
                Text("Close")
                    .font(.system(size: Theme.FontSize.body))
                    .foregroundColor(Theme.Color.muted)
            }
        }
        .padding(Theme.Spacing.md)
    }

    // MARK: — Unavailable

    private func unavailableView(message: String) -> some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 48))
                .foregroundColor(Theme.Color.warning)
            Text(message)
                .font(.system(size: Theme.FontSize.body))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)
            Button(action: onDismiss) {
                Text("Close")
                    .font(.system(size: Theme.FontSize.body))
                    .foregroundColor(Theme.Color.muted)
            }
        }
        .padding(Theme.Spacing.md)
    }

    // MARK: — Helpers

    private var navigationTitle: String {
        switch vm.phase {
        case .loading, .unavailable: return "Biometric"
        case .disclosure:            return "Disclosure"
        case .consent:               return "Consent"
        case .done:                  return "Done"
        }
    }

    private var closeButton: some ToolbarContent {
        ToolbarItem(placement: .navigationBarLeading) {
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(Theme.Color.onSurface)
            }
        }
    }

    private var loadingOverlay: some View {
        Color.black.opacity(0.15)
            .ignoresSafeArea()
            .overlay(ProgressView())
    }

    @ViewBuilder
    private func actionButton(
        label: String,
        color: Color,
        action: @escaping () async -> Void
    ) -> some View {
        Button {
            Task { await action() }
        } label: {
            Text(label)
                .font(.system(size: Theme.FontSize.body, weight: .semibold))
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, Theme.Spacing.sm)
                .background(color)
                .cornerRadius(Theme.Radius.sm)
        }
        .disabled(vm.isLoading)
    }
}

// BiometricClientError conformance for .alert(item:)
extension BiometricClientError: Identifiable {
    var id: String { userFacingMessage }
}
