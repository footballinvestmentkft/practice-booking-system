import SwiftUI

// Auth-gated root: shows LoginView until isLoggedIn, then MainTabView.
struct RootView: View {
    @EnvironmentObject var authManager: AuthManager

    var body: some View {
        if authManager.isLoggedIn {
            MainTabView()
        } else {
            LoginView()
        }
    }
}

// MARK: — Main tab navigation

struct MainTabView: View {
    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("Dashboard", systemImage: "house.fill") }

            PlaceholderScreen(title: "Education",
                              subtitle: "Education Center — Phase E",
                              icon: "book.fill")
                .tabItem { Label("Education", systemImage: "book.fill") }

            PlaceholderScreen(title: "Training",
                              subtitle: "Skeleton tracking — Phase F",
                              icon: "stopwatch.fill")
                .tabItem { Label("Training", systemImage: "stopwatch.fill") }

            ProfileTab()
                .tabItem { Label("Profile", systemImage: "person.fill") }
        }
        .accentColor(Theme.Color.primary)
    }
}

// MARK: — Profile tab

private struct ProfileTab: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var dashboardVM: DashboardViewModel

    var body: some View {
        NavigationView {
            VStack(spacing: Theme.Spacing.md) {
                profileHeader
                Spacer()
                signOutButton
            }
            .navigationTitle("Profile")
        }
        .navigationViewStyle(.stack)
    }

    @ViewBuilder
    private var profileHeader: some View {
        if let profile = dashboardVM.profile {
            VStack(spacing: Theme.Spacing.sm) {
                Image(systemName: "person.circle.fill")
                    .font(.system(size: 72))
                    .foregroundColor(Theme.Color.primary)
                    .padding(.top, Theme.Spacing.xl)

                Text(profile.displayName)
                    .font(.title2.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)

                Text(profile.email)
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)

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
        } else {
            VStack(spacing: Theme.Spacing.sm) {
                Image(systemName: "person.fill")
                    .font(.system(size: 52))
                    .foregroundColor(Theme.Color.muted)
                    .padding(.top, Theme.Spacing.xl)

                Text("Profile")
                    .font(.title2.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)

                if case .loading = dashboardVM.loadState {
                    ProgressView()
                        .padding(.top, Theme.Spacing.sm)
                }
            }
        }
    }

    private var signOutButton: some View {
        Button {
            authManager.logout()
        } label: {
            Text("Sign Out")
                .fontWeight(.semibold)
                .frame(maxWidth: .infinity)
                .frame(height: 48)
                .background(Theme.Color.error.opacity(0.12))
                .foregroundColor(Theme.Color.error)
                .cornerRadius(Theme.Radius.sm)
        }
        .padding(.horizontal, Theme.Spacing.xl)
        .padding(.bottom, Theme.Spacing.xl)
    }
}

// MARK: — Generic placeholder for future tabs

struct PlaceholderScreen: View {
    let title:    String
    let subtitle: String
    let icon:     String

    var body: some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: icon)
                .font(.system(size: 52))
                .foregroundColor(Theme.Color.muted)
            Text(title)
                .font(.title2.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)
            Text(subtitle)
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
        }
        .padding(Theme.Spacing.xl)
    }
}
