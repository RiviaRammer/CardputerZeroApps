#!/bin/sh
set -eu
cd "$(dirname "$0")"
builder=${SPLASH_BUILDER:-../../初始化配置/build_splash.py}
test -f "$builder" || { echo "Missing build_splash.py; set SPLASH_BUILDER to its absolute path." >&2; exit 1; }
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
chmod 755 "$stage"

install -Dm755 wallpaper "$stage/usr/bin/wallpaper"
install -Dm755 askpass "$stage/usr/lib/wallpaper/askpass"
install -Dm755 write-asset "$stage/usr/lib/wallpaper/write-asset"
install -Dm644 "$builder" "$stage/usr/lib/wallpaper/build_splash.py"
install -m644 assets.py current.py keyboard.py local_sudo.py main.py web.py index.html "$stage/usr/lib/wallpaper/"
install -Dm644 wallpaper.desktop "$stage/usr/share/APPLaunch/applications/wallpaper.desktop"
install -Dm644 icon.png "$stage/usr/share/APPLaunch/share/images/wallpaper.png"
install -Dm644 README.md "$stage/usr/share/doc/wallpaper/README.md"
cat > "$stage/DEBIAN/control" <<'EOF'
Package: wallpaper
Version: 0.4.1
Architecture: arm64
Section: utils
Priority: optional
Maintainer: CardputerZeroApps
Depends: ffmpeg, fonts-dejavu-core, iproute2, python3 (>= 3.10), python3-evdev, python3-pil (>= 9.1), sudo
Conflicts: cardputer-boot-assets
Description: Boot and lock-screen media editor for CardputerZero
 Edit five device assets from a local browser after confirmation on the device.
EOF
mkdir -p dist
dpkg-deb --root-owner-group --build "$stage" dist/wallpaper_0.4.1_arm64.deb
