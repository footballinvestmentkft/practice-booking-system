import SwiftUI

// Central hub — mirrors hub_specializations.html.
// Login → MainHubView (this) → LFA card tap → LFASpecTabView (fullScreenCover).
// LFASpecTabView is only accessible from the .active card state.
//
// Specialization grid: 2×2, ordered by minimum age (5+ / 5+ / 14+ / 18+).
// .adaptive(minimum: 150) gives 2 columns on normal iPhones and collapses to 1
// on very narrow screens (iOS 14-compatible, no dynamicTypeSize API needed).
struct MainHubView: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var dashboardVM: DashboardViewModel
    @State private var isShowingLFASpec           = false
    @State private var isShowingAcademyID         = false
    @State private var isShowingCredits           = false
    @State private var isShowingProfile           = false
    @State private var isShowingUnlockConfirm     = false
    @State private var isShowingOnboarding        = false
    @State private var isShowingCompletionScreen  = false
    // Session-level: shown when profile is 80/80 but licence is not active.
    // Not persisted — resets on app restart.
    @State private var showLicenceWarningBanner   = false

    private let gridColumns = [GridItem(.adaptive(minimum: 150), spacing: Theme.Spacing.sm)]

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: Theme.Spacing.md) {
                    greetingSection
                    creditSection

                    // Shown only when the user reached 80/80 but their licence
                    // is not active (expired, inactive, or setup pending).
                    // Session-level: resets on app restart.
                    if showLicenceWarningBanner {
                        licenceWarningBanner
                    }

                    Divider()

                    // 2×2 specialization grid — ascending minimum age order.
                    let lfaState = dashboardVM.lfaCardState
                    LazyVGrid(columns: gridColumns, spacing: Theme.Spacing.sm) {

                        // Row 1 — age 5+
                        SpecCard(
                            icon:     "⚽",
                            title:    "LFA Football Player",
                            subtitle: lfaSubtitle(for: lfaState),
                            ageLabel: "5+",
                            status:   lfaSpecStatus(for: lfaState),
                            // .active → open LFASpecTabView
                            // .insufficientCredits → open CreditsView (R3A)
                            action:   lfaCardAction(for: lfaState)
                        )
                        SpecCard(
                            icon:     "🏮",
                            title:    "GānCuju Player",
                            subtitle: "8-level martial arts",
                            ageLabel: "5+",
                            status:   .comingSoon,
                            action:   nil
                        )

                        // Row 2 — age 14+ / 18+
                        SpecCard(
                            icon:     "📋",
                            title:    "LFA Coach",
                            subtitle: "Coaching licence",
                            ageLabel: "14+",
                            status:   .comingSoon,
                            action:   nil
                        )
                        SpecCard(
                            icon:     "💼",
                            title:    "Internship",
                            subtitle: "IT Career Program",
                            ageLabel: "18+",
                            status:   .comingSoon,
                            action:   nil
                        )
                    }

                    Divider().padding(.vertical, Theme.Spacing.xs)
                    academyIDButton
                    creditsButton
                    profileButton
                    Divider().padding(.vertical, Theme.Spacing.xs)
                    signOutButton
                }
                .padding(Theme.Spacing.md)
            }
            .navigationTitle("LFA Education Center")
        }
        .navigationViewStyle(.stack)
        .onAppear {
            Task { await dashboardVM.load(using: authManager) }
        }
        // Trigger celebration check whenever the dashboard finishes loading.
        // Covers both first launch and re-logins after an interrupted session.
        .onChange(of: dashboardVM.loadState) { state in
            if state == .loaded { checkCompletionCelebration() }
        }
        .fullScreenCover(isPresented: $isShowingCompletionScreen) {
            ProfileCompletionCelebrationView {
                // Sequence: markSeen → dismiss → reload → navigate.
                // markSeen is synchronous and runs before dismiss so the flag
                // is set even if the reload task is cancelled (e.g. network loss).
                if let userId = dashboardVM.profile?.id {
                    CompletionCelebrationStore.markSeen(forUserId: userId)
                }
                isShowingCompletionScreen = false
                Task {
                    await dashboardVM.reload(using: authManager)
                    navigateAfterCompletion()
                }
            }
        }
        .fullScreenCover(isPresented: $isShowingLFASpec) {
            LFASpecTabView()
        }
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
        .fullScreenCover(isPresented: $isShowingUnlockConfirm) {
            UnlockConfirmView()
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
        }
        .fullScreenCover(isPresented: $isShowingOnboarding) {
            LFAOnboardingView()
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
        }
    }

    // MARK: — Profile completion celebration

    // Shows the full-screen celebration exactly once per user, on the load cycle
    // where isAvailableComplete first becomes true.  If the app was killed before
    // the user tapped "Continue", the store flag is still false, so the screen
    // reappears on the next launch.
    private func checkCompletionCelebration() {
        guard let profile  = dashboardVM.profile,
              let userId   = profile.id else { return }

        let score = ProfileCompletionScore.compute(
            profile:             profile,
            lfaLicense:          dashboardVM.lfaLicense,
            selfRatingCompleted: dashboardVM.selfRatingCompleted,
            moodPhotosCompleted: dashboardVM.moodPhotosCompleted
        )

        guard score.isAvailableComplete,
              !CompletionCelebrationStore.hasBeenSeen(forUserId: userId) else { return }

        isShowingCompletionScreen = true
    }

    // Called from the celebration onContinue Task, after reload completes.
    // Runs on the MainActor (Task inherits the calling actor — MainHubView is Main).
    private func navigateAfterCompletion() {
        guard dashboardVM.loadState == .loaded else { return }
        switch dashboardVM.lfaCardState {
        case .active:
            // Delay lets the celebration cover finish its dismiss animation before
            // presenting LFASpecTabView, avoiding an iOS 14 fullScreenCover collision.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                isShowingLFASpec = true
            }
        case .setupPending:
            // Licence exists but is expired, inactive, or onboarding incomplete.
            showLicenceWarningBanner = true
        default:
            // unlockAvailable / insufficientCredits / loading / error —
            // no automatic navigation; hub state handles these normally.
            break
        }
    }

    // Session-level licence warning — shown when profile is 80/80 but
    // lfaCardState == .setupPending (expired or inactive licence).
    private var licenceWarningBanner: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 14))
                .foregroundColor(Theme.Color.warning)
            Text("Profile complete. Renew or activate your LFA licence to access the dashboard.")
                .font(.caption)
                .foregroundColor(Theme.Color.onSurface)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
            Button {
                showLicenceWarningBanner = false
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(Theme.Color.muted)
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.warning.opacity(0.10))
        .cornerRadius(Theme.Radius.sm)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.sm)
                .stroke(Theme.Color.warning.opacity(0.30), lineWidth: 1)
        )
    }

    // MARK: — LFA card helpers

    // Returns the tap action for the LFA Football Player SpecCard.
    // .active              → open LFASpecTabView (specialization dashboard)
    // .unlockAvailable     → open UnlockConfirmView (R3B: confirm + pay 100 CR)
    // .setupPending        → open LFAOnboardingView (R3C: minimum onboarding)
    // .insufficientCredits → open CreditsView (R3A: no more zsákutca)
    // all other states     → nil (tap disabled)
    private func lfaCardAction(for state: LFACardState) -> (() -> Void)? {
        switch state {
        case .active:              return { isShowingLFASpec = true }
        case .unlockAvailable:     return { isShowingUnlockConfirm = true }
        case .setupPending:        return { isShowingOnboarding = true }
        case .insufficientCredits: return { isShowingCredits = true }
        default:                   return nil
        }
    }

    private func lfaSpecStatus(for state: LFACardState) -> SpecStatus {
        switch state {
        case .loading:             return .comingSoon
        case .ageLocked:           return .ageLocked
        case .insufficientCredits: return .insufficientCredits
        case .unlockAvailable:     return .unlockAvailable
        case .setupPending:        return .setupPending
        case .active:              return .active
        }
    }

    private func lfaSubtitle(for state: LFACardState) -> String {
        switch state {
        case .loading:             return "Loading..."
        case .ageLocked:           return "Min. age: 5 years"
        case .insufficientCredits:
            let cr = dashboardVM.profile?.creditBalance ?? 0
            return "\(cr)/100 CR · Tap for credits"
        case .unlockAvailable:
            let cr = dashboardVM.profile?.creditBalance ?? 0
            return "\(cr) CR · Ready"
        case .setupPending:        return "Tap to complete setup"
        case .active:              return "Skills · Cards"
        }
    }

    // MARK: — Sections

    @ViewBuilder
    private var greetingSection: some View {
        if let name = dashboardVM.profile?.displayName {
            HStack {
                Text("Welcome, \(name)")
                    .font(.headline)
                    .foregroundColor(Theme.Color.onSurface)
                Spacer()
            }
        }
    }

    @ViewBuilder
    private var creditSection: some View {
        if let balance = dashboardVM.profile?.creditBalance {
            HStack {
                Spacer()
                Label("\(balance) CR", systemImage: "creditcard.fill")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.secondary)
                    .padding(.horizontal, Theme.Spacing.sm)
                    .padding(.vertical, 6)
                    .background(Theme.Color.secondary.opacity(0.12))
                    .cornerRadius(Theme.Radius.sm)
            }
        }
    }

    // My Academy ID — universal entry (not gated by LFA card state)
    private var academyIDButton: some View {
        hubEntryButton(
            icon: "creditcard.fill",
            label: "My Academy ID",
            action: { isShowingAcademyID = true }
        )
    }

    // Credits — balance overview + history + how to get info
    private var creditsButton: some View {
        hubEntryButton(
            icon: "banknote",
            label: "Credits",
            badge: "\(dashboardVM.profile?.creditBalance ?? 0) CR",
            action: { isShowingCredits = true }
        )
    }

    // Profile — read-only name / email / role / Academy ID link
    private var profileButton: some View {
        hubEntryButton(
            icon: "person.fill",
            label: "Profile",
            action: { isShowingProfile = true }
        )
    }

    private func hubEntryButton(
        icon: String,
        label: String,
        badge: String? = nil,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack {
                Image(systemName: icon)
                    .font(.system(size: 15))
                    .foregroundColor(Theme.Color.secondary)
                    .frame(width: 24)
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
            .padding(.horizontal, Theme.Spacing.md)
            .frame(height: 48)
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.sm)
        }
    }

    private var signOutButton: some View {
        Button {
            authManager.logout()
        } label: {
            Text("Sign Out")
                .fontWeight(.semibold)
                .frame(maxWidth: .infinity)
                .frame(height: 44)
                .background(Theme.Color.error.opacity(0.12))
                .foregroundColor(Theme.Color.error)
                .cornerRadius(Theme.Radius.sm)
        }
    }
}

