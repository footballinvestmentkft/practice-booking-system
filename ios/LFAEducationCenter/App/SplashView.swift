import SwiftUI

// Branded launch / session-restore screen.
//
// Shown while AuthManager.validateSession() is in progress (isValidatingSession = true).
// This is NOT a fake timer-based delay — it disappears the moment session validation
// completes. On slow networks or token-refresh cycles the user sees it longer;
// on a cached valid session it flashes briefly before MainHubView appears.
//
// RootView routing:
//   isValidatingSession = true  → SplashView  (this)
//   isLoggedIn = true           → MainHubView
//   isLoggedIn = false          → LoginView
struct SplashView: View {
    var body: some View {
        ZStack {
            Theme.Color.background
                .ignoresSafeArea()

            VStack(spacing: Theme.Spacing.lg) {
                Spacer()

                BrandLogoView()
                    .frame(maxWidth: 240)
                    .padding(.horizontal, Theme.Spacing.xl)

                Text("LFA Education Center")
                    .font(.title3.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)

                Spacer()

                ProgressView()
                    .scaleEffect(1.2)
                    .padding(.bottom, Theme.Spacing.xl)
            }
        }
    }
}
