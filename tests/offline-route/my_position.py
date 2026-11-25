# SPDX-License-Identifier: GPL-3.0-only
"""Run independent cases in the installed WebDebug application and retain only bounded proof."""
import hashlib
import json
import re
import subprocess
import time


CASES = {
    "permissionDenied": ("position_open", "permission_denied", "cleared"),
    "acceptedAndIncompleteFixes": ("position_open", "single_fix_after_tap", "replacement_cleared",
                                   "missing_vertical_refused", "old_time_refused"),
    "simulatedFixProvenance": ("position_open", "simulated_fix_identified",
                               "replacement_assessed_after_tap", "stale_simulation_refused", "provider_filters_preserved"),
    "expiryOfSelectedObservation": ("position_open", "selected_fix_shown", "selected_fix_cleared", "expired_fix_refused"),
    "foregroundAndSelectionRevocation": ("position_open", "single_fix_after_tap", "map_backgrounded",
                                         "existing_task_requested", "resumed_without_assessment", "other_place_hidden"),
}


def exercise(inputs, package, routes, adb, device):
    test_apk = inputs / "map-quality-tests.apk"
    assert adb("install", "--no-streaming", test_apk, timeout=120).endswith("Success")
    with test_apk.open("rb") as stream:
        test_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    proof = dict(map_commit=routes["map_commit"], apk_sha256=routes["apk_sha256"], test_apk_sha256=test_digest,
                 core_revision=(inputs / "map-quality-core").read_text().strip(),
                 native_pedestrian_route_summaries=routes["native_pedestrian_route_summaries"], cases={})
    progress = inputs / "my-position-progress.json"

    def save():
        pending = progress.with_suffix(".next")
        pending.write_text(json.dumps(proof, separators=(",", ":")) + "\n")
        pending.replace(progress)

    def guest_identity():
        try:
            boot = device("cat", "/proc/sys/kernel/random/boot_id", timeout=5)
            server = device("pidof", "system_server", timeout=5)
            return boot, server
        except (subprocess.SubprocessError, ValueError):
            return None

    runner = package + ".test/androidx.test.runner.AndroidJUnitRunner"
    suite = "org.volparossa.map.dps.MyPositionQualityTest"
    for method, expected in CASES.items():
        if method == "acceptedAndIncompleteFixes":
            for name in ["ACCESS_COARSE_LOCATION", "ACCESS_FINE_LOCATION"]:
                perm = "android.permission." + name
                device("pm", "clear-permission-flags", package, perm, "user-set", "user-fixed")
                device("pm", "grant", package, perm)
        case = proof["cases"][method] = dict(status="started", observed=[])
        save()
        original_guest = guest_identity()
        started = time.monotonic()
        try:
            raw = device("am", "instrument", "-w", "-r", "-e", "class", suite + "#" + method, runner,
                         timeout=150, check=False)
        except subprocess.TimeoutExpired as error:
            raw = (error.stdout or b"").decode("utf-8", errors="replace")
            case["status"] = "deadline"
        observed = re.findall(r"^INSTRUMENTATION_STATUS: volparossa_checkpoint=([a-z_]+)\s*$", raw, re.MULTILINE)
        case["observed"] = [entry for entry in observed if entry in expected]
        case["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        if "INSTRUMENTATION_ABORTED:" in raw:
            case["status"] = "instrumentation_aborted"
        elif tuple(observed) == expected and "OK (1 test)" in raw and "INSTRUMENTATION_CODE: -1" in raw:
            case["status"] = "passed"
        elif case["status"] == "started":
            case["status"] = "failed"
        if case["status"] != "passed":
            final_guest = guest_identity()
            if original_guest and final_guest:
                case["same_boot"] = original_guest[0] == final_guest[0]
                case["same_system_server"] = original_guest[1] == final_guest[1]
            case["test_source_lines"] = sorted(set(int(line) for line in re.findall(r"MyPositionQualityTest\.java:(\d+)", raw)))
        if method == "simulatedFixProvenance" and case["status"] == "passed":
            # Inspect only the pinned provider callback in this isolated emulator. Retain counts only.
            log = device("logcat", "-b", "main", "-d", "-v", "raw", "-s", "LocationState:D")
            callback_messages = re.findall(r"LocationHelper\.java:\d+ onLocationChanged\(\): (.*)$", log, re.MULTILINE)
            rejected = {"Location update rejected by accuracy filter", "Location update did not improve the saved fix"}
            seen = set(callback_messages)
            if not rejected <= seen <= (rejected | {"Provider is not active"}):
                case["status"] = "provider_diagnostics_failed"
            case["coordinate_free_callback_messages"] = sum(message in rejected for message in callback_messages)
        save()
        print(method, case, flush=True)
        if case["status"] != "passed":
            raise RuntimeError("Android failed the identified My Position case: " + method)
        device("am", "force-stop", package)
    return proof
