#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fixed official archive acquisition/extraction for the guarded hosted fixture only.

No SDK manager, license acceptance, arbitrary URLs or silently installed tools.
Google's published SHA1 is the acquisition pin, not a collision-resistant release
signature. Observed SHA256 receipts do not upgrade that trust claim.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import os
import shutil
import stat
import time
import urllib.parse
import urllib.request
import zipfile
from prepare import pins, require


def safe(name):
    require(isinstance(name, str) and 0 < len(name) <= 512 and "\\" not in name
            and all(32 <= ord(c) < 127 for c in name), "ARCHIVE_PATH")
    parts = name.split("/")
    require(all(p not in ("", ".", "..") for p in parts) and len(parts) <= 32
            and not PurePosixPath(name).is_absolute(), "ARCHIVE_PATH")
    return name


class Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, original):
        self.original, self.count = original, 0

    def redirect_request(self, request, response, code, message, headers, url):
        self.count += 1
        parsed = urllib.parse.urlsplit(url)
        require(self.original == pins()["archives"]["gradle"]["url"] and self.count <= 2
                and parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and (url ==
                "https://github.com/gradle/gradle-distributions/releases/download/v9.6.0/gradle-9.6.0-bin.zip"
                or parsed.hostname == "release-assets.githubusercontent.com"), "ARCHIVE_REDIRECT")
        return super().redirect_request(request, response, code, message, headers, url)


def fetch(pin, target):
    expected = hashlib.new(pin["algorithm"])
    observed = hashlib.sha256()
    opener = urllib.request.build_opener(Redirects(pin["url"]))
    total, stop = 0, time.monotonic() + 600
    with opener.open(pin["url"], timeout=30) as source, target.open("xb") as output:
        while True:
            require(time.monotonic() < stop, "ARCHIVE_DEADLINE")
            block = source.read(1024**2)
            if not block:
                break
            total += len(block)
            require(total <= pin["bytes"], "ARCHIVE_BOUND")
            expected.update(block); observed.update(block); output.write(block)
    require(total == pin["bytes"] and expected.hexdigest() == pin["digest"], "ARCHIVE_DIGEST")
    return dict(bytes=total, sha256=observed.hexdigest(), acquisition_algorithm=pin["algorithm"],
                acquisition_digest=expected.hexdigest(), checksum_source=pin["checksum_source"])


def extract(archive, destination, prefix):
    require(not destination.exists() and not destination.is_symlink(), "NEW_TOOL_OUTPUT")
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        require(len(members) <= 150000, "ARCHIVE_MEMBERS")
        records, expanded = {}, 0
        for item in members:
            name = safe(item.filename.rstrip("/"))
            if prefix:
                require(name == prefix or name.startswith(prefix + "/"), "ARCHIVE_ROOT")
                name = name[len(prefix):].lstrip("/")
            if not name:
                continue
            require(name not in records and not item.flag_bits & 1, "ARCHIVE_DUPLICATE")
            mode = item.external_attr >> 16
            kind = stat.S_IFMT(mode)
            require(kind in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK), "ARCHIVE_TYPE")
            require(0 <= item.file_size <= 4 * 1024**3, "ARCHIVE_FILE_BOUND")
            expanded += item.file_size
            require(expanded <= 12 * 1024**3, "ARCHIVE_EXPANDED_BOUND")
            records[name] = item
        links = {name for name, item in records.items() if stat.S_ISLNK(item.external_attr >> 16)}
        for name in records:
            require(not any(p.as_posix() in links for p in PurePosixPath(name).parents), "ARCHIVE_LINK_PARENT")
        destination.mkdir(parents=True, mode=0o700)
        for name, item in records.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir():
                path.mkdir(exist_ok=True)
            elif name in links:
                raw = source.read(item)
                require(len(raw) <= 512, "ARCHIVE_LINK_BOUND")
                target = raw.decode("ascii")
                require(not target.startswith("/") and "\\" not in target
                        and (path.parent / target).resolve().is_relative_to(destination.resolve()), "ARCHIVE_LINK_ESCAPE")
                path.symlink_to(target)
            else:
                with source.open(item) as inp, path.open("xb") as out:
                    shutil.copyfileobj(inp, out, 1024**2)
                path.chmod(0o700 if item.external_attr >> 16 & 0o111 else 0o600)
        for name in links:
            require((destination / name).resolve(strict=True).is_relative_to(destination.resolve()), "ARCHIVE_LINK_TARGET")


def materialize(root):
    # No command-line --fetch entry point; guard even an accidental direct call.
    from hosted import guards
    guards(os.environ)
    require(not root.exists() and root.parent.resolve(strict=True) == root.parent, "NEW_TOOL_ROOT")
    root.mkdir(mode=0o700)
    receipts = {}
    for name, pin in pins()["archives"].items():
        archive = root / (name + ".zip")
        receipts[name] = fetch(pin, archive)
        extract(archive, root / pin["destination"], pin["strip_prefix"])
        archive.unlink()
    (root / "acquisition.json").write_text(json.dumps(receipts, indent=2) + "\n")
    return receipts
