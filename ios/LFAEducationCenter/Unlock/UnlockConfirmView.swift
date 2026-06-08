import SwiftUI

// Confirm modal for unlocking LFA Football Player (costs 100 CR).
//
// Presented as fullScreenCover from MainHubView when lfaCardState == .unlockAvailable.
//
// Flow:
//   1. User sees cost / current balance / balance-after summary
//   2. Tap "Confirm Unlock" → UnlockViewModel.performUnlock()
//   3a. Success → brief success view → dashboardVM.reloadAfterUnlock() → auto-dismiss
//   3b. Error   → error message + "Try Again" / "Cancel"
//
// Duplicate tap: blocked by UnlockViewModel.state guard (.idle only).
// 409 already-unlocked: treated as success (idempotent).
struct UnlockConfirmView: View {

    @EnvironmentObject private var authManager: AuthManager
    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @StateObject         private var viewModel  = UnlockViewModel()
    @Environment(\.presentationMode) private var presentationMode

    private let cost = 100

    private var balance: Int { dashboardVM.profile?.creditBalance ?? 0 }

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    heroSection
                    summarySection
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

            Text("Unlock this specialization to start your LFA journey.")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Theme.Spacing.xl)
    }

    // MARK: — Cost / balance summary

    private var summarySection: some View {
        VStack(spacing: 1) {
            summaryRow(label: "Unlock Cost",      value: "\(cost) CR",              valueColor: Theme.Color.error)
            summaryRow(label: "Your Balance",     value: "\(balance) CR",           valueColor: Theme.Color.onSurface)
            summaryRow(label: "Balance After",    value: "\(balance - cost) CR",    valueColor: balance >= cost ? Theme.Color.onSurface : Theme.Color.error)
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
                Task { await viewModel.performUnlock(using: authManager) }
            } label: {
                Text("Confirm Unlock — \(cost) CR")
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
        }
        .frame(maxWidth: .infinity)
        .padding(Theme.Spacing.xl)
        .onAppear {
            // Reload dashboard (sets .unlocking state, then fetches fresh profile+license)
            // then dismiss — short delay so user sees the success tick.
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
