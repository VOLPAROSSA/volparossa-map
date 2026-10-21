#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Inert source bindings only. Does not establish JNI/controller/Android behavior."""
import ast
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from prepare import HERE, pins, require


def main():
    locked = pins()
    upstream = json.loads((HERE.parents[1] / "upstream.lock.json").read_text())
    require(locked["organicmaps"]["revision"] == upstream["commit"], "UPSTREAM_PIN")
    require(locked["core"]["revision"] == "4d4612200e0a4a46546e0c827a3f3e7f0b73c669", "CORE_PIN")
    require(locked["rust"] == "1.85.0" and locked["rust_target"] == "x86_64-linux-android", "RUST_PIN")
    for source in HERE.glob("*.py"):
        ast.parse(source.read_text(), filename=str(source))
    for pin in locked["archives"].values():
        require(pin["url"].startswith(("https://dl.google.com/android/repository/", "https://services.gradle.org/distributions/"))
                and type(pin["bytes"]) is int and 0 < pin["bytes"] < 2 * 1024**3
                and pin["algorithm"] in ("sha1", "sha256")
                and re.fullmatch("[0-9a-f]{" + str(40 if pin["algorithm"] == "sha1" else 64) + "}", pin["digest"]), "TOOL_PIN")
    module = HERE / "module"
    ns = "{http://schemas.android.com/apk/res/android}"
    for source in module.rglob("AndroidManifest.xml"):
        root = ET.fromstring(source.read_text())
        require(not root.findall("uses-permission"), "NO_DECLARED_PERMISSION")
        for activity in root.findall("application/activity"):
            require(activity.get(ns + "exported") == "false", "NONEXPORTED_FIXTURE")
    require(not (module / "src/main/java/org/volparossa/map/dps/QualityActivity.java").exists(), "DEBUG_ONLY_ACTIVITY")
    cmake = (module / "src/main/cpp/CMakeLists.txt").read_text()
    require("add_library(volparossa_dps_jni SHARED quality_jni.c)" in cmake and "dps_rust" in cmake
            and "organicmaps" not in cmake and "jni_helper" not in cmake, "ISOLATED_JNI_TARGET")
    forbidden = ("LocationHelper", "nativeLocationUpdated", "SensorManager", "LocationManager",
                 "android.net.", "java.net.", "android.util.Log", "System.out", "SharedPreferences", "SQLiteDatabase")
    for source in list(module.rglob("*.java")) + list(module.rglob("*.c")):
        raw = source.read_text()
        require(all(token not in raw for token in forbidden) and "JNI_OnLoad" not in raw, "NO_PLATFORM_OR_LOGGING_API")
    tests = (module / "src/androidTest/java/org/volparossa/map/dps/QualityInstrumentedTest.java").read_text()
    hosted = (HERE / "hosted.py").read_text()
    require(len(re.findall(r"@Test public void ", tests)) == 8 and "OK (8 tests)" in hosted, "INSTRUMENTATION_INVENTORY")
    workflow = (HERE.parents[1] / ".github/workflows/dps-native.yml").read_text()
    require("run: exec python3 -B integrations/dps-native/hosted.py --reviewed-hosted-fixture" in workflow,
            "DIRECT_SIGNAL_ENTRY")
    print("DPS source bindings passed; no native or Android behavior executed")


if __name__ == "__main__":
    main()
