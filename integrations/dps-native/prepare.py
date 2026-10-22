#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Auditable source-only preparation. No tool execution, acquisition or installation.

Inputs must be clean exact Git checkouts. Output must be a new disposable path;
source repositories, notices, location callbacks and existing modules stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
SETTINGS_ADD = b"\n// VOLPAROSSA synthetic DPS quality fixture; no dependency on :sdk.\ninclude ':volparossa_dps_jni'\n"


def require(value, code):
    if not value:
        raise ValueError(code)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], timeout=60)


def digest(raw):
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def pins():
    return json.loads((HERE / "pins.json").read_text())


def verify(root, pin):
    require(root.resolve(strict=True) == root and root.is_dir(), "SOURCE_PATH")
    require(git(root, "rev-parse", "HEAD").decode().strip() == pin["revision"], "SOURCE_REVISION")
    require(not git(root, "status", "--porcelain", "--untracked-files=all"), "SOURCE_DIRTY")
    require(git(root, "rev-parse", "--is-shallow-repository").strip() == b"false", "SOURCE_HISTORY")
    for name, expected in pin["files"].items():
        path = root / name
        require(path.is_file() and not path.is_symlink() and digest(path.read_bytes()) == expected, "SOURCE_BYTES")


def prepare(upstream, core, output):
    locked = pins()
    upstream, core, output = Path(upstream).absolute(), Path(core).absolute(), Path(output).absolute()
    verify(upstream, locked["organicmaps"])
    verify(core, locked["core"])
    require(output.parent.resolve(strict=True) == output.parent and not output.exists()
            and not output.is_symlink() and not output.is_relative_to(upstream)
            and not output.is_relative_to(core), "NEW_DISPOSABLE_OUTPUT")
    # Preserve full Git metadata for upstream's version.sh. Never vendor this clone.
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(upstream), str(output)], check=True, timeout=180)
    git(output, "checkout", "--quiet", "--detach", locked["organicmaps"]["revision"])
    verify(output, locked["organicmaps"])
    settings = output / "android/settings.gradle"
    before = settings.read_bytes()
    module = output / "android/volparossa_dps_jni"
    require(not module.exists() and b"volparossa_dps_jni" not in before, "UPSTREAM_MODULE_COLLISION")
    shutil.copytree(HERE / "module", module)
    settings.write_bytes(before + SETTINGS_ADD)
    ffi = core / "crates/volparossa-dps-ffi"
    include = module / "rust/include"
    include.mkdir(parents=True)
    shutil.copyfile(ffi / "include/volparossa_dps.h", include / "volparossa_dps.h")
    fixtures = ffi / "tests/fixtures"
    manifest = json.loads((fixtures / "manifest.json").read_text())
    require(manifest["synthetic_only"] is True and manifest["actual_android_acceptance"] is False
            and len(manifest["vectors"]) == 4, "SYNTHETIC_FIXTURES_ONLY")
    assets = module / "src/androidTest/assets"
    assets.mkdir(parents=True)
    debug_assets = module / "src/debug/assets"
    debug_assets.mkdir(parents=True)
    for row in manifest["vectors"]:
        require(row["file"] in {"relative_consistent.pb", "relative_conflict.pb", "stale.pb", "empty.pb"}
                and digest((fixtures / row["file"]).read_bytes()) == {k: row[k] for k in ("bytes", "sha256")}, "FIXTURE_BINDING")
        shutil.copyfile(fixtures / row["file"], assets / row["file"])
        shutil.copyfile(fixtures / row["file"], debug_assets / row["file"])
    shutil.copyfile(fixtures / "manifest.json", assets / "manifest.json")
    changed = git(output, "diff", "--name-only").decode().splitlines()
    require(changed == ["android/settings.gradle"], "ADDITIVE_TRANSFORM_ONLY")
    for name, expected in locked["organicmaps"]["files"].items():
        if name != "android/settings.gradle":
            require(digest((output / name).read_bytes()) == expected, "UPSTREAM_PRESERVATION")
    receipt = dict(schema=1, kind="source-preparation-only", organicmaps=locked["organicmaps"]["revision"],
                   core=locked["core"]["revision"], settings_before=digest(before),
                   settings_after=digest(settings.read_bytes()), files={
                       str(p.relative_to(module)): digest(p.read_bytes()) for p in sorted(module.rglob("*")) if p.is_file()},
                   native_executed=False, android_acceptance=False, sensor_access=False)
    (output / "dps-preparation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.upstream, args.core, args.output)


if __name__ == "__main__":
    main()
