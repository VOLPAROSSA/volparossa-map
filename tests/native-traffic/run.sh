#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Run inside a disposable Linux network namespace with only loopback.
set -euo pipefail
umask 077
if [[ $# != 8 ]]; then
  echo 'Expected upstream build map-inputs Core-checkout Core-binaries Debian-image new-trial-dir kvm|tcg' >&2
  exit 2
fi
upstream=$1 build=$2 maps=$3 core=$4 binaries=$5 disk=$6 trial=$7 accel=$8
case "$accel" in kvm) cpu=host ;; tcg) cpu=max ;; *) exit 2 ;; esac
repo=$(cd "$(dirname "$0")/../.." && pwd -P)
top="$repo/tests/native-traffic"
test "$(id -u)" != 0
python3 -c 'import socket; assert [n for _, n in socket.if_nameindex()] == ["lo"]'
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha512"])' "$core/tests/helper/debian13-amd64-image-v1.json")
echo "$expected  $disk" | sha512sum --strict -c -
mkdir -m 0700 "$trial"
vm=$(mktemp -d "$trial/vm.XXXXXX")
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -rf --one-file-system -- "$vm"
}
trap cleanup EXIT
probe=(--probe "$build/volparossa_traffic_probe" --upstream "$upstream" --maps "$maps")
qt=(xvfb-run -a -s '-screen 0 900x700x24 +extension GLX -nolisten tcp' /usr/bin/python3 -B)
"${qt[@]}" "$top/route_trial.py" baseline "${probe[@]}" --trial "$vm/baseline" --output "$vm/roads.json"
mkdir "$vm/medium"
python3 -B "$top/guest_stage.py" stage --core "$core" --binaries "$binaries" --mwm "$maps/Monaco.mwm" \
  --roads "$vm/roads.json" --output "$vm/medium/trial.tar"
genisoimage -quiet -R -V MAPDATA -o "$vm/data.iso" "$vm/medium"
rm -- "$vm/medium/trial.tar"
rmdir "$vm/medium"
echo 'instance-id: map-native-traffic' > "$vm/meta"
cloud-localds "$vm/seed.img" "$top/guest.sh" "$vm/meta"
qemu-img create -q -f qcow2 -F qcow2 -b "$disk" "$vm/overlay.qcow2" 8G
timeout --kill-after=10s 600s qemu-system-x86_64 -accel "$accel" -cpu "$cpu" -m 2048 -smp 2 -nic none -display none \
  -monitor none -serial "file:$vm/console" -no-reboot -device virtio-serial-pci \
  -chardev "file,id=result,path=$vm/result" -device virtserialport,chardev=result,name=map.result \
  -chardev "file,id=handoff,path=$vm/packet" -device virtserialport,chardev=handoff,name=map.handoff \
  -drive "if=virtio,format=qcow2,file=$vm/overlay.qcow2" -drive "if=virtio,readonly=on,format=raw,file=$vm/seed.img" \
  -drive "media=cdrom,readonly=on,format=raw,file=$vm/data.iso" &
pids+=("$!")
if ! python3 -B "$top/guest_stage.py" receive --packet "$vm/packet" --output "$vm/feed"; then
  grep -Eo 'MAP_TRIAL_FAILED=[a-z-]{1,40}|MAP_GUEST_LINE=[0-9]{1,5}' "$vm/result" "$vm/console" | tail -5 >&2 || true
  exit 1
fi
publisher=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["publisher"])' "$vm/feed/source.json")
manifest=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["manifest"])' "$vm/feed/source.json")
for mode in expiry removal; do
  "${qt[@]}" "$repo/tests/native-toolchains/traffic_frames.py" "$mode" --app "$build/OrganicMaps" \
    --upstream "$upstream" --maps "$maps" --handoff "$vm/feed/handoff.json" --publisher "$publisher" \
    --manifest "$manifest" --trial "$vm/render-$mode" &
  pids+=("$!")
done
for mode in expiry replacement; do
  "${qt[@]}" "$top/route_trial.py" "$mode" "${probe[@]}" --handoff "$vm/feed/handoff.json" \
    --source "$vm/feed/source.json" --trial "$vm/route-$mode" --output "$vm/result-$mode.json" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid"; done
pids=()
python3 - "$vm/result" "$vm/feed/source.json" <<'PY'
import json,sys
assert json.load(open(sys.argv[1])) == json.load(open(sys.argv[2]))
PY
for mode in expiry removal; do install -m0600 "$vm/render-$mode/receipt.json" "$trial/drape-$mode.json"; done
for mode in expiry replacement; do install -m0600 "$vm/result-$mode.json" "$trial/route-$mode.json"; done
echo "$expected  $disk" | sha512sum --strict -c -
