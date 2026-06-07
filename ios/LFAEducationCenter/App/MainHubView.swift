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
    @State private var isShowingLFASpec = false

    private let gridColumns = [GridItem(.adaptive(minimum: 150), spacing: Theme.Spacing.sm)]

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: Theme.Spacing.md) {
                    greetingSection
                    creditSection
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
                            action:   lfaState == .active ? { isShowingLFASpec = true } : nil
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
        .fullScreenCover(isPresented: $isShowingLFASpec) {
            LFASpecTabView()
        }
    }

    // MARK: — LFA card helpers

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
        case .insufficientCredits: return "100 CR to unlock"
        case .unlockAvailable:
            let cr = dashboardVM.profile?.creditBalance ?? 0
            return "\(cr) CR · Ready"
        case .setupPending:        return "Onboarding pending"
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

    // Full opacity + primary title colour for actionable/prominent states.
    var isProminent: Bool {
        switch self {
        case .active, .unlockAvailable: return true
        default:                        return false
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
