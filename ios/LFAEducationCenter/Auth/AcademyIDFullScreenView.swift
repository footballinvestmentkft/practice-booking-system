import SwiftUI
import UIKit

// Full-screen My Academy ID view — suitable for on-site verification.
//
// Layout (scrollable):
//   AcademyIDCardView  — same component shown in RegisterView, now with real user data
//   CardStatusBanner   — shown when card is not yet verified (replaces ACCESS VERIFIED)
//   Divider
//   QR scan panel      — 200×200pt, white background, tap to boost brightness
//   Hint + verify URL
//
// Visual consistency: uses the identical AcademyIDCardView component as the
// registration preview, so the user sees the same card identity both during
// registration and later in "My Academy ID".
//
// Brightness boost:
//   tap QR → UIScreen.main.brightness = 1.0
//   tap again or dismiss → restore original brightness
//
// Privacy: email / phone / user_id / credits are never rendered.
// Offline: fast path uses cached publicToken — QR visible without network.
//
// Card status (computed from dashboardVM):
//   verified            — photo + active licence + onboarding + not expired
//   no_licence          — no LFA_FOOTBALL_PLAYER licence
//   inactive            — licence.isActive == false
//   expired             — licence.isExpired == true
//   onboarding_required — licence active but onboarding not complete
//   photo_required      — licence + onboarding OK but no profile photo
//
// Reveal animation (flip + shimmer + glow):
//   Plays the FIRST time a user sees the card in .verified state.
//   Persisted per-user via CardRevealStore (UserDefaults).
//   Subsequent verified openings: static display.
//   Non-verified: always static — no flip, shimmer or glow.
//   Reduce Motion: simple fade-in instead of 3D flip (no shimmer/glow).

// MARK: — Card reveal persistence (per-user, UserDefaults)

// Keyed by userId so multiple users on the same device each get the reveal once.
// markSeen() is called with a safe delay AFTER the animation completes (~1.6 s).
// If the app is force-killed mid-animation the reveal replays on next launch — by design.
// The flag is only ever set when cardStatus == .verified at animation start.
private enum CardRevealStore {

    static func hasBeenSeen(forUserId userId: Int) -> Bool {
        UserDefaults.standard.bool(forKey: _key(userId))
    }

    // Call only after the animation has safely completed.
    static func markSeen(forUserId userId: Int) {
        UserDefaults.standard.set(true, forKey: _key(userId))
    }

    // Testing / admin reset — not exposed in production UI.
    static func reset(forUserId userId: Int) {
        UserDefaults.standard.removeObject(forKey: _key(userId))
    }

    private static func _key(_ userId: Int) -> String {
        "academyID.revealSeen.\(userId)"
    }
}

// MARK: — Card status model

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
}

// MARK: — AcademyIDFullScreenView

struct AcademyIDFullScreenView: View {

    @EnvironmentObject private var authManager: AuthManager
    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @StateObject         private var viewModel  = AcademyIDViewModel()

    @Environment(\.presentationMode)       private var presentationMode
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var brightnessBoostActive = false
    @State private var originalBrightness: CGFloat = UIScreen.main.brightness

    // MARK: — Reveal animation state

    private enum RevealMode { case pending, fullFlip, reducedFade, staticDisplay }

    @State private var revealMode:    RevealMode = .pending
    // fullFlip: back face 0 → 90, front face 90 → 0 (same rotation direction)
    @State private var backDegree:    Double     = 0      // back starts face-up
    @State private var frontDegree:   Double     = 90     // front starts edge-on (hidden)
    @State private var shimmerOffset: CGFloat    = -1.5   // shimmer sweep position (−1.5 → 1.5)
    @State private var glowOpacity:   Double     = 0
    @State private var glowRadius:    CGFloat    = 0
    @State private var fadeOpacity:   Double     = 0      // reduce motion: 0 → 1

    // MARK: — Body

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    cardSection
                    if cardStatus != .verified {
                        cardStatusBanner
                            .padding(.top, Theme.Spacing.sm)
                    }
                    Divider()
                        .background(Theme.Color.secondary.opacity(0.15))
                        .padding(.vertical, Theme.Spacing.lg)
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
        .onAppear {
            Task { await viewModel.load(using: authManager, profile: dashboardVM.profile) }
            scheduleReveal()
        }
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

    // MARK: — Card status (strict AND chain)
    // verified ↔ licence.isActive + !isExpired + onboardingCompleted + profilePhoto

