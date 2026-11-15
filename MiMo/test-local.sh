#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -B -m unittest discover -s tests -v
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Release -DMIMO_UI_TEST=ON \
  -DLVGL_SOURCE_DIR="$(cd ../../projects/CardputerZero-Template/.cache/prior-cp0-deps/lvgl-src && pwd)"
cmake --build build/host -j4 --target mimo-ui-test > build/host/build.log 2>&1 || { tail -60 build/host/build.log; exit 1; }
cd build/host
if [ ! -f /usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf ]; then
    if [ ! -f fonts/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf ]; then
        apt-get download fonts-droid-fallback
        dpkg-deb -x ./fonts-droid-fallback_*.deb fonts
    fi
    export MIMO_FONT_FILE="A:$(pwd)/fonts/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
fi
./mimo-ui-test
