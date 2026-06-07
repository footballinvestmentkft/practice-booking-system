import SwiftUI

// Shown after a successful registration, before MainHubView.
//
// Triggered by: AuthManager.justRegistered = true (set only in register()).
// Dismissed by: "Continue to Hub" → AuthManager.clearJustRegistered() → RootView → MainHubView.
// Never shown on login, session restore, or app restart.
//
// Credit balance: fetched async from /api/v1/users/me. If the fetch fails or
// is still in-flight the credit line is hidden — the screen never enters an error state.
struct WelcomeSuccessView: View {
    @EnvironmentObject private var authManager: AuthManager

    @State private var creditBalance: Int?   = nil
    @State private var appeared:      Bool   = false

    private var firstName: String {
        authManager.registeredUserName ?? "there"
    }

    var body: some View {
        ZStack {
            Theme.Color.background.ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                // Logo
                BrandLogoView()
                    .frame(maxWidth: 160)
                    .padding(.bottom, Theme.Spacing.xl)
                    .opacity(appeared ? 1 : 0)
                    .scaleEffect(appeared ? 1 : 0.88)

                // Welcome headline — name on its own line, width-constrained to force wrap.
                VStack(spacing: 6) {
                    Text("Welcome,")
                        .font(.title2.weight(.bold))
                        .foregroundColor(Theme.Color.onSurface)
                    Text("\(firstName)!")
                        .font(.title2.weight(.bold))
                        .foregroundColor(Theme.Color.primary)
                        .multilineTextAlignment(.center)
                        .lineLimit(nil)
                    Text("Lion Football Academy")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(Theme.Color.muted)
                }
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Spacing.xl)
                .opacity(appeared ? 1 : 0)
                .padding(.bottom, Theme.Spacing.sm)

                // Sub-messages
                VStack(spacing: Theme.Spacing.sm) {
                    Text("Your account has been created successfully.")
                        .font(.subheadline)
                        .foregroundColor(Theme.Color.muted)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)

                    Text("Choose your first specialization and start your journey.")
                        .font(.subheadline)
                        .foregroundColor(Theme.Color.muted)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)

                    // Credit balance — shown only when loaded; hidden on fetch error.
                    if let cr = creditBalance {
                        HStack(spacing: 6) {
                            Image(systemName: "creditcard.fill")
                                .font(.subheadline)
                                .foregroundColor(Theme.Color.secondary)
                            Text("You received \(cr) CR.")
                                .font(.subheadline.weight(.semibold))
                                .foregroundColor(Theme.Color.secondary)
                        }
                        .padding(.horizontal, Theme.Spacing.md)
                        .padding(.vertical, Theme.Spacing.sm)
                        .background(Theme.Color.secondary.opacity(0.10))
                        .cornerRadius(Theme.Radius.sm)
                        .transition(.opacity)
                    }
                }
                .padding(.horizontal, Theme.Spacing.xl)
                .opacity(appeared ? 1 : 0)

                Spacer()

                // Continue button
                Button {
                    authManager.clearJustRegistered()
                } label: {
                    Text("Continue to Hub →")
                        .fontWeight(.semibold)
                        .frame(maxWidth: .infinity)
                        .frame(height: 50)
                        .background(Theme.Color.primary)
                        .foregroundColor(.white)
                        .cornerRadius(Theme.Radius.md)
                }
                .padding(.horizontal, Theme.Spacing.xl)
                .padding(.bottom, Theme.Spacing.xl)
                .opacity(appeared ? 1 : 0)
            }
        }
        .onAppear {
            withAnimation(.easeOut(duration: 0.45)) {
                appeared = true
            }
            Task { await fetchCreditBalance() }
        }
    }

    // Non-fatal: silently ignores any error — credit line stays hidden.
    private func fetchCreditBalance() async {
        let profile: UserProfile? = try? await authManager.authenticatedGet(
            path: "/api/v1/users/me"
        )
        if let balance = profile?.creditBalance {
            withAnimation(.easeIn(duration: 0.3)) {
                creditBalance = balance
            }
        }
    }
}
