import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var authManager: AuthManager

    @State private var email:             String = ""
    @State private var password:          String = ""
    @State private var isShowingRegister: Bool   = false

    private var isSubmittable: Bool {
        !email.trimmingCharacters(in: .whitespaces).isEmpty &&
        !password.isEmpty &&
        !authManager.isLoading
    }

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            // Brand header
            VStack(spacing: Theme.Spacing.md) {
                BrandLogoView()
                    .frame(maxWidth: 220)
                    .padding(.horizontal, Theme.Spacing.xl)
                Text("Sign in to continue")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }

            Spacer()

            // Form fields
            VStack(spacing: Theme.Spacing.md) {
                TextField("Email", text: $email)
                    .keyboardType(.emailAddress)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                    .textContentType(.emailAddress)
                    .padding()
                    .background(Theme.Color.surface)
                    .cornerRadius(Theme.Radius.sm)

                SecureField("Password", text: $password)
                    .textContentType(.password)
                    .padding()
                    .background(Theme.Color.surface)
                    .cornerRadius(Theme.Radius.sm)

                // Error message — visible only when authManager.errorMessage is set
                if let error = authManager.errorMessage {
                    Text(error)
                        .font(.footnote)
                        .foregroundColor(Theme.Color.error)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Theme.Spacing.xs)
                }

                // Login button
                Button {
                    dismissKeyboard()
                    Task {
                        await authManager.login(
                            email: email.trimmingCharacters(in: .whitespaces),
                            password: password
                        )
                    }
                } label: {
                    ZStack {
                        if authManager.isLoading {
                            ProgressView()
                                .progressViewStyle(CircularProgressViewStyle(tint: .white))
                        } else {
                            Text("Sign In")
                                .fontWeight(.semibold)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(isSubmittable ? Theme.Color.primary : Theme.Color.muted)
                    .foregroundColor(.white)
                    .cornerRadius(Theme.Radius.sm)
                }
                .disabled(!isSubmittable)
            }
            .padding(.horizontal, Theme.Spacing.xl)

            // Create account link
            Button {
                isShowingRegister = true
            } label: {
                Text("Don't have an account? ")
                    .foregroundColor(Theme.Color.muted) +
                Text("Create Account")
                    .foregroundColor(Theme.Color.primary)
                    .bold()
            }
            .font(.subheadline)
            .padding(.top, Theme.Spacing.sm)

            Spacer()
        }
        .background(Theme.Color.background.ignoresSafeArea())
        .fullScreenCover(isPresented: $isShowingRegister) {
            RegisterView()
        }
    }

    // Works on iOS 14+ without @FocusState (iOS 15+ only).
    private func dismissKeyboard() {
        UIApplication.shared.sendAction(
            #selector(UIResponder.resignFirstResponder),
            to: nil, from: nil, for: nil
        )
    }
}
