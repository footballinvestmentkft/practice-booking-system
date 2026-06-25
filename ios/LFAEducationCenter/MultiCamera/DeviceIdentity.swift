import Foundation
import Security

enum DeviceIdentity {

    static let keychainAccount = "lfa_mc_device_uuid"

    static func stableDeviceUUID() -> String {
        if let stored = load() { return stored }
        let fresh = UUID().uuidString
        save(fresh)
        return fresh
    }

    static func logSafeIdentifier() -> String {
        let full = stableDeviceUUID()
        let suffix = full.suffix(8)
        return "...\(suffix)"
    }

    #if DEBUG
    static func resetForTesting() {
        keychainDelete()
        UserDefaults.standard.removeObject(forKey: keychainAccount)
    }
    #endif

    // MARK: - Persistent storage (Keychain primary, UserDefaults fallback)

    private static let service = "com.lovas-zoltan.lfa-education-center.device"

    private static func save(_ value: String) {
        let keychainOK = keychainSave(value)
        if !keychainOK {
            UserDefaults.standard.set(value, forKey: keychainAccount)
        }
    }

    private static func load() -> String? {
        if let kc = keychainLoad() { return kc }
        return UserDefaults.standard.string(forKey: keychainAccount)
    }

    // MARK: - Keychain (isolated from KeychainService.clearAll)

    @discardableResult
    private static func keychainSave(_ value: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }
        let query: [String: Any] = [
            kSecClass          as String: kSecClassGenericPassword,
            kSecAttrService    as String: service,
            kSecAttrAccount    as String: keychainAccount,
            kSecValueData      as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        SecItemDelete(query as CFDictionary)
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    private static func keychainLoad() -> String? {
        let query: [String: Any] = [
            kSecClass       as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: keychainAccount,
            kSecReturnData  as String: kCFBooleanTrue as Any,
            kSecMatchLimit  as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let string = String(data: data, encoding: .utf8)
        else { return nil }
        return string
    }

    private static func keychainDelete() {
        let query: [String: Any] = [
            kSecClass       as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
