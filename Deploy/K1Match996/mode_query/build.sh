#!/bin/bash
set -euo pipefail
mode_task_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
mode_bhuman_root="${1:-/Users/kenrim2ab/vscodekk/BHumanCodeRelease}"
mode_tools=/opt/miniconda3/envs/robocup_mujoco/bin
export PATH="$mode_tools:$PATH"
"$mode_tools/clang++" -std=c++20 -Wall -Wextra -Werror -pedantic \
  "$mode_task_dir/query_policy_test.cpp" -o "$mode_task_dir/query_policy_test"
"$mode_task_dir/query_policy_test"
"$mode_tools/cmake" -S "$mode_task_dir" -B "$mode_task_dir/build_arm64" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$mode_bhuman_root/Make/CMake/BoosterToolchain.cmake" \
  -DBHUMAN_ROOT="$mode_bhuman_root" -DCMAKE_BUILD_TYPE=Release
"$mode_tools/cmake" --build "$mode_task_dir/build_arm64" --parallel 2
file "$mode_task_dir/build_arm64/k1_mode_query"
shasum -a 256 "$mode_task_dir/build_arm64/k1_mode_query" \
  "$mode_bhuman_root/Util/BoosterRoboticsSDK/lib/LinuxArm64/libbooster_robotics_sdk.a"
