#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch these exact publicly released files; retain only digests in the receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--upstream", type=Path, help="use this pinned source tree's release catalogue")
parser.add_argument("--built-apk", type=Path, help="use a locally assembled WebDebug x86-64 package")
args = parser.parse_args()
if args.built_apk and not args.upstream:
    parser.error("--built-apk requires --upstream")
root = args.root
pins = json.loads(Path(__file__).with_name("inputs.json").read_bytes())


def download(url, path, size, digest):
    subprocess.run(["curl", "-fsSL", "--proto", "=https", "--proto-redir", "=https",
                    "--max-redirs", "3", "--max-time", "180", "--max-filesize", str(size),
                    "--output", path, url], check=True)
    with path.open("rb") as stream:
        assert path.stat().st_size == size and hashlib.file_digest(stream, "sha256").hexdigest() == digest


assert pins["source"] == json.loads(Path("upstream.lock.json").read_bytes())["commit"]
apk = root / "OrganicMaps.apk"
if args.upstream:
    head = subprocess.check_output(["git", "-C", args.upstream, "rev-parse", "HEAD"], text=True).strip()
    assert head == pins["source"]
    catalog = json.loads((args.upstream / "data/countries.json").read_bytes())
    if args.built_apk:
        with zipfile.ZipFile(args.built_apk) as archive:
            assert archive.getinfo("lib/x86_64/liborganicmaps.so").file_size > 0
            assert json.loads(archive.read("assets/countries.json")) == catalog
        shutil.copyfile(args.built_apk, apk)
        with apk.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        (root / "web-debug-apk.sha256").write_text(digest + "\n")
else:
    download("https://github.com/organicmaps/organicmaps/releases/download/" + pins["release"] +
             "/OrganicMaps-26092932-web-release.apk", apk, 63888223, pins["apk_sha256"])
    with zipfile.ZipFile(apk) as archive:
        assert archive.getinfo("lib/x86_64/liborganicmaps.so").file_size > 0
        catalog = json.loads(archive.read("assets/countries.json"))
assert catalog["v"] == pins["data_version"]


def leaves(node):
    yield node
    for child in node.get("g", []):
        yield from leaves(child)


for region, (size, short_hash, digest) in pins["maps"].items():
    entry, = [node for node in leaves(catalog) if node["id"] == region]
    assert (entry["s"], entry["h"]) == (size, short_hash)
    download(f"https://cdn.organicmaps.app/maps/{catalog['v']}/{region}.mwm", root / (region + ".mwm"), size, digest)
