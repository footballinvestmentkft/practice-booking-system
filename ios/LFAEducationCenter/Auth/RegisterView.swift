import SwiftUI

// 4-step registration form for POST /api/v1/auth/register-with-invitation.
// Presented as fullScreenCover from LoginView.
// All fields required by the backend — validation is per-step before advancing.
// On success: AuthManager.isLoggedIn = true → RootView → MainHubView.
struct RegisterView: View {
    @EnvironmentObject private var authManager: AuthManager
    @Environment(\.presentationMode) private var presentationMode

    // Step tracker (0-3)
    @State private var step = 0

    // Step 0 — Account
    @State private var email    = ""
    @State private var password = ""
    @State private var firstName = ""
    @State private var lastName  = ""
    @State private var nickname  = ""

    // Step 1 — Personal
    @State private var phone = ""
    @State private var dateOfBirth: Date = Calendar.current.date(
        byAdding: .year, value: -16, to: Date()
    ) ?? Date()
    @State private var nationality = "HU"   // ISO 2-letter code; backend accepts free string
    @State private var gender = "Male"

    // (code, display label) pairs sent to backend as the code value.
    private let nationalityOptions: [(String, String)] = [
        ("HU", "🇭🇺 Hungarian"),
        ("AT", "🇦🇹 Austrian"),
        ("DE", "🇩🇪 German"),
        ("SK", "🇸🇰 Slovak"),
        ("RO", "🇷🇴 Romanian"),
        ("RS", "🇷🇸 Serbian"),
        ("HR", "🇭🇷 Croatian"),
        ("SI", "🇸🇮 Slovenian"),
        ("UA", "🇺🇦 Ukrainian"),
        ("PL", "🇵🇱 Polish"),
        ("CZ", "🇨🇿 Czech"),
        ("Other", "🌐 Other"),
    ]

    // Step 2 — Address
    @State private var streetAddress = ""
    @State private var city          = ""
    @State private var postalCode    = ""
    @State private var country       = ""

    // Step 3 — Invitation
    @State private var invitationCode = ""

    private let stepTitles = ["Account", "Personal", "Address", "Invitation"]
    private let maxDate    = Calendar.current.date(byAdding: .year, value: -5, to: Date()) ?? Date()

    var body: some View {
        VStack(spacing: 0) {
            // Navigation bar
            headerBar

            // Step indicator
            stepIndicator
                .padding(.vertical, Theme.Spacing.md)

            // Scrollable fields
            ScrollView {
                VStack(spacing: Theme.Spacing.md) {
                    switch step {
                    case 0: accountStep
                    case 1: personalStep
                    case 2: addressStep
                    default: invitationStep
                    }
                }
                .padding(.horizontal, Theme.Spacing.xl)
                .padding(.bottom, Theme.Spacing.xl)
            }

            Divider()

            // Next / Back / Submit
            navigationButtons
                .padding(Theme.Spacing.xl)
        }
        .background(Theme.Color.background.ignoresSafeArea())
        .onAppear { authManager.errorMessage = nil }
    }

    // MARK: — Header

    private var headerBar: some View {
        HStack {
            Button("Cancel") { presentationMode.wrappedValue.dismiss() }
                .foregroundColor(Theme.Color.primary)
            Spacer()
            Text("Create Account")
                .font(.headline)
                .foregroundColor(Theme.Color.onSurface)
            Spacer()
            // Invisible balance button for centering
            Text("Cancel").foregroundColor(.clear)
        }
        .padding(.horizontal, Theme.Spacing.xl)
        .padding(.top, Theme.Spacing.md)
    }

    // MARK: — Step indicator

    private var stepIndicator: some View {
        VStack(spacing: Theme.Spacing.xs) {
            HStack(spacing: Theme.Spacing.sm) {
                ForEach(0..<4) { i in
                    Circle()
                        .fill(i <= step ? Theme.Color.primary : Theme.Color.muted.opacity(0.25))
                        .frame(width: 8, height: 8)
                }
            }
            Text("Step \(step + 1) of 4 — \(stepTitles[step])")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
        }
    }

    // MARK: — Step 0: Account

