#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Prepare a clean pinned Organic Maps checkout and build the quality-enabled WebDebug APK.
set -euo pipefail
src=$(realpath "${1:?Organic Maps checkout}")
core=$(realpath "${2:?Core checkout}")
abi=${3:?Android ABI}
here=$(cd "$(dirname "$0")" && pwd)
map=$(git -C "$here" rev-parse --show-toplevel)
source_pin=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$map/upstream.lock.json")
[[ $(git -C "$src" rev-parse HEAD) = "$source_pin" && -z $(git -C "$src" status --porcelain) ]]
[[ $(git -C "$core" rev-parse HEAD) = "$(cat "$here/core-revision")" && -z $(git -C "$core" status --porcelain) ]]
case "$abi" in
  x86_64) target=x86_64-linux-android; gradle_abi=x86_64 ;;
  arm64-v8a) target=aarch64-linux-android; gradle_abi=arm64 ;;
  *) echo 'Supported ABIs are x86_64 and arm64-v8a.' >&2; exit 2 ;;
esac
ndk=${ANDROID_HOME:?}/ndk/29.0.14206865
compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/${target}21-clang"
[[ -x $compiler ]]
main="$src/android/app/src/main"
assets="$main/assets/volparossa-licenses"
native="$main/jniLibs/$abi"
cd "$core"
export RUSTUP_AUTO_INSTALL=0
toolchain=$(sed -n 's/^channel = "\([^"]*\)"/\1/p' rust-toolchain.toml)
[[ $toolchain =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
if ! rustup toolchain list | grep -Eq "^$toolchain-"; then
  echo "The required Rust $toolchain toolchain must already be installed." >&2; exit 1
fi
if ! rustup target list --installed --toolchain "$toolchain" | grep -Fxq "$target"; then
  echo "The required Rust $target target must already be installed." >&2; exit 1
fi
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$core/target}"
mkdir -p "$CARGO_TARGET_DIR"
evidence=$(mktemp -d "$CARGO_TARGET_DIR/my-position-notices.XXXXXX")
trap 'rm -rf -- "$evidence"' EXIT
cargo "+$toolchain" build --locked --release --package volparossa-dps-ffi --target "$target" \
  --message-format=json-render-diagnostics > "$evidence/build.jsonl"
cargo "+$toolchain" metadata --locked --manifest-path crates/volparossa-dps-ffi/Cargo.toml \
  --filter-platform "$target" --format-version 1 > "$evidence/metadata.json"
python3 -B "$here/notice_graph.py" "$core" "$evidence/metadata.json" "$evidence/build.jsonl" > "$evidence/linked.json"
"$core/packaging/collect-cargo-licenses.sh" "$evidence/linked.json" "$evidence/crates"
git -C "$src" apply --check "$here/organicmaps.patch"
git -C "$src" apply "$here/organicmaps.patch"
install -d "$native" "$assets"
"$compiler" -std=c11 -O2 -fPIC -shared -fvisibility=hidden -Wall -Wextra -Werror \
  -I"$core/crates/volparossa-dps-ffi/include" "$here/quality_jni.c" \
  "$CARGO_TARGET_DIR/$target/release/libvolparossa_dps_ffi.a" \
  -Wl,--no-undefined -Wl,--exclude-libs,ALL -Wl,-z,max-page-size=16384 -llog -ldl -lm -lunwind \
  -o "$native/libvolparossa_map_quality.so"
install -m 0644 "$map/LICENSE" "$assets/COPYING"
cp -R "$evidence/crates" "$assets/"
# The NDK notice includes the statically linked Android unwind runtime.
install -m 0644 "$ndk/NOTICE" "$assets/NDK-NOTICE"
if [[ -f $ndk/NOTICE.toolchain ]]; then install -m 0644 "$ndk/NOTICE.toolchain" "$assets/NDK-toolchain-NOTICE"; fi
rust_doc="$(rustup run "$toolchain" rustc --print sysroot)/share/doc/rust"
install -m 0644 "$rust_doc/COPYRIGHT-library.html" "$assets/Rust-library-COPYRIGHT.html"
cp -R "$rust_doc/licenses" "$assets/rust-licenses"
install -Dm644 "$here/java/MyPositionQuality.java" "$main/java/org/volparossa/map/dps/MyPositionQuality.java"
install -Dm644 "$here/strings.xml" "$main/res/values/volparossa_dps_strings.xml"
install -Dm644 "$here/test/MyPositionQualityTest.java" \
  "$src/android/app/src/androidTest/java/org/volparossa/map/dps/MyPositionQualityTest.java"
cd "$src/android"
./gradlew --no-daemon "-P$gradle_abi" -Pandroid.builder.sdkDownload=false :app:assembleWebDebug :app:assembleWebDebugAndroidTest
