import SwiftUI

// Academy ID colour picker sheet — Phase 2 (free + premium colours).
//
// Layout:
//   Free section    — Official / Ivory / Charcoal  (always selectable, no lock)
//   Premium section — Navy / Burgundy / Forest     (locked until purchased)
//
// Locked swatch: dimmed circle + lock icon + "300 CR" badge.
// Tap locked swatch → unlock confirmation alert.
// Unlock confirmation: colour name, price, one-time purchase note, Unlock / Cancel.
//
// Unlock success:
//   ownership updated → colour auto-selects → card switches → haptic (medium).
//
// Error banner: shown for select/unlock failures, dismissible.
// Reduce Motion: respected for swatch selection animations.

struct AcademyIDColorPickerView: View {

    @EnvironmentObject private var authManager: AuthManager
    @ObservedObject var colorVM: AcademyIDColorViewModel
    @Environment(\.presentationMode) private var presentationMode
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var pendingUnlockTheme: AcademyIDColorTheme? = nil

    private let swatchSize: CGFloat = 44

    // MARK: — Body

    var body: some View {
        NavigationView {
            VStack(alignment: .leading, spacing: 0) {

                if let err = colorVM.errorMessage {
                    errorBanner(err)
                }

                if colorVM.colors.isEmpty {
                    emptyState
                } else {
                    swatchSections
                        .padding(.horizontal, Theme.Spacing.md)
                        .padding(.top, Theme.Spacing.lg)
                }

                Spacer()

                if colorVM.isUnlocking {
                    HStack(spacing: Theme.Spacing.sm) {
                        ProgressView().scaleEffect(0.8)
                        Text("Unlocking…")
                            .font(.system(size: 12))
                            .foregroundColor(Theme.Color.muted)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, Theme.Spacing.md)
                }
            }
            .navigationTitle("ID Card Style")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { presentationMode.wrappedValue.dismiss() }
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(Theme.Color.primary)
                }
            }
        }
        .navigationViewStyle(.stack)
        .alert(item: $pendingUnlockTheme) { theme in
            Alert(
                title: Text("Unlock \(theme.label)?"),
                message: Text("300 CR · One-time purchase · Yours forever"),
                primaryButton: .default(Text("Unlock — 300 CR")) {
                    Task { await colorVM.unlock(colorId: theme.id, using: authManager) }
                },
                secondaryButton: .cancel(Text("Cancel"))
            )
        }
    }

    // MARK: — Two-section swatch layout

    private var swatchSections: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
            swatchSection(
                title: "FREE",
                themes: colorVM.colors.filter { !$0.isPremium }
            )
            swatchSection(
                title: "PREMIUM",
                themes: colorVM.colors.filter { $0.isPremium }
            )
        }
    }

    private func swatchSection(title: String, themes: [AcademyIDColorTheme]) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text(title)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .tracking(0.8)

            HStack(spacing: Theme.Spacing.lg) {
                ForEach(themes) { theme in
                    swatchButton(theme)
                }
                Spacer()
            }
        }
    }

    // MARK: — Swatch button

    @ViewBuilder
    private func swatchButton(_ theme: AcademyIDColorTheme) -> some View {
        let isActive  = colorVM.activeColorId == theme.id
        let isLocked  = theme.isPremium && !theme.isOwned

        Button {
            if isLocked {
                pendingUnlockTheme = theme
            } else {
                let animation: Animation = reduceMotion
                    ? .easeInOut(duration: 0.20)
                    : .spring(response: 0.30, dampingFraction: 0.75)
                withAnimation(animation) {
                    Task { await colorVM.select(colorId: theme.id, using: authManager) }
                }
            }
        } label: {
            VStack(spacing: 6) {
                ZStack {
                    Circle()
                        .fill(Color(hex: theme.dotColor))
                        .frame(width: swatchSize, height: swatchSize)
                        .opacity(isLocked ? 0.45 : 1.0)
                        .overlay(
                            Circle().stroke(
                                isActive ? Theme.Color.primary : Color.clear,
                                lineWidth: 2.5
                            )
                        )
                        .shadow(
                            color: isActive ? Theme.Color.primary.opacity(0.30) : .clear,
                            radius: 4
                        )

                    if isLocked {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.white)
                    } else if isActive {
                        Image(systemName: "checkmark")
                            .font(.system(size: 14, weight: .bold))
                            .foregroundColor(checkmarkColor(for: theme.dotColor))
                            .transition(.scale.combined(with: .opacity))
                    }
                }
                .animation(.spring(response: 0.25, dampingFraction: 0.70), value: isActive)

                Text(theme.label)
                    .font(.system(size: 11, weight: isActive ? .semibold : .regular))
                    .foregroundColor(
                        isActive  ? Theme.Color.primary :
                        isLocked  ? Theme.Color.muted.opacity(0.55) :
                                    Theme.Color.muted
                    )

                if isLocked {
                    Text("300 CR")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(Theme.Color.secondary)
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(colorVM.isLoading || colorVM.isUnlocking)
    }

    // MARK: — Error banner

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: "exclamationmark.circle")
                .font(.system(size: 13))
            Text(message)
                .font(.system(size: 12))
            Spacer()
            Button { colorVM.errorMessage = nil } label: {
                Image(systemName: "xmark").font(.system(size: 11))
            }
        }
        .foregroundColor(.red)
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, Theme.Spacing.sm)
        .background(Color.red.opacity(0.08))
    }

    // MARK: — Empty state

    private var emptyState: some View {
        VStack {
            Spacer()
            if colorVM.isLoading {
                ProgressView().frame(maxWidth: .infinity)
            } else {
                Text("No styles available.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .frame(maxWidth: .infinity)
            }
            Spacer()
        }
    }

    // MARK: — Checkmark colour (white on dark swatches, dark on light)

    private func checkmarkColor(for hexColor: String) -> Color {
        let h = hexColor.trimmingCharacters(in: .init(charactersIn: "#"))
        var rgb: UInt64 = 0
        Scanner(string: h).scanHexInt64(&rgb)
        let r = Double((rgb >> 16) & 0xFF) * 299
        let g = Double((rgb >>  8) & 0xFF) * 587
        let b = Double( rgb        & 0xFF) * 114
        let brightness = (r + g + b) / (255 * 1000)
        return brightness < 0.40 ? .white : .black.opacity(0.75)
    }
}
