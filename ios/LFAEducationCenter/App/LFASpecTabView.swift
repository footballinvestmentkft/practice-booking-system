import SwiftUI

// LFA Football Player specialization — scoped tab navigator.
// Presented as a fullScreenCover from MainHubView.
// "Back to Hub" (via dismiss) returns the user to MainHubView.
// Logout from the Profile tab triggers AuthManager → RootView resets to LoginView.
struct LFASpecTabView: View {
    @Environment(\.presentationMode) private var presentationMode
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var dashboardVM: DashboardViewModel
    @EnvironmentObject var educationVM: EducationViewModel

    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("Dashboard", systemImage: "house.fill") }

            EducationView()
                .tabItem { Label("Education", systemImage: "book.fill") }

            PlaceholderScreen(title: "Training",
                              subtitle: "Skeleton tracking — Phase F",
                              icon: "stopwatch.fill")
                .tabItem { Label("Training", systemImage: "stopwatch.fill") }

            LFAProfileTab(onReturnToHub: { presentationMode.wrappedValue.dismiss() })
                .tabItem { Label("Profile", systemImage: "person.fill") }
        }
        .accentColor(Theme.Color.primary)
    }
}

// MARK: — LFA Profile tab

private struct LFAProfileTab: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var dashboardVM: DashboardViewModel
    let onReturnToHub: () -> Void

    var body: some View {
        NavigationView {
            VStack(spacing: Theme.Spacing.md) {
                profileHeader
                Spacer()
                returnToHubButton
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
                    ProgressView().padding(.top, Theme.Spacing.sm)
                }
            }
        }
    }

    private var returnToHubButton: some View {
        Button {
            onReturnToHub()
        } label: {
            Label("Back to Hub", systemImage: "house.fill")
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 48)
                .background(Theme.Color.primary.opacity(0.12))
                .foregroundColor(Theme.Color.primary)
                .cornerRadius(Theme.Radius.sm)
        }
        .padding(.horizontal, Theme.Spacing.xl)
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