    private var cardStatus: IDCardStatus {
        guard let licence = dashboardVM.lfaLicense else { return .noLicence }
        guard licence.isActive  else { return .inactive }
        guard !licence.isExpired else { return .expired }
        guard licence.onboardingCompleted else { return .onboardingRequired }
        let hasPhoto = dashboardVM.profile?.profilePhotoUrl != nil
                    || dashboardVM.profile?.profilePhotoProcessedUrl != nil
        return hasPhoto ? .verified : .photoRequired
    }

    // MARK: — Active specialization label
    // Derived from dashboardVM.lfaLicense — works on both fast path and slow path.

    private var activeSpecializationLabel: String? {
        guard let licence = dashboardVM.lfaLicense,
              licence.isActive,
              !licence.isExpired else { return nil }
        return "LFA Football Player"
    }

    // MARK: — Card section (routes based on revealMode)

    @ViewBuilder
    private var cardSection: some View {
        switch revealMode {
        case .pending, .staticDisplay:
            // Static — no animation. Used for non-verified state and
            // all subsequent verified openings after the reveal was seen.
            liveCard

        case .fullFlip:
            // First verified opening: back face flips away, front face flips in,
            // shimmer sweeps across, glow pulses around the border.
            ZStack {
                cardBackFace
                    .rotation3DEffect(
                        .degrees(backDegree),
                        axis: (x: 0, y: 1, z: 0),
                        perspective: 0.35
                    )
                liveCard
                    .rotation3DEffect(
                        .degrees(frontDegree),
                        axis: (x: 0, y: 1, z: 0),
                        perspective: 0.35
                    )
            }
            .overlay(shimmerOverlay)
            .overlay(glowBorderOverlay)

        case .reducedFade:
            // Reduce Motion fallback: simple fade-in, no 3D, no shimmer, no glow.
            liveCard
                .opacity(fadeOpacity)
        }
    }

    // MARK: — Live card (source of truth — AcademyIDCardView, data unchanged)

