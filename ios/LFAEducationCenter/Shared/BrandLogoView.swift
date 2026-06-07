import SwiftUI

// Displays the LFA Education Center logo from Assets.xcassets/LFALogo.imageset.
// The asset catalog serves logo-dark.png (light mode) and logo-light.png (dark mode)
// automatically — SwiftUI picks the correct variant based on the active color scheme.
//
// Usage:
//   BrandLogoView()             — natural size, caller constrains width
//   BrandLogoView().frame(maxWidth: 200)
struct BrandLogoView: View {
    var body: some View {
        Image("LFALogo")
            .resizable()
            .scaledToFit()
            .accessibilityLabel("LFA Education Center logo")
    }
}
