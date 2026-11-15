#!/bin/sh
# Run from WSL: sh /mnt/d/gits/Cardputer_Zero/CardputerZeroApps/MiMo/build-local.sh
set -eu
cd "$(dirname "$0")"
cmake -S . -B build/arm64 -G Ninja \
  -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
  -DLVGL_SOURCE_DIR="$(cd ../../projects/CardputerZero-Template/.cache/prior-cp0-deps/lvgl-src && pwd)"
cmake --build build/arm64 -j 4
sh package.sh
