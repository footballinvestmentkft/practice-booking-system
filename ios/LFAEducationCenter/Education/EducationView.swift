import SwiftUI

struct EducationView: View {
    @EnvironmentObject var authManager:  AuthManager
    @EnvironmentObject var educationVM:  EducationViewModel

    var body: some View {
        NavigationView {
            Group {
                switch educationVM.loadState {
                case .idle, .loading:
                    loadingView
                case .loaded:
                    loadedView
                case .error(let message):
                    errorView(message: message)
                }
            }
            .navigationTitle("Education Center")
        }
        .navigationViewStyle(.stack)
        .onAppear {
            Task { await educationVM.load(using: authManager) }
        }
    }

    // MARK: — Loading

    private var loadingView: some View {
        VStack {
            Spacer()
            ProgressView("Loading Education Center…")
                .foregroundColor(Theme.Color.muted)
            Spacer()
        }
    }

    // MARK: — Error

    @ViewBuilder
    private func errorView(message: String) -> some View {
        VStack(spacing: Theme.Spacing.lg) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 48))
                .foregroundColor(Theme.Color.warning)
            Text(message)
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Spacing.xl)
            Button("Retry") {
                Task { await educationVM.reload(using: authManager) }
            }
            .font(.body.weight(.semibold))
            .foregroundColor(Theme.Color.primary)
            Spacer()
        }
    }

    // MARK: — Loaded

    @ViewBuilder
    private var loadedView: some View {
        ScrollView {
            VStack(spacing: Theme.Spacing.md) {
                // Current specialization — header card
                if let detail = educationVM.status?.specialization {
                    SpecializationHeaderCard(detail: detail)
                } else {
                    NoSpecializationCard()
                }

                // LFA Player license status
                if let license = educationVM.lfaLicense {
                    LicenseStatusCard(license: license)
                }

                // XP / progress data for active specialization
                if let specCode = educationVM.status?.specialization?.code,
                   let progress = educationVM.progressData[specCode] {
                    ProgressCard(progress: progress)
                }

                // Skill summary (requires active LFA license)
                if let profile = educationVM.skillProfile {
                    SkillSummaryCard(profile: profile)
                } else if educationVM.lfaLicense != nil {
                    // Has license but no skill data yet (pre-tournament / pre-assessment)
                    PlaceholderEduCard(
                        title: "Skill Profile",
                        subtitle: "Participate in your first tournament to unlock skill tracking",
                        icon: "sportscourt.fill"
                    )
                }

                // Available specializations catalog
                if !educationVM.availableSpecs.isEmpty {
                    AvailableSpecializationsSection(specs: educationVM.availableSpecs)
                }
            }
            .padding(Theme.Spacing.md)
        }
    }
}

// MARK: — Specialization header card

private struct SpecializationHeaderCard: View {
    let detail: SpecializationStatus.Detail

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            Text(detail.icon)
                .font(.system(size: 40))

            VStack(alignment: .leading, spacing: 4) {
                Text(detail.name)
                    .font(.headline)
                    .foregroundColor(Theme.Color.onSurface)
                Text(detail.code)
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
            }
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(Theme.Color.primary)
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

private struct NoSpecializationCard: View {
    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            Image(systemName: "questionmark.circle")
                .font(.system(size: 32))
                .foregroundColor(Theme.Color.muted)
            VStack(alignment: .leading, spacing: 4) {
                Text("No Specialization Selected")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                Text("Complete onboarding to choose your track")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
            }
            Spacer()
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — License status card

private struct LicenseStatusCard: View {
    let license: LFAPlayerLicense

