import SwiftUI
import UIKit

// My Academy ID — static base page.
//
// Layout (scrollable):
//   AcademyIDCardView  — static, no shimmer / glow / swipe
//                        tap → opens AcademyIDCardPreviewView (fullScreenCover)
//   CardStatusBanner   — shown when not verified
//   Divider
//   QR scan panel      — always here, never in the preview
//
// Color: AcademyIDColorViewModel drives activeColorConfig; 🎨 toolbar button.
// Brightness: tap QR → UIScreen.main.brightness = 1.0
// Offline: fast path uses cached publicToken.

// MARK: — Card status (module-level: also used by AcademyIDCardPreviewView)

enum IDCardStatus {
    case verified
    case noLicence
    case inactive
    case expired
    case onboardingRequired
    case photoRequired

    var statusIcon: String {
        switch self {
        case .verified:            return "checkmark.shield.fill"
        case .noLicence:           return "exclamationmark.circle"
        case .inactive:            return "nosign"
        case .expired:             return "clock.badge.xmark"
        case .onboardingRequired:  return "list.clipboard"
        case .photoRequired:       return "camera.badge.ellipsis"
        }
    }

    var statusMessage: String {
        switch self {
        case .verified:            return "ACCESS VERIFIED"
        case .noLicence:           return "No active licence"
        case .inactive:            return "Licence inactive"
        case .expired:             return "Licence expired"
        case .onboardingRequired:  return "Onboarding required"
        case .photoRequired:       return "Profile photo required"
        }
    }

    var isVerified: Bool { self == .verified }

    var accentColor: Color {
        switch self {
        case .verified:                                 return Theme.Color.primary
        case .expired:                                  return .red
        case .inactive:                                 return Color(red: 0.98, green: 0.57, blue: 0.24)
        case .noLicence, .onboardingRequired,
             .photoRequired:                            return Theme.Color.secondary
        }
    }
}

// MARK: — Main view

struct AcademyIDFullScreenView: View {

    @EnvironmentObject private var authManager: AuthManager
    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @StateObject         private var viewModel  = AcademyIDViewModel()
    @StateObject         private var colorVM    = AcademyIDColorViewModel()

    @Environment(\.presentationMode) private var presentationMode
    @Environment(\.colorScheme)      private var colorScheme

    @State private var isShowingCardPreview  = false
    @State private var isShowingColorPicker  = false
    @State private var brightnessBoostActive = false
    @State private var originalBrightness: CGFloat = UIScreen.main.brightness

    private var activeColorConfig: AcademyIDColorConfig {
        AcademyIDColorConfig.resolve(colorVM.activeColorId)
    }

    // MARK: — Body

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    staticCard
                        .padding(.horizontal, Theme.Spacing.md)
                        .padding(.top, Theme.Spacing.lg)

                    tapHint
                        .padding(.top, Theme.Spacing.xs)

                    if cardStatus != .verified {
                        cardStatusBanner
                            .padding(.top, Theme.Spacing.sm)
                            .padding(.horizontal, Theme.Spacing.md)
                    }

                    Divider()
                        .background(Theme.Color.secondary.opacity(0.15))
                        .padding(.vertical, Theme.Spacing.lg)

                    qrSection

