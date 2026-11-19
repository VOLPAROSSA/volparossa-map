#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Render a verified Core handoff and observe removal or expiry in one native desktop session."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time

from PIL import Image, ImageChops
from qt_session import PINS, POINTS, digest, first_run, key_in_window, offline_profile, verify_completed_profile


def framebuffer(path):
    # The upstream renderer logs Ready just before saving the PNG on the GUI thread.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        png = path.read_bytes() if path.is_file() else b""
        if png.endswith(bytes.fromhex("0000000049454e44ae426082")):
            with Image.open(io.BytesIO(png)) as frame:
                assert frame.size == (384, 288)
                image = frame.convert("RGB")
            path.unlink()
            return image
        time.sleep(0.02)
    raise TimeoutError("native framebuffer did not finish saving")


def red_pixels(image):
    return sum(count for count, (r, g, b) in image.getcolors(image.width * image.height)
               if r > 150 and 10*r > 17*g and 10*r > 14*b)


def sample(base, image):
    r, g, b = ImageChops.difference(base, image).split()
    changed = image.width * image.height - ImageChops.lighter(r, ImageChops.lighter(g, b)).histogram()[0]
    return dict(changed=changed, additional_red=red_pixels(image) - red_pixels(base),
                rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest())


def complete(views):
    return all(len(items) == 2 for items in views)


def trial(args):
    payload = args.handoff.read_bytes()
    envelope = json.loads(payload)
    snapshot = envelope["snapshot"]
    assert envelope["format"] == "volparossa-local-traffic-v1"
    assert envelope["publisher_key"] == args.publisher and envelope["manifest_id"] == args.manifest
    assert snapshot["region"] == "Monaco" and snapshot["map_version"] == PINS["data_version"]
    assert snapshot["map_sha256"] == PINS["maps"]["Monaco"][2] and 0 < len(snapshot["roads"]) <= 4096
    remaining = snapshot["expires"] - time.time()
    assert 0 < remaining <= 300
    maps, env, renderer = offline_profile(args.upstream, args.maps, args.trial)
    feed = args.trial / "feed"
    feed.mkdir(mode=0o700)
    slot = feed / "slot.json"
    frames = args.trial / "frames"
    frames.mkdir()
    # Keep the same two public cameras available until the live wall-clock boundary.
    points = args.trial / "points.txt"
    points.write_text(("\n".join(POINTS) + "\n") * int((remaining + 30) * 12))
    command = [args.app, "--lang=en", f"--data_path={maps.parent}", f"--resources_path={args.upstream / 'data'}",
               "--width=384", "--height=288", f"--dst_path={frames}", f"--points={points}",
               f"--volparossa_traffic={slot}", f"--volparossa_traffic_publisher={args.publisher}",
               "--volparossa_traffic_region=Monaco"]
    log = args.trial / "desktop.log"
    bases = {}
    samples = {state: [[], []] for state in ("absent", "visible", "cleared")}
    submitted = removed = closing = False
    accepts = clears = total = 0
    state = "absent"
    expected = f"Local advisory traffic accepted {envelope['revision']} directed records {len(snapshot['roads'])}"
    deadline = time.monotonic() + remaining + 75
    with log.open("wb") as output, log.open() as events:
        app = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, env=env)
        try:
            first_run(app.pid)
            while app.poll() is None and time.monotonic() < deadline:
                for line in events:
                    if "Local advisory traffic accepted" in line:
                        assert submitted and line.rstrip().endswith(expected)
                        state = "visible"
                        accepts += 1
                    if "Local advisory traffic cleared" in line:
                        assert state == "visible"
                        assert removed if args.mode == "removal" else time.time() >= snapshot["expires"]
                        state = "cleared"
                        clears += 1
                    ready = re.search(r"file: (point_(\d+)_en) state: Ready", line)
                    if not ready:
                        continue
                    total += 1
                    camera = int(ready[2]) % len(POINTS)
                    image = framebuffer(frames / (ready[1] + ".png"))
                    if camera not in bases:
                        assert state == "absent"
                        bases[camera] = image
                    measurement = sample(bases[camera], image)
                    changed, red = measurement["changed"], measurement["additional_red"]
                    visible = changed >= 256 and red >= 32
                    background = changed <= 32 and red == 0
                    if (visible if state == "visible" else background) and len(samples[state][camera]) < 2:
                        samples[state][camera].append(measurement)
                if not submitted and complete(samples["absent"]):
                    staging = feed / "pending"
                    with staging.open("xb") as file:
                        os.fchmod(file.fileno(), 0o600)
                        file.write(payload)
                    staging.replace(slot)
                    submitted = True
                if args.mode == "removal" and not removed and complete(samples["visible"]):
                    assert time.time() < snapshot["expires"]
                    slot.unlink()
                    removed = True
                if not closing and complete(samples["visible"]) and complete(samples["cleared"]):
                    key_in_window(app.pid, "^Organic Maps$", "ctrl+q")
                    closing = True
                time.sleep(0.1)
            assert app.wait(timeout=3) == 0
        finally:
            if app.poll() is None:
                app.kill()
                app.wait()
    assert accepts == clears == 1 and closing and all(complete(views) for views in samples.values())
    assert samples["absent"][0][0]["rgb_sha256"] != samples["absent"][1][0]["rgb_sha256"]
    verify_completed_profile(log.read_text(), maps)
    return dict(mode=args.mode, upstream=PINS["source"], executable_sha256=digest(args.app), renderer=renderer,
                original_handoff_sha256=hashlib.sha256(payload).hexdigest(), directed_segments=len(snapshot["roads"]),
                maps_version=snapshot["map_version"], revision=envelope["revision"], interfaces=["lo"],
                native_accepts=accepts, native_clears=clears, frames_compared=total, frame_size=[384, 288],
                samples=samples, application_exit=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("expiry", "removal"))
    for name in ("app", "upstream", "maps", "handoff", "trial"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--publisher", required=True, help="public signer from the independent Core fixture")
    parser.add_argument("--manifest", required=True, help="manifest ID from Core's publication receipt")
    args = parser.parse_args()
    receipt = trial(args)
    (args.trial / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
