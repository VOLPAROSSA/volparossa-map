#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
umask 077
trial=$1
root=$2
[[ $(id -u) != 0 && -c /dev/kvm ]]
[[ "$(ip -j link | python3 -c 'import sys,json;print([v["ifname"] for v in json.load(sys.stdin)])')" = "['lo']" ]]
emu="$trial/sdk/emulator/emulator"
adb="$trial/sdk/platform-tools/adb"
"$emu" -accel-check
"$adb" start-server
"$emu" -avd offline-map -port 5554 -accel on -no-window -no-audio -no-boot-anim -no-snapshot \
    -gpu software -feature -Vulkan -no-metrics -cores 2 -memory 3072 >"$trial/emulator.log" 2>&1 &
pid=$!
finish() {
    "$adb" -s emulator-5554 emu kill >/dev/null 2>&1 || true
    kill "$pid" 2>/dev/null || true
    wait "$pid" || true
    "$adb" kill-server >/dev/null 2>&1 || true
}
trap finish EXIT
timeout --kill-after=10s 720s python3 -B "$root/tests/offline-route/android.py" "$adb" "$trial"
