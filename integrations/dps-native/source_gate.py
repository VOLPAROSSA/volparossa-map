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


def kvm_configuration_bindings(parsed):
    calls = []
    for function in parsed.body:
        if not isinstance(function, ast.FunctionDef): continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "baseline_unchanged":
                require(len(node.args) == 2 and not any(isinstance(arg, ast.Starred) for arg in node.args),
                        "KVM_BASELINE_CALL_CONTRACT")
                if node.keywords:
                    require(function.name == "finalize" and len(node.keywords) == 1
                            and node.keywords[0].arg == "kvm_configuration"
                            and isinstance(node.keywords[0].value, ast.Constant) and node.keywords[0].value.value is True,
                            "KVM_CONFIGURATION_COMPLETED_FINALIZER_ONLY")
                calls.append((function.name, bool(node.keywords)))
    require(sorted(calls) == [("finalize", False), ("finalize", True), ("group_reaping_control", False)],
            "KVM_BASELINE_CALL_INVENTORY")


def ipv6_diagnostic_bindings(parsed):
    functions = {node.name: node for node in parsed.body if isinstance(node, ast.FunctionDef)}
    profiles = [node.value for node in parsed.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "IPV6_ROUTE_RENDERER_PROFILES"
                        for target in node.targets)]
    require(len(profiles) == 1 and ast.literal_eval(profiles[0]) == {
        "linux-6.8-native-seq-v1": r"6\.8\.0-[0-9]{1,6}-(?:generic|azure)",
        "linux-7.0-azure-native-seq-v1": r"7\.0\.0-[0-9]{1,6}-azure",
    }, "IPV6_REVIEWED_RENDERER_PROFILES")
    baseline = functions["baseline_unchanged"]
    ignored = [node.value for node in baseline.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "ignored" for target in node.targets)]
    expected = ast.parse('{"firewall", "kvm", "ipv6_routes", "ipv6_route_metadata"} if kvm_configuration else {"firewall"}', mode="eval").body
    require(len(ignored) == 1 and ast.dump(ignored[0]) == ast.dump(expected), "IPV6_CONFIGURATION_EXCEPTION_ALLOWLIST")
    # Parse rejection must run before the existing equality fast path, with no
    # route exception in default/quiet/control comparisons.
    validate = next((index for index, node in enumerate(baseline.body)
                     if any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id == "ipv6_route_comparison" for call in ast.walk(node))), None)
    equality = next((index for index, node in enumerate(baseline.body) if isinstance(node, ast.If)
                     and ast.unparse(node.test) == "before == after"), None)
    require(validate is not None and equality is not None and validate < equality, "IPV6_VALIDATE_BEFORE_EQUALITY")
    producers = [call for call in ast.walk(functions["baseline"]) if isinstance(call, ast.Call)
                 and isinstance(call.func, ast.Name) and call.func.id == "read_ipv6_route_snapshot"]
    require(len(producers) == 1 and not producers[0].args and not producers[0].keywords,
            "IPV6_SINGLE_CAPTURE")
    renderers = [call for call in ast.walk(functions["baseline"]) if isinstance(call, ast.Call)
                 and isinstance(call.func, ast.Name) and call.func.id == "ipv6_renderer_snapshot"]
    require(len(renderers) == 1 and not renderers[0].args and not renderers[0].keywords,
            "IPV6_SINGLE_RENDERER_CAPTURE")
    renderer_validate = next((index for index, node in enumerate(baseline.body)
                     if any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id == "validate_ipv6_renderer" for call in ast.walk(node))), None)
    require(renderer_validate is not None and renderer_validate < equality, "IPV6_RENDERER_BEFORE_EQUALITY")
    approved = ast.parse('not kvm_configuration or ipv6["classification"] in ("UNCHANGED", "REFERENCE_METADATA_ONLY")', mode="eval").body
    require(any(ast.dump(node) == ast.dump(approved) for node in ast.walk(baseline)), "IPV6_CONFIGURATION_CLOSED_CLASSES")
    main = functions["main"]
    captures = [node.lineno for node in ast.walk(main) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "baseline"]
    phases = [node.lineno for node in ast.walk(main) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == "run_phase"]
    require(len(captures) == 1 and phases and captures[0] < min(phases), "IPV6_ADMISSION_BEFORE_PHASES")
    admissions = [node.lineno for node in ast.walk(main) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == "ipv6_renderer_admission"]
    require(len(admissions) == 1 and admissions[0] < captures[0], "IPV6_RENDERER_ADMISSION_BEFORE_PHASES")
    for function in functions.values():
        for call in ast.walk(function):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "validate_ipv6_renderer":
                require(len(call.args) == 1 and not isinstance(call.args[0], ast.Starred), "IPV6_RENDERER_CALL_CONTRACT")
                if call.keywords:
                    require(function.name == "ipv6_renderer_snapshot" and len(call.keywords) == 1
                            and call.keywords[0].arg == "supported" and isinstance(call.keywords[0].value, ast.Constant)
                            and call.keywords[0].value.value is False, "IPV6_RENDERER_CAPTURE_ONLY_UNSUPPORTED")


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
    kvm_configuration_bindings(parsed)
    ipv6_diagnostic_bindings(parsed)
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in parsed.body
                 if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in ("PHASES", "KVM_PHASES", "KVM_GET_API_VERSION")}
    require(constants == dict(PHASES=("kvm-probe", "public-build", "offline-build", "emulator"),
                KVM_PHASES=("kvm-probe", "emulator"), KVM_GET_API_VERSION=0xAE00), "KVM_PHASE_ALLOWLIST")
    for node in ast.walk(parsed):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            require(node.func.attr not in ("chmod", "chown", "setxattr", "removexattr", "initgroups"), "NO_KVM_HOST_MUTATION")
    require("PREEXISTING_KVM_ACCESS_REQUIRED" not in hosted and "KVM_CREATE_VM" not in hosted,
            "KVM_QUERY_ONLY")
    workflow = (HERE.parents[1] / ".github/workflows/dps-native.yml").read_text()
    require("run: exec python3 -B integrations/dps-native/hosted.py --reviewed-hosted-fixture" in workflow,
            "DIRECT_SIGNAL_ENTRY")
    print("DPS source bindings passed; no native or Android behavior executed")


if __name__ == "__main__":
    main()