    private var accountStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            formField("Email", text: $email, keyboard: .emailAddress, autocap: false)
            secureFormField("Password (min. 6 characters)", text: $password)
            formField("First Name", text: $firstName)
            formField("Last Name", text: $lastName)
            formField("Nickname", text: $nickname)
        }
    }

    // MARK: — Step 1: Personal

    private var personalStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            formField("Phone (e.g. +36201234567)", text: $phone, keyboard: .phonePad)

            VStack(alignment: .leading, spacing: 6) {
                Text("Date of Birth")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                DatePicker(
                    "",
                    selection: $dateOfBirth,
                    in: ...maxDate,
                    displayedComponents: .date
                )
                .datePickerStyle(.compact)
                .labelsHidden()
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.vertical, Theme.Spacing.sm)
                .background(Theme.Color.surface)
                .cornerRadius(Theme.Radius.sm)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Nationality")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                Picker("Nationality", selection: $nationality) {
                    ForEach(nationalityOptions, id: \.0) { code, label in
                        Text(label).tag(code)
                    }
                }
                .pickerStyle(.menu)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.vertical, Theme.Spacing.sm)
                .background(Theme.Color.surface)
                .cornerRadius(Theme.Radius.sm)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Gender")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                Picker("Gender", selection: $gender) {
                    Text("Male").tag("Male")
                    Text("Female").tag("Female")
                    Text("Other").tag("Other")
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, Theme.Spacing.md)
                .padding(.vertical, Theme.Spacing.sm)
                .background(Theme.Color.surface)
                .cornerRadius(Theme.Radius.sm)
            }
        }
    }

    // MARK: — Step 2: Address

    private var addressStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            formField("Street Address", text: $streetAddress)
            formField("City", text: $city)
            formField("Postal Code", text: $postalCode, keyboard: .numbersAndPunctuation)
            formField("Country (e.g. Hungary)", text: $country)
        }
    }

    // MARK: — Step 3: Invitation

    private var invitationStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Enter your invitation code to create an account.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }

            formField("Invitation Code", text: $invitationCode, autocap: true)

            if let error = authManager.errorMessage {
                Text(error)
                    .font(.footnote)
                    .foregroundColor(Theme.Color.error)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.xs)
            }
        }
    }

    // MARK: — Navigation buttons

    private var navigationButtons: some View {
        HStack(spacing: Theme.Spacing.md) {
            if step > 0 {
                Button("← Back") {
                    authManager.errorMessage = nil
                    step -= 1
                }
                .font(.body.weight(.medium))
                .foregroundColor(Theme.Color.muted)
            }

            Spacer()

            Button(step < 3 ? "Next →" : "Create Account") {
                if step < 3 {
                    step += 1
                } else {
                    submitRegistration()
                }
            }
            .font(.body.weight(.semibold))
            .foregroundColor(canProceed ? Theme.Color.primary : Theme.Color.muted)
            .disabled(!canProceed)
        }
    }

    // MARK: — Validation

    private var canProceed: Bool {
        switch step {
        case 0: return isStep0Valid
        case 1: return isStep1Valid
        case 2: return isStep2Valid
        case 3: return isStep3Valid && !authManager.isLoading
        default: return false
        }
    }

    private var isStep0Valid: Bool {
        isValidEmail(email) &&
        password.count >= 6 &&
        !firstName.trimmingCharacters(in: .whitespaces).isEmpty &&
        !lastName.trimmingCharacters(in: .whitespaces).isEmpty &&
        !nickname.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var isStep1Valid: Bool {
        !phone.trimmingCharacters(in: .whitespaces).isEmpty &&
        !nationality.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var isStep2Valid: Bool {
        !streetAddress.trimmingCharacters(in: .whitespaces).isEmpty &&
        !city.trimmingCharacters(in: .whitespaces).isEmpty &&
        !postalCode.trimmingCharacters(in: .whitespaces).isEmpty &&
        !country.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var isStep3Valid: Bool {
        !invitationCode.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func isValidEmail(_ value: String) -> Bool {
        value.contains("@") && value.contains(".")
    }

    // MARK: — Submit

    private func submitRegistration() {
        dismissKeyboard()
        let dob = formattedDate(dateOfBirth)
        Task {
            await authManager.register(
                email: email.trimmingCharacters(in: .whitespaces),
                password: password,
                firstName: firstName.trimmingCharacters(in: .whitespaces),
                lastName: lastName.trimmingCharacters(in: .whitespaces),
                nickname: nickname.trimmingCharacters(in: .whitespaces),
                phone: phone.trimmingCharacters(in: .whitespaces),
                dateOfBirth: dob,
                nationality: nationality.trimmingCharacters(in: .whitespaces),
                gender: gender,
                streetAddress: streetAddress.trimmingCharacters(in: .whitespaces),
                city: city.trimmingCharacters(in: .whitespaces),
                postalCode: postalCode.trimmingCharacters(in: .whitespaces),
                country: country.trimmingCharacters(in: .whitespaces),
                invitationCode: invitationCode.trimmingCharacters(in: .whitespaces).uppercased()
            )
            // On success: authManager.isLoggedIn = true → RootView switches to MainHubView.
            // The fullScreenCover disappears automatically when LoginView leaves the hierarchy.
        }
    }

    // MARK: — Helpers

    // ISO 8601 date string expected by backend datetime field.
    private func formattedDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: date)
    }

    private func dismissKeyboard() {
        UIApplication.shared.sendAction(
            #selector(UIResponder.resignFirstResponder),
            to: nil, from: nil, for: nil
        )
    }
}

// MARK: — Form field helpers

private func formField(
    _ placeholder: String,
    text: Binding<String>,
    keyboard: UIKeyboardType = .default,
    autocap: Bool = true
) -> some View {
    TextField(placeholder, text: text)
        .keyboardType(keyboard)
        .autocapitalization(autocap ? .words : .none)
        .disableAutocorrection(!autocap)
        .padding()
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.sm)
}

private func secureFormField(_ placeholder: String, text: Binding<String>) -> some View {
    SecureField(placeholder, text: text)
        .textContentType(.newPassword)
        .padding()
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.sm)
}
