import Foundation

// Persists whether the profile-completion celebration screen has been shown
// for a given user.
//
// Key design rules:
//   - Keyed by userId so multiple users on the same device each see it once.
//   - markSeen() is called ONLY when the user taps "Continue" at the end of the
//     celebration screen — never when the screen first appears.  This ensures
//     that if the app is force-killed mid-animation the screen re-appears on
//     the next launch.
//   - hasBeenSeen() returns false for any userId not yet stored (new user,
//     fresh install, first completion).
enum CompletionCelebrationStore {

    static func hasBeenSeen(forUserId userId: Int) -> Bool {
        UserDefaults.standard.bool(forKey: _key(userId))
    }

    // Call only after the user explicitly taps the final CTA.
    static func markSeen(forUserId userId: Int) {
        UserDefaults.standard.set(true, forKey: _key(userId))
    }

    // Testing / admin reset — not exposed in production UI.
    static func reset(forUserId userId: Int) {
        UserDefaults.standard.removeObject(forKey: _key(userId))
    }

    private static func _key(_ userId: Int) -> String {
        "profileCompletion.celebrationSeen.\(userId)"
    }
}
