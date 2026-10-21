#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Inert source bindings only. Does not establish JNI/controller/Android behavior."""
import ast
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from prepare import HERE, pins, require


def manifest_boundary(source, module):
    root = ET.fromstring(source.read_text())
    ns = "{http://schemas.android.com/apk/res/android}"
    if source == module / "src/androidTest/AndroidManifest.xml":
        require(root.tag == "manifest" and not root.attrib and len(root) == 1,
                "TEST_PERMISSION_REMOVAL_ONLY")
        marker = root[0]
        require(marker.tag == "uses-permission" and marker.attrib == {
            ns + "name": "android.permission.REORDER_TASKS",
            "{http://schemas.android.com/tools}node": "remove"}
            and not len(marker) and not (marker.text or "").strip()
            and not (root.text or "").strip() and not (marker.tail or "").strip(),
            "TEST_PERMISSION_REMOVAL_ONLY")
    else:
        require(not root.findall("uses-permission"), "NO_DECLARED_PERMISSION")
        for activity in root.findall("application/activity"):
            require(activity.get(ns + "exported") == "false", "NONEXPORTED_FIXTURE")


def kvm_diagnostic_boundary(parsed):
    """Only the fixed, pre-acquisition diagnostic helper can create one empty VM."""
    functions = {node.name: node for node in parsed.body if isinstance(node, ast.FunctionDef)}
    expected = {"kvm_open_query": "fcntl.ioctl(fd, KVM_GET_API_VERSION, 0)",
                "kvm_empty_vm_control": "fcntl.ioctl(system_fd, KVM_CREATE_VM, 0)"}
    calls = []
    for name, function in functions.items():
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "ioctl":
                require(name in expected and ast.dump(node) == ast.dump(ast.parse(expected[name], mode="eval").body),
                        "KVM_DIAGNOSTIC_IOCTL_ONLY")
                calls.append(name)
    require(sorted(calls) == sorted(expected) and sum(isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "ioctl" for node in ast.walk(parsed)) == 2,
            "KVM_DIAGNOSTIC_IOCTL_INVENTORY")
    main = functions["main"]
    workload = {"acquire_build", "offline_build", "emulator_trial", "cancellation_control", "group_reaping_control"}
    main_calls = [node for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    require(not any(node.func.id in workload for node in main_calls), "KVM_DIAGNOSTIC_NO_WORKLOAD")
    phase_calls = [node for node in main_calls if node.func.id == "run_phase"]
    require(len(phase_calls) == 1 and ast.dump(phase_calls[0]) == ast.dump(
            ast.parse('run_phase(state, "kvm-probe", env, 10)', mode="eval").body), "KVM_DIAGNOSTIC_PHASE_BOUND")
    tries = [node for node in main.body if isinstance(node, ast.Try)]
    require(len(tries) == 1 and isinstance(tries[0].body[-1], ast.Raise)
            and ast.dump(tries[0].body[-1]) == ast.dump(ast.parse('raise KvmFailure("KVM_DIAGNOSTIC_ONLY_STOP")').body[0]),
            "KVM_DIAGNOSTIC_STOP_REQUIRED")
    require(not any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "complete"
            for target in node.targets) and isinstance(node.value, ast.Constant) and node.value.value is True
            for node in ast.walk(main)), "KVM_DIAGNOSTIC_WORKLOAD_INCOMPLETE")


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
    require((module / "src/androidTest/AndroidManifest.xml").is_file(), "TEST_PERMISSION_REMOVAL_REQUIRED")
    for source in module.rglob("AndroidManifest.xml"):
        manifest_boundary(source, module)
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
    parsed = ast.parse(hosted)
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in parsed.body
                 if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in ("PHASES", "KVM_PHASES", "KVM_GET_API_VERSION", "KVM_CREATE_VM")}
    require(constants == dict(PHASES=("kvm-probe", "public-build", "offline-build", "emulator"),
                KVM_PHASES=("kvm-probe", "emulator"), KVM_GET_API_VERSION=0xAE00, KVM_CREATE_VM=0xAE01), "KVM_PHASE_ALLOWLIST")
    for node in ast.walk(parsed):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            require(node.func.attr not in ("chmod", "chown", "setxattr", "removexattr", "initgroups"), "NO_KVM_HOST_MUTATION")
    require("PREEXISTING_KVM_ACCESS_REQUIRED" not in hosted, "NO_PREEXISTING_ACCESS_ASSUMPTION")
    kvm_diagnostic_boundary(parsed)
    workflow = (HERE.parents[1] / ".github/workflows/dps-native.yml").read_text()
    require("run: exec python3 -B integrations/dps-native/hosted.py --reviewed-hosted-fixture" in workflow,
            "DIRECT_SIGNAL_ENTRY")
    print("DPS source bindings passed; no native or Android behavior executed")


if __name__ == "__main__":
    main()
