import SwiftUI

// Auth-gated root.
//   isLoggedIn = false → LoginView
//   isLoggedIn = true  → MainHubView (specialization selector hub)
//
// Navigation tree:
//   LoginView → MainHubView → [LFA card tap] → LFASpecTabView (fullScreenCover)
//   LFASpecTabView.Profile → "Back to Hub" → MainHubView
//   LFASpecTabView.Profile → "Sign Out" → AuthManager.logout() → LoginView
struct RootView: View {
    @EnvironmentObject var authManager: AuthManager

    var body: some View {
        if authManager.isLoggedIn {
            MainHubView()
        } else {
            LoginView()
        }
    }
}

// MARK: — Generic placeholder (used by LFASpecTabView for future tabs)

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
