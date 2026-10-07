#!/bin/bash
# Build WiFiLab.app and install it to /Applications.
# The bundle embeds libpython from a venv, so the Location Services permission (needed for SSIDs and BSSIDs on
# macOS 14+) is granted to "WiFiLab" itself and not to whatever terminal started Python.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${WIFILAB_VENV:-$HOME/.venvs/wifilab}"
PYTHON="${WIFILAB_PYTHON:-python3}"
# Build outside ~/Documents: iCloud xattrs and dataless files break codesign there.
DIST="${TMPDIR:-/tmp}/wifilab-dist"
APP="$DIST/WiFiLab.app"
DEST="${WIFILAB_APP_DIR:-/Applications}"

# The bundle is only the launcher; the app itself is the package in the venv. Info.plist is sealed by the ad-hoc
# signature and macOS ties the Location permission to it: bump LAUNCHER_VERSION only when launcher.c or the
# plist change, or users get asked again.
LAUNCHER_VERSION="0.1.0"

if [ ! -x "$VENV/bin/python" ]; then
  echo "== creating venv $VENV =="
  "$PYTHON" -m venv "$VENV"
fi
# Not an editable install: the bundle must not depend on the source tree.
"$VENV/bin/pip" install -q "$ROOT[build]"
VERSION="$("$VENV/bin/python" -c 'import wifilab; print(wifilab.__version__)')"
echo "== building WiFiLab.app (launcher $LAUNCHER_VERSION) for wifilab $VERSION =="

rm -rf "$DIST"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

PYHOME="$("$VENV/bin/python" -c 'import sys; print(sys.base_prefix)')"
LIBDIR="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
SITEPKG="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
PYVER="$("$VENV/bin/python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
clang -O2 -o "$APP/Contents/MacOS/WiFiLab" "$ROOT/scripts/launcher.c" \
  -DPYHOME="\"$PYHOME\"" -DSITE_PACKAGES="\"$SITEPKG\"" \
  -L"$LIBDIR" -lpython"$PYVER" -Wl,-rpath,"$LIBDIR"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>WiFiLab</string>
    <key>CFBundleDisplayName</key><string>WiFiLab</string>
    <key>CFBundleIdentifier</key><string>io.github.igrbtn.wifilab</string>
    <key>CFBundleVersion</key><string>$LAUNCHER_VERSION</string>
    <key>CFBundleShortVersionString</key><string>$LAUNCHER_VERSION</string>
    <key>CFBundleExecutable</key><string>WiFiLab</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSUIElement</key><true/>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSLocationWhenInUseUsageDescription</key>
    <string>WiFiLab needs Location Services to read Wi-Fi network names (SSID) and access point addresses (BSSID) in scans. Your location is not stored or sent anywhere.</string>
    <key>NSLocationUsageDescription</key>
    <string>WiFiLab needs Location Services to read Wi-Fi network names (SSID) and access point addresses (BSSID) in scans. Your location is not stored or sent anywhere.</string>
</dict>
</plist>
EOF

ICONSET="$DIST/AppIcon.iconset"
mkdir -p "$ICONSET"
"$VENV/bin/python" - "$ICONSET" <<'PYEOF'
import sys
from pathlib import Path
from PIL import Image, ImageDraw

iconset = Path(sys.argv[1])
size = 1024
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
m = size // 10
d.rounded_rectangle([m, m, size - m, size - m], radius=size // 5, fill=(18, 26, 38, 255))
# Wi-Fi arcs over a heatmap-coloured dot
cx, cy = size // 2, int(size * 0.66)
for i, col in enumerate([(32, 92, 218), (62, 186, 82), (245, 128, 32)]):
    r = int(size * (0.14 + 0.11 * i))
    d.arc([cx - r, cy - r, cx + r, cy + r], start=225, end=315, fill=col + (255,), width=int(size * 0.05))
d.ellipse([cx - 48, cy - 48, cx + 48, cy + 48], fill=(230, 237, 243, 255))
for px in (16, 32, 128, 256, 512):
    img.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{px}x{px}.png")
    img.resize((px * 2, px * 2), Image.LANCZOS).save(iconset / f"icon_{px}x{px}@2x.png")
PYEOF
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET"

xattr -cr "$APP"
codesign --force --deep -s - "$APP"

rm -rf "$DEST/WiFiLab.app"
cp -R "$APP" "$DEST/"
echo "== installed $DEST/WiFiLab.app =="
echo "Run: open '$DEST/WiFiLab.app'  (it opens the UI in the browser on a free port; log: ~/Library/Logs/WiFiLab.log)"
