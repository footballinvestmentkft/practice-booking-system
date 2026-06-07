import SwiftUI

// Central hub — mirrors hub_specializations.html.
// Login → MainHubView (this) → LFA card tap → LFASpecTabView (fullScreenCover).
// All other specializations are "Coming Soon" for now.
struct MainHubView: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var dashboardVM: DashboardViewModel
    @State private var isShowingLFASpec = false

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: Theme.Spacing.md) {
                    greetingSection
                    creditSection
                    Divider()

                    // LFA Football Player — primary active specialization
                    SpecCard(
                        icon:     "⚽",
                        title:    "LFA Football Player",
                        subtitle: "Skill development · Tournaments · Cards",
                        status:   .active
                    ) {
                        isShowingLFASpec = true
                    }

                    // Coming-soon specializations
                    SpecCard(icon: "🏮", title: "GānCuju Player",
                             subtitle: "8-level martial arts progression",
                             status: .comingSoon, action: nil)
                    SpecCard(icon: "📋", title: "LFA Coach",
                             subtitle: "Coaching licence progression",
                             status: .comingSoon, action: nil)
                    SpecCard(icon: "💼", title: "Internship",
                             subtitle: "IT Career Program",
                             status: .comingSoon, action: nil)

                    Divider().padding(.vertical, Theme.Spacing.xs)
                    signOutButton
                }
                .padding(Theme.Spacing.md)
            }
            .navigationTitle("LFA Education Center")
        }
        .navigationViewStyle(.stack)
        .onAppear {
            // Pre-load profile so credit balance appears without entering the spec.
            Task { await dashboardVM.load(using: authManager) }
        }
        .fullScreenCover(isPresented: $isShowingLFASpec) {
            LFASpecTabView()
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

// MARK: — Specialization card

private enum SpecStatus { case active, comingSoon }

private struct SpecCard: View {
    let icon:     String
    let title:    String
    let subtitle: String
    let status:   SpecStatus
    let action:   (() -> Void)?

    var body: some View {
        Button { action?() } label: {
            HStack(spacing: Theme.Spacing.md) {
                Text(icon)
                    .font(.system(size: 36))
                    .frame(width: 48)

                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(status == .active ? Theme.Color.onSurface : Theme.Color.muted)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                        .lineLimit(1)
                }

                Spacer()
                statusBadge
            }
            .padding(Theme.Spacing.md)
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)
            .opacity(status == .active ? 1.0 : 0.55)
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
        case .comingSoon:
            Text("Coming Soon")
                .font(.caption.weight(.semibold))
                .foregroundColor(Theme.Color.muted)
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(Theme.Color.muted.opacity(0.15))
                .cornerRadius(4)
        }
    }
}
