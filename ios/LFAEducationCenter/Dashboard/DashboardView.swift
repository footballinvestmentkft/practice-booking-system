import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var dashboardVM: DashboardViewModel

    var body: some View {
        NavigationView {
            Group {
                switch dashboardVM.loadState {
                case .idle, .loading:
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

                LicenseSection(licenses: dashboardVM.licenses)

                // /api/v1/licenses/dashboard — schema not yet confirmed.
                // Deferred to Phase E; shown as a placeholder card here.
                PlaceholderInfoCard(
                    title: "Training Stats",
                    subtitle: "Full stats coming in Phase E",
                    icon: "chart.bar.fill"
                )
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
    let balance: Double

    var body: some View {
        HStack {
            Image(systemName: "creditcard.fill")
                .foregroundColor(Theme.Color.secondary)
            Text("Credits")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)
            Spacer()
            Text("\(Int(balance)) CR")
                .font(.headline.monospacedDigit())
                .foregroundColor(Theme.Color.secondary)
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — Licenses section

private struct LicenseSection: View {
    let licenses: [UserLicense]

    private var activeLicenses: [UserLicense] {
        licenses.filter { $0.isActive == true }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("My Licenses (\(activeLicenses.count) active)")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)

            if licenses.isEmpty {
                Text("No licenses found.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .padding(.vertical, Theme.Spacing.sm)
            } else {
                ForEach(licenses) { license in
                    LicenseRow(license: license)
                    if license.id != licenses.last?.id {
                        Divider()
                    }
                }
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

private struct LicenseRow: View {
    let license: UserLicense

    var body: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: license.isActive == true ? "checkmark.circle.fill" : "circle")
                .foregroundColor(license.isActive == true ? Theme.Color.primary : Theme.Color.muted)

            VStack(alignment: .leading, spacing: 2) {
                Text(license.displayName)
                    .font(.subheadline.weight(.medium))
                    .foregroundColor(Theme.Color.onSurface)

                HStack(spacing: Theme.Spacing.xs) {
                    if license.isOnboarded == true {
                        Label("Onboarded", systemImage: "person.badge.plus")
                            .font(.caption)
                            .foregroundColor(Theme.Color.muted)
                    }
                    if let level = license.level {
                        Label("Level \(level)", systemImage: "star.fill")
                            .font(.caption)
                            .foregroundColor(Theme.Color.muted)
                    }
                }
            }
            Spacer()
        }
        .padding(.vertical, Theme.Spacing.xs)
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
