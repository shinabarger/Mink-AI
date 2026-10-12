#!/bin/bash
# Builds dist/Mink.app and dist/Mink.dmg. Runs on an Apple Silicon Mac with Xcode or its
# command line tools, and in GitHub Actions (.github/workflows/mac-app.yml).
#
# Optional signing (needs a paid Apple Developer account). Without these the app is
# ad-hoc signed, and people approve it once in System Settings the first time they open it.
#   MAC_CERT_P12_BASE64, MAC_CERT_PASSWORD     Developer ID Application certificate
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD   for notarization
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/Mink.app"
VERSION="$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)"

# uv installs Python and Mink's packages on first launch. Pinned and checksum verified.
UV_VERSION="0.11.32"
UV_SHA256="ed336d0ba49db8ef89b2b41fffa372ce63bd032f22a56f001c265891aec32829"

echo "Building Mink $VERSION"
rm -rf "$DIST"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

echo "1/5 Compiling the app"
swiftc -O -target arm64-apple-macos14.0 -framework AppKit -framework WebKit \
  "$HERE/Mink.swift" -o "$APP/Contents/MacOS/Mink"
sed "s/__VERSION__/$VERSION/g" "$HERE/Info.plist" > "$APP/Contents/Info.plist"

echo "2/5 Making the icon"
ICONSET="$DIST/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$HERE/AppIcon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$HERE/AppIcon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET"

echo "3/5 Adding uv and Mink"
TMP="$(mktemp -d)"
curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz" -o "$TMP/uv.tar.gz"
echo "$UV_SHA256  $TMP/uv.tar.gz" | shasum -a 256 -c -
tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
cp "$TMP/uv-aarch64-apple-darwin/uv" "$APP/Contents/MacOS/uv"
rm -rf "$TMP"
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$ROOT/README.md" "$ROOT/LICENSE" "$APP/Contents/Resources/app/"
rsync -a --exclude __pycache__ --exclude .DS_Store "$ROOT/mink" "$APP/Contents/Resources/app/"

echo "4/5 Signing"
IDENTITY="-"
if [ -n "${MAC_CERT_P12_BASE64:-}" ]; then
  KEYCHAIN="$DIST/build.keychain"
  security create-keychain -p mink "$KEYCHAIN"
  security set-keychain-settings "$KEYCHAIN"
  security unlock-keychain -p mink "$KEYCHAIN"
  echo "$MAC_CERT_P12_BASE64" | base64 --decode > "$DIST/cert.p12"
  security import "$DIST/cert.p12" -k "$KEYCHAIN" -P "$MAC_CERT_PASSWORD" -T /usr/bin/codesign
  security set-key-partition-list -S apple-tool:,apple: -s -k mink "$KEYCHAIN" >/dev/null
  security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')
  rm "$DIST/cert.p12"
  IDENTITY="Developer ID Application"
fi
SIGN_FLAGS=(--force --sign "$IDENTITY")
[ "$IDENTITY" != "-" ] && SIGN_FLAGS+=(--options runtime --timestamp)
codesign "${SIGN_FLAGS[@]}" "$APP/Contents/MacOS/uv"
codesign "${SIGN_FLAGS[@]}" "$APP"
codesign --verify --strict "$APP"

echo "5/5 Packaging the disk image"
STAGE="$DIST/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"  # Drag Mink onto this to install
hdiutil create -volname "Mink" -srcfolder "$STAGE" -ov -format UDZO "$DIST/Mink.dmg" >/dev/null
rm -rf "$STAGE"

if [ "$IDENTITY" != "-" ] && [ -n "${APPLE_ID:-}" ]; then
  echo "Notarizing"
  codesign --force --sign "$IDENTITY" --timestamp "$DIST/Mink.dmg"
  xcrun notarytool submit "$DIST/Mink.dmg" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" --wait
  xcrun stapler staple "$DIST/Mink.dmg"
fi

echo "Done: $DIST/Mink.dmg"
