import CoreImage
import UIKit

// Generates a QR code UIImage from an arbitrary string using CoreImage.
// No external dependencies — CIQRCodeGenerator is available since iOS 7.
//
// Usage:
//   let img = QRCodeGenerator.image(from: "https://lfa.hu/verify/...")
//
// The output is a square black-on-white UIImage, scaled 10× to avoid
// pixelation when rendered at small sizes (e.g. the 56×56 pt card panel).
enum QRCodeGenerator {
    static func image(from string: String, scale: CGFloat = 10) -> UIImage? {
        guard
            let data   = string.data(using: .ascii),
            let filter = CIFilter(name: "CIQRCodeGenerator")
        else { return nil }

        filter.setValue(data,  forKey: "inputMessage")
        filter.setValue("M",   forKey: "inputCorrectionLevel") // ~15 % ECC

        guard let output = filter.outputImage else { return nil }

        let transform = CGAffineTransform(scaleX: scale, y: scale)
        let scaled    = output.transformed(by: transform)

        return UIImage(ciImage: scaled)
    }
}
