#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Bind Core's original notice collector to the packages used to build this static library."""
import json
from pathlib import Path
import runpy
import sys

core, metadata_path, messages_path = map(Path, sys.argv[1:])
closure = runpy.run_path(str(core / "packaging/cargo-notice-closure.py"))["select_closure"]
metadata = json.loads(metadata_path.read_bytes())
with messages_path.open() as messages:
    compiled = {message["package_id"] for line in messages
                if (message := json.loads(line)).get("reason") == "compiler-artifact"}
root = metadata["resolve"]["root"]
if not root or root not in compiled or root not in metadata["workspace_members"]:
    raise ValueError("missing compiled FFI workspace root")
# Retain Cargo's actual IDs and dependency edges; the other Core applications were not linked.
metadata["workspace_members"] = [pkg for pkg in metadata["workspace_members"] if pkg in compiled]
selected = closure(metadata)
if not compiled.issubset({pkg["id"] for pkg in selected["packages"]}):
    raise ValueError("a compiled package is absent from the selected license graph")
json.dump(selected, sys.stdout)
