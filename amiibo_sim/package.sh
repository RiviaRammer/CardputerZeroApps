#!/bin/sh
set -eu
cd "$(dirname "$0")"
build_dir=${1:-build/lcd-arm64}
version=0.1.9-1
test -f "$build_dir/amiibo_sim"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
chmod 755 "$stage"
DESTDIR="$stage" cmake --install "$build_dir" --prefix /usr
install -Dm644 amiibo_sim.png "$stage/usr/share/APPLaunch/share/images/amiibo_sim.png"
install -d -m755 "$stage/DEBIAN"
cat > "$stage/DEBIAN/control" <<EOF
Package: amiibo-sim
Version: $version
Conflicts: zero-amiibo
Replaces: zero-amiibo
Architecture: arm64
Section: utils
Priority: optional
Maintainer: amiibo_sim
Depends: libc6 (>= 2.34), libstdc++6, python3, python3-cryptography, fonts-droid-fallback
Description: amiibo_sim for Cardputer Zero and Cap CC1101
 Browse local NTAG215 dumps and emulate an Amiibo using the Cap NFC chip.
EOF
mkdir -p dist
dpkg-deb --root-owner-group --build "$stage" "dist/amiibo-sim_${version}_arm64.deb"
