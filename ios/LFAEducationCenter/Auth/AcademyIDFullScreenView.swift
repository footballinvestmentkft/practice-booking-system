import SwiftUI
import UIKit

// Full-screen My Academy ID view — suitable for on-site verification.
//
// Layout (scrollable):
//   Profile photo (96pt circle, processed → original → placeholder)
//   Full name + lfa_academy_id (amber monospaced)
//   Verified LFA Member badge
//   Chip row: Member since YYYY | Specialization
//   QR code (200×200pt, white background) — tap to toggle brightness boost
//   Hint + verify URL
//
// Brightness boost:
//   tap QR → UIScreen.main.brightness = 1.0
//   tap again or dismiss → restore original brightness
//
// Privacy: email / phone / user_id / credits are never rendered.
// Offline: fast path uses cached publicToken — QR visible without network.
struct AcademyIDFullScreenView: View {

    @EnvironmentObject private var authManager: AuthManager
    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @StateObject         private var viewModel  = AcademyIDViewModel()

    @Environment(\.presentationMode) private var presentationMode

    @State private var brightnessBoostActive = false
    @State private var originalBrightness: CGFloat = UIScreen.main.brightness

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    photoSection
                    nameSection
                    verifiedBadge
                    chipRow
                    qrSection
                    Spacer(minLength: Theme.Spacing.xl)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.top, Theme.Spacing.lg)
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("My Academy ID")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        restoreBrightness()
                        presentationMode.wrappedValue.dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.onSurface)
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task { await viewModel.reload(using: authManager, profile: dashboardVM.profile) }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.primary)
                    }
                    .disabled(isReloading)
                }
            }
        }
        .navigationViewStyle(.stack)
        .onAppear { Task { await viewModel.load(using: authManager, profile: dashboardVM.profile) } }
        .onDisappear { restoreBrightness() }
        // If the slow path just lazy-assigned a new Academy ID, reload the dashboard so
        // ProfileView subtitle and ProfileCompletionScore.academyID (+10%) update immediately.
        .onChange(of: viewModel.loadState.isLoaded) { isLoaded in
            guard isLoaded,
                  dashboardVM.profile?.lfaAcademyId == nil,
                  viewModel.loadState.response?.lfaAcademyId != nil else { return }
            Task { await dashboardVM.reload(using: authManager) }
        }
    }

    // MARK: — Profile photo

    private var photoSection: some View {
        let photoUrl = dashboardVM.profile?.profilePhotoProcessedUrl
                    ?? dashboardVM.profile?.profilePhotoUrl

        return Group {
            if let url = photoUrl {
                URLPhotoView(urlPath: url)
                    .frame(width: 96, height: 96)
                    .clipShape(Circle())
                    .overlay(Circle().stroke(Theme.Color.secondary.opacity(0.4), lineWidth: 2))
            } else {
                Circle()
                    .fill(Theme.Color.muted.opacity(0.08))
                    .frame(width: 96, height: 96)
                    .overlay(
                        Image(systemName: "person.fill")
                            .font(.system(size: 40))
                            .foregroundColor(Theme.Color.muted.opacity(0.3))
                    )
            }
        }
        .padding(.bottom, Theme.Spacing.md)
    }

    // MARK: — Name + Academy ID

    private var nameSection: some View {
        VStack(spacing: 6) {
            if let name = dashboardVM.profile?.displayName, !name.isEmpty {
                Text(name)
                    .font(.title2.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                    .multilineTextAlignment(.center)
            }

            if let aid = viewModel.loadState.response?.lfaAcademyId
                      ?? dashboardVM.profile?.lfaAcademyId {
                Text(aid)
                    .font(.system(size: 15, weight: .bold, design: .monospaced))
                    .foregroundColor(Color(red: 0.91, green: 0.72, blue: 0.29)) // amber
                    .kerning(1.2)
            }
        }
        .padding(.bottom, Theme.Spacing.md)
    }

    // MARK: — Verified badge

    private var verifiedBadge: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 16))
            Text("Verified LFA Member")
                .font(.subheadline.weight(.semibold))
        }
        .foregroundColor(Color(red: 0.18, green: 0.80, blue: 0.44)) // green
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, 10)
        .background(Color(red: 0.18, green: 0.80, blue: 0.44).opacity(0.12))
        .cornerRadius(Theme.Radius.md)
        .padding(.bottom, Theme.Spacing.sm)
    }

    // MARK: — Chip row: Member since + Specialization

    private var chipRow: some View {
        HStack(spacing: Theme.Spacing.sm) {
            if let year = memberSinceYear {
                chip(label: "Member since", value: "\(year)")
            }
            chip(label: "Specialization", value: specializationLabel)
        }
        .padding(.bottom, Theme.Spacing.lg)
    }

    private func chip(label: String, value: String) -> some View {
        VStack(spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 9, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.5)
            Text(value)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, Theme.Spacing.sm)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.sm)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.sm)
                .stroke(Theme.Color.secondary.opacity(0.15), lineWidth: 1)
        )
    }

    // MARK: — QR section

    @ViewBuilder
    private var qrSection: some View {
        switch viewModel.loadState {
        case .loading:
            ProgressView()
                .frame(width: 200, height: 200)
                .padding(.bottom, Theme.Spacing.md)

        case .loaded(let response):
            loadedQR(qrData: response.qrData)

        case .error(let msg):
            errorState(message: msg)

        case .idle:
            ProgressView()
                .frame(width: 200, height: 200)
                .padding(.bottom, Theme.Spacing.md)
        }
    }

    private func loadedQR(qrData: String) -> some View {
        VStack(spacing: Theme.Spacing.sm) {
            if let qrImage = QRCodeGenerator.image(from: qrData, scale: 20) {
                Image(uiImage: qrImage)
                    .interpolation(.none)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 200, height: 200)
                    .padding(12)
                    .background(Color.white)
                    .cornerRadius(Theme.Radius.md)
                    .overlay(
                        RoundedRectangle(cornerRadius: Theme.Radius.md)
                            .stroke(Theme.Color.secondary.opacity(0.2), lineWidth: 1)
                    )
                    .contentShape(Rectangle())
                    .onTapGesture { toggleBrightness() }
            }

            // Brightness hint
            HStack(spacing: 5) {
                Image(systemName: brightnessBoostActive ? "sun.max.fill" : "sun.min")
                    .font(.system(size: 11))
                Text(brightnessBoostActive
                     ? "Brightness boosted — tap to restore"
                     : "Tap QR to boost brightness for scanning")
                    .font(.system(size: 11))
            }
            .foregroundColor(brightnessBoostActive ? Theme.Color.secondary : Theme.Color.muted)
            .animation(.easeInOut(duration: 0.2), value: brightnessBoostActive)

            // Verify URL (tiny, muted — for transparency)
            Text(qrData)
                .font(.system(size: 8))
                .foregroundColor(Theme.Color.muted.opacity(0.5))
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.top, 2)
        }
    }

    private func errorState(message: String) -> some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: "qrcode")
                .font(.system(size: 56))
                .foregroundColor(Theme.Color.muted.opacity(0.25))
                .frame(width: 200, height: 200)

            Text(message)
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)

            Button {
                Task { await viewModel.reload(using: authManager, profile: dashboardVM.profile) }
            } label: {
                Text("Try Again")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.primary)
                    .padding(.horizontal, Theme.Spacing.lg)
                    .padding(.vertical, 10)
                    .background(Theme.Color.primary.opacity(0.12))
                    .cornerRadius(Theme.Radius.sm)
            }
        }
        .padding(.vertical, Theme.Spacing.lg)
    }

    // MARK: — Brightness

    private func toggleBrightness() {
        brightnessBoostActive.toggle()
        UIScreen.main.brightness = brightnessBoostActive ? 1.0 : originalBrightness
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    private func restoreBrightness() {
        if brightnessBoostActive {
            UIScreen.main.brightness = originalBrightness
            brightnessBoostActive = false
        }
    }

    // MARK: — Computed helpers

    private var isReloading: Bool {
        if case .loading = viewModel.loadState { return true }
        return false
    }

    private var memberSinceYear: Int? {
        // Could come from profile if it had created_at — use lfaAcademyId year as proxy
        guard let aid = viewModel.loadState.response?.lfaAcademyId
                     ?? dashboardVM.profile?.lfaAcademyId,
              aid.count >= 8 else { return nil }
        // Format: LFA-YYYY-NNNNN — year is characters 4..7
        let start = aid.index(aid.startIndex, offsetBy: 4)
        let end   = aid.index(start, offsetBy: 4)
        return Int(String(aid[start..<end]))
    }

    private var specializationLabel: String {
        // Primary: lfaLicense from DashboardViewModel
        if let spec = dashboardVM.lfaLicense?.specializationType {
            return specLabel(spec) ?? spec
        }
        // Fallback: first active license from profile
        if let active = dashboardVM.profile?.licenses?.first(where: { $0.isActive }) {
            return specLabel(active.specializationType) ?? active.specializationType
        }
        return "No active specialization"
    }

    private func specLabel(_ raw: String) -> String? {
        let map: [String: String] = [
            "lfa_football_player": "LFA Football Player",
            "lfa_coach":           "LFA Coach",
            "gancuju_player":      "GānCuju Player",
            "internship":          "Internship",
        ]
        return map[raw.lowercased()]
    }
}


// MARK: — URL photo loader (reused from AcademyIDCardView, scoped privately)

private struct URLPhotoView: View {
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
