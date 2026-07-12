import SwiftUI

@main
struct LFAEducationCenterApp: App {
    @StateObject private var authManager  = AuthManager()
    @StateObject private var dashboardVM  = DashboardViewModel()
    @StateObject private var educationVM  = EducationViewModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(authManager)
                .environmentObject(dashboardVM)
                .environmentObject(educationVM)
                // Validate existing session on every cold launch.
                // AuthManager.init() sets isLoggedIn optimistically from Keychain;
                // validateSession() corrects it if tokens are expired or revoked.
                .onAppear {
                    Task { await authManager.validateSession() }
                }
                // Reset all view-model data on logout so next login fetches fresh.
                .onChange(of: authManager.isLoggedIn) { isLoggedIn in
                    if !isLoggedIn {
                        dashboardVM.reset()
                        educationVM.reset()
                    }
                }
                #if DEBUG
                // MC1-AUTO-1: routes lfa-mc1:// deep links to the automation bridge
                // so a script can drive the Session Lab without manual taps.
                .onOpenURL { url in
                    MC1AutomationBridge.shared.handle(url: url)
                }
                #endif
        }
    }
}
