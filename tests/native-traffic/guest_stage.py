#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stage only the isolated guest's immutable inputs and confirm its returned Core handoff."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
CORE = "7fe73bf18d8f8f0d5b1096d4fd6485c3315a6e69"


def stage(args):
    assert subprocess.check_output(["git", "-C", args.core, "rev-parse", "HEAD"], text=True).strip() == CORE
    with args.mwm.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == "5ae7d547e0940862cea624c6c8248c6abbcf125f817411cf55598c4ecad0fada"
    assert 0 < len(json.loads(args.roads.read_bytes())["fixture_roads"]) <= 4096
    files = {"Monaco.mwm": args.mwm, "roads.json": args.roads}
    for binary in ("volparossa", "volparossa-agent", "volparossa-helper"):
        files["bin/" + binary] = args.binaries / binary
    files["bin/remember_traffic_manifest"] = args.binaries / "examples/remember_traffic_manifest"
    for unit in ("volparossa-helper.service", "volparossa-agent.service", "volparossa.sysusers", "volparossa.tmpfiles"):
        files["units/" + unit] = args.core / "packaging/systemd" / unit
    for module in ("core_traffic.py", "traffic_snapshot.py"):
        files["src/" + module] = ROOT / "src" / module
    with tarfile.open(args.output, "x") as archive:
        for dest, file in files.items():
            archive.add(file, arcname=dest, recursive=False)


def receive(args):
    stop = time.monotonic() + 420
    while time.monotonic() < stop:
        if args.packet.is_file() and 0 < args.packet.stat().st_size <= 3 * 1024 * 1024:
            packet = args.packet.read_bytes()
            if packet.endswith(b"\n"):
                break
        time.sleep(.1)
    else:
        raise TimeoutError("guest handoff unavailable")
    data = json.loads(packet)
    raw = base64.b64decode(data["bytes"], validate=True)
    receipt = data["receipt"]
    envelope = json.loads(raw)
    assert len(raw) <= 2 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == receipt["handoff_sha256"]
    assert receipt["publisher"] == envelope["publisher_key"] and receipt["manifest"] == envelope["manifest_id"]
    assert receipt["revision"] == envelope["revision"] and receipt["expires"] == envelope["snapshot"]["expires"]
    assert int(time.time()) < receipt["expires"] and receipt["peer_bytes"] == receipt["providers_used"] == 0
    assert receipt["distinct_app_owner"] and receipt["app_owner_output_mode"] == "0600"
    args.output.mkdir(mode=0o700)
    for name, value in (("handoff.json", raw), ("source.json", json.dumps(receipt).encode())):
        with (args.output / name).open("xb") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(value)


parser = argparse.ArgumentParser(description=__doc__)
subs = parser.add_subparsers(dest="op", required=True)
for name, options in (("stage", ("core", "binaries", "mwm", "roads", "output")), ("receive", ("packet", "output"))):
    sub = subs.add_parser(name)
    for option in options:
        sub.add_argument("--" + option, type=Path, required=True)
args = parser.parse_args()
(stage if args.op == "stage" else receive)(args)
