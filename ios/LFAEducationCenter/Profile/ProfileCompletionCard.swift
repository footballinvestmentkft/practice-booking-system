import SwiftUI

// MARK: — Completion score

// Client-side profile completion score — no extra API call.
// All data comes from already-loaded DashboardViewModel.
//
// Weights (total = 100):
//   Position & Physical   30  ← lfaLicense.onboardingCompleted
//   Academy ID            10  ← profile.lfaAcademyId != nil
//   Profile Photo         15  ← profile.profilePhotoUrl != nil
//   Baseline Self-Rating  15  ← selfRatingCompleted (motivation_scores["self_assessment_completed"])
//   Goals & Motivation     0  ← Coming Next (separate future module)
//   Skill Assessment      20  ← Coming Next (R3E)
//   Mood Photos           10  ← Coming Next (R3D)
struct ProfileCompletionScore {
    let positionPhysical:  Int
    let academyID:         Int
    let profilePhoto:      Int
    let baselineSelfRating: Int  // R3F — live from selfRatingCompleted
    let skillAssessment:   Int   // R3E — always 0 until implemented
    let moodPhotos:        Int   // R3D — always 0 until implemented

    var total: Int {
        positionPhysical + academyID + profilePhoto
        + baselineSelfRating + skillAssessment + moodPhotos
    }
    var fraction: Double { min(Double(total) / 100.0, 1.0) }

    static func compute(profile: UserProfile,
                        lfaLicense: LFAPlayerLicense?,
                        selfRatingCompleted: Bool = false) -> ProfileCompletionScore {
        ProfileCompletionScore(
            positionPhysical:  lfaLicense?.onboardingCompleted == true ? 30 : 0,
            academyID:         profile.lfaAcademyId != nil ? 10 : 0,
            profilePhoto:      profile.profilePhotoUrl != nil ? 15 : 0,
            baselineSelfRating: selfRatingCompleted ? 15 : 0,
            skillAssessment:   0,
            moodPhotos:        0
        )
    }
}

// MARK: — Row state

enum CompletionRowState {
    case complete
    case incomplete(action: (() -> Void)?)
    case upcoming(String)   // module tag: "R3F", "R3E", "R3D"
    case locked
}

// MARK: — Dashboard compact card

// Shown below LFALicenseCard when onboarding is complete.
// Displays overall %, progress bar, 2 top missing items, and a CTA.
struct ProfileCompletionCard: View {
    let score: ProfileCompletionScore
    let onViewAll: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            headerRow
            ProgressView(value: score.fraction)
                .accentColor(Theme.Color.primary)
            motivationText
            missingItems
            viewAllButton
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }

    private var headerRow: some View {
        HStack {
            Image(systemName: "person.text.rectangle.fill")
                .foregroundColor(Theme.Color.primary)
            Text("Your Player Profile")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)
            Spacer()
            Text("\(score.total)%")
                .font(.subheadline.weight(.bold))
                .foregroundColor(Theme.Color.primary)
        }
    }

    private var motivationText: some View {
        Text("Your profile is ready. Add more details to unlock richer cards and insights.")
            .font(.caption)
            .foregroundColor(Theme.Color.muted)
            .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private var missingItems: some View {
        if score.baselineSelfRating == 0 {
            missingPill(icon: "chart.bar.doc.horizontal",
                        label: "Baseline Self-Rating")
        }
        if score.skillAssessment == 0 {
            missingPill(icon: "slider.horizontal.3",
                        label: "Skill Assessment")
        }
    }

    private func missingPill(icon: String, label: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.system(size: 11))
                .foregroundColor(Theme.Color.muted)
            Text(label)
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
            Spacer()
            Text("Coming Next")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.secondary)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Theme.Color.secondary.opacity(0.10))
                .cornerRadius(4)
        }
    }

    private var viewAllButton: some View {
        Button(action: onViewAll) {
            HStack {
                Spacer()
                Text("Complete Profile")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.primary)
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.Color.primary)
                Spacer()
            }
        }
        .padding(.top, 2)
    }
}

// MARK: — ProfileView full section

// Full checklist shown in ProfileView.
// Academy ID, Profile Photo, and Baseline Self-Rating rows are tappable.
struct ProfileCompletionSection: View {
    let score:                   ProfileCompletionScore
    let onAcademyIDTap:          () -> Void
    let onPhotoTap:              () -> Void
    let onBaselineSelfRatingTap: () -> Void

