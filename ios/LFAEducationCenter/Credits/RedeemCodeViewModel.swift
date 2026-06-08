import Foundation

// MARK: — API response types

private struct CouponValidateResponse: Decodable {
    let code:          String
    let type:          String        // "BONUS_CREDITS" | "PURCHASE_DISCOUNT_PERCENT" | "PURCHASE_BONUS_CREDITS"
    let discountValue: Double        // credits (BONUS_CREDITS) or fraction (PURCHASE_DISCOUNT_*)
    let description:   String
    let valid:         Bool

    enum CodingKeys: String, CodingKey {
        case code, type, description, valid
        case discountValue = "discount_value"
    }
}

private struct InvitationValidateResponse: Decodable {
    let success:      Bool
    let valid:        Bool
    let bonusCredits: Int
    let invitedName:  String?
    let expiresAt:    String?

    enum CodingKeys: String, CodingKey {
        case success, valid
        case bonusCredits = "bonus_credits"
        case invitedName  = "invited_name"
        case expiresAt    = "expires_at"
    }
}

private struct RedeemResponse: Decodable {
    let success:      Bool?   // optional — coupon apply omits this field
    let message:      String?
    let bonusCredits: Int?
    let creditsAwarded: Int?
    let newBalance:   Int?

    // Covers both coupon apply and invitation redeem response shapes
    enum CodingKeys: String, CodingKey {
        case success, message
        case bonusCredits  = "bonus_credits"
        case creditsAwarded = "credits_awarded"
        case newBalance    = "new_balance"
    }

    var awardedAmount: Int {
        bonusCredits ?? creditsAwarded ?? 0
    }

    var resolvedBalance: Int { newBalance ?? 0 }
}

// MARK: — Preview model

struct RedeemPreview {
    let rawCode:         String
    let codeType:        RedeemCodeType
    let description:     String
    let creditsToAward:  Int?      // nil if purchase_discount type
    let requiresInvoice: Bool      // true for PURCHASE_DISCOUNT_* coupons
    let invitedName:     String?
}

enum RedeemCodeType { case coupon, invitationCode }

// MARK: — ViewModel

// Manages the two-step redeem flow: validate → preview → confirm → redeem.
//
// Smart detection:
//   INV-* prefix  → invitation code path
//   everything else → coupon path
//
// Coupon types:
//   BONUS_CREDITS           → azonnali credit jóváírás (confirm → POST /coupons/apply)
//   PURCHASE_DISCOUNT_*     → preview mutatja az infót, nincs Confirm Redeem gomb
//
// Invitation codes → POST /api/v1/invitation-codes/redeem-authenticated (Bearer)
@MainActor
final class RedeemCodeViewModel: ObservableObject {

    enum State {
        case idle
        case validating
        case preview(RedeemPreview)
        case redeeming
        case success(creditsAwarded: Int, newBalance: Int)
        case error(String)
    }

    @Published private(set) var state: State = .idle
    @Published var codeInput: String = ""

    // MARK: — Step 1: Validate

    func validate(using authManager: AuthManager) async {
        let code = codeInput.trimmingCharacters(in: .whitespaces)
        guard !code.isEmpty else {
            state = .error("Please enter a code.")
            return
        }
        state = .validating

        if code.uppercased().hasPrefix("INV-") {
            await validateInvitation(code: code, authManager: authManager)
        } else {
            await validateCoupon(code: code, authManager: authManager)
        }
    }

    // MARK: — Step 2: Confirm redeem

    func confirm(using authManager: AuthManager) async {
        guard case .preview(let preview) = state else { return }  // duplicate-tap guard
        state = .redeeming

        if preview.codeType == .invitationCode {
            await redeemInvitation(code: preview.rawCode, authManager: authManager)
        } else {
            await redeemCoupon(code: preview.rawCode, authManager: authManager)
        }
    }

    func reset() { state = .idle; codeInput = "" }
    func resetToIdle() { state = .idle }

    // MARK: — Private: validate

    private func validateCoupon(code: String, authManager: AuthManager) async {
        let encoded = code.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? code
        do {
            let resp: CouponValidateResponse = try await authManager.authenticatedPost(
                path: "/api/v1/coupons/validate/\(encoded)",
                body: [String: String]()   // code is in path; empty body expected by endpoint
            )
            guard resp.valid else {
                state = .error("This coupon code is not valid.")
                return
            }
            let requiresInvoice = resp.type != "BONUS_CREDITS"
            let credits: Int? = requiresInvoice ? nil : Int(resp.discountValue)
            state = .preview(RedeemPreview(
                rawCode:        code,
                codeType:       .coupon,
                description:    resp.description,
                creditsToAward: credits,
                requiresInvoice: requiresInvoice,
                invitedName:    nil
            ))
        } catch APIError.httpError(let c, _) where c == 404 {
            state = .error("Coupon code not found.")
        } catch APIError.httpError(let c, let d) where c == 400 {
            state = .error(d ?? "This coupon is no longer valid.")
        } catch {
            state = .error("Could not validate code. Check your connection.")
        }
    }

    private func validateInvitation(code: String, authManager: AuthManager) async {
        do {
            let resp: InvitationValidateResponse = try await authManager.authenticatedPost(
                path: "/api/v1/invitation-codes/validate",
                body: ["code": code]
            )
            guard resp.valid else {
                state = .error("This invitation code is not valid.")
                return
            }
            state = .preview(RedeemPreview(
                rawCode:        code,
                codeType:       .invitationCode,
                description:    "Invitation bonus — \(resp.bonusCredits) credits",
                creditsToAward: resp.bonusCredits,
                requiresInvoice: false,
                invitedName:    resp.invitedName
            ))
        } catch APIError.httpError(let c, _) where c == 404 {
            state = .error("Invitation code not found.")
        } catch APIError.httpError(let c, let d) where c == 400 {
            state = .error(d ?? "This invitation code is no longer valid.")
        } catch {
            state = .error("Could not validate code. Check your connection.")
        }
    }

    // MARK: — Private: redeem

    private func redeemCoupon(code: String, authManager: AuthManager) async {
        do {
            let resp: RedeemResponse = try await authManager.authenticatedPost(
                path: "/api/v1/coupons/apply",
                body: ["code": code]
            )
            state = .success(creditsAwarded: resp.awardedAmount, newBalance: resp.resolvedBalance)
        } catch APIError.httpError(let c, let d) where c == 400 {
            state = .error(d ?? "This coupon could not be applied.")
        } catch APIError.httpError(let c, let d) where c == 409 {
            state = .error(d ?? "This coupon has already been used.")
        } catch APIError.unauthorized {
            state = .error("Session expired. Please sign in again.")
        } catch {
            state = .error("Network error. Please try again.")
        }
    }

    private func redeemInvitation(code: String, authManager: AuthManager) async {
        do {
            let resp: RedeemResponse = try await authManager.authenticatedPost(
                path: "/api/v1/invitation-codes/redeem-authenticated",
                body: ["code": code]
            )
            state = .success(creditsAwarded: resp.awardedAmount, newBalance: resp.resolvedBalance)
        } catch APIError.httpError(let c, let d) where c == 400 {
            state = .error(d ?? "This invitation code could not be redeemed.")
        } catch APIError.httpError(let c, _) where c == 403 {
            state = .error("This code is restricted to a different email address.")
        } catch APIError.httpError(let c, let d) where c == 404 {
            state = .error(d ?? "Invitation code not found.")
        } catch APIError.unauthorized {
            state = .error("Session expired. Please sign in again.")
        } catch {
            state = .error("Network error. Please try again.")
        }
    }
}
