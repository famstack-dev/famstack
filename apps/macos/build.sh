#!/usr/bin/env bash
# Build the menu bar app into build/famstack.app.
#
# SwiftPM builds the binary; this wraps it in the minimal bundle macOS needs
# to treat it as a menu bar app (LSUIElement, a bundle id for its settings)
# and signs it ad hoc so it runs on this Mac. Command Line Tools are enough,
# no Xcode project.
#
#   apps/macos/build.sh          release build
#   apps/macos/build.sh debug    debug build
set -euo pipefail

cd "$(dirname "$0")"
config="${1:-release}"

swift build -c "$config"

app="build/famstack.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
cp ".build/$config/StackMenu" "$app/Contents/MacOS/StackMenu"

# The icon comes from the same drawing as the menu bar mark.
mkdir -p "$app/Contents/Resources"
rm -rf build/AppIcon.iconset
".build/$config/StackMenu" --icon build/AppIcon.iconset
iconutil -c icns -o "$app/Contents/Resources/AppIcon.icns" build/AppIcon.iconset

cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>     <string>dev.famstack.menubar</string>
    <key>CFBundleName</key>           <string>famstack</string>
    <key>CFBundleExecutable</key>     <string>StackMenu</string>
    <key>CFBundleIconFile</key>       <string>AppIcon</string>
    <key>CFBundlePackageType</key>    <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>0.1.0</string>
    <key>LSMinimumSystemVersion</key> <string>14.0</string>
    <key>LSUIElement</key>            <true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$app"
echo "$(pwd)/$app"