    private static let successGreen = Color(red: 0.18, green: 0.80, blue: 0.44)

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            sectionHeader
            ProgressView(value: score.fraction)
                .accentColor(Theme.Color.primary)
            motivationText
            checklistRows
        }
    }

    private var sectionHeader: some View {
        HStack {
            Text("PROFILE COMPLETION")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.8)
            Spacer()
            Text("\(score.total)%")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.primary)
        }
    }

    private var motivationText: some View {
        Text("Your player profile is ready. Add more details to unlock richer cards and insights.")
            .font(.caption)
            .foregroundColor(Theme.Color.muted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.bottom, 4)
    }

    private var checklistRows: some View {
        VStack(spacing: 1) {
            row(icon: "figure.run",
                title: "Position & Physical",
                subtitle: "Primary position, height, weight, foot",
                state: score.positionPhysical > 0 ? .complete : .incomplete(action: nil))

            row(icon: "creditcard.fill",
                title: "Academy ID",
                subtitle: "Your LFA Football Player ID",
                state: score.academyID > 0 ? .complete : .incomplete(action: onAcademyIDTap),
                tapAction: onAcademyIDTap)

            row(icon: "camera.fill",
                title: "Profile Photo",
                subtitle: "Your player profile photo",
                state: score.profilePhoto > 0 ? .complete : .incomplete(action: onPhotoTap),
                tapAction: score.profilePhoto > 0 ? onPhotoTap : nil)

            row(icon: "chart.bar.doc.horizontal",
                title: "Baseline Self-Rating",
                subtitle: "Rate your 44 football skills",
                state: score.baselineSelfRating > 0 ? .complete : .incomplete(action: onBaselineSelfRatingTap),
                tapAction: score.baselineSelfRating > 0 ? onBaselineSelfRatingTap : nil)

            row(icon: "target",
                title: "Goals & Motivation",
                subtitle: "Your football goals and drive",
                state: .upcoming("R3G"))

            row(icon: "slider.horizontal.3",
                title: "Skill Assessment",
                subtitle: "Validated skill measurement",
                state: .upcoming("R3E"))

            row(icon: "photo.on.rectangle",
                title: "Mood Photos",
                subtitle: "Match-ready expressions for your card",
                state: score.moodPhotos > 0 ? .complete : .upcoming("R3D"))
        }
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }

    @ViewBuilder
    private func row(icon: String, title: String,
                     subtitle: String, state: CompletionRowState,
                     tapAction: (() -> Void)? = nil) -> some View {
        let content = rowContent(icon: icon, title: title, subtitle: subtitle, state: state)
        switch state {
        case .complete where tapAction != nil:
            Button(action: tapAction!) { content }
        case .incomplete(let action) where action != nil:
            Button(action: action!) { content }
        case .upcoming, .locked:
            content.opacity(0.65)
        default:
            content
        }
    }

    private func rowContent(icon: String, title: String,
                             subtitle: String, state: CompletionRowState) -> some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: icon)
                .font(.system(size: 15))
                .foregroundColor(iconColor(state))
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(titleColor(state))
                Text(subtitle)
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
            }
            Spacer()
            trailingView(state)
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private func trailingView(_ state: CompletionRowState) -> some View {
        switch state {
        case .complete:
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 16))
                .foregroundColor(Self.successGreen)
        case .incomplete(let action):
            if action != nil {
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.Color.muted)
            }
        case .upcoming:
            Text("Coming Next")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.secondary)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Theme.Color.secondary.opacity(0.10))
                .cornerRadius(4)
        case .locked:
            Image(systemName: "lock")
                .font(.system(size: 13))
                .foregroundColor(Theme.Color.muted)
        }
    }

    private func iconColor(_ state: CompletionRowState) -> Color {
        switch state {
        case .complete:             return Self.successGreen
        case .incomplete:           return Theme.Color.primary
        default:                    return Theme.Color.muted
        }
    }

    private func titleColor(_ state: CompletionRowState) -> Color {
        switch state {
        case .complete, .incomplete: return Theme.Color.onSurface
        default:                     return Theme.Color.muted
        }
    }
}
