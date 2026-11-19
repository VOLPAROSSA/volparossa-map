#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
umask 077
[[ "$GITHUB_ACTIONS:$RUNNER_ENVIRONMENT" = true:github-hosted ]]
[[ "$(git rev-parse HEAD)" = "$GITHUB_SHA" ]]
trial=$ROUTE_TRIAL
repo=$GITHUB_WORKSPACE
sdk=${ANDROID_HOME:?}
runner_uid=$(id -u)
[[ $runner_uid -ne 0 && -c /dev/kvm ]]
kvm_gid=$(stat -c %g /dev/kvm)
[[ "$kvm_gid" != 0 && $(getent group "$kvm_gid" | cut -d: -f1) = kvm ]]
image="$trial/sdk/system-images/android-35/default/x86_64"
install -d -m 0700 "$trial/sdk" "$trial/home" "$trial/android-home" "$trial/avds" "$trial/tmp"
cp -r -- "$sdk/licenses" "$trial/sdk/licenses"
manager="$sdk/cmdline-tools/latest/bin"
[[ -x "$manager/sdkmanager" && -x "$manager/avdmanager" && -x "$sdk/platform-tools/adb" && -x "$sdk/emulator/emulator" ]]
export ANDROID_USER_HOME="$trial/android-home" ANDROID_AVD_HOME="$trial/avds"
"$manager/sdkmanager" --sdk_root="$trial/sdk" "system-images;android-35;default;x86_64"
test -s "$image/system.img"
# The distribution's avdmanager locates packages relative to its SDK. Create
# a temporary SDK that shares the installed tools and owns just this system image.
ln -s "$sdk/platform-tools" "$trial/sdk/platform-tools"
ln -s "$sdk/emulator" "$trial/sdk/emulator"
install -d -m 0700 "$trial/sdk/cmdline-tools"
cp -aL -- "$sdk/cmdline-tools/latest" "$trial/sdk/cmdline-tools/latest"
export ANDROID_HOME="$trial/sdk" ANDROID_SDK_ROOT="$trial/sdk"
printf 'no\n' | "$trial/sdk/cmdline-tools/latest/bin/avdmanager" create avd --name offline-map \
    --package 'system-images;android-35;default;x86_64' --device pixel_5
# Only this ephemeral guest namespace gets loopback. The parent runner's
# addresses, routes, DNS configuration and device permissions are untouched.
sudo -n /usr/bin/unshare --net -- /bin/bash -ceu '
    ip link set lo up
    exec /usr/bin/setpriv --reuid "$1" --regid "$2" --clear-groups --inh-caps=-all --ambient-caps=-all \
        --bounding-set=-all --no-new-privs --reset-env -- /usr/bin/env HOME="$3/home" ANDROID_HOME="$3/sdk" \
        ANDROID_SDK_ROOT="$3/sdk" ANDROID_USER_HOME="$3/android-home" ANDROID_AVD_HOME="$3/avds" TMPDIR="$3/tmp" \
        GITHUB_SHA="$4" PATH=/usr/bin:/bin /bin/bash "$5/tests/offline-route/run-guest.sh" "$3" "$5"
' offline-map "$runner_uid" "$kvm_gid" "$trial" "$GITHUB_SHA" "$repo"
