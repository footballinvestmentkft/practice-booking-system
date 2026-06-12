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

            JugglingVideoListView()
                .tabItem { Label("Training", systemImage: "video.fill") }

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

    @State private var isShowingAcademyID = false
    @State private var isShowingCredits   = false
    @State private var isShowingProfile   = false

    var body: some View {
        NavigationView {
            VStack(spacing: Theme.Spacing.md) {
                profileHeader
                Spacer()
                academyIDRow
                creditsRow
                profileRow
                returnToHubButton
                signOutButton
            }
            .navigationTitle("Profile")
            .fullScreenCover(isPresented: $isShowingAcademyID) {
                AcademyIDFullScreenView()
                    .environmentObject(authManager)
                    .environmentObject(dashboardVM)
            }
            .fullScreenCover(isPresented: $isShowingCredits) {
                CreditsView()
                    .environmentObject(authManager)
                    .environmentObject(dashboardVM)
            }
            .fullScreenCover(isPresented: $isShowingProfile) {
                ProfileView()
                    .environmentObject(authManager)
                    .environmentObject(dashboardVM)
            }
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

    private func tabRow(icon: String, label: String, badge: String? = nil, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Image(systemName: icon)
                    .font(.system(size: 15))
                    .foregroundColor(Theme.Color.secondary)
                    .frame(width: 28)
                Text(label)
                    .font(.body.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                Spacer()
                if let badge = badge {
                    Text(badge)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(Theme.Color.secondary)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Theme.Color.secondary.opacity(0.12))
                        .cornerRadius(Theme.Radius.sm)
                }
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.Color.muted)
            }
            .padding(.horizontal, Theme.Spacing.xl)
            .frame(height: 48)
        }
    }

    private var academyIDRow: some View {
        tabRow(icon: "creditcard.fill", label: "My Academy ID") { isShowingAcademyID = true }
    }

    private var creditsRow: some View {
        let cr = dashboardVM.profile?.creditBalance ?? 0
        return tabRow(icon: "banknote", label: "Credits", badge: "\(cr) CR") { isShowingCredits = true }
    }

    private var profileRow: some View {
        tabRow(icon: "person.fill", label: "Profile") { isShowingProfile = true }
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
