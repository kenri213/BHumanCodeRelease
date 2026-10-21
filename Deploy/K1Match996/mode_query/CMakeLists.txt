cmake_minimum_required(VERSION 3.16)
project(K1ModeQueryOnly LANGUAGES CXX)
set(BHUMAN_ROOT "/Users/kenrim2ab/vscodekk/BHumanCodeRelease" CACHE PATH "Existing B-Human source tree")
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64|ARM64)$")
  message(FATAL_ERROR "This SDK query target requires the existing Linux ARM64 cross toolchain")
endif()
set(SDK "${BHUMAN_ROOT}/Util/BoosterRoboticsSDK")
find_package(Threads REQUIRED)
add_executable(k1_mode_query mode_query.cpp)
target_compile_features(k1_mode_query PRIVATE cxx_std_20)
target_include_directories(k1_mode_query SYSTEM PRIVATE "${SDK}/include")
target_compile_options(k1_mode_query PRIVATE -Wall -Wextra -Werror -pedantic)
target_link_libraries(k1_mode_query PRIVATE
  "${SDK}/lib/LinuxArm64/libbooster_robotics_sdk.a"
  "${SDK}/lib/LinuxArm64/libfastrtps.so"
  "${SDK}/lib/LinuxArm64/libfastcdr.so"
  "${SDK}/lib/LinuxArm64/libfoonathan_memory-0.7.3.a"
  Threads::Threads)
set_target_properties(k1_mode_query PROPERTIES SKIP_BUILD_RPATH TRUE)
