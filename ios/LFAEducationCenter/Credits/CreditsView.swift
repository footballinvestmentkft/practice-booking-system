import SwiftUI

// Credits overview — balance hero, pending invoices, credit acquisition CTAs, history.
//
// Balance:          DashboardViewModel.profile.creditBalance (no extra fetch)
// Transactions:     CreditsViewModel → GET /api/v1/users/me/credit-transactions?limit=50
// Pending invoices: PendingInvoicesViewModel → GET /api/v1/invoices/my-invoices
//
// Credit acquisition:
//   Redeem a Code  → RedeemCodeView (coupon + INV-* invitation codes)
//   Request Invoice → InvoiceRequestView (package picker → offline bank transfer)
struct CreditsView: View {

    @EnvironmentObject private var authManager:         AuthManager
    @EnvironmentObject private var dashboardVM:         DashboardViewModel
    @StateObject         private var viewModel          = CreditsViewModel()
    @StateObject         private var pendingInvoicesVM  = PendingInvoicesViewModel()

    @Environment(\.presentationMode) private var presentationMode

    @State private var isShowingRedeemCode     = false
    @State private var isShowingInvoiceRequest = false

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {
                    balanceHero
                    pendingInvoicesSection
                    getCreditsSection
                    howToGetSection
                    transactionSection
                    Spacer(minLength: Theme.Spacing.xl)
                }
            }
            .background(Color(UIColor.systemBackground).ignoresSafeArea())
            .navigationTitle("Credits")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button { presentationMode.wrappedValue.dismiss() } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.onSurface)
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task {
                            await viewModel.reload(using: authManager)
                            await pendingInvoicesVM.reload(using: authManager)
                            // Also reload dashboard so the Hub balance badge and
                            // DashboardView hero reflect the latest credit_balance
                            // (e.g. after admin verifies an invoice).
                            await dashboardVM.reload(using: authManager)
                        }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.primary)
                    }
                    .disabled(isLoading)
                }
            }
        }
        .navigationViewStyle(.stack)
        .onAppear {
            Task { await viewModel.load(using: authManager) }
            Task { await pendingInvoicesVM.load(using: authManager) }
        }
        .fullScreenCover(isPresented: $isShowingRedeemCode, onDismiss: {
            // Reload transaction history so the new credit entry appears immediately.
            Task { await viewModel.reload(using: authManager) }
        }) {
            RedeemCodeView()
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
        }
        .fullScreenCover(isPresented: $isShowingInvoiceRequest, onDismiss: {
            // Reload when InvoiceRequestView closes so a new invoice appears immediately.
            Task { await pendingInvoicesVM.reload(using: authManager) }
        }) {
            InvoiceRequestView()
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
        }
    }

    // MARK: — Balance hero

    // Prefer the credit_balance from the transaction page (fresher than dashboardVM,
    // which is only reloaded on DashboardView appear). Falls back to dashboardVM when
    // the transaction page hasn't loaded yet.
    private var displayBalance: Int {
        if case .loaded(let page) = viewModel.loadState { return page.creditBalance }
        return dashboardVM.profile?.creditBalance ?? 0
    }

    private var balanceHero: some View {
        VStack(spacing: 8) {
            Image(systemName: "creditcard.fill")
                .font(.system(size: 36))
                .foregroundColor(Theme.Color.secondary)

            Text("\(displayBalance)")
                .font(.system(size: 48, weight: .bold, design: .rounded))
                .foregroundColor(Theme.Color.onSurface)

            Text("CR Balance")
                .font(.subheadline)
                .foregroundColor(Theme.Color.muted)

            if displayBalance < 100 {
                let needed = 100 - displayBalance
                Text("You need \(needed) more CR to unlock LFA Football Player")
                    .font(.caption)
                    .foregroundColor(Theme.Color.error)
                    .multilineTextAlignment(.center)
                    .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Theme.Spacing.xl)
        .padding(.horizontal, Theme.Spacing.md)
        .background(Theme.Color.surface)
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.top, Theme.Spacing.md)
        .cornerRadius(Theme.Radius.md)
    }

    // MARK: — Pending invoices

    // Shown only when there are pending invoices — hidden when empty (normal state).
    @ViewBuilder
    private var pendingInvoicesSection: some View {
        if pendingInvoicesVM.hasPending {
            VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                Text("PENDING INVOICES")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(Theme.Color.muted)
                    .kerning(0.8)
                    .padding(.horizontal, Theme.Spacing.md)
                    .padding(.top, Theme.Spacing.lg)

                VStack(spacing: Theme.Spacing.sm) {
                    ForEach(pendingInvoicesVM.pendingInvoices.prefix(3)) { invoice in
                        pendingInvoiceCard(invoice)
                    }
                }
                .padding(.horizontal, Theme.Spacing.md)
            }
        }
    }

    private func pendingInvoiceCard(_ invoice: InvoiceItem) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            HStack {
                Image(systemName: "doc.text.fill")
                    .font(.system(size: 14))
                    .foregroundColor(Theme.Color.secondary)
                Text("Invoice \(invoice.priceLabel)")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                Spacer()
                Text(invoice.statusLabel)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(Theme.Color.secondary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Theme.Color.secondary.opacity(0.12))
                    .cornerRadius(4)
            }

            // Reference (monospaced, tappable for copy)
            HStack(spacing: 6) {
                Text(invoice.paymentReference)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundColor(Theme.Color.primary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Spacer()
                Button {
                    UIPasteboard.general.string = invoice.paymentReference
                } label: {
                    Image(systemName: "doc.on.doc")
                        .font(.system(size: 13))
                        .foregroundColor(Theme.Color.primary)
                }
            }

            HStack {
                Text("\(invoice.creditsLabel) on approval")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                Spacer()
                if !invoice.createdAtFormatted.isEmpty {
                    Text(invoice.createdAtFormatted)
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                }
            }

            Text("Credits will be added after admin verifies your bank transfer.")
                .font(.caption2)
                .foregroundColor(Theme.Color.muted)
        }
        .padding(Theme.Spacing.md)
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
    }

    // MARK: — Get credits CTAs

    private var getCreditsSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("GET CREDITS")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.8)
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.top, Theme.Spacing.lg)

            VStack(spacing: 1) {
                ctaRow(
                    icon: "tag.fill",
                    title: "Redeem a Code",
                    subtitle: "Coupon or invitation code",
                    action: { isShowingRedeemCode = true }
                )
                ctaRow(
                    icon: "doc.text.fill",
                    title: "Request Invoice",
                    subtitle: "Credit packages via bank transfer",
                    action: { isShowingInvoiceRequest = true }
                )
            }
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)
            .padding(.horizontal, Theme.Spacing.md)
        }
    }

    private func ctaRow(icon: String, title: String, subtitle: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: Theme.Spacing.sm) {
                Image(systemName: icon)
                    .font(.system(size: 15))
                    .foregroundColor(Theme.Color.primary)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(Theme.Color.onSurface)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.Color.muted)
            }
            .padding(.horizontal, Theme.Spacing.md)
            .padding(.vertical, 10)
        }
    }

    // MARK: — How credits work

    private var howToGetSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("HOW CREDITS WORK")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
                .kerning(0.8)
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.top, Theme.Spacing.lg)

            VStack(spacing: 1) {
                infoRow(icon: "gift.fill",       title: "Invitation Code",  detail: "Redeem an INV-* code above")
                infoRow(icon: "tag.fill",         title: "Coupon Code",     detail: "Apply a BONUS_CREDITS coupon above")
                infoRow(icon: "doc.text.fill",    title: "Invoice Payment", detail: "Request a package invoice, pay by bank transfer")
                infoRow(icon: "building.columns", title: "Admin Grant",     detail: "Credits granted by your Academy administrator")
            }
            .background(Theme.Color.surface)
            .cornerRadius(Theme.Radius.md)
            .padding(.horizontal, Theme.Spacing.md)
        }
    }

    private func infoRow(icon: String, title: String, detail: String) -> some View {
        HStack(spacing: Theme.Spacing.sm) {
            Image(systemName: icon)
                .font(.system(size: 15))
                .foregroundColor(Theme.Color.secondary)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
            }
            Spacer()
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, 10)
    }

    // MARK: — Transaction history

    @ViewBuilder
    private var transactionSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            HStack {
                Text("TRANSACTION HISTORY")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(Theme.Color.muted)
                    .kerning(0.8)
                Spacer()
                Text("Latest first")
                    .font(.system(size: 10))
                    .foregroundColor(Theme.Color.muted)
            }
            .padding(.horizontal, Theme.Spacing.md)
            .padding(.top, Theme.Spacing.lg)

            switch viewModel.loadState {
            case .loading:
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .padding(Theme.Spacing.lg)

            case .loaded(let page) where page.transactions.isEmpty:
                Text("No transactions yet.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .frame(maxWidth: .infinity)
                    .padding(Theme.Spacing.lg)

            case .loaded(let page):
                VStack(spacing: 1) {
                    ForEach(page.transactions) { tx in
                        transactionRow(tx)
                    }
                }
                .background(Theme.Color.surface)
                .cornerRadius(Theme.Radius.md)
                .padding(.horizontal, Theme.Spacing.md)

            case .error(let msg):
                Text(msg)
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                    .frame(maxWidth: .infinity)
                    .padding(Theme.Spacing.lg)

            case .idle:
                EmptyView()
            }
        }
    }

    private func transactionRow(_ tx: CreditTransaction) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                Text(tx.typeLabel)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
                if let desc = tx.description, !desc.isEmpty {
                    Text(desc)
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted)
                        .lineLimit(1)
                }
                let date = tx.formattedDate
                if !date.isEmpty {
                    Text(date)
                        .font(.caption2)
                        .foregroundColor(Theme.Color.muted.opacity(0.7))
                }
            }
            Spacer()
            Text(tx.amountDisplay)
                .font(.subheadline.weight(.bold))
                .foregroundColor(tx.isCredit ? Color(red: 0.18, green: 0.80, blue: 0.44) : Theme.Color.error)
                .padding(.top, 2)
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, 10)
    }

    // MARK: — Helpers

    private var isLoading: Bool {
        if case .loading = viewModel.loadState { return true }
        return false
    }
}
