#!/bin/sh
set -eu
cd "$(dirname "$0")"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
version=$(python3 -B -c 'from cardputersdr import __version__; print(__version__)')
chmod 755 "$stage"
install -Dm755 cardputersdr-launch "$stage/usr/bin/cardputersdr"
find cardputersdr -type f \( -name '*.py' -o -name '*.html' -o -name '*.css' -o -name '*.js' \) | while read -r file; do
  install -Dm644 "$file" "$stage/usr/lib/cardputersdr/$file"
done
install -Dm644 cardputersdr.desktop "$stage/usr/share/APPLaunch/applications/cardputersdr.desktop"
install -Dm644 packaging/icon.png "$stage/usr/share/APPLaunch/share/images/cardputersdr.png"
for file in README.md LICENSE; do
  install -Dm644 "$file" "$stage/usr/share/doc/cardputersdr/$file"
done
install -d "$stage/DEBIAN"
cat > "$stage/DEBIAN/control" <<EOF
Package: cardputersdr
Version: $version
Architecture: arm64
Section: hamradio
Priority: optional
Maintainer: RiviaRammer <RiviaRammer@users.noreply.github.com>
Depends: python3 (>= 3.11), libairspy0, python3-aiohttp, python3-numpy, python3-pil (>= 10.1), python3-evdev
Description: Airspy spectrum analyzer for Cardputer Zero
 LCD spectrum and waterfall, or browser-side FFT from bounded IQ snapshots.
EOF
mkdir -p dist
dpkg-deb --root-owner-group --build "$stage" "dist/cardputersdr_${version}_arm64.deb"
