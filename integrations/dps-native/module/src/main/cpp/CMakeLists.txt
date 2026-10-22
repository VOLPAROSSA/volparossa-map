# SPDX-License-Identifier: GPL-3.0-only
cmake_minimum_required(VERSION 3.22.1)
project(volparossa_dps_jni C)
if(NOT ANDROID_ABI STREQUAL "x86_64")
  message(FATAL_ERROR "Only the reviewed synthetic x86_64 fixture is supported")
endif()
add_library(dps_rust STATIC IMPORTED)
set_target_properties(dps_rust PROPERTIES IMPORTED_LOCATION
  "${CMAKE_CURRENT_LIST_DIR}/../../../rust/x86_64/libvolparossa_dps_ffi.a")
add_library(volparossa_dps_jni SHARED quality_jni.c)
target_compile_features(volparossa_dps_jni PRIVATE c_std_11)
target_compile_options(volparossa_dps_jni PRIVATE -Wall -Wextra -Werror -fvisibility=hidden)
target_include_directories(volparossa_dps_jni PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../../rust/include")
target_link_options(volparossa_dps_jni PRIVATE -Wl,--no-undefined -Wl,--exclude-libs,ALL)
# Android system runtime libraries required by Rust std are not app/SDK dependencies.
target_link_libraries(volparossa_dps_jni PRIVATE dps_rust log dl m unwind)
