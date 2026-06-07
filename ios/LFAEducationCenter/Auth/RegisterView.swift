import SwiftUI

// Academy Dialogue + ID Builder Hybrid registration.
//
// Flow: Intro → Step 0–4 → submitRegistration() → WelcomeSuccessView
// step == -1: intro screen (no card shown)
// step  0–4:  question-based enrollment with live AcademyIDCardView
//
// All backend fields are preserved — RegisterRequest and
// POST /api/v1/auth/register-with-invitation are unchanged.
struct RegisterView: View {
    @EnvironmentObject private var authManager: AuthManager
    @Environment(\.presentationMode) private var presentationMode

    // Navigation
    @State private var step:           Int  = -1   // -1 = intro
    @State private var isGoingForward: Bool = true

    // Step 0 — Identity
    @State private var firstName = ""
    @State private var lastName  = ""
    @State private var nickname  = ""

    // Step 1 — Profile
    @State private var dateOfBirth: Date = Calendar.current.date(
        byAdding: .year, value: -16, to: Date()
    ) ?? Date()
    @State private var nationality = "HU"
    @State private var gender      = "Male"

    // Step 2 — Contact
    @State private var phone    = ""
    @State private var email    = ""
    @State private var password = ""

    // Step 3 — Location
    @State private var streetAddress = ""
    @State private var city          = ""
    @State private var postalCode    = ""
    @State private var country       = ""

    // Step 4 — Invitation
    @State private var invitationCode = ""

    // Visual-only LFA-ID generated once per registration session
    @State private var lfaDisplayID = String(format: "%06d", Int.random(in: 100_000...999_999))

    private let maxDate = Calendar.current.date(byAdding: .year, value: -5, to: Date()) ?? Date()

    private let nationalityOptions: [(String, String)] = [
        ("HU", "🇭🇺 Hungarian"), ("AT", "🇦🇹 Austrian"), ("DE", "🇩🇪 German"),
        ("SK", "🇸🇰 Slovak"),   ("RO", "🇷🇴 Romanian"), ("RS", "🇷🇸 Serbian"),
        ("HR", "🇭🇷 Croatian"), ("SI", "🇸🇮 Slovenian"), ("UA", "🇺🇦 Ukrainian"),
        ("PL", "🇵🇱 Polish"),   ("CZ", "🇨🇿 Czech"),   ("Other", "🌐 Other"),
    ]

    private let stepMeta: [(title: String, subtitle: String)] = [
        ("What should we call you?",
         "Your name will appear on your Academy profile."),
        ("Tell us about yourself.",
         "This helps us personalise your Academy experience."),
        ("How can the academy reach you?",
         "Your contact information stays private."),
        ("Where will you be joining from?",
         "Your location helps us connect you to local sessions."),
        ("You were personally invited.",
         "Enter your invitation code to complete enrolment."),
    ]

    // MARK: — Body

    var body: some View {
        Group {
            if step == -1 {
                introScreen
            } else {
                enrollmentScreen
            }
        }
        .onAppear { authManager.errorMessage = nil }
    }

    // MARK: — Intro screen

