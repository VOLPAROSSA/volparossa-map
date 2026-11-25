#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -Eeuo pipefail
trap 'echo "Android preparation stopped at line $LINENO" >&2' ERR
umask 077
[[ "$GITHUB_ACTIONS:$RUNNER_ENVIRONMENT" = true:github-hosted ]]
[[ "$(git rev-parse HEAD)" = "$GITHUB_SHA" ]]
mode=${1:-release}
[[ $# -le 1 && ( $mode = release || $mode = web-debug || $mode = web-debug-quality ) ]]
trial=$ROUTE_TRIAL
repo=$GITHUB_WORKSPACE
sdk=${ANDROID_HOME:?}
runner_uid=$(id -u)
[[ $runner_uid -ne 0 && -c /dev/kvm ]]
kvm_gid=$(stat -Lc %g /dev/kvm)
[[ "$kvm_gid" != 0 && $(getent group "$kvm_gid" | cut -d: -f1) = kvm ]]
variant=default
if [[ $mode != release ]]; then
  abi=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["abi"])' "$trial/android-build.json")
  case "$abi" in
    arm64-v8a) variant=google_apis ;;
    x86_64) ;;
    *) exit 2 ;;
  esac
fi
package="system-images;android-35;$variant;x86_64"
image="$trial/sdk/system-images/android-35/$variant/x86_64"
install -d -m 0700 "$trial/sdk" "$trial/home" "$trial/android-home" "$trial/avds" "$trial/tmp"
cp -r -- "$sdk/licenses" "$trial/sdk/licenses"
manager="$sdk/cmdline-tools/latest/bin"
[[ -x "$manager/sdkmanager" && -x "$manager/avdmanager" ]]
export ANDROID_USER_HOME="$trial/android-home" ANDROID_AVD_HOME="$trial/avds"
"$manager/sdkmanager" --sdk_root="$trial/sdk" platform-tools emulator "$package"
test -s "$image/system.img"
# The distribution's avdmanager locates packages relative to its SDK. Create
# a temporary SDK that shares the installed tools and owns just this system image.
install -d -m 0700 "$trial/sdk/cmdline-tools"
cp -aL -- "$sdk/cmdline-tools/latest" "$trial/sdk/cmdline-tools/latest"
export ANDROID_HOME="$trial/sdk" ANDROID_SDK_ROOT="$trial/sdk"
printf 'no\n' | "$trial/sdk/cmdline-tools/latest/bin/avdmanager" create avd --name offline-map \
    --package "$package" --device pixel_5
# Only this ephemeral guest namespace gets loopback. The parent runner's
# addresses, routes, DNS configuration and device permissions are untouched.
sudo -n /usr/bin/unshare --net --pid --fork --kill-child=KILL --mount-proc -- /bin/bash -ceu '
    ip link set lo up
    exec /usr/bin/setpriv --reuid "$1" --regid "$2" --clear-groups --inh-caps=-all --ambient-caps=-all \
        --bounding-set=-all --no-new-privs --reset-env -- /usr/bin/env HOME="$3/home" ANDROID_HOME="$3/sdk" \
        ANDROID_SDK_ROOT="$3/sdk" ANDROID_USER_HOME="$3/android-home" ANDROID_AVD_HOME="$3/avds" TMPDIR="$3/tmp" \
        GITHUB_SHA="$4" PATH=/usr/bin:/bin /usr/bin/python3 -B "$5/tests/offline-route/run_guest.py" "$3" "$6"
' offline-map "$runner_uid" "$kvm_gid" "$trial" "$GITHUB_SHA" "$repo" "$mode"
