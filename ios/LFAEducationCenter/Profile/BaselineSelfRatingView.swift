import SwiftUI

// Baseline Self-Rating — 44-skill self-assessment for LFA Football Player.
//
// Collects a self-rating (0–99) for each of the 44 football skills,
// grouped into 4 categories matching the backend SKILL_CATEGORIES order.
// POSTs to POST /api/v1/lfa-player/self-assessment.
//
// Rules:
//   - current_level stays at 60.0 (SYSTEM_BASELINE) — not touched
//   - system_baseline / baseline / OVR / onboarding_completed — not touched
//   - Only football_skills[key].self_assessment is updated per skill
//   - motivation_scores["self_assessment_completed"] is set to true on success
//
// On success: onSuccess() → dashboardVM.reload() → ProfileCompletionScore +15.
struct BaselineSelfRatingView: View {

    @EnvironmentObject private var authManager: AuthManager
    @Environment(\.presentationMode) private var presentationMode

    let onSuccess: () -> Void

    @StateObject private var vm = BaselineSelfRatingViewModel()

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                    headerSection
                    ForEach(SkillConfig.categories, id: \.nameEn) { category in
                        categorySection(category)
                    }
                    saveButton
                    Spacer(minLength: Theme.Spacing.xl)
                }
                .padding(Theme.Spacing.md)
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("Baseline Self-Rating")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        presentationMode.wrappedValue.dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.onSurface)
                    }
                    .disabled(isSaving)
                }
            }
        }
        .navigationViewStyle(.stack)
        .onChange(of: successTriggered) { triggered in
            if triggered {
                onSuccess()
                presentationMode.wrappedValue.dismiss()
            }
        }
    }

    // MARK: — Header

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.xs) {
            Text("Rate yourself honestly. Your real skill level starts from 60 and will evolve based on your training and assessments.")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: — Category section

    private func categorySection(_ category: SkillCategory) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            sectionHeader(category.nameEn)
            VStack(spacing: 0) {
                ForEach(category.skills, id: \.key) { skill in
                    skillRow(skill: skill)
                    if skill.key != category.skills.last?.key {
                        Divider()
                            .padding(.leading, Theme.Spacing.md)
                    }
                }
            }
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)
        }
    }

    private func skillRow(skill: SkillDefinition) -> some View {
        HStack(spacing: Theme.Spacing.sm) {
            Text(skill.nameEn)
                .font(.subheadline)
                .foregroundColor(Theme.Color.onSurface)
                .frame(width: 130, alignment: .leading)
                .minimumScaleFactor(0.8)
                .lineLimit(1)
            Slider(
                value: Binding(
                    get: { vm.ratings[skill.key] ?? 60.0 },
                    set: { vm.ratings[skill.key] = $0 }
                ),
                in: 0...99,
                step: 1
            )
            .accentColor(Theme.Color.primary)
            .disabled(isSaving)
            Text("\(Int((vm.ratings[skill.key] ?? 60.0).rounded()))")
                .font(.system(size: 14, weight: .bold, design: .monospaced))
                .foregroundColor(Theme.Color.primary)
                .frame(width: 28, alignment: .trailing)
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, 9)
    }

    // MARK: — Save button

    @ViewBuilder
    private var saveButton: some View {
        switch vm.state {
        case .idle:
            primaryButton(title: "Save Self-Rating") {
                Task { await vm.save(using: authManager) }
            }

        case .saving:
            HStack {
                Spacer()
                ProgressView()
                    .padding(.vertical, 12)
                Spacer()
            }
            .frame(maxWidth: .infinity)
            .background(Theme.Color.primary.opacity(0.08))
            .cornerRadius(Theme.Radius.sm)

        case .success:
            HStack(spacing: 8) {
                Spacer()
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(successGreen)
                Text("Saved!")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(successGreen)
                Spacer()
            }
            .padding(.vertical, 12)
            .background(successGreen.opacity(0.10))
            .cornerRadius(Theme.Radius.sm)

        case .error(let message):
            VStack(spacing: Theme.Spacing.sm) {
                Text(message)
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.error)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                primaryButton(title: "Try Again") {
                    vm.reset()
                }
            }
        }
    }

    // MARK: — Helpers

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(Theme.Color.muted)
            .kerning(0.8)
    }

    private func primaryButton(title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 48)
                .background(Theme.Color.primary)
                .foregroundColor(.white)
                .cornerRadius(Theme.Radius.sm)
        }
    }

    private let successGreen = Color(red: 0.18, green: 0.80, blue: 0.44)

    private var isSaving: Bool {
        if case .saving = vm.state { return true }
        return false
    }

    private var successTriggered: Bool {
        if case .success = vm.state { return true }
        return false
    }
}
