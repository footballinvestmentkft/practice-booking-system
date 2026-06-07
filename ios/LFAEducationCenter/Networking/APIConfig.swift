import Foundation

// Single source of truth for the backend base URL.
// Change here only — never scatter URLs across files.
//
// Simulator:       http://localhost:8000
// Physical iPhone: http://<Mac-LAN-IP>:8000   (run `ifconfig | grep "inet " | grep -v 127` on Mac)
// Staging:         https://staging.your-domain.com
// Production:      https://your-domain.com
enum APIConfig {
    // Simulator:       http://localhost:8000
    // Physical iPhone: http://192.168.1.129:8000  ← current Mac LAN IP
    static let baseURL = "http://192.168.1.129:8000"

    // Versioned path prefix — matches backend API_V1_STR.
    static let v1 = baseURL + "/api/v1"
}
