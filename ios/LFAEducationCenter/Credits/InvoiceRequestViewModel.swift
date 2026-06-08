import Foundation

// Credit packages — mirrors credits.html and POST /api/v1/users/request-invoice package_type values.
enum CreditPackage: String, CaseIterable, Identifiable {
    case starter      = "PACKAGE_100"
    case professional = "PACKAGE_250"
    case premium      = "PACKAGE_500"
    case enterprise   = "PACKAGE_1000"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .starter:      return "Starter"
        case .professional: return "Professional"
        case .premium:      return "Premium"
        case .enterprise:   return "Enterprise"
        }
    }

    var priceEur: Int {
        switch self {
        case .starter:      return 100
        case .professional: return 250
        case .premium:      return 500
        case .enterprise:   return 1_000
        }
    }

    var credits: Int {
        switch self {
        case .starter:      return 500
        case .professional: return 1_400
        case .premium:      return 3_000
        case .enterprise:   return 6_500
        }
    }

    var priceLabel: String   { "€\(priceEur)" }
    var creditsLabel: String { "\(credits) CR" }
}

// MARK: — API types

private struct InvoiceRequestBody: Encodable {
    let packageType:          String
    let specializationType:   String

    enum CodingKeys: String, CodingKey {
        case packageType        = "package_type"
        case specializationType = "specialization_type"
    }
}

struct InvoiceResult {
    let paymentReference: String
    let amountEur:        Double
    let creditAmount:     Int
    let status:           String
}

private struct InvoiceResponse: Decodable {
    let id:               Int?
    let paymentReference: String?
    let amountEur:        Double?
    let creditAmount:     Int?
    let status:           String?
    let message:          String?

    enum CodingKeys: String, CodingKey {
        case id, status, message
        case paymentReference = "payment_reference"
        case amountEur        = "amount_eur"
        case creditAmount     = "credit_amount"
    }
}

// MARK: — ViewModel

// Manages credit package selection and invoice request.
//
// API: POST /api/v1/users/request-invoice (Bearer JSON)
// Request: { "package_type": "PACKAGE_500", "specialization_type": "LFA_FOOTBALL_PLAYER" }
// Response: payment_reference, amount_eur, credit_amount, status="pending"
//
// IMPORTANT: No immediate credit jóváírás — admin verifies bank transfer, then credits added.
@MainActor
final class InvoiceRequestViewModel: ObservableObject {

    enum State {
        case idle
        case loading
        case success(InvoiceResult)
        case error(String)
    }

    @Published var selectedPackage: CreditPackage = .professional
    @Published private(set) var state: State = .idle

    func request(using authManager: AuthManager) async {
        guard case .idle = state else { return }
        state = .loading

        let body = InvoiceRequestBody(
            packageType:        selectedPackage.rawValue,
            specializationType: "LFA_FOOTBALL_PLAYER"
        )

        do {
            let resp: InvoiceResponse = try await authManager.authenticatedPost(
                path: "/api/v1/users/request-invoice",
                body: body
            )
            guard let ref = resp.paymentReference,
                  let eur = resp.amountEur,
                  let cr  = resp.creditAmount else {
                state = .error("Unexpected response from server. Please try again.")
                return
            }
            state = .success(InvoiceResult(
                paymentReference: ref,
                amountEur:        eur,
                creditAmount:     cr,
                status:           resp.status ?? "pending"
            ))
        } catch APIError.httpError(let code, let detail) where code == 409 {
            state = .error(detail ?? "You already have a pending invoice. Complete your existing payment first.")
        } catch APIError.httpError(let code, let detail) where code == 400 || code == 422 {
            state = .error(detail ?? "Invalid package selection. Please try again.")
        } catch APIError.unauthorized {
            state = .error("Session expired. Please sign in again.")
        } catch {
            state = .error("Network error. Please check your connection and try again.")
        }
    }

    func reset() { state = .idle }
}
