#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch a public, publisher-authenticated traffic snapshot through the existing core.

Linux development adapter, not a mobile binding or a location collector. The core
owns discovery, signatures, peer transport, policy and the cached revision floor.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time

from traffic_snapshot import TrafficSnapshotError, validate_snapshot

MAX_PAYLOAD = 2 * 1024 * 1024
MAX_RECEIPT = 256 * 1024
HEX32 = re.compile(r"[0-9a-f]{64}\Z")


class TrafficFetchError(ValueError):
    """Closed error codes; do not reflect peers, paths, source bytes or stderr."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise TrafficFetchError(code)


def natural(value, minimum=0, maximum=2**64 - 1):
    return type(value) is int and minimum <= value <= maximum


def canonical_path(value):
    path = Path(value)
    require(path.is_absolute() and path != Path("/")
            and str(path) == str(value) and path.resolve() == path
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in str(path)),
            "invalid_path")
    return path


def private_directory(path):
    path = canonical_path(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, "private_directory_required")


def validate_configuration(args):
    require(HEX32.fullmatch(args.publisher_key) is not None, "invalid_publisher")
    require(1 <= len(args.name.encode("utf-8")) <= 128
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in args.name), "invalid_name")
    require(natural(args.min_revision, 1) and natural(args.timeout, 1, 600), "invalid_limits")
    for key in ("core", "control_socket", "cache", "output"):
        canonical_path(getattr(args, key))
    info = Path(args.core).stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.getuid())
            and info.st_mode & 0o111 and not info.st_mode & 0o022, "invalid_core_executable")
    info = Path(args.control_socket).stat()
    require(stat.S_ISSOCK(info.st_mode) and not info.st_mode & 0o007, "invalid_control_socket")
    private_directory(Path(args.output).parent)
    require(not os.path.lexists(args.output), "output_exists")
    # Dataset parameters are checked before contacting peers too. An empty snapshot is
    # a valid clear; constructing it here grants no authority to received traffic.
    now = int(time.time())
    probe = {"format": "volparossa-road-traffic-v1", "region": args.region,
             "map_version": args.map_version, "map_sha256": args.map_sha256,
             "window_start": now - 60, "window_end": now, "expires": now + 60, "roads": []}
    validate_snapshot(json.dumps(probe).encode(), region=args.region, map_version=args.map_version,
                      map_sha256=args.map_sha256, now=now, publication_expires=now + 60)


def command(args, output):
    argv = [args.core, "--control-socket", args.control_socket, "content", "fetch-name",
            "--publisher-key", args.publisher_key, "--name", args.name,
            "--min-revision", str(args.min_revision), "--cache", args.cache,
            "--local-output", str(output), "--quota-bytes", str(MAX_PAYLOAD),
            "--max-entries", "64"]
    if args.reuse_cache:
        argv.append("--reuse-cache")
    return argv


def run_core(argv, timeout):
    """Bound output and elapsed time, and join this exact process on every failure."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    output = bytearray()
    deadline = time.monotonic() + timeout
    counts = {"stdout": 0, "stderr": 0}
    complete = False
    try:
        with selectors.DefaultSelector() as selector:
            for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, "core_timeout")
                for key, _ in selector.select(min(remaining, 0.2)):
                    part = os.read(key.fd, 65536)
                    if not part:
                        selector.unregister(key.fileobj)
                        continue
                    counts[key.data] += len(part)
                    require(counts[key.data] <= (MAX_RECEIPT if key.data == "stdout" else 65536),
                            "core_output_limit")
                    if key.data == "stdout":
                        output.extend(part)
            require(process.wait(timeout=max(0.001, deadline - time.monotonic())) == 0, "core_failed")
        complete = True
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise TrafficFetchError("core_timeout") from None
    finally:
        if not complete:
            # The leader may have exited while a descendant still owns the pipes.
            # Kill only the process group created above, not arbitrary host processes.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        process.stdout.close()
        process.stderr.close()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "invalid_core_receipt")
        result[key] = value
    return result


def receipt(raw, args, payload, output, now):
    try:
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        raise TrafficFetchError("invalid_core_receipt") from None
    require(type(value) is dict, "invalid_core_receipt")
    require(value.get("operation") == "named_content_download"
            and value.get("publisher_key") == args.publisher_key and value.get("name") == args.name
            and natural(value.get("revision"), args.min_revision)
            and value.get("local_delivery") is True and value.get("output_mode") == "0600"
            and value.get("ownership_changed") is False
            and value.get("origin_authenticated") is False and value.get("globally_latest") is False
            and value.get("local_output") == str(output)
            and natural(value.get("bytes"), 1, MAX_PAYLOAD) and value["bytes"] == len(payload)
            and value.get("sha256") == hashlib.sha256(payload).hexdigest()
            and type(value.get("manifest_id")) is str and HEX32.fullmatch(value["manifest_id"])
            and natural(value.get("publication_expires_unix_seconds"), now + 1), "invalid_core_receipt")
    return value


def read_private_output(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= MAX_PAYLOAD, "invalid_core_file")
        payload = stream.read(MAX_PAYLOAD + 1)
        after = os.fstat(stream.fileno())
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(len(payload) == before.st_size
                and all(getattr(before, key) == getattr(after, key) for key in stable), "invalid_core_file")
        return payload


def fetch(args):
    validate_configuration(args)
    destination = Path(args.output)
    with tempfile.TemporaryDirectory(prefix=".volparossa-traffic-", dir=destination.parent) as scratch:
        local = Path(scratch) / "signed-content.json"
        raw = run_core(command(args, local), args.timeout)
        payload = read_private_output(local)
        now = int(time.time())
        verified = receipt(raw, args, payload, local, now)
        snapshot = validate_snapshot(payload, region=args.region, map_version=args.map_version,
                                     map_sha256=args.map_sha256, now=now,
                                     publication_expires=verified["publication_expires_unix_seconds"])
        # This local envelope is not independently signed. It is a private handoff from
        # this verified operation, never a public re-publication or portable peer proof.
        result = {"format": "volparossa-local-traffic-v1", "publisher_key": args.publisher_key,
                  "revision": verified["revision"], "manifest_id": verified["manifest_id"],
                  "snapshot": snapshot}
        candidate = Path(scratch) / "accepted.json"
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        require(int(time.time()) < snapshot["expires"], "snapshot_expired")
        try:
            os.link(candidate, destination, follow_symlinks=False)
        except FileExistsError:
            raise TrafficFetchError("output_exists") from None
        # Native consumers must recheck expiry and geometry when applying/reading this
        # snapshot and clear both the renderer and routing cache at expiry.
        return {"operation": "traffic_snapshot_received", "roads": len(snapshot["roads"]),
                "expires": snapshot["expires"], "revision": verified["revision"],
                "native_map_applied": False, "mobile_integration_proven": False,
                "independent_observations_proven": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "control-socket", "publisher-key", "name", "cache", "output",
                 "region", "map-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--map-version", required=True, type=int)
    parser.add_argument("--min-revision", required=True, type=int)
    parser.add_argument("--timeout", default=600, type=int)
    parser.add_argument("--reuse-cache", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(fetch(args), separators=(",", ":")))
        return 0
    except (TrafficFetchError, TrafficSnapshotError) as error:
        print(json.dumps({"operation": "traffic_snapshot_failed", "code": error.code}))
    except (OSError, ValueError, OverflowError):
        print(json.dumps({"operation": "traffic_snapshot_failed", "code": "local_io_failed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