                    Spacer(minLength: Theme.Spacing.xl)
                }
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
                    HStack(spacing: Theme.Spacing.sm) {
                        if dashboardVM.lfaLicense != nil {
                            Button { isShowingColorPicker = true } label: {
                                Image(systemName: "paintpalette")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundColor(Theme.Color.primary)
                            }
                        }
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
        }
        .navigationViewStyle(.stack)
        .sheet(isPresented: $isShowingColorPicker) {
            AcademyIDColorPickerView(colorVM: colorVM)
                .environmentObject(authManager)
        }
        .fullScreenCover(isPresented: $isShowingCardPreview) {
            AcademyIDCardPreviewView(
                colorConfig:  activeColorConfig,
                isVerified:   cardStatus.isVerified,
                lfaAcademyId: viewModel.loadState.response?.lfaAcademyId
                              ?? dashboardVM.profile?.lfaAcademyId,
                publicToken:  viewModel.loadState.response?.publicToken
                              ?? dashboardVM.profile?.publicToken
            )
            .environmentObject(dashboardVM)
            .environmentObject(authManager)
        }
        .onAppear {
            Task { await viewModel.load(using: authManager, profile: dashboardVM.profile) }
            Task { await colorVM.load(using: authManager) }
        }
        .onDisappear { restoreBrightness() }
        .onChange(of: viewModel.loadState.isLoaded) { isLoaded in
            guard isLoaded,
                  dashboardVM.profile?.lfaAcademyId == nil,
                  viewModel.loadState.response?.lfaAcademyId != nil else { return }
            Task { await dashboardVM.reload(using: authManager) }
        }
    }

    // MARK: — Static card (no shimmer, no glow, no swipe)

    private var staticCard: some View {
        AcademyIDCardView(
            firstName:                cardFirstName,
            lastName:                 cardLastName,
            nickname:                 dashboardVM.profile?.nickname,
            age:                      dashboardVM.profile?.calculatedAge,
            nationality:              dashboardVM.profile?.nationality ?? "",
            gender:                   dashboardVM.profile?.gender,
            city:                     dashboardVM.profile?.city,
            country:                  dashboardVM.profile?.country,
            profileImage:             nil,
            profilePhotoURL:          dashboardVM.profile?.profilePhotoUrl,
            profilePhotoProcessedURL: dashboardVM.profile?.profilePhotoProcessedUrl,
            isVerified:               cardStatus.isVerified,
            lfaAcademyId:             viewModel.loadState.response?.lfaAcademyId
                                      ?? dashboardVM.profile?.lfaAcademyId,
            publicToken:              viewModel.loadState.response?.publicToken
                                      ?? dashboardVM.profile?.publicToken,
            colorConfig:              activeColorConfig
        )
        .contentShape(Rectangle())
        .onTapGesture {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            isShowingCardPreview = true
        }
    }

    // MARK: — Tap hint

    private var tapHint: some View {
        HStack(spacing: 6) {
            Image(systemName: "hand.tap")
                .font(.system(size: 10))
            Text("Koppints a kártyára a megnyitáshoz")
                .font(.system(size: 11))
        }
        .foregroundColor(Theme.Color.muted)
    }

    // MARK: — Card status

    private var cardStatus: IDCardStatus {
        guard let licence = dashboardVM.lfaLicense else { return .noLicence }
        guard licence.isActive   else { return .inactive }
        guard !licence.isExpired else { return .expired }
        guard licence.onboardingCompleted else { return .onboardingRequired }
        let hasPhoto = dashboardVM.profile?.profilePhotoUrl != nil
                    || dashboardVM.profile?.profilePhotoProcessedUrl != nil
        return hasPhoto ? .verified : .photoRequired
    }

    // MARK: — Status banner

    private var cardStatusBanner: some View {
        let accent = cardStatus.accentColor
        return HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: cardStatus.statusIcon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(cardStatus.statusMessage)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(accent)
                if cardStatus == .expired,
                   let expiry = dashboardVM.lfaLicense?.expiryDisplayString {
                    Text("Expired \(expiry)")
                        .font(.system(size: 10))
                        .foregroundColor(Theme.Color.muted)
                }
            }
            Spacer()
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, Theme.Spacing.sm)
        .background(accent.opacity(0.08))
        .cornerRadius(Theme.Radius.sm)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.sm)
                .stroke(accent.opacity(0.25), lineWidth: 1)
        )
    }

    // MARK: — QR section

    @ViewBuilder
    private var qrSection: some View {
        switch viewModel.loadState {
        case .loading, .idle:
            ProgressView()
                .frame(width: 200, height: 200)
                .padding(.bottom, Theme.Spacing.md)
        case .loaded(let response):
            loadedQR(qrData: response.qrData)
        case .error(let msg):
            errorState(message: msg)
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
        guard brightnessBoostActive else { return }
        UIScreen.main.brightness = originalBrightness
        brightnessBoostActive = false
    }

    // MARK: — Helpers

    private var isReloading: Bool {
        if case .loading = viewModel.loadState { return true }
        return false
    }

    private var cardFirstName: String? {
        guard let name = dashboardVM.profile?.displayName, !name.isEmpty else { return nil }
        return name.split(separator: " ", maxSplits: 1).first.map(String.init)
    }

    private var cardLastName: String? {
        guard let name = dashboardVM.profile?.displayName else { return nil }
        let parts = name.split(separator: " ", maxSplits: 1)
        return parts.count > 1 ? String(parts[1]) : nil
    }
}
