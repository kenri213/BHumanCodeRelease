#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 2 ]] || { echo 'Usage: build_helpers.sh <absolute-repository-root> <absolute-build-directory-outside-repository>' >&2; exit 2; }
[[ "$1" = /* && "$2" = /* && "$2" != / ]] || { echo 'Explicit absolute source/build paths required' >&2; exit 2; }
source_root="$(cd -- "$1" && pwd -P)"
[[ -f "$source_root/Make/CMake/BoosterToolchain.cmake" && -f "$source_root/Tools/K1Commissioning/dds_observer.cpp" ]] || { echo 'Complete matching B-Human source required' >&2; exit 2; }
[[ -f "$source_root/Util/BoosterRoboticsSDK/lib/LinuxArm64/libbooster_robotics_sdk.a" ]] || { echo 'Authorized matching SDK required; no bundled SDK in this publication' >&2; exit 2; }
mkdir -p -- "$2"
build_root="$(cd -- "$2" && pwd -P)"
case "$build_root" in "$source_root"|"$source_root"/*) echo 'Keep all generated helper outputs outside the source repository' >&2; exit 2 ;; esac
helper_root="$source_root/Tools/K1Match977/helpers"
"${CXX:-c++}" -std=c++20 -Wall -Wextra -Werror -pedantic "$helper_root/query_policy_test.cpp" -o "$build_root/query_policy_test"
"$build_root/query_policy_test" # CPU-only policy; no SDK, sensor input or robot command.
cmake -S "$helper_root" -B "$build_root/arm64" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$source_root/Make/CMake/BoosterToolchain.cmake" \
  -DBHUMAN_ROOT="$source_root" -DCMAKE_BUILD_TYPE=Release
cmake --build "$build_root/arm64" --parallel 2
# Never run the generated ARM executables here.
