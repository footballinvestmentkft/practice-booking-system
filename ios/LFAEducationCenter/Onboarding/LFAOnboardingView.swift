import SwiftUI

// Minimum onboarding for LFA Football Player (R3C).
// Presented as fullScreenCover when lfaCardState == .setupPending.
//
// Collects:
//   - Primary + secondary positions (pitch map)
//   - Height (cm), weight (kg), preferred foot, foot dominance
//
// Skill payload: 44 keys × 50 (neutral / not self-assessed — R3E adds sliders).
//
// On success: dashboardVM.reload() → lfaLicense.onboardingCompleted = true
//             → lfaCardState → .active → LFASpecTabView becomes accessible.
struct LFAOnboardingView: View {

    @EnvironmentObject private var authManager: AuthManager
    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @StateObject         private var viewModel  = LFAOnboardingViewModel()
    @Environment(\.presentationMode) private var presentationMode

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    positionSection
                    sectionDivider
                    physiqueSection
                    sectionDivider
                    submitSection
                    Spacer(minLength: Theme.Spacing.xl)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.top, Theme.Spacing.md)
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("LFA Player Setup")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if !isSubmitting {
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

    // MARK: — Position section

    private var positionSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            sectionHeader("⚽", title: "YOUR POSITION")

            Text("Tap to set primary. Tap again to add secondary (max 3).")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
                .padding(.bottom, 2)

            PitchSelectorView(
                primaryPosition: $viewModel.primaryPosition,
                secondaryPositions: $viewModel.secondaryPositions
            )

            if let primary = viewModel.primaryPosition {
                positionSummary(primary: primary)
            } else {
                Text("No position selected yet.")
                    .font(.caption)
                    .foregroundColor(Theme.Color.error.opacity(0.8))
                    .padding(.top, 2)
            }
        }
    }

    private func positionSummary(primary: FootballPosition) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                positionBadge(primary, isPrimary: true)
                ForEach(viewModel.secondaryPositions) { pos in
                    positionBadge(pos, isPrimary: false)
                }
            }
            .padding(.vertical, 2)
        }
    }

    private func positionBadge(_ position: FootballPosition, isPrimary: Bool) -> some View {
        VStack(spacing: 2) {
            Text(position.short)
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(.white)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(isPrimary ? Theme.Color.primary : Theme.Color.secondary)
                .cornerRadius(4)
            Text(isPrimary ? "Primary" : "Secondary")
                .font(.system(size: 9))
                .foregroundColor(Theme.Color.muted)
        }
    }

    // MARK: — Physique section

    private var physiqueSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            sectionHeader("📐", title: "PHYSICAL PROFILE")

            // Height
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Height")
                        .font(.subheadline)
                        .foregroundColor(Theme.Color.onSurface)
                    Spacer()
                    Text("\(Int(viewModel.heightCm.rounded())) cm")
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(Theme.Color.primary)
                }
                Slider(value: $viewModel.heightCm, in: 120...230, step: 1)
                    .accentColor(Theme.Color.primary)
                HStack {
                    Text("120 cm").font(.caption2).foregroundColor(Theme.Color.muted)
                    Spacer()
                    Text("230 cm").font(.caption2).foregroundColor(Theme.Color.muted)
                }
            }

            // Weight
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Weight")
                        .font(.subheadline)
                        .foregroundColor(Theme.Color.onSurface)
                    Spacer()
                    Text("\(Int(viewModel.weightKg.rounded())) kg")
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(Theme.Color.primary)
                }
                Slider(value: $viewModel.weightKg, in: 35...160, step: 1)
                    .accentColor(Theme.Color.primary)
                HStack {
                    Text("35 kg").font(.caption2).foregroundColor(Theme.Color.muted)
                    Spacer()
                    Text("160 kg").font(.caption2).foregroundColor(Theme.Color.muted)
                }
            }

            // Preferred foot
            VStack(alignment: .leading, spacing: 6) {
                Text("Preferred Foot")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.onSurface)
                Picker("Preferred Foot", selection: $viewModel.preferredFoot) {
                    Text("Left").tag("left")
                    Text("Right").tag("right")
                    Text("Both").tag("both")
                }
                .pickerStyle(.segmented)
            }

            // Foot dominance
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Foot Dominance")
                        .font(.subheadline)
                        .foregroundColor(Theme.Color.onSurface)
                    Spacer()
                    Text("\(Int(viewModel.footDominance.rounded()))%")
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(Theme.Color.muted)
                }
                Slider(value: $viewModel.footDominance, in: 0...100, step: 1)
                    .accentColor(Theme.Color.secondary)
                HStack {
                    Text("← Left").font(.caption2).foregroundColor(Theme.Color.muted)
                    Spacer()
                    Text("Right →").font(.caption2).foregroundColor(Theme.Color.muted)
                }
            }
        }
    }

    // MARK: — Submit section

    @ViewBuilder
    private var submitSection: some View {
        switch viewModel.submitState {
        case .idle:
            Button {
                Task { await viewModel.submit(using: authManager) }
            } label: {
                Text("Complete Setup")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(viewModel.canSubmit
                                ? Theme.Color.primary
                                : Theme.Color.muted.opacity(0.25))
                    .foregroundColor(viewModel.canSubmit ? .white : Theme.Color.muted)
                    .cornerRadius(Theme.Radius.sm)
            }
            .disabled(!viewModel.canSubmit)

        case .loading:
            HStack(spacing: Theme.Spacing.sm) {
                ProgressView().scaleEffect(0.9)
                Text("Saving profile…")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 50)

        case .success:
            HStack(spacing: Theme.Spacing.sm) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 22))
                    .foregroundColor(Color(red: 0.18, green: 0.80, blue: 0.44))
                Text("Profile saved! Opening LFA Player…")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: 50)
            .onAppear {
                Task {
                    try? await Task.sleep(nanoseconds: 800_000_000)
                    await dashboardVM.reload(using: authManager)
                    presentationMode.wrappedValue.dismiss()
                }
            }

        case .error(let message):
            VStack(spacing: Theme.Spacing.sm) {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(Theme.Color.error)
                    Text(message)
                        .font(.caption)
                        .foregroundColor(Theme.Color.onSurface)
                        .multilineTextAlignment(.leading)
                }

                Button {
                    viewModel.reset()
                } label: {
                    Text("Try Again")
                        .font(.body.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .frame(height: 44)
                        .background(Theme.Color.primary)
                        .foregroundColor(.white)
                        .cornerRadius(Theme.Radius.sm)
                }
            }
        }
    }

    // MARK: — Helpers

    private var isSubmitting: Bool {
        if case .loading = viewModel.submitState { return true }
        if case .success = viewModel.submitState { return true }
        return false
    }

    private var sectionDivider: some View {
        Divider().padding(.vertical, Theme.Spacing.md)
    }

    private func sectionHeader(_ emoji: String, title: String) -> some View {
        HStack(spacing: 6) {
            Text(emoji)
            Text(title)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.8)
        }
    }
}
