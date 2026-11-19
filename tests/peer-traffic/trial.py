#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Owner-side phases for the disposable Core content-provider integration."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "synthetic-dataset.json"
ADAPTER = ROOT / "core_traffic.py"
NAME = "traffic/Synthetic/1"
ROAD = dict(feature_id=42, segment_index=3, direction=1, speed_group=2, observation_count=8)


def dataset():
    raw = DATASET.read_bytes()
    value = json.loads(raw)
    assert value["roads"][0].items() >= {k: ROAD[k] for k in ("feature_id", "segment_index", "direction")}.items()
    return value["region"], value["version"], hashlib.sha256(raw).hexdigest()


def private(path, mode):
    info = path.lstat()
    assert info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == mode
    assert (stat.S_ISDIR(info.st_mode) if mode == 0o700 else stat.S_ISREG(info.st_mode))


def prepare(path):
    region, version, digest = dataset()
    now = int(time.time())
    snapshot = dict(format="volparossa-road-traffic-v1", region=region, map_version=version,
                    map_sha256=digest, window_start=now - 60, window_end=now,
                    expires=now + 150, roads=[ROAD])
    (path / "payload").write_bytes(json.dumps(snapshot, separators=(",", ":")).encode())
    (path / "passphrase").write_text(secrets.token_hex(32) + "\n")
    for name in ("payload", "passphrase"):
        private(path / name, 0o600)


def consume(args):
    path = args.owner
    region, version, digest = dataset()
    pub = json.loads((path / "public.json").read_bytes())
    phase = args.phase
    assert pub["name"] == NAME and pub["revision"] == 1
    assert args.parent_namespace != os.readlink("/proc/self/ns/net")
    assert os.getuid() != 0 and not os.access(args.cache, os.R_OK)
    output = path / (phase + ".json")
    assert not output.exists()
    command = [sys.executable, "-B", ADAPTER, "--core", args.core, "--control-socket", args.socket,
               "--publisher-key", pub["publisher_key_hex"], "--name", NAME, "--min-revision", "1",
               "--cache", args.cache, "--output", output, "--region", region,
               "--map-version", str(version), "--map-sha256", digest, "--timeout", "90"]
    if phase != "cold":
        command.append("--cache-only")
    if phase == "expired":
        wait = pub["expires_unix_seconds"] + 1 - int(time.time())
        assert wait <= 185
        if wait > 0:
            time.sleep(wait)
    run = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=100, check=False)
    assert len(run.stdout) < 2048 and not run.stderr
    report = json.loads(run.stdout)
    assert not any(path.glob(".volparossa-traffic-*"))
    if phase == "expired":
        assert run.returncode == 1 and report == {"operation":"traffic_snapshot_failed", "code":"core_failed"}
        assert not output.exists()
        previous = json.loads((path / "cold.json").read_bytes())["snapshot"]
        assert time.time() >= previous["expires"] and previous["map_sha256"] == digest
        return dict(phase=phase, expired_publication_refused=True, no_new_handoff=True,
                    signed_expiry=pub["expires_unix_seconds"], observed_at=int(time.time()),
                    synthetic_dataset_sha256=digest)
    assert run.returncode == 0 and report["operation"] == "traffic_snapshot_received", report
    private(output, 0o600)
    accepted = json.loads(output.read_bytes())
    assert accepted["publisher_key"] == pub["publisher_key_hex"] and accepted["manifest_id"] == pub["manifest_id"]
    snapshot = accepted["snapshot"]
    assert accepted["revision"] == 1 and snapshot["region"] == region and snapshot["map_version"] == version
    assert snapshot["map_sha256"] == digest and snapshot["roads"] == [ROAD]
    assert time.time() < snapshot["expires"] <= pub["expires_unix_seconds"]
    assert report["peer_bytes"] == (pub["bytes"] if phase == "cold" else 0)
    assert report["providers_used"] == (1 if phase == "cold" else 0)
    if phase == "warm":
        assert output.read_bytes() == (path / "cold.json").read_bytes()
    return dict(phase=phase, verified_payload_bytes=pub["bytes"], peer_bytes=report["peer_bytes"],
                providers=report["providers_used"], owner_file_mode="0600", synthetic_dataset_sha256=digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "cold", "warm", "expired"])
    parser.add_argument("owner", type=Path)
    parser.add_argument("--core", type=Path)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--parent-namespace")
    args = parser.parse_args()
    assert os.getuid() != 0 and not sys.flags.optimize
    os.umask(0o077)
    private(args.owner, 0o700)
    if args.phase == "prepare":
        prepare(args.owner)
    else:
        print(json.dumps(consume(args), separators=(",", ":")))


if __name__ == "__main__":
    main()