// MARK: — Specialization card (grid-optimized vertical layout)

private enum SpecStatus {
    case active
    case ageLocked
    case insufficientCredits
    case unlockAvailable
    case setupPending
    case comingSoon

    // Full opacity for actionable/prominent states.
    var isProminent: Bool {
        switch self {
        case .active, .unlockAvailable, .insufficientCredits, .setupPending: return true
        default:                                                               return false
        }
    }
}

private struct SpecCard: View {
    let icon:     String
    let title:    String
    let subtitle: String
    let ageLabel: String
    let status:   SpecStatus
    let action:   (() -> Void)?

    var body: some View {
        Button { action?() } label: {
            VStack(alignment: .center, spacing: Theme.Spacing.sm) {

                // Age pill + icon
                HStack(spacing: 4) {
                    Spacer()
                    Text(ageLabel)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(Theme.Color.muted)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 2)
                        .background(Theme.Color.muted.opacity(0.12))
                        .cornerRadius(4)
                }

                Text(icon)
                    .font(.system(size: 34))

                // Title + subtitle
                VStack(spacing: 3) {
                    Text(title)
                        .font(.caption.weight(.semibold))
                        .foregroundColor(status.isProminent ? Theme.Color.onSurface : Theme.Color.muted)
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)

                    Text(subtitle)
                        .font(.caption2)
                        .foregroundColor(Theme.Color.muted)
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity)

                Spacer(minLength: 4)

                // Status badge — pinned to bottom
                statusBadge
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(Theme.Spacing.sm)
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)
            .opacity(status.isProminent ? 1.0 : 0.55)
        }
        .disabled(action == nil)
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch status {
        case .active:
            Text("Open →")
                .font(.caption.weight(.semibold))
                .foregroundColor(Theme.Color.primary)

        case .unlockAvailable:
            badgeText("Unlock Available", color: Theme.Color.primary)

        case .setupPending:
            badgeText("Setup Pending", color: Theme.Color.secondary)

        case .ageLocked:
            badgeText("Age Locked", color: Theme.Color.error)

        case .insufficientCredits:
            badgeText("Need Credits", color: Theme.Color.muted)

        case .comingSoon:
            badgeText("Coming Soon", color: Theme.Color.muted)
        }
    }

    private func badgeText(_ label: String, color: Color) -> some View {
        Text(label)
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(color.opacity(0.12))
            .cornerRadius(4)
    }
}
