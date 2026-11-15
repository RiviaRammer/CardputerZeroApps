#!/bin/sh
set -eu
cd "$(dirname "$0")"
build_dir=${1:-build/arm64}
test -f "$build_dir/mimo-talk"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
chmod 755 "$stage"
DESTDIR="$stage" cmake --install "$build_dir" --prefix /usr
install -d -m755 "$stage/DEBIAN"
cat > "$stage/DEBIAN/control" <<EOF
Package: mimo-talk
Version: 0.2.2
Architecture: arm64
Section: utils
Priority: optional
Maintainer: CardputerZeroApps
Depends: libc6 (>= 2.34), libstdc++6, python3, python3-requests, alsa-utils, libasound2-plugins, ca-certificates, fonts-droid-fallback
Description: MiMo Talk voice assistant for CardputerZero
 Streaming ASR and chat with PCM playback using preset voices.
EOF
mkdir -p dist
dpkg-deb --root-owner-group --build "$stage" dist/mimo-talk_0.2.2_arm64.deb
