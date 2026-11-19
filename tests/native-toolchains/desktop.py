#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run the original Qt/Drape renderer in a disposable, offline display."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image

from qt_session import PINS, POINTS, digest, first_run, offline_profile, verify_completed_profile

app, source, trial = map(Path, sys.argv[1:])
profile = trial / "qt-profile"
maps, env, renderer = offline_profile(source, trial, profile)
frames = profile / "frames"
frames.mkdir()
log = profile / "desktop.log"
with log.open("wb") as writer:
    process = subprocess.Popen([app, "--lang=en", f"--data_path={maps.parent}", f"--resources_path={source / 'data'}",
                                "--points=" + ";".join(POINTS), "--width=384",
                                "--height=288", f"--dst_path={frames}"], stdout=writer, stderr=subprocess.STDOUT, env=env)
    try:
        first_run(process.pid)
        assert process.wait(timeout=150) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
text = log.read_text()
verify_completed_profile(text, maps)
assert re.findall(r"file: (point_\d+_en) state: Ready", text) == ["point_0000_en", "point_0001_en"]
assert text.count("state: Done") == 1
qt = re.search(r"built with QT: ([\d.]+) runtime QT: ([\d.]+)", text)
assert qt and qt[1] == qt[2]
images = []
for path in sorted(frames.glob("*.png")):
    with Image.open(path) as image:
        assert image.size == (384, 288)
        rgb = image.convert("RGB")
        colours = len(rgb.getcolors(maxcolors=384 * 288))
        assert colours >= 64
        images.append(dict(width=rgb.width, height=rgb.height, colours=colours,
                           rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest()))
assert len(images) == 2 and images[0]["rgb_sha256"] != images[1]["rgb_sha256"]
result = dict(map_commit=os.environ["GITHUB_SHA"], upstream=PINS["source"], cmake_target="desktop",
              executable_sha256=digest(app), qt=qt[1], renderer=renderer, interfaces=["lo"],
              maps_version=PINS["data_version"], registered_maps=sorted(PINS["maps"]), frames=images, application_exit=0)
(trial / "receipt.json").write_text(json.dumps(result, sort_keys=True) + "\n")
