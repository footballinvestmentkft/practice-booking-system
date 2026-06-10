import SwiftUI

// Disclosure + consent flow — two sequential steps gated by backend flags.
//
// Disclosure text is a dev/test placeholder (v1.0).
// Legal review of the final Hungarian disclosure text is required before production use.
// The disclosureVersion sent to the backend is "v1.0" (kBiometricDisclosureVersion).
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
                    title: Text("Hiba"),
                    message: Text(err.userFacingMessage),
                    dismissButton: .default(Text("OK"))
                )
            }
            .fullScreenCover(isPresented: $showLiveness) {
                BiometricLivenessView(
                    service: makeService(),
                    onDismiss: { showLiveness = false }
                )
                .environmentObject(authManager)
            }
        }
        .onAppear {
            vm.onReadyForLiveness = { showLiveness = true }
            Task { await vm.load() }
        }
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
                Text("Biometrikus Tájékoztató")
                    .font(.system(size: Theme.FontSize.title3, weight: .bold))
                    .foregroundColor(Theme.Color.onSurface)

                // DEV/TEST PLACEHOLDER — not legally approved production text.
                // Legal review of the Hungarian disclosure is required before production.
                Text(disclosureBodyText)
                    .font(.system(size: Theme.FontSize.body))
                    .foregroundColor(Theme.Color.onSurface)
                    .lineSpacing(4)

                Text("Verzió: \(kBiometricDisclosureVersion)")
                    .font(.system(size: Theme.FontSize.caption))
                    .foregroundColor(Theme.Color.muted)

                actionButton(
                    label: "Elfogadom",
                    color: Theme.Color.primary
                ) { await vm.acceptDisclosure() }

                Button(action: onDismiss) {
                    Text("Elutasítom / Később")
                        .font(.system(size: Theme.FontSize.body))
                        .foregroundColor(Theme.Color.muted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, Theme.Spacing.sm)
                }
            }
            .padding(Theme.Spacing.md)
        }
    }

    // DEV/TEST placeholder disclosure text.
    // This is NOT the legally reviewed final text.
    // Legal sign-off is required before this text can be used in production.
    private var disclosureBodyText: String {
        """
        Az LFA Football Academy biometrikus arcfelismerési rendszert alkalmaz az \
        edzéslátogatás és az Academy ID azonosítás ellenőrzéséhez.

        Az arcfelismerési adatokat (arc-embedding vektort) titkosítva tároljuk. \
        Ezek az adatok kizárólag belső azonosítási célra kerülnek felhasználásra, \
        és a GDPR 9. cikke szerinti különleges kategóriájú személyes adatnak minősülnek.

        A hozzájárulás bármikor visszavonható a Profil → Biometrikus beállítások menüben. \
        Visszavonás esetén az arc-embedding adatot 30 napon belül töröljük.

        Ez a tájékoztató fejlesztői/tesztkörnyezetre vonatkozik. \
        A végleges jogi szöveg külön jóváhagyás tárgyát képezi.
        """
    }

    // MARK: — Consent step

    private var consentStep: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                Text("Biometrikus Hozzájárulás")
                    .font(.system(size: Theme.FontSize.title3, weight: .bold))
                    .foregroundColor(Theme.Color.onSurface)

                Text(
                    "A tájékoztatót elfogadtad. A biometrikus ellenőrzés aktiválásához " +
                    "szükséges az explicit GDPR Art. 9(2)(a) szerinti hozzájárulás megadása is."
                )
                .font(.system(size: Theme.FontSize.body))
                .foregroundColor(Theme.Color.onSurface)
                .lineSpacing(4)

                Text("Hozzájárulás verziója: \(kBiometricConsentVersion)")
                    .font(.system(size: Theme.FontSize.caption))
                    .foregroundColor(Theme.Color.muted)

                actionButton(
                    label: "Hozzájárulok",
                    color: Theme.Color.primary
                ) { await vm.grantConsent() }

                Button(action: onDismiss) {
                    Text("Mégsem")
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
            Text("Tájékoztató és hozzájárulás elfogadva.")
                .font(.system(size: Theme.FontSize.title3, weight: .semibold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)

            actionButton(label: "Folytatás a liveness teszthez", color: Theme.Color.primary) {
                showLiveness = true
            }
            Button(action: onDismiss) {
                Text("Bezárás")
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
                Text("Bezárás")
                    .font(.system(size: Theme.FontSize.body))
                    .foregroundColor(Theme.Color.muted)
            }
        }
        .padding(Theme.Spacing.md)
    }

    // MARK: — Helpers

    private var navigationTitle: String {
        switch vm.phase {
        case .loading, .unavailable: return "Biometrikus"
        case .disclosure:            return "Tájékoztató"
        case .consent:               return "Hozzájárulás"
        case .done:                  return "Kész"
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

    private func makeService() -> BiometricService {
        BiometricService(auth: authManager)
    }
}

// BiometricClientError conformance for .alert(item:)
extension BiometricClientError: Identifiable {
    var id: String { userFacingMessage }
}
