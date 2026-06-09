import SwiftUI

// Confirm modal for unlocking LFA Football Player.
//
// Flow:
//   1. User selects a duration package (1 / 3 / 6 / 12 months)
//   2. Cost, balance-after, and expected expiry update live
//   3. Tap "Confirm Unlock" → UnlockViewModel.performUnlock(durationMonths:)
//   4a. Success → brief success view → dashboardVM.reloadAfterUnlock() → auto-dismiss
//   4b. Error   → error message + "Try Again" / "Cancel"
//
// Pricing (matches backend licence_package.py UNLOCK_DURATION_COST):
//   1 month  → 100 CR
//   3 months → 250 CR
//   6 months → 450 CR
//   12 months→ 800 CR
//
// Duplicate tap: blocked by UnlockViewModel.state guard (.idle only).
// 409 already-unlocked: treated as success (idempotent).

// MARK: — Duration package model

private struct DurationPackage: Identifiable {
    let months:      Int
    let cost:        Int
    let label:       String
    let badgeLabel:  String?   // nil → no badge

    var id: Int { months }

    // Compute the expected expiry date client-side for display only.
    // Actual expiry is set server-side with relativedelta.
    func expectedExpiry(from now: Date = Date()) -> String {
        var components         = DateComponents()
        components.month       = months
        let cal                = Calendar.current
        let expiry             = cal.date(byAdding: components, to: now) ?? now
        let fmt                = DateFormatter()
        fmt.dateStyle          = .medium
        fmt.timeStyle          = .none
        return fmt.string(from: expiry)
    }
}

private let _packages: [DurationPackage] = [
    DurationPackage(months: 1,  cost: 100, label: "1 Month",   badgeLabel: nil),
    DurationPackage(months: 3,  cost: 250, label: "3 Months",  badgeLabel: nil),
    DurationPackage(months: 6,  cost: 450, label: "6 Months",  badgeLabel: nil),
    DurationPackage(months: 12, cost: 800, label: "12 Months", badgeLabel: "Best Value"),
]

// MARK: — View

struct UnlockConfirmView: View {

    @EnvironmentObject private var authManager: AuthManager
    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @StateObject         private var viewModel  = UnlockViewModel()
    @Environment(\.presentationMode) private var presentationMode

    @State private var selectedMonths: Int = 1

    private var balance: Int { dashboardVM.profile?.creditBalance ?? 0 }

