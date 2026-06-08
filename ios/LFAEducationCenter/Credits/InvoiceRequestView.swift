import SwiftUI

// Credit package invoice request.
//
// User selects a credit package and taps "Request Invoice".
// The backend creates a pending InvoiceRequest and returns a payment reference.
// Credits are added ONLY after an administrator verifies the bank transfer.
//
// API: POST /api/v1/users/request-invoice (Bearer JSON)
// No immediate credit jóváírás — pending admin approval.
struct InvoiceRequestView: View {

    @EnvironmentObject private var authManager: AuthManager
    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @StateObject         private var viewModel  = InvoiceRequestViewModel()
    @Environment(\.presentationMode) private var presentationMode

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    headerSection
                    packageSection
                    submitSection
                    Spacer(minLength: Theme.Spacing.xl)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.top, Theme.Spacing.md)
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("Request Invoice")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if !isLoading {
                        Button { presentationMode.wrappedValue.dismiss() } label: {
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

    // MARK: — Header

    private var headerSection: some View {
        VStack(spacing: Theme.Spacing.sm) {
            Image(systemName: "doc.text.fill")
                .font(.system(size: 36))
                .foregroundColor(Theme.Color.secondary)

            Text("Credit Packages")
                .font(.title3.weight(.bold))
                .foregroundColor(Theme.Color.onSurface)

            Text("Select a package and request an invoice. After completing your bank transfer, an administrator will verify your payment and add credits to your account.")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Theme.Spacing.lg)
    }

    // MARK: — Package picker

    private var packageSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("SELECT PACKAGE")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.8)
                .padding(.top, Theme.Spacing.sm)

            VStack(spacing: 1) {
                ForEach(CreditPackage.allCases) { pkg in
                    packageRow(pkg)
                }
            }
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)
        }
    }

    private func packageRow(_ pkg: CreditPackage) -> some View {
        let isSelected = viewModel.selectedPackage == pkg

        return Button { viewModel.selectedPackage = pkg } label: {
            HStack(spacing: Theme.Spacing.sm) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 18))
                    .foregroundColor(isSelected ? Theme.Color.primary : Theme.Color.muted)

                VStack(alignment: .leading, spacing: 2) {
                    Text(pkg.label)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(Theme.Color.onSurface)
                    Text("\(pkg.creditsLabel) credits")
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                }

                Spacer()

                Text(pkg.priceLabel)
                    .font(.subheadline.weight(.bold))
                    .foregroundColor(isSelected ? Theme.Color.primary : Theme.Color.onSurface)
            }
            .padding(.horizontal, Theme.Spacing.md)
            .padding(.vertical, 12)
            .background(isSelected ? Theme.Color.primary.opacity(0.05) : Color.clear)
        }
        .disabled(isLoading)
    }

    // MARK: — Submit / result section

    @ViewBuilder
    private var submitSection: some View {
        switch viewModel.state {
        case .idle:
            VStack(spacing: Theme.Spacing.sm) {
                selectedSummary
                requestButton
            }
            .padding(.top, Theme.Spacing.md)

        case .loading:
            HStack(spacing: Theme.Spacing.sm) {
                ProgressView().scaleEffect(0.9)
                Text("Creating invoice…")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }
            .frame(maxWidth: .infinity)
            .padding(Theme.Spacing.lg)

        case .success(let result):
            successView(result: result)

        case .error(let message):
            VStack(spacing: Theme.Spacing.sm) {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(Theme.Color.error)
                    Text(message)
                        .font(.caption)
                        .foregroundColor(Theme.Color.onSurface)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                }
                .padding(Theme.Spacing.md)
                .background(Theme.Color.error.opacity(0.08))
                .cornerRadius(Theme.Radius.sm)
                .padding(.top, Theme.Spacing.md)

                Button { viewModel.reset() } label: {
                    Text("Try Again")
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(Theme.Color.primary)
                }
            }
        }
    }

    private var selectedSummary: some View {
        HStack {
            Text("Selected:")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)
            Text("\(viewModel.selectedPackage.label) — \(viewModel.selectedPackage.creditsLabel)")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)
            Spacer()
            Text(viewModel.selectedPackage.priceLabel)
                .font(.subheadline.weight(.bold))
                .foregroundColor(Theme.Color.primary)
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.sm)
    }

    private var requestButton: some View {
        Button {
            Task { await viewModel.request(using: authManager) }
        } label: {
            Text("Request Invoice")
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 50)
                .background(Theme.Color.primary)
                .foregroundColor(.white)
                .cornerRadius(Theme.Radius.sm)
        }
    }

    // MARK: — Success view

    private func successView(result: InvoiceResult) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 22))
                    .foregroundColor(Color(red: 0.18, green: 0.80, blue: 0.44))
                Text("Invoice Created")
                    .font(.headline.weight(.bold))
                    .foregroundColor(Theme.Color.onSurface)
            }

            VStack(spacing: 1) {
                resultRow(label: "Payment Reference", value: result.paymentReference, isMonospaced: true)
                resultRow(label: "Amount", value: String(format: "€%.2f", result.amountEur), isMonospaced: false)
                resultRow(label: "Credits on approval", value: "\(result.creditAmount) CR", isMonospaced: false)
                resultRow(label: "Status", value: result.status.capitalized, isMonospaced: false)
            }
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)

            // Copy reference button
            Button {
                UIPasteboard.general.string = result.paymentReference
            } label: {
                Label("Copy Payment Reference", systemImage: "doc.on.doc")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 44)
                    .background(Theme.Color.primary.opacity(0.12))
                    .foregroundColor(Theme.Color.primary)
                    .cornerRadius(Theme.Radius.sm)
            }

            Text("Include this reference in your bank transfer description. Credits will be added to your account after your payment is verified by an administrator.")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
                .fixedSize(horizontal: false, vertical: true)

            Button { presentationMode.wrappedValue.dismiss() } label: {
                Text("Done")
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 48)
                    .background(Theme.Color.primary)
                    .foregroundColor(.white)
                    .cornerRadius(Theme.Radius.sm)
            }
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
        .padding(.top, Theme.Spacing.md)
    }

    private func resultRow(label: String, value: String, isMonospaced: Bool) -> some View {
        HStack {
            Text(label)
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
            Spacer()
            Text(value)
                .font(isMonospaced
                      ? .system(size: 13, weight: .semibold, design: .monospaced)
                      : .subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, 10)
    }

    // MARK: — Helpers

    private var isLoading: Bool {
        if case .loading = viewModel.state { return true }
        return false
    }
}