    private var introScreen: some View {
        VStack(spacing: 0) {
            HStack {
                Button("Cancel") { presentationMode.wrappedValue.dismiss() }
                    .foregroundColor(Theme.Color.muted)
                Spacer()
            }
            .padding(.horizontal, Theme.Spacing.xl)
            .padding(.top, Theme.Spacing.md)

            Spacer()

            VStack(spacing: Theme.Spacing.lg) {
                BrandLogoView()
                    .frame(maxWidth: 130)

                VStack(spacing: Theme.Spacing.sm) {
                    Text("Welcome to the Academy.")
                        .font(.title2.weight(.bold))
                        .foregroundColor(Theme.Color.onSurface)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)

                    Text("Let's build your LFA profile.")
                        .font(.subheadline)
                        .foregroundColor(Theme.Color.muted)
                        .multilineTextAlignment(.center)

                    Text("This will only take a few minutes.")
                        .font(.caption)
                        .foregroundColor(Theme.Color.muted.opacity(0.65))
                }
            }
            .padding(.horizontal, Theme.Spacing.xl)

            Spacer()

            Button {
                advance(to: 0)
            } label: {
                Text("Begin →")
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(Theme.Color.primary)
                    .foregroundColor(.white)
                    .cornerRadius(Theme.Radius.md)
            }
            .padding(.horizontal, Theme.Spacing.xl)
            .padding(.bottom, Theme.Spacing.xl)
        }
        .background(Theme.Color.background.ignoresSafeArea())
    }

    // MARK: — Enrollment screen (steps 0–4)

    private var enrollmentScreen: some View {
        VStack(spacing: 0) {
            topBar

            // Animated progress bar
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Theme.Color.muted.opacity(0.15))
                        .frame(height: 3)
                    Capsule()
                        .fill(Theme.Color.primary)
                        .frame(
                            width: geo.size.width * Double(step + 1) / 5.0,
                            height: 3
                        )
                        .animation(.easeInOut(duration: 0.3), value: step)
                }
            }
            .frame(height: 3)
            .padding(.horizontal, Theme.Spacing.xl)
            .padding(.bottom, Theme.Spacing.sm)

            // Scrollable content: ID card + step
            ScrollView {
                VStack(spacing: Theme.Spacing.md) {
                    // Academy ID live preview
                    AcademyIDCardView(
                        firstName:   opt(firstName),
                        lastName:    opt(lastName),
                        nickname:    opt(nickname),
                        age:         computedAge,
                        nationality: nationality,
                        gender:      gender,
                        city:        opt(city),
                        country:     opt(country),
                        isInvited:   invitationCode.trimmingCharacters(in: .whitespaces).count >= 8,
                        lfaID:       lfaDisplayID
                    )
                    .padding(.horizontal, Theme.Spacing.md)

                    Divider()

                    // Step header + fields
                    VStack(alignment: .leading, spacing: Theme.Spacing.md) {
                        stepHeader
                        currentStepContent
                            .id(step)
                            .transition(currentTransition)
                    }
                    .padding(.horizontal, Theme.Spacing.xl)
                    .padding(.bottom, Theme.Spacing.xl)
                }
            }

            Divider()
            navigationBar
                .padding(.horizontal, Theme.Spacing.xl)
                .padding(.vertical, Theme.Spacing.md)
        }
        .background(Theme.Color.background.ignoresSafeArea())
    }

    // MARK: — Top bar

    private var topBar: some View {
        HStack {
            Button("Cancel") {
                authManager.errorMessage = nil
                presentationMode.wrappedValue.dismiss()
            }
            .foregroundColor(Theme.Color.muted)

            Spacer()

            Text("Academy Enrolment")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.onSurface)

            Spacer()

            Text("Cancel").foregroundColor(.clear) // centering balance
        }
        .padding(.horizontal, Theme.Spacing.xl)
        .padding(.top, Theme.Spacing.md)
        .padding(.bottom, Theme.Spacing.sm)
    }

    // MARK: — Step header

    private var stepHeader: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Step \(step + 1) of 5")
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
            Text(stepMeta[step].title)
                .font(.title3.weight(.bold))
                .foregroundColor(Theme.Color.onSurface)
                .fixedSize(horizontal: false, vertical: true)
            Text(stepMeta[step].subtitle)
                .font(.caption)
                .foregroundColor(Theme.Color.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: — Step content router

    @ViewBuilder
    private var currentStepContent: some View {
        switch step {
        case 0: identityStep
        case 1: profileStep
        case 2: contactStep
        case 3: locationStep
        default: invitationStep
        }
    }

    // MARK: — Step 0: Identity

    private var identityStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            regField("First Name", text: $firstName)
            regField("Last Name", text: $lastName)
            regField("Nickname", text: $nickname)
        }
    }

    // MARK: — Step 1: Profile

    private var profileStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Date of Birth")
                    .font(.caption)
                    .foregroundColor(Theme.Color.muted)
                DatePicker("", selection: $dateOfBirth, in: ...maxDate, displayedComponents: .date)
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

    // MARK: — Step 2: Contact

    private var contactStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            regField("Phone (e.g. +36201234567)", text: $phone, keyboard: .phonePad)
            regField("Email", text: $email, keyboard: .emailAddress, autocap: false)
            regSecureField("Password (min. 6 characters)", text: $password)
        }
    }

    // MARK: — Step 3: Location

    private var locationStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            regField("Street Address", text: $streetAddress)
            regField("City", text: $city)
            regField("Postal Code", text: $postalCode, keyboard: .numbersAndPunctuation)
            regField("Country (e.g. Hungary)", text: $country)
        }
    }

    // MARK: — Step 4: Invitation

    private var invitationStep: some View {
        VStack(spacing: Theme.Spacing.md) {
            if invitationCode.trimmingCharacters(in: .whitespaces).count >= 8 {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(Theme.Color.primary)
                    Text("Invitation code ready.")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(Theme.Color.primary)
                }
                .transition(.opacity.combined(with: .scale(scale: 0.9, anchor: .leading)))
            }

            regField("Invitation Code", text: $invitationCode, autocap: true)

            if let error = authManager.errorMessage {
                Text(error)
                    .font(.footnote)
                    .foregroundColor(Theme.Color.error)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: invitationCode.count >= 8)
    }

    // MARK: — Navigation bar

    private var navigationBar: some View {
        HStack(spacing: Theme.Spacing.md) {
            Button("← Back") {
                authManager.errorMessage = nil
                retreat()
            }
            .font(.body.weight(.medium))
            .foregroundColor(Theme.Color.muted)

            Spacer()

            if step < 4 {
                Button("Next →") { advance(to: step + 1) }
                    .font(.body.weight(.semibold))
                    .foregroundColor(canProceed ? Theme.Color.primary : Theme.Color.muted)
                    .disabled(!canProceed)
            } else {
                Button(authManager.isLoading ? "Joining..." : "Join the Academy") {
                    submitRegistration()
                }
                .font(.body.weight(.semibold))
                .foregroundColor(canProceed ? Theme.Color.primary : Theme.Color.muted)
                .disabled(!canProceed)
            }
        }
    }

    // MARK: — Validation

    private var canProceed: Bool {
        switch step {
        case 0: return isStep0Valid
        case 1: return true  // all fields have defaults
        case 2: return isStep2Valid
        case 3: return isStep3Valid
        case 4: return isStep4Valid && !authManager.isLoading
        default: return false
        }
    }

    private var isStep0Valid: Bool {
        !firstName.trimmingCharacters(in: .whitespaces).isEmpty &&
        !lastName.trimmingCharacters(in: .whitespaces).isEmpty &&
        !nickname.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var isStep2Valid: Bool {
        isValidEmail(email) &&
        password.count >= 6 &&
        !phone.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var isStep3Valid: Bool {
        !streetAddress.trimmingCharacters(in: .whitespaces).isEmpty &&
        !city.trimmingCharacters(in: .whitespaces).isEmpty &&
        !postalCode.trimmingCharacters(in: .whitespaces).isEmpty &&
        !country.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var isStep4Valid: Bool {
        !invitationCode.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func isValidEmail(_ s: String) -> Bool {
        s.contains("@") && s.contains(".")
    }

    // MARK: — Navigation helpers

    private func advance(to target: Int) {
        isGoingForward = true
        withAnimation(.easeInOut(duration: 0.28)) { step = target }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }

    private func retreat() {
        isGoingForward = false
        withAnimation(.easeInOut(duration: 0.28)) { step -= 1 }
    }

    private var currentTransition: AnyTransition {
        isGoingForward
            ? .asymmetric(
                insertion: .move(edge: .bottom).combined(with: .opacity),
                removal:   .move(edge: .top).combined(with: .opacity)
              )
            : .asymmetric(
                insertion: .move(edge: .top).combined(with: .opacity),
                removal:   .move(edge: .bottom).combined(with: .opacity)
              )
    }

    // MARK: — Submit

    private func submitRegistration() {
        dismissKeyboard()
        let dob = formattedDate(dateOfBirth)
        Task {
            await authManager.register(
                email:         email.trimmingCharacters(in: .whitespaces),
                password:      password,
                firstName:     firstName.trimmingCharacters(in: .whitespaces),
                lastName:      lastName.trimmingCharacters(in: .whitespaces),
                nickname:      nickname.trimmingCharacters(in: .whitespaces),
                phone:         phone.trimmingCharacters(in: .whitespaces),
                dateOfBirth:   dob,
                nationality:   nationality,
                gender:        gender,
                streetAddress: streetAddress.trimmingCharacters(in: .whitespaces),
                city:          city.trimmingCharacters(in: .whitespaces),
                postalCode:    postalCode.trimmingCharacters(in: .whitespaces),
                country:       country.trimmingCharacters(in: .whitespaces),
                invitationCode: invitationCode.trimmingCharacters(in: .whitespaces).uppercased()
            )
        }
    }

    // MARK: — Computed helpers for card

    private var computedAge: Int? {
        Calendar.current.dateComponents([.year], from: dateOfBirth, to: Date()).year
    }

    // Returns nil for empty/whitespace-only strings so the card shows "———"
    private func opt(_ s: String) -> String? {
        let t = s.trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : t
    }

    // MARK: — Utility

    private func formattedDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        f.timeZone   = TimeZone(identifier: "UTC")
        return f.string(from: date)
    }

    private func dismissKeyboard() {
        UIApplication.shared.sendAction(
            #selector(UIResponder.resignFirstResponder),
            to: nil, from: nil, for: nil
        )
    }
}

// MARK: — Form field helpers (file-private)

private func regField(
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
        .background(Color(UIColor.secondarySystemBackground))
        .cornerRadius(8)
}

private func regSecureField(_ placeholder: String, text: Binding<String>) -> some View {
    SecureField(placeholder, text: text)
        .textContentType(.newPassword)
        .padding()
        .background(Color(UIColor.secondarySystemBackground))
        .cornerRadius(8)
}
