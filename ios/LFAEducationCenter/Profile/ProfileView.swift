import SwiftUI

// Read-only profile overview — name, email, role, Academy ID link.
//
// All data comes from DashboardViewModel.profile (already loaded — no extra fetch).
// Photo: uses profile photo from Phase 1 (processed → original → placeholder).
// Edit and mood photos are deferred to a future phase.
struct ProfileView: View {

    @EnvironmentObject private var authManager:  AuthManager
    @EnvironmentObject private var dashboardVM:  DashboardViewModel

    @Environment(\.presentationMode) private var presentationMode

    @State private var isShowingAcademyID       = false
    @State private var isShowingPhotoUpload     = false
    @State private var isShowingBaselineSelfRating = false

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    photoSection
                    identitySection
                    academyIDSection
                    completionSection
                    Spacer(minLength: Theme.Spacing.xl)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.top, Theme.Spacing.lg)
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("Profile")
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
                }
            }
            .fullScreenCover(isPresented: $isShowingAcademyID) {
                AcademyIDFullScreenView()
                    .environmentObject(authManager)
                    .environmentObject(dashboardVM)
            }
            .fullScreenCover(isPresented: $isShowingPhotoUpload) {
                ProfilePhotoUploadView {
                    Task { await dashboardVM.reload(using: authManager) }
                }
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
            }
            .fullScreenCover(isPresented: $isShowingBaselineSelfRating) {
                BaselineSelfRatingView {
                    Task { await dashboardVM.reload(using: authManager) }
                }
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
            }
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Photo

    private var photoSection: some View {
        let photoUrl = dashboardVM.profile?.profilePhotoProcessedUrl
                    ?? dashboardVM.profile?.profilePhotoUrl

        return VStack(spacing: Theme.Spacing.sm) {
            Button { isShowingPhotoUpload = true } label: {
                Group {
                    if let url = photoUrl {
                        ProfileURLPhotoView(urlPath: url)
                            .frame(width: 88, height: 88)
                            .clipShape(Circle())
                            .overlay(Circle().stroke(Theme.Color.secondary.opacity(0.3), lineWidth: 2))
                    } else {
                        Circle()
                            .fill(Theme.Color.muted.opacity(0.08))
                            .frame(width: 88, height: 88)
                            .overlay(
                                Image(systemName: "person.fill")
                                    .font(.system(size: 36))
                                    .foregroundColor(Theme.Color.muted.opacity(0.3))
                            )
                    }
                }
                // Camera badge — bottom trailing corner (iOS 13-compatible overlay form)
                .overlay(
                    Circle()
                        .fill(Theme.Color.primary)
                        .frame(width: 26, height: 26)
                        .overlay(
                            Image(systemName: "camera.fill")
                                .font(.system(size: 11))
                                .foregroundColor(.white)
                        )
                        .offset(x: 2, y: 2),
                    alignment: .bottomTrailing
                )
            }
            .padding(.bottom, 4)
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Theme.Spacing.sm)
    }

    // MARK: — Identity

    private var identitySection: some View {
        VStack(spacing: Theme.Spacing.sm) {
            if let name = dashboardVM.profile?.displayName, !name.isEmpty {
                Text(name)
                    .font(.title2.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                    .multilineTextAlignment(.center)
            }

            Text(dashboardVM.profile?.email ?? "")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)

            if let role = dashboardVM.profile?.role {
                Text(role.capitalized)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, Theme.Spacing.sm)
                    .padding(.vertical, 4)
                    .background(Theme.Color.primary.opacity(0.15))
                    .foregroundColor(Theme.Color.primary)
                    .cornerRadius(Theme.Radius.sm)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Theme.Spacing.lg)
    }

    // MARK: — Profile completion checklist

    @ViewBuilder
    private var completionSection: some View {
        if let profile = dashboardVM.profile,
           dashboardVM.lfaLicense?.onboardingCompleted == true {
            let score = ProfileCompletionScore.compute(
                profile:             profile,
                lfaLicense:          dashboardVM.lfaLicense,
                selfRatingCompleted: dashboardVM.selfRatingCompleted
            )
            ProfileCompletionSection(
                score:                   score,
                onAcademyIDTap:          { isShowingAcademyID = true },
                onPhotoTap:              { isShowingPhotoUpload = true },
                onBaselineSelfRatingTap: { isShowingBaselineSelfRating = true }
            )
                .padding(.top, Theme.Spacing.lg)
        }
    }

    // MARK: — Academy ID entry

    private var academyIDSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("ACADEMY ID")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.8)

            Button { isShowingAcademyID = true } label: {
                HStack {
                    Image(systemName: "creditcard.fill")
                        .font(.system(size: 15))
                        .foregroundColor(Theme.Color.secondary)
                        .frame(width: 28)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("My Academy ID")
                            .font(.subheadline.weight(.semibold))
                            .foregroundColor(Theme.Color.onSurface)
                        if let aid = dashboardVM.profile?.lfaAcademyId {
                            Text(aid)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundColor(Theme.Color.muted)
                        }
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(Theme.Color.muted)
                }
                .padding(Theme.Spacing.md)
                .background(Theme.Color.surface)
                .cornerRadius(Theme.Radius.md)
            }
        }
    }
}

// MARK: — URL photo loader (scoped to ProfileView)

private struct ProfileURLPhotoView: View {
    let urlPath: String
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let img = image {
                Image(uiImage: img).resizable().scaledToFill()
            } else {
                Rectangle()
                    .fill(Theme.Color.muted.opacity(0.08))
                    .overlay(ProgressView().scaleEffect(0.7))
            }
        }
        .onAppear { loadImage() }
    }

    private func loadImage() {
        guard image == nil,
              let url = URL(string: APIConfig.baseURL + urlPath) else { return }
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data = data, let img = UIImage(data: data) else { return }
            DispatchQueue.main.async { self.image = img }
        }.resume()
    }
}
