#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Measure the pinned native vehicle route before, during and after the local advisory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native-toolchains"))
from qt_session import offline_profile


def wait_file(path, process, deadline):
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_bytes())
        if process.poll() is not None:
            break
        time.sleep(.05)
    raise RuntimeError("native route observation unavailable")


def compact(row):
    geometry = json.dumps(row["geometry"], separators=(",", ":")).encode()
    return {key: value for key, value in row.items() if key not in ("geometry", "fixture_roads")} | {
        "directed_route_segments": len(row["geometry"]), "geometry_sha256": hashlib.sha256(geometry).hexdigest()}


def trial(mode, probe, upstream, inputs, directory, handoff=None, source=None):
    maps, env, _ = offline_profile(upstream, inputs, directory)
    feed = directory / "feed"
    feed.mkdir(mode=0o700)
    publisher = source["publisher"] if source else "0" * 64
    with (directory / "probe.log").open("wb") as log:
        app = subprocess.Popen([probe, upstream / "data", maps.parent, feed, publisher],
                               env=env, stdout=log, stderr=log)
        try:
            first = wait_file(feed / "route-1.json", app, time.monotonic()+30)
            if mode == "baseline":
                return first
            assert first["cached_segments"] == 0 and first["route_advisories"] == 0
            raw = handoff.read_bytes()
            original = json.loads(raw)
            assert original["publisher_key"] == publisher and original["manifest_id"] == source["manifest"]
            assert hashlib.sha256(raw).hexdigest() == source["handoff_sha256"]
            assert int(time.time()) < source["expires"] and source["expires"] == original["snapshot"]["expires"]
            temp = feed / "next.json"
            with temp.open("xb") as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(raw)
            temp.rename(feed / "slot.json")
            applied = wait_file(feed / "route-2.json", app, time.monotonic()+30)
            assert applied["route_advisories"] > 0 and applied["cached_segments"] == source["roads"]
            assert applied["route_seconds"] > first["route_seconds"] and applied["geometry"] != first["geometry"]
            if mode == "replacement":
                mwm = maps / "Monaco.mwm"
                replacement = maps / "Monaco.new"
                shutil.copyfile(mwm, replacement)
                assert mwm.stat().st_ino != replacement.stat().st_ino
                replacement.replace(mwm)
            restored = wait_file(feed / "route-3.json", app, time.monotonic()+310)
            remaining = source["expires"] - int(time.time())
            assert (remaining > 0) if mode == "replacement" else (remaining <= 0)
            assert restored["cached_segments"] == restored["route_advisories"] == 0
            assert restored["geometry"] == first["geometry"] and restored["route_seconds"] == first["route_seconds"]
            return {"mode":mode,"manifest":source["manifest"],"handoff_sha256":source["handoff_sha256"],
                    "remaining_seconds_when_cleared":remaining,"route_states":[compact(r) for r in (first, applied, restored)]}
        finally:
            (feed / "stop").touch()
            try:
                rc = app.wait(15)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait()
                raise
            if rc:
                raise RuntimeError(f"native routing probe exited {rc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("baseline", "expiry", "replacement"))
    for field in ("probe", "upstream", "maps", "trial", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.source.read_bytes()) if args.source else None
    result = trial(args.mode, args.probe, args.upstream, args.maps, args.trial, args.handoff, evidence)
    with args.output.open("x") as out:
        json.dump(result, out, separators=(",", ":"))
