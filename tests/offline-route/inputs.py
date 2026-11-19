#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch these exact publicly released files; retain only digests in the receipt."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

root = Path(sys.argv[1])
pins = json.loads(Path(__file__).with_name("inputs.json").read_bytes())


def download(url, path, size, digest):
    subprocess.run(["curl", "-fsSL", "--proto", "=https", "--proto-redir", "=https",
                    "--max-redirs", "3", "--max-time", "180", "--max-filesize", str(size),
                    "--output", path, url], check=True)
    with path.open("rb") as stream:
        assert path.stat().st_size == size and hashlib.file_digest(stream, "sha256").hexdigest() == digest


assert pins["source"] == json.loads(Path("upstream.lock.json").read_bytes())["commit"]
apk = root / "OrganicMaps.apk"
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
