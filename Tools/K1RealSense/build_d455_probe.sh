#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then
  printf '%s\n' 'Build on Linux ARM64 with librealsense2; this script never contacts a robot.' >&2
  exit 2
fi
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) printf '%s\n' 'Bundled librealsense is Linux ARM64 only.' >&2; exit 2 ;;
esac
tool_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
tool_output="${1:-/tmp/bhuman_d455_probe}"
tool_compiler="${CXX:-g++}"
tool_cflags=()
tool_ldflags=()
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists realsense2; then
  read -r -a tool_cflags <<< "$(pkg-config --cflags realsense2)"
  read -r -a tool_ldflags <<< "$(pkg-config --libs realsense2)"
  printf 'Using existing system librealsense pkg-config version: %s\n' "$(pkg-config --modversion realsense2)"
elif [[ -f /usr/local/include/librealsense2/rs.h && -f /usr/local/lib/librealsense2.so ]]; then
  tool_cflags=(-I/usr/local/include)
  tool_ldflags=(-L/usr/local/lib -Wl,-rpath,/usr/local/lib -lrealsense2)
  printf '%s\n' 'Using existing /usr/local librealsense SDK (no installation).'
else
  tool_sdk="$tool_repo_root/Util/librealsense2"
  if [[ ! -f "$tool_sdk/lib/LinuxArm64/librealsense2.so.2.55.1" ]]; then
    printf '%s\n' 'No existing system or bundled librealsense SDK found; nothing installed or changed.' >&2
    exit 2
  fi
  tool_cflags=(-I"$tool_sdk/include")
  tool_ldflags=(-L"$tool_sdk/lib/LinuxArm64" -Wl,-rpath,"$tool_sdk/lib/LinuxArm64" -l:librealsense2.so.2.55.1)
  printf '%s\n' 'Using existing repository-bundled Linux ARM64 SDK.'
fi
"$tool_compiler" -std=c++17 -O2 -Wall -Wextra -Werror -pedantic \
  "${tool_cflags[@]}" "$tool_repo_root/Tools/K1RealSense/d455_probe.cpp" \
  "${tool_ldflags[@]}" -o "$tool_output"
printf 'Built camera-only diagnostic: %s\n' "$tool_output"
printf '%s\n' 'Not executed. Default is passive; capture requires an explicit --capture argument.'
