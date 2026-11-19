#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# NoCloud boot of the disposable Debian 13 VM without a network card.
set -euo pipefail
umask 077
root=/opt/map-trial
status=/dev/virtio-ports/map.result
stage=inputs
finish() {
    result=$?
    if ((result != 0)); then
        echo "MAP_TRIAL_FAILED=$stage" > "$status"
        journalctl -b -u volparossa-agent -u volparossa-helper --no-pager -o cat |
            python3 -c 'import re,sys; print("guest diagnostics",sorted(set(re.findall(r"\x22diagnostic_code\x22:\s*\x22([A-Z0-9_]{1,80})\x22",sys.stdin.read(65536)))))' > /dev/ttyS0 || true
    fi
    systemctl poweroff --no-block
}
trap finish EXIT
trap 'echo "MAP_GUEST_LINE=$LINENO" > /dev/ttyS0' ERR
test "$(id -u)" = 0
case "$(systemd-detect-virt --vm)" in kvm|qemu) ;; *) exit 1 ;; esac
test -c "$status"
test "$(ls /sys/class/net)" = lo
install -d -m 0755 "$root"
mount -o ro,nodev,nosuid,noexec /dev/disk/by-label/MAPDATA /mnt
tar --extract --file=/mnt/trial.tar --directory="$root" --no-same-owner
umount /mnt
cd "$root"
stage=install
install -m 0755 bin/volparossa bin/volparossa-agent /usr/bin
install -D -m 0755 bin/volparossa-helper /usr/libexec/volparossa/volparossa-helper
install -m 0644 units/*.service /etc/systemd/system/
systemd-sysusers "$root/units/volparossa.sysusers"
systemd-tmpfiles --create "$root/units/volparossa.tmpfiles"
useradd --system --user-group --no-create-home --groups volparossa-users --shell /usr/sbin/nologin mapowner
cat > /etc/volparossa/config.yaml <<'CONFIG'
runtime_mode: development
roles:
  client: true
  relay: false
  exit: false
network:
  uplink: local_only
  listen_addresses: [/ip4/127.0.0.1/udp/0/quic-v1]
  bootstrap_peers: []
privacy:
  metrics_enabled: false
content_contribution:
  enabled: false
dns_cache:
  enabled: false
sharing:
  enabled: false
download_sharing:
  enabled: false
wifi_mesh:
  enabled: false
CONFIG
chown root:volparossa /etc/volparossa/config.yaml
chmod 0640 /etc/volparossa/config.yaml
stage=identity
state=/var/lib/volparossa
umask 077
head -c 32 /dev/urandom | base64 > "$state/trial-passphrase"
chown volparossa:volparossa "$state/trial-passphrase"
runuser -u volparossa -- volparossa init --identity "$state/identity.key" --passphrase-file "$state/trial-passphrase" >/dev/null
runuser -u volparossa -- volparossa config validate >/dev/null
install -d -m 0700 /etc/credstore.encrypted
systemd-creds encrypt --with-key=host --name=identity-passphrase "$state/trial-passphrase" /etc/credstore.encrypted/identity-passphrase
stage=helper
systemctl daemon-reload
systemctl start volparossa-helper
for i in {1..100}; do [[ -S /run/volparossa/helper.sock ]] && break; sleep .1; done
test -S /run/volparossa/helper.sock
systemctl is-active --quiet volparossa-helper
stage=agent
systemctl start volparossa-agent
for i in {1..200}; do [[ -S /run/volparossa/control/agent.sock ]] && break; sleep .1; done
test -S /run/volparossa/control/agent.sock
systemctl is-active --quiet volparossa-agent

stage=seed
install -d -m 0700 -o mapowner -g mapowner /run/map-owner
chmod 0755 "$root" "$root/src" "$root/bin"
chmod 0644 src/*.py roads.json Monaco.mwm
chmod 0755 bin/remember_traffic_manifest
python3 - <<'PAYLOAD'
import hashlib,json,time
from pathlib import Path
now=int(time.time())
with open("Monaco.mwm","rb") as src: binding=hashlib.file_digest(src,"sha256").hexdigest()
assert binding=="5ae7d547e0940862cea624c6c8248c6abbcf125f817411cf55598c4ecad0fada"
roads=json.load(open("roads.json"))["fixture_roads"]
assert 0 < len(roads) <= 4096
payload=dict(format="volparossa-road-traffic-v1",region="Monaco",map_version=260928,map_sha256=binding,
             window_start=now-30,window_end=now,expires=now+105,roads=roads)
Path("payload.json").write_text(json.dumps(payload,separators=(",",":")))
PAYLOAD
install -o volparossa -g volparossa -m 0600 payload.json "$state/public.json"
runuser -u volparossa -- volparossa content publish --input "$state/public.json" --cache "$state/traffic-cache" \
    --identity "$state/identity.key" --passphrase-file "$state/trial-passphrase" --manifest "$state/trial-manifest" \
    --name traffic/Monaco/260928 --revision 1 --content-type application/vnd.volparossa.road-traffic.v1+json \
    --lifetime-seconds 300 --quota-bytes 2097152 --max-entries 64 --min-free-bytes 67108864 > publication.json
key=$(python3 -c 'import json;print(json.load(open("publication.json"))["publisher_key_hex"])')
runuser -u volparossa -- "$root/bin/remember_traffic_manifest" "$state/traffic-cache" "$state/trial-manifest" "$key"
rm -- "$state/public.json" "$state/trial-manifest" "$state/trial-passphrase"
stage=adapter
test "$(id -u volparossa)" != "$(id -u mapowner)"
runuser -u mapowner -- test ! -r "$state/identity.key"
runuser -u mapowner -- test ! -r "$state/traffic-cache"
unshare --net -- runuser -u mapowner -- sh -ec '
    test "$(ip -o link show | wc -l)" = 1
    ip link show lo >/dev/null
    exec python3 -B /opt/map-trial/src/core_traffic.py --core /usr/bin/volparossa \
      --control-socket /run/volparossa/control/agent.sock --publisher-key "$1" --name traffic/Monaco/260928 \
      --min-revision 1 --cache /var/lib/volparossa/traffic-cache --region Monaco --map-version 260928 \
      --map-sha256 5ae7d547e0940862cea624c6c8248c6abbcf125f817411cf55598c4ecad0fada \
      --reuse-cache --cache-only --output /run/map-owner/handoff.json' map-owner "$key" > adapter.json
test "$(stat -c %a /run/map-owner/handoff.json)" = 600
test "$(stat -c %U /run/map-owner/handoff.json)" = mapowner
stage=handoff
python3 - <<'HANDOFF'
import base64,hashlib,json,time
from pathlib import Path
received=Path("/run/map-owner/handoff.json").read_bytes()
assert 0 < len(received) <= 2*1024*1024
h=json.loads(received); a=json.load(open("adapter.json")); p=json.load(open("publication.json"))
assert a["operation"]=="traffic_snapshot_received" and a["peer_bytes"]==a["providers_used"]==0
assert a["revision"]==p["revision"]==h["revision"]==1 and h["publisher_key"]==p["publisher_key_hex"]
assert h["manifest_id"]==p["manifest_id"] and int(time.time()) < h["snapshot"]["expires"] <= p["expires_unix_seconds"]
assert h["snapshot"]["roads"]==json.load(open("payload.json"))["roads"]
receipt=dict(publisher=h["publisher_key"],manifest=h["manifest_id"],revision=h["revision"],
             expires=h["snapshot"]["expires"],handoff_sha256=hashlib.sha256(received).hexdigest(),
             map_sha256=h["snapshot"]["map_sha256"],roads=len(h["snapshot"]["roads"]),peer_bytes=0,
             providers_used=0,distinct_app_owner=True,app_owner_output_mode="0600")
with open("/dev/virtio-ports/map.handoff","w") as port:
    port.write(json.dumps(dict(receipt=receipt,bytes=base64.b64encode(received).decode()),separators=(",",":"))+"\n")
Path("/run/map-result.json").write_text(json.dumps(receipt))
HANDOFF
stage=cleanup
rm -- /run/map-owner/handoff.json
rmdir /run/map-owner
systemctl stop volparossa-agent volparossa-helper
test ! -S /run/volparossa/control/agent.sock
test ! -S /run/volparossa/helper.sock
test "$(systemctl show --property=MainPID --value volparossa-agent)" = 0
test "$(systemctl show --property=MainPID --value volparossa-helper)" = 0
cat /run/map-result.json > "$status"
