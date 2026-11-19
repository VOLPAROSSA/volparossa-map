#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Apply the Linux desktop traffic integration to a clean, independently pinned upstream checkout."""
import argparse
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
args = argparse.ArgumentParser(description=__doc__)
args.add_argument("upstream", type=Path)
args.add_argument("--routing-probe", action="store_true", help="also configure the disposable Monaco test executable")
selected = args.parse_args()
source = selected.upstream.resolve(strict=True)


def git(*command):
    return subprocess.check_output(["git", "-C", source, *command], text=True).strip()


expected = json.loads((root / "upstream.lock.json").read_text())["commit"]
if git("rev-parse", "HEAD") != expected or git("status", "--porcelain", "--untracked-files=all"):
    raise SystemExit("a clean checkout at the exact Organic Maps lock is required")
cmake = root / "tests/native-traffic/probe.cmake"
if selected.routing_probe and "]]" in str(cmake):
    raise SystemExit("unsupported CMake bracket delimiter in checkout path")
patch = Path(__file__).with_name("organicmaps.patch")
git("apply", "--check", str(patch))
git("apply", str(patch))
if selected.routing_probe:
    with (source / "qt/CMakeLists.txt").open("a") as out:
        out.write(f"\ninclude([[{cmake}]])\n")
