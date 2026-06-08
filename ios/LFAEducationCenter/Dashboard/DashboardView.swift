import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var dashboardVM: DashboardViewModel

    var body: some View {
        NavigationView {
            Group {
                switch dashboardVM.loadState {
                case .idle, .loading, .unlocking:
                    loadingView
                case .loaded:
                    if let profile = dashboardVM.profile {
                        loadedView(profile: profile)
                    } else {
                        loadingView
                    }
                case .error(let message):
                    errorView(message: message)
                }
            }
            .navigationTitle("Dashboard")
        }
        .navigationViewStyle(.stack)
        .onAppear {
            Task { await dashboardVM.load(using: authManager) }
        }
    }

    // MARK: — Loading

    private var loadingView: some View {
        VStack {
            Spacer()
            ProgressView("Loading…")
                .foregroundColor(Theme.Color.muted)
            Spacer()
        }
    }

    // MARK: — Error

    @ViewBuilder
    private func errorView(message: String) -> some View {
        VStack(spacing: Theme.Spacing.lg) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 48))
                .foregroundColor(Theme.Color.warning)
            Text(message)
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Spacing.xl)
            Button("Retry") {
                Task { await dashboardVM.reload(using: authManager) }
            }
            .font(.body.weight(.semibold))
            .foregroundColor(Theme.Color.primary)
            Spacer()
        }
    }

    // MARK: — Loaded

    @ViewBuilder
    private func loadedView(profile: UserProfile) -> some View {
        ScrollView {
            VStack(spacing: Theme.Spacing.md) {
                ProfileCard(profile: profile)

                if let balance = profile.creditBalance {
                    CreditCard(balance: balance)
                }

                // LFA Player license — from /api/v1/lfa-player/licenses/me
                LFALicenseCard(license: dashboardVM.lfaLicense)

                // Overall progress from /api/v1/licenses/dashboard — shown if decode succeeds
                if let progress = dashboardVM.dashboard?.overallProgress {
                    DashboardProgressCard(progress: progress)
                } else {
                    PlaceholderInfoCard(
                        title: "Training Stats",
                        subtitle: "Full stats coming in Phase E",
                        icon: "chart.bar.fill"
                    )
                }
            }
            .padding(Theme.Spacing.md)
        }
    }
}

// MARK: — Profile card

private struct ProfileCard: View {
    let profile: UserProfile

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            Image(systemName: "person.circle.fill")
                .font(.system(size: 44))
                .foregroundColor(Theme.Color.primary)

            VStack(alignment: .leading, spacing: 4) {
                Text(profile.displayName)
                    .font(.headline)
                    .foregroundColor(Theme.Color.onSurface)
                Text(profile.email)
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                if let pos = profile.position {
                    Text(pos)
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                }
                if let xp = profile.xpBalance, xp > 0 {
                    Text("\(xp) XP")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(Theme.Color.secondary)
                }
            }

            Spacer()

            if let role = profile.role {
                Text(role.capitalized)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, Theme.Spacing.sm)
                    .padding(.vertical, 4)
                    .background(Theme.Color.primary.opacity(0.15))
                    .foregroundColor(Theme.Color.primary)
                    .cornerRadius(Theme.Radius.sm)
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — Credits card

private struct CreditCard: View {
    let balance: Int  // Int matches backend credit_balance type

    var body: some View {
        HStack {
            Image(systemName: "creditcard.fill")
                .foregroundColor(Theme.Color.secondary)
            Text("Credits")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)
            Spacer()
            Text("\(balance) CR")
                .font(.headline.monospacedDigit())
                .foregroundColor(Theme.Color.secondary)
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — LFA Player license card

private struct LFALicenseCard: View {
    let license: LFAPlayerLicense?

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("LFA Player License")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)

            if let license = license {
                HStack(spacing: Theme.Spacing.sm) {
                    Image(systemName: license.isActive ? "checkmark.seal.fill" : "seal")
                        .foregroundColor(license.isActive ? Theme.Color.primary : Theme.Color.muted)

                    VStack(alignment: .leading, spacing: 2) {
                        Text("Level \(license.currentLevel)")
                            .font(.subheadline.weight(.medium))
                            .foregroundColor(Theme.Color.onSurface)
                        Text(license.isActive ? "Active" : "Inactive")
                            .font(.caption)
                            .foregroundColor(license.isActive ? Theme.Color.primary : Theme.Color.muted)
                    }

                    Spacer()

                    if license.onboardingCompleted {
                        Label("Onboarded", systemImage: "person.badge.plus")
                            .font(.caption)
                            .foregroundColor(Theme.Color.muted)
                    }
                }
            } else {
                Text("No active LFA Player license.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .padding(.vertical, Theme.Spacing.xs)
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — Dashboard overall progress card

private struct DashboardProgressCard: View {
    let progress: LicenseDashboard.OverallProgress

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            HStack {
                Image(systemName: "chart.bar.fill")
                    .foregroundColor(Theme.Color.primary)
                Text("Overall Progress")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                Spacer()
                Text(String(format: "%.1f%%", progress.percentage))
                    .font(.headline.monospacedDigit())
                    .foregroundColor(Theme.Color.primary)
            }
            ProgressView(value: min(progress.percentage / 100.0, 1.0))
                .accentColor(Theme.Color.primary)
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — Placeholder info card

private struct PlaceholderInfoCard: View {
    let title:    String
    let subtitle: String
    let icon:     String

    var body: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: icon)
                .foregroundColor(Theme.Color.muted)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.muted)
                Text(subtitle)
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
            }
            Spacer()
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}
