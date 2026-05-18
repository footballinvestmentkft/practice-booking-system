#!/usr/bin/env bash
# download_fonts.sh — Self-hosted font acquisition for FIFA Classic export templates
#
# Downloads 9 woff2 font files from Google Fonts CDN using a Chrome user-agent
# (required to receive woff2 format instead of ttf).
#
# Usage (one-time, from project root):
#   bash scripts/download_fonts.sh
#
# Output: app/static/fonts/*.woff2
# Gate:   tests/unit/test_font_assets.py  FA-01..03
#
# Re-running is idempotent: existing files are overwritten with fresh copies.
# Commit the resulting woff2 files to the repository — Playwright export renders
# them via /static/fonts/ without any runtime internet dependency.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FONT_DIR="$PROJECT_ROOT/app/static/fonts"

# Chrome 120 UA — necessary for woff2 delivery from Google Fonts API
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

mkdir -p "$FONT_DIR"

# Fetch the first woff2 URL from a Google Fonts CSS2 response
# Args: <family_url_encoded> <weight>
_gfont_url() {
    local family="$1"
    local weight="$2"
    local css_url="https://fonts.googleapis.com/css2?family=${family}:wght@${weight}&display=block"
    curl -s --max-time 30 -A "$UA" "$css_url" \
        | grep -o "https://fonts\.gstatic\.com/[^)]*\.woff2" \
        | head -1
}

# Download one font file. Args: <family_url> <weight> <output_filename>
download_font() {
    local family="$1"
    local weight="$2"
    local outfile="$3"
    local url

    url=$(_gfont_url "$family" "$weight")
    if [[ -z "$url" ]]; then
        echo "  ERROR: could not resolve woff2 URL for $family:$weight" >&2
        return 1
    fi

    curl -s --max-time 30 -o "$FONT_DIR/$outfile" "$url"
    local size
    size=$(wc -c < "$FONT_DIR/$outfile")
    printf "  ✓  %-40s  %d bytes\n" "$outfile" "$size"
}

echo ""
echo "FIFA Classic Export — Font Acquisition"
echo "======================================="
echo "Target: $FONT_DIR"
echo ""

download_font "Bebas+Neue"         "400"  "BebasNeue-Regular.woff2"
download_font "Barlow+Condensed"   "300"  "BarlowCondensed-300.woff2"
download_font "Barlow+Condensed"   "400"  "BarlowCondensed-400.woff2"
download_font "Barlow+Condensed"   "600"  "BarlowCondensed-600.woff2"
download_font "Barlow+Condensed"   "700"  "BarlowCondensed-700.woff2"
download_font "Barlow+Condensed"   "800"  "BarlowCondensed-800.woff2"
download_font "Rajdhani"           "500"  "Rajdhani-500.woff2"
download_font "Rajdhani"           "600"  "Rajdhani-600.woff2"
download_font "Rajdhani"           "700"  "Rajdhani-700.woff2"

echo ""
echo "Done. Font directory contents:"
ls -lh "$FONT_DIR"
echo ""
echo "Run tests/unit/test_font_assets.py to verify FA-01..03."
