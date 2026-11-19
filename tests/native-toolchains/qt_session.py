# SPDX-License-Identifier: GPL-3.0-only
"""Shared setup for the genuine offline Organic Maps desktop."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

PINS = json.loads(Path(__file__).resolve().parents[1].joinpath("offline-route/inputs.json").read_bytes())
POINTS = ("43.73731,7.42668,18", "43.73932,7.42740,18")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def offline_profile(upstream, inputs, profile):
    assert [name for _, name in socket.if_nameindex()] == ["lo"]
    assert subprocess.check_output(["git", "-C", upstream, "rev-parse", "HEAD"], text=True).strip() == PINS["source"]
    profile.mkdir(mode=0o700)
    maps = profile / "maps" / str(PINS["data_version"])
    maps.mkdir(parents=True)
    for name, (size, _, expected) in PINS["maps"].items():
        item = inputs / f"{name}.mwm"
        assert item.stat().st_size == size and digest(item) == expected
        shutil.copyfile(item, maps / item.name)
    runtime = profile / "runtime"
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(profile), XDG_CONFIG_HOME=str(profile / "config"),
               XDG_RUNTIME_DIR=str(runtime), LIBGL_ALWAYS_SOFTWARE="1", QT_QPA_PLATFORM="xcb")
    gl = subprocess.check_output(["glxinfo", "-B"], text=True, env=env)
    renderer = next(line.split(": ", 1)[1] for line in gl.splitlines() if line.startswith("OpenGL renderer string: "))
    assert "llvmpipe" in renderer.lower()
    return maps, env, renderer


def key_in_window(pid, title, key):
    windows = subprocess.check_output(["xdotool", "search", "--all", "--onlyvisible", "--sync",
                                       "--pid", str(pid), "--name", title], text=True, timeout=30)
    subprocess.run(["xdotool", "windowfocus", windows.splitlines()[0], "key", key], check=True)


def first_run(pid):
    key_in_window(pid, "^Organic Maps$", "Return")
    key_in_window(pid, "^Welcome to Organic Maps", "Escape")


def verify_completed_profile(log, maps):
    assert f"Loaded countries list for version: {PINS['data_version']}" in log
    for name, (size, _, _) in PINS["maps"].items():
        assert f"Found file: {name} in directory: {maps} with size: {size}" in log
    assert "Organic Maps finished with code 0" in log
