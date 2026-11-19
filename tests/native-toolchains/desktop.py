#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run the original Qt/Drape renderer in a disposable, offline display."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

from PIL import Image

app, source, trial = map(Path, sys.argv[1:])
pins = json.loads(Path(__file__).resolve().parents[1].joinpath("offline-route/inputs.json").read_bytes())
assert [name for _, name in socket.if_nameindex()] == ["lo"]
assert subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip() == pins["source"]
version = str(pins["data_version"])
profile = trial / "qt-profile"
profile.mkdir(mode=0o700)
data = profile / "maps"
maps = data / version
maps.mkdir(parents=True)
frames = profile / "frames"
frames.mkdir()
for name, (size, _, digest) in pins["maps"].items():
    item = trial / f"{name}.mwm"
    with item.open("rb") as stream:
        assert item.stat().st_size == size and hashlib.file_digest(stream, "sha256").hexdigest() == digest
    shutil.copyfile(item, maps / item.name)
runtime = profile / "runtime"
runtime.mkdir(mode=0o700)
env = dict(os.environ, HOME=str(profile), XDG_CONFIG_HOME=str(profile / "config"),
           XDG_RUNTIME_DIR=str(runtime), LIBGL_ALWAYS_SOFTWARE="1", QT_QPA_PLATFORM="xcb")
gl = subprocess.check_output(["glxinfo", "-B"], text=True, env=env)
renderer = next(line.split(": ", 1)[1] for line in gl.splitlines() if line.startswith("OpenGL renderer string: "))
assert "llvmpipe" in renderer.lower()
log = profile / "desktop.log"
with log.open("wb") as writer:
    process = subprocess.Popen([app, "--lang=en", f"--data_path={data}", f"--resources_path={source / 'data'}",
                                "--points=43.73731,7.42668,18;43.73932,7.42740,18", "--width=384",
                                "--height=288", f"--dst_path={frames}"], stdout=writer, stderr=subprocess.STDOUT, env=env)
    try:
        windows = subprocess.check_output(["xdotool", "search", "--all", "--onlyvisible", "--sync", "--pid",
                                           str(process.pid), "--name", "^Organic Maps$"], text=True, timeout=30)
        subprocess.run(["xdotool", "windowfocus", windows.splitlines()[0], "key", "Return"], check=True)
        welcome = subprocess.check_output(["xdotool", "search", "--all", "--onlyvisible", "--sync", "--pid",
                                            str(process.pid), "--name", "^Welcome to Organic Maps"], text=True, timeout=30)
        subprocess.run(["xdotool", "windowfocus", welcome.splitlines()[0], "key", "Escape"], check=True)
        assert process.wait(timeout=150) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
text = log.read_text()
assert f"Loaded countries list for version: {version}" in text
for name, (size, _, _) in pins["maps"].items():
    assert f"Found file: {name} in directory: {maps} with size: {size}" in text
assert re.findall(r"file: (point_\d+_en) state: Ready", text) == ["point_0000_en", "point_0001_en"]
assert text.count("state: Done") == 1 and "Organic Maps finished with code 0" in text
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
with app.open("rb") as stream:
    executable_digest = hashlib.file_digest(stream, "sha256").hexdigest()
result = dict(map_commit=os.environ["GITHUB_SHA"], upstream=pins["source"], cmake_target="desktop",
              executable_sha256=executable_digest, qt=qt[1], renderer=renderer, interfaces=["lo"],
              maps_version=pins["data_version"], registered_maps=sorted(pins["maps"]), frames=images, application_exit=0)
(trial / "receipt.json").write_text(json.dumps(result, sort_keys=True) + "\n")