    private var liveCard: some View {
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
            specialization:           activeSpecializationLabel
        )
    }

    // MARK: — Card back face (shown before flip reveals the live card)
    // Elegant placeholder — LFA branding only, no user data, no status text.

    private var cardBackFace: some View {
        ZStack {
            // Base card surface
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .fill(Theme.Color.surface)
            // Diagonal gradient texture (subtle depth, no hardcoded colour)
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .fill(
                    LinearGradient(
                        colors: [
                            Theme.Color.secondary.opacity(0.04),
                            Theme.Color.secondary.opacity(0.10),
                            Theme.Color.secondary.opacity(0.04),
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            // Centred LFA branding — no user data, no verified text
            VStack(spacing: 10) {
                Image("LFALogo")
                    .resizable()
                    .scaledToFit()
                    .frame(height: 28)
                    .opacity(0.28)
                Text("LFA EDUCATION CENTER")
                    .font(.system(size: 7, weight: .semibold))
                    .tracking(1.8)
                    .foregroundColor(Theme.Color.muted.opacity(0.45))
            }
            // Card border — matches AcademyIDCardView overlay
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .stroke(Theme.Color.secondary.opacity(0.28), lineWidth: 1)
        }
        .frame(minHeight: 180)
        .shadow(color: .black.opacity(0.06), radius: 6, x: 0, y: 2)
    }

    // MARK: — Shimmer overlay (sweeps left → right while front face reveals)

    private var shimmerOverlay: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let shimmerW = w * 0.55
            LinearGradient(
                colors: [.clear, .white.opacity(0.50), .clear],
                startPoint: .leading,
                endPoint: .trailing
            )
            .frame(width: shimmerW)
            .offset(x: shimmerOffset * (w + shimmerW))
        }
        .blendMode(.overlay)
        .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.md))
        .allowsHitTesting(false)
    }

    // MARK: — Glow border overlay (pulses after flip completes)

    private var glowBorderOverlay: some View {
        RoundedRectangle(cornerRadius: Theme.Radius.md)
            .stroke(Theme.Color.secondary, lineWidth: 1.5)
            .shadow(color: Theme.Color.secondary.opacity(glowOpacity * 0.75),
                    radius: glowRadius)
            .opacity(glowOpacity)
            .allowsHitTesting(false)
    }

    // MARK: — Reveal scheduling (called once on onAppear)

    private func scheduleReveal() {
        guard revealMode == .pending else { return }

        guard cardStatus == .verified,
              let userId = dashboardVM.profile?.id,
              !CardRevealStore.hasBeenSeen(forUserId: userId)
        else {
            revealMode = .staticDisplay
            return
        }

        revealMode = reduceMotion ? .reducedFade : .fullFlip

        // Small delay lets SwiftUI finish its first render pass before animating.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            switch revealMode {
            case .fullFlip:   playRevealAnimation(forUserId: userId)
            case .reducedFade: playReducedFadeIn(forUserId: userId)
            default: break
            }
        }
    }

    // MARK: — Full flip + shimmer + glow sequence
    //
    // t = 0.00 – 0.28s  Back face flips away (easeIn, 0° → 90°)
    // t = 0.22 – 0.65s  Front face flips in from same direction (spring, 90° → 0°)
    // t = 0.25 – 0.90s  Shimmer sweeps left → right (easeInOut, 0.65s)
    // t = 0.52 – 1.35s  Glow pulses (in → partial → second pulse → fade out)
    // t = 1.60s         Mark seen in UserDefaults (safe delay after animation)

    private func playRevealAnimation(forUserId userId: Int) {
        // Phase 1: back face flips away to the right
        withAnimation(.easeIn(duration: 0.28)) {
            backDegree = 90
        }

        // Phase 2: front face flips in from the right (same direction, slight overlap)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.22) {
            withAnimation(.spring(response: 0.40, dampingFraction: 0.80)) {
                frontDegree = 0
            }
        }

        // Phase 3: shimmer sweep during front reveal
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            withAnimation(.easeInOut(duration: 0.65)) {
                shimmerOffset = 1.5
            }
        }

        // Phase 4: glow pulse — in, first fade, second pulse, final fade
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.52) {
            withAnimation(.easeOut(duration: 0.22)) {
                glowOpacity = 1.0
                glowRadius  = 18
            }
            withAnimation(.easeInOut(duration: 0.28).delay(0.24)) {
                glowOpacity = 0.45
                glowRadius  = 6
            }
            withAnimation(.easeOut(duration: 0.18).delay(0.56)) {
                glowOpacity = 0.80
                glowRadius  = 13
            }
            withAnimation(.easeIn(duration: 0.40).delay(0.76)) {
                glowOpacity = 0
                glowRadius  = 0
            }
        }

        // Phase 5: mark seen after animation safely completes
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
            CardRevealStore.markSeen(forUserId: userId)
        }
    }

    // MARK: — Reduce Motion fallback (fade-in only, no 3D, no shimmer, no glow)

    private func playReducedFadeIn(forUserId userId: Int) {
        withAnimation(.easeIn(duration: 0.40)) {
            fadeOpacity = 1
        }
        // Mark seen after fade completes
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.80) {
            CardRevealStore.markSeen(forUserId: userId)
        }
    }

    // MARK: — Status banner (shown only when not verified)

    private var cardStatusBanner: some View {
        let isExpiredStatus  = cardStatus == .expired
        let isInactiveStatus = cardStatus == .inactive
        let accentColor: Color = isExpiredStatus  ? .red
                               : isInactiveStatus ? Color(red: 0.98, green: 0.57, blue: 0.24)
                               : Theme.Color.secondary

        return HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: cardStatus.statusIcon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(accentColor)
            VStack(alignment: .leading, spacing: 2) {
                Text(cardStatus.statusMessage)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(accentColor)
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
        .background(accentColor.opacity(0.08))
        .cornerRadius(Theme.Radius.sm)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.sm)
                .stroke(accentColor.opacity(0.25), lineWidth: 1)
        )
    }

    // MARK: — QR scan panel (200×200, brightness boost)

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
        if brightnessBoostActive {
            UIScreen.main.brightness = originalBrightness
            brightnessBoostActive = false
        }
    }

    // MARK: — Helpers

    private var isReloading: Bool {
        if case .loading = viewModel.loadState { return true }
        return false
    }

    // Split displayName ("R2Test Beta") → firstName ("R2Test") / lastName ("Beta")
    private var cardFirstName: String? {
        guard let name = dashboardVM.profile?.displayName, !name.isEmpty else { return nil }
        return name.split(separator: " ", maxSplits: 1).first.map(String.init)
    }

    private var cardLastName: String? {
        guard let name = dashboardVM.profile?.displayName else { return nil }
        let parts = name.split(separator: " ", maxSplits: 1)
        guard parts.count > 1 else { return nil }
        return String(parts[1])
    }
}