    var body: some View {
        HStack(spacing: Theme.Spacing.md) {
            Image(systemName: license.isActive ? "checkmark.seal.fill" : "seal")
                .font(.system(size: 28))
                .foregroundColor(license.isActive ? Theme.Color.primary : Theme.Color.muted)

            VStack(alignment: .leading, spacing: 4) {
                Text("LFA Player License — Level \(license.currentLevel)")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                HStack(spacing: Theme.Spacing.sm) {
                    Text(license.isActive ? "Active" : "Inactive")
                        .font(.caption)
                        .foregroundColor(license.isActive ? Theme.Color.primary : Theme.Color.muted)
                    if license.onboardingCompleted {
                        Text("·")
                            .foregroundColor(Theme.Color.muted)
                        Text("Onboarded")
                            .font(.caption)
                            .foregroundColor(Theme.Color.muted)
                    }
                }
            }
            Spacer()
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — Progress card

private struct ProgressCard: View {
    let progress: SpecializationProgressData

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("Progress")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)

            HStack(spacing: 0) {
                StatCell(label: "XP",
                         value: progress.xp.map { "\($0)" } ?? "—")
                Divider().frame(height: 32)
                StatCell(label: "Sessions",
                         value: progress.sessionsCompleted.map { "\($0)" } ?? "—")
                Divider().frame(height: 32)
                StatCell(label: "Projects",
                         value: progress.projectsCompleted.map { "\($0)" } ?? "—")
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

private struct StatCell: View {
    let label: String
    let value: String

    var body: some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.headline.monospacedDigit())
                .foregroundColor(Theme.Color.onSurface)
            Text(label)
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: — Skill summary card

private struct SkillSummaryCard: View {
    let profile: SkillProfile

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            HStack {
                Text("Skill Profile")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                Spacer()
                Text(String(format: "Avg %.1f", profile.averageLevel))
                    .font(.caption.weight(.semibold))
                    .foregroundColor(Theme.Color.primary)
            }

            // Activity summary row
            HStack(spacing: Theme.Spacing.md) {
                Label("\(profile.totalTournaments) tournaments", systemImage: "trophy.fill")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                Label("\(profile.totalAssessments) assessments", systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
            }

            Divider()

            // Top 5 skills by current level
            VStack(spacing: Theme.Spacing.xs) {
                ForEach(profile.topSkills(limit: 5), id: \.name) { entry in
                    SkillRow(name: entry.name, data: entry.data)
                }
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

private struct SkillRow: View {
    let name: String
    let data: SkillProfile.SkillData

    var body: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Text(data.tierEmoji)
                .font(.body)

            Text(name.replacingOccurrences(of: "_", with: " ").capitalized)
                .font(.caption.weight(.medium))
                .foregroundColor(Theme.Color.onSurface)
                .frame(minWidth: 80, alignment: .leading)

            ProgressView(value: min(data.currentLevel / 100.0, 1.0))
                .accentColor(Theme.Color.primary)

            Text(String(format: "%.0f", data.currentLevel))
                .font(.caption.monospacedDigit())
                .foregroundColor(Theme.Color.muted)
                .frame(width: 28, alignment: .trailing)
        }
    }
}

// MARK: — Available specializations section

private struct AvailableSpecializationsSection: View {
    let specs: [SpecializationInfo]

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("Available Specializations")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)

            ForEach(specs) { spec in
                HStack(spacing: Theme.Spacing.sm) {
                    Text(spec.icon)
                        .font(.title3)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(spec.name)
                            .font(.subheadline.weight(.medium))
                            .foregroundColor(Theme.Color.onSurface)
                        Text(spec.description)
                            .font(.caption)
                            .foregroundColor(Theme.Color.muted)
                            .lineLimit(2)
                    }
                    Spacer()
                }
                .padding(.vertical, Theme.Spacing.xs)
                if spec.id != specs.last?.id {
                    Divider()
                }
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}

// MARK: — Generic placeholder card for Education

private struct PlaceholderEduCard: View {
    let title:    String
    let subtitle: String
    let icon:     String

    var body: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: icon)
                .foregroundColor(Theme.Color.muted)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.muted)
                Text(subtitle)
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                    .lineLimit(2)
            }
            Spacer()
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }
}
