import SwiftUI

// Full-screen profile completion celebration.
//
// Shown once per user when ProfileCompletionScore.isAvailableComplete becomes true.
// Persisted via CompletionCelebrationStore — if the app is killed mid-animation,
// the screen re-appears on the next launch.
//
// Not skippable: there is no dismiss button.  The only exit is the "Continue →" CTA
// at the bottom, which calls onContinue().  The caller (MainHubView) is responsible
// for marking the celebration as seen and reloading the dashboard.
//
// Content placeholders are marked // CONTENT-TBD — replace with final design.
struct ProfileCompletionCelebrationView: View {

    // Called when the user taps "Continue →".
    // Caller must: CompletionCelebrationStore.markSeen(forUserId:) + dismiss + reload.
    let onContinue: () -> Void

    @State private var appeared = false

    var body: some View {
        ZStack {
            // Background — CONTENT-TBD: replace with final gradient / visual
            Color(UIColor.systemBackground)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                // ── Hero area — CONTENT-TBD ───────────────────────────────
                VStack(spacing: Theme.Spacing.lg) {

                    // Icon placeholder — CONTENT-TBD: lottie / custom illustration
                    ZStack {
                        Circle()
                            .fill(Theme.Color.primary.opacity(0.10))
                            .frame(width: 120, height: 120)
                        Image(systemName: "checkmark.seal.fill")
                            .font(.system(size: 64))
                            .foregroundColor(Theme.Color.primary)
                    }
                    .scaleEffect(appeared ? 1 : 0.5)
                    .opacity(appeared ? 1 : 0)
                    .animation(.spring(response: 0.5, dampingFraction: 0.65).delay(0.15),
                               value: appeared)

                    Text("Onboarding Complete")
                        .font(.largeTitle.weight(.bold))
                        .foregroundColor(Theme.Color.onSurface)
                        .multilineTextAlignment(.center)
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 16)
                        .animation(.easeOut(duration: 0.4).delay(0.3), value: appeared)

                    Text("Your LFA onboarding is complete. You're ready to continue to your player dashboard.")
                        .font(.body)
                        .foregroundColor(Theme.Color.muted)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, Theme.Spacing.xl)
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 12)
                        .animation(.easeOut(duration: 0.4).delay(0.45), value: appeared)
                }
                // ── End hero area ─────────────────────────────────────────

                Spacer()

                Spacer(minLength: Theme.Spacing.xl)

                // Continue CTA — non-skippable exit
                Button(action: onContinue) {
                    HStack(spacing: 8) {
                        Text("Continue")
                            .font(.body.weight(.semibold))
                        Image(systemName: "arrow.right")
                            .font(.system(size: 14, weight: .semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 52)
                    .background(Theme.Color.primary)
                    .foregroundColor(.white)
                    .cornerRadius(Theme.Radius.md)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.bottom, Theme.Spacing.xl)
                .opacity(appeared ? 1 : 0)
                .animation(.easeOut(duration: 0.3).delay(0.75), value: appeared)
            }
        }
        .onAppear { appeared = true }
        // fullScreenCover has no swipe-to-dismiss on iOS 14+.
        // The only exit is the "Continue →" button — intentionally non-skippable.
    }
}
