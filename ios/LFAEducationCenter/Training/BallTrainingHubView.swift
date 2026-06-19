import SwiftUI

// MARK: — BallTrainingHubView
//
// "Segíts tanítani a rendszert" — standalone tab entry point.
// Manages the full session lifecycle: loading → frame display → session complete.
// Navigation: 5th tab in LFASpecTabView.
//
// AuthManager is passed at call time (matching JugglingVideoListViewModel pattern).
// The ViewModel lazily creates BallTrainingAPIClient on first loadQueue(authManager:).

struct BallTrainingHubView: View {

    @EnvironmentObject var authManager: AuthManager
    @StateObject private var vm = BallTrainingHubViewModel()

    var body: some View {
        NavigationView {
            content
                .navigationTitle("Train the AI")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button {
                            Task { await vm.reload(authManager: authManager) }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .disabled(vm.isSubmitting || vm.isFrameLoading)
                        .opacity(isReloadVisible ? 1 : 0)
                    }
                }
        }
        .navigationViewStyle(.stack)
        .task { await vm.loadQueue(authManager: authManager) }
    }

    private var isReloadVisible: Bool {
        switch vm.sessionState {
        case .ready, .empty, .sessionComplete, .error: return true
        default: return false
        }
    }

    @ViewBuilder
    private var content: some View {
        switch vm.sessionState {
        case .idle, .loading:
            loadingView

        case .ready:
            readyView

        case .sessionComplete:
            sessionCompleteView

        case .empty:
            emptyView

        case .unavailable:
            unavailableView

        case .forbidden:
            forbiddenView

        case .error(let msg):
            errorView(message: msg)
        }
    }

    // MARK: — Loading

    private var loadingView: some View {
        VStack(spacing: Theme.Spacing.lg) {
            ProgressView()
                .progressViewStyle(.circular)
                .scaleEffect(1.4)
            Text("Feladatok betöltése…")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Ready (frame display)

    @ViewBuilder
    private var readyView: some View {
        if vm.isFrameLoading {
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                    .progressViewStyle(.circular)
                    .scaleEffect(1.2)
                Text("Kép betöltése…")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let msg = vm.frameErrorMessage {
            frameErrorView(message: msg)
        } else if let item = vm.currentItem, let data = vm.frameData {
            BallTrainingFrameView(vm: vm, item: item, frameData: data)
        } else {
            loadingView
        }
    }

    private func frameErrorView(message: String) -> some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundColor(Theme.Color.warning)
            Text(message)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(Theme.Color.muted)
                .padding(.horizontal, Theme.Spacing.xl)
            Button("Újrapróbál") {
                Task { await vm.fetchCurrentFrame() }
            }
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .background(Theme.Color.primary.opacity(0.12))
            .foregroundColor(Theme.Color.primary)
            .cornerRadius(Theme.Radius.sm)
            .padding(.horizontal, Theme.Spacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Session complete

    private var sessionCompleteView: some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 56))
                .foregroundColor(Theme.Color.primary)

            Text("Köszönjük!")
                .font(.title2.weight(.bold))
                .foregroundColor(Theme.Color.onSurface)

            Text("Sikeresen segítettél a modell tanításában.\nHolnap új feladatok várnak.")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(Theme.Color.muted)
                .padding(.horizontal, Theme.Spacing.xl)

            Button("Újabb feladatok") {
                Task { await vm.reload(authManager: authManager) }
            }
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .background(Theme.Color.primary.opacity(0.12))
            .foregroundColor(Theme.Color.primary)
            .cornerRadius(Theme.Radius.sm)
            .padding(.horizontal, Theme.Spacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Empty queue

    private var emptyView: some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "tray.fill")
                .font(.system(size: 48))
                .foregroundColor(Theme.Color.muted)
            Text("Jelenleg nincs elérhető feladat")
                .font(.headline)
                .foregroundColor(Theme.Color.onSurface)
            Text("Nézz vissza hamarosan – a rendszer folyamatosan gyűjt új kereteket.")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(Theme.Color.muted)
                .padding(.horizontal, Theme.Spacing.xl)
            Button("Frissítés") {
                Task { await vm.reload(authManager: authManager) }
            }
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .background(Theme.Color.primary.opacity(0.12))
            .foregroundColor(Theme.Color.primary)
            .cornerRadius(Theme.Radius.sm)
            .padding(.horizontal, Theme.Spacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Unavailable (feature flag off)

    private var unavailableView: some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: "lock.fill")
                .font(.system(size: 44))
                .foregroundColor(Theme.Color.muted)
            Text("A funkció jelenleg nem elérhető")
                .font(.headline)
                .foregroundColor(Theme.Color.onSurface)
            Text("A labdadetektálás értékelő módja átmenetileg le van tiltva.")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(Theme.Color.muted)
                .padding(.horizontal, Theme.Spacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Forbidden (not in allowlist)

    private var forbiddenView: some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: "person.fill.xmark")
                .font(.system(size: 44))
                .foregroundColor(Theme.Color.muted)
            Text("Hozzáférés szükséges")
                .font(.headline)
                .foregroundColor(Theme.Color.onSurface)
            Text("A labdadetektálás értékelőhöz belső hozzáférés szükséges.")
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(Theme.Color.muted)
                .padding(.horizontal, Theme.Spacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: — Generic error

    private func errorView(message: String) -> some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 44))
                .foregroundColor(Theme.Color.error)
            Text(message)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundColor(Theme.Color.muted)
                .padding(.horizontal, Theme.Spacing.xl)
            Button("Újrapróbál") {
                Task { await vm.reload(authManager: authManager) }
            }
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .background(Theme.Color.error.opacity(0.12))
            .foregroundColor(Theme.Color.error)
            .cornerRadius(Theme.Radius.sm)
            .padding(.horizontal, Theme.Spacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