    private var selectedPackage: DurationPackage {
        _packages.first { $0.months == selectedMonths } ?? _packages[0]
    }

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    heroSection
                    durationSection
                        .padding(.top, Theme.Spacing.lg)
                    summarySection
                        .padding(.top, Theme.Spacing.md)
                    Spacer(minLength: Theme.Spacing.xl)
                    actionSection
                    Spacer(minLength: Theme.Spacing.xl)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.top, Theme.Spacing.lg)
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("Unlock Specialization")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if !isInProgress {
                        Button {
                            presentationMode.wrappedValue.dismiss()
                        } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundColor(Theme.Color.onSurface)
                        }
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Hero

    private var heroSection: some View {
        VStack(spacing: Theme.Spacing.sm) {
            Text("⚽")
                .font(.system(size: 56))
                .padding(.bottom, 4)

            Text("LFA Football Player")
                .font(.title2.weight(.bold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)

            Text("Choose a licence duration to start your LFA journey.")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Theme.Spacing.lg)
    }

    // MARK: — Duration selector

    private var durationSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("LICENCE DURATION")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.8)

            VStack(spacing: 1) {
                ForEach(_packages) { pkg in
                    durationRow(pkg)
                }
            }
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)
        }
    }

    private func durationRow(_ pkg: DurationPackage) -> some View {
        let isSelected = pkg.months == selectedMonths
        return Button {
            selectedMonths = pkg.months
        } label: {
            HStack {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 18))
                    .foregroundColor(isSelected ? Theme.Color.primary : Theme.Color.muted.opacity(0.4))

                Text(pkg.label)
                    .font(.subheadline.weight(isSelected ? .semibold : .regular))
                    .foregroundColor(Theme.Color.onSurface)

                if let badge = pkg.badgeLabel {
                    Text(badge)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(Theme.Color.secondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Theme.Color.secondary.opacity(0.10))
                        .cornerRadius(4)
                }

                Spacer()

                Text("\(pkg.cost) CR")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(isSelected ? Theme.Color.primary : Theme.Color.onSurface)
            }
            .padding(.horizontal, Theme.Spacing.md)
            .padding(.vertical, 12)
            .background(isSelected ? Theme.Color.primary.opacity(0.06) : Color.clear)
            .animation(.easeInOut(duration: 0.15), value: selectedMonths)
        }
        .buttonStyle(.plain)
    }

    // MARK: — Cost / balance summary

    private var summarySection: some View {
        VStack(spacing: 1) {
            summaryRow(label: "Duration",      value: selectedPackage.label,                   valueColor: Theme.Color.onSurface)
            summaryRow(label: "Unlock Cost",   value: "\(selectedPackage.cost) CR",            valueColor: Theme.Color.error)
            summaryRow(label: "Your Balance",  value: "\(balance) CR",                         valueColor: Theme.Color.onSurface)
            summaryRow(label: "Balance After", value: "\(balance - selectedPackage.cost) CR",  valueColor: balance >= selectedPackage.cost ? Theme.Color.onSurface : Theme.Color.error)
            summaryRow(label: "Expires",       value: selectedPackage.expectedExpiry(),         valueColor: Theme.Color.muted)
        }
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }

    private func summaryRow(label: String, value: String, valueColor: Color) -> some View {
        HStack {
            Text(label)
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
            Spacer()
            Text(value)
                .font(.subheadline.weight(.semibold))
                .foregroundColor(valueColor)
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, 12)
    }

    // MARK: — Action area (state-driven)

    @ViewBuilder
    private var actionSection: some View {
        switch viewModel.state {
        case .idle:
            idleActions

        case .loading:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                    .scaleEffect(1.2)
                Text("Unlocking…")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }
            .frame(maxWidth: .infinity)
            .padding(Theme.Spacing.xl)

        case .success(let newBalance):
            successView(newBalance: newBalance)

        case .error(let message):
            errorView(message: message)
        }
    }

    private var idleActions: some View {
        VStack(spacing: Theme.Spacing.sm) {
            Button {
                Task {
                    await viewModel.performUnlock(
                        using: authManager,
                        durationMonths: selectedMonths
                    )
                }
            } label: {
                Text("Confirm Unlock — \(selectedPackage.cost) CR")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(balance >= selectedPackage.cost ? Theme.Color.primary : Theme.Color.muted.opacity(0.3))
                    .foregroundColor(.white)
                    .cornerRadius(Theme.Radius.sm)
            }
            .disabled(balance < selectedPackage.cost)

            Button {
                presentationMode.wrappedValue.dismiss()
            } label: {
                Text("Cancel")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(Theme.Color.muted.opacity(0.10))
                    .foregroundColor(Theme.Color.muted)
                    .cornerRadius(Theme.Radius.sm)
            }
        }
    }

    private func successView(newBalance: Int) -> some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 52))
                .foregroundColor(Color(red: 0.18, green: 0.80, blue: 0.44))

            Text("Unlocked!")
                .font(.title2.weight(.bold))
                .foregroundColor(Theme.Color.onSurface)

            Text("LFA Football Player is now active.")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)

            Text("Expires \(selectedPackage.expectedExpiry())")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
        }
        .frame(maxWidth: .infinity)
        .padding(Theme.Spacing.xl)
        .onAppear {
            Task {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
                await dashboardVM.reloadAfterUnlock(using: authManager)
                presentationMode.wrappedValue.dismiss()
            }
        }
    }

    private func errorView(message: String) -> some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 36))
                .foregroundColor(Theme.Color.error)

            Text(message)
                .font(.subheadline)
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Spacing.md)

            Button {
                viewModel.reset()
            } label: {
                Text("Try Again")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(Theme.Color.primary)
                    .foregroundColor(.white)
                    .cornerRadius(Theme.Radius.sm)
            }

            Button {
                presentationMode.wrappedValue.dismiss()
            } label: {
                Text("Cancel")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(Theme.Color.muted.opacity(0.10))
                    .foregroundColor(Theme.Color.muted)
                    .cornerRadius(Theme.Radius.sm)
            }
        }
        .padding(Theme.Spacing.xl)
    }

    // MARK: — Helpers

    private var isInProgress: Bool {
        if case .loading = viewModel.state { return true }
        if case .success = viewModel.state { return true }
        return false
    }
}
