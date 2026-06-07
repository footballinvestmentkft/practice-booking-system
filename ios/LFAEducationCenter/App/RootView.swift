import SwiftUI

// Auth-gated root with splash screen during session restore.
//
// State machine:
//   isValidatingSession = true               → SplashView (shown on every cold launch)
//   isLoggedIn = true  AND justRegistered    → WelcomeSuccessView (new registration only)
//   isLoggedIn = true  AND !justRegistered   → MainHubView
//   isLoggedIn = false                       → LoginView
//
// justRegistered is set only by AuthManager.register() — never by login() or
// validateSession() — so returning users and session-restore flows are unaffected.
struct RootView: View {
    @EnvironmentObject var authManager: AuthManager

    var body: some View {
        if authManager.isValidatingSession {
            SplashView()
        } else if authManager.isLoggedIn && authManager.justRegistered {
            WelcomeSuccessView()
        } else if authManager.isLoggedIn {
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
