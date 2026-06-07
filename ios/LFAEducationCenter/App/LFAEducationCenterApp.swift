import SwiftUI

@main
struct LFAEducationCenterApp: App {
    @StateObject private var authManager = AuthManager()
    @StateObject private var dashboardVM = DashboardViewModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
                // Validate existing session on every cold launch.
                // AuthManager.init() sets isLoggedIn optimistically from Keychain;
                // validateSession() corrects it if tokens are expired or revoked.
                .onAppear {
                    Task { await authManager.validateSession() }
                }
                // Reset dashboard data on logout so next login fetches fresh data.
                .onChange(of: authManager.isLoggedIn) { isLoggedIn in
                    if !isLoggedIn { dashboardVM.reset() }
                }
        }
    }
}
