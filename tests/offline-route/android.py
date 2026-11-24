#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the native app through its normal URL/UI on a disconnected test emulator."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ADB, INPUTS, mode = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
assert mode in {"release", "web-debug", "web-debug-quality"}
PINS = json.loads(Path(__file__).with_name("inputs.json").read_bytes())
built_digest = INPUTS / "web-debug-apk.sha256"
expected_digest = built_digest.read_text().strip() if mode.startswith("web-debug") else PINS["apk_sha256"]
with (INPUTS / "OrganicMaps.apk").open("rb") as stream:
    apk_digest = hashlib.file_digest(stream, "sha256").hexdigest()
assert apk_digest == expected_digest
PACKAGE = "app.organicmaps.web.debug" if mode.startswith("web-debug") else "app.organicmaps.web"
COMPONENT = PACKAGE + "/app.organicmaps.SplashActivity"
# Two public street points, no device location or personal route.
START = "43.73731,7.42668"
END = "43.73932,7.42740"
ROUTES = [(START, END), (START, "43.73974,7.42972")]


def adb(*args, timeout=35, check=True):
    result = subprocess.run([ADB, "-s", "emulator-5554", *map(str, args)], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=timeout, check=check)
    if len(result.stdout) > 500_000:
        raise ValueError("oversized adb result")
    return result.stdout.decode("utf-8", errors="strict").strip()


def device(*args, **kwargs):
    return adb("shell", shlex.join(map(str, args)), **kwargs)


def set_adb_identity(command, uid):
    for _ in range(6):
        adb("wait-for-device", timeout=20)
        if device("id", "-u", check=False) == uid:
            return
        adb(command, check=False)
        time.sleep(1)
    adb("wait-for-device", timeout=20)
    assert device("id", "-u") == uid, "unexpected Android shell identity"


def assert_offline():
    assert [name for _, name in socket.if_nameindex()] == ["lo"]
    assert device("settings", "get", "global", "airplane_mode_on") == "1"


def layout():
    device("uiautomator", "dump", "/data/local/tmp/map-ui.xml", timeout=12)
    raw = device("cat", "/data/local/tmp/map-ui.xml")
    return [node.attrib for node in ET.fromstring(raw).iter("node")]


def tap(node):
    a, b, c, d = map(int, re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node["bounds"]).groups())
    device("input", "tap", (a+c)//2, (b+d)//2)


def open_route(start, end):
    uri = f"om://route?sll={start}&saddr=Public-A&dll={end}&daddr=Public-B&type=pedestrian"
    output = device("am", "start", "-W", "-n", COMPONENT, "-a", "android.intent.action.VIEW", "-d", uri)
    assert "Error" not in output and "Status: ok" in output
    return uri


def route(start, end, previous_summary):
    assert_offline()
    device("logcat", "-b", "crash", "-c")
    open_route(start, end)
    deadline = time.monotonic() + 115
    retried = False
    controls = set()
    summaries = set()
    nodes = []
    while time.monotonic() < deadline:
        try:
            nodes = layout()
        except (ET.ParseError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            time.sleep(1)
            continue
        for node in nodes:
            pkg, rid, label = node["package"], node["resource-id"].split("/")[-1], node["text"].strip()
            if pkg == PACKAGE:
                controls.add(rid)
                if rid == "time" and "•" in label and re.search(r"\d", label):
                    duration, distance = (part.strip() for part in label.split("•", 1))
                    if (re.search(r"\d", duration) and re.fullmatch(r"[\d.,\s]+(?:km|m|mi|ft)", distance)
                            and len(label) <= 80):
                        summaries.add(label)
            if not node.get("enabled") == "true":
                continue
            if pkg == "com.android.permissioncontroller" and (
                    rid in {"permission_deny_button", "permission_deny_and_dont_ask_again_button"}
                    or label.replace("’", "'").casefold() in {"don't allow", "deny"}):
                tap(node)
                break
            if pkg == PACKAGE and label.casefold() in {"accept", "continue", "next", "done", "got it", "ok", "close", "understood"}:
                tap(node)
                break
        for summary in sorted(summaries):
            # The activity can draw a provisional or restored previous route
            # before its asynchronous native calculation reaches the new destination.
            if summary != previous_summary and re.search(r"[1-9]", summary.split("•", 1)[1]):
                assert_offline()
                return summary
        if not retried and time.monotonic() > deadline - 55:
            open_route(start, end)
            retried = True
        time.sleep(1)
    print("Observed app controls", sorted(controls)[:75], file=sys.stderr)
    print("Foreground packages", sorted({node["package"] for node in nodes}), file=sys.stderr)
    print("Observed native route summaries", sorted(summaries)[:8], file=sys.stderr)
    for node in nodes:
        if node["package"] == "com.android.permissioncontroller" and node["text"]:
            print("Android permission dialog", node["resource-id"], node["text"][:120], file=sys.stderr)
    exits = device("dumpsys", "activity", "exit-info", PACKAGE)
    for line in exits.splitlines():
        if "reason=" in line or "description=" in line:
            print("App exit", line.strip()[:320], file=sys.stderr)
            break
    crash = device("logcat", "-b", "crash", "-d", "-t", "80")
    for line in crash.splitlines():
        if any(tag in line for tag in ("FATAL EXCEPTION", "Exception:", "Abort message:", "signal ", "liborganicmaps.so", "libmapswithme.so")):
            print("Guest crash", line.strip()[:360], file=sys.stderr)
    for node in nodes:
        if node["resource-id"].endswith(("/head_message", "/download_message", "/error")):
            print("Public fixture app status", node["text"][:180], file=sys.stderr)
    raise RuntimeError("no native route duration and distance")


adb("wait-for-device", timeout=180)
for _ in range(120):
    if device("getprop", "sys.boot_completed", check=False) == "1":
        break
    time.sleep(2)
else:
    raise RuntimeError("emulator startup timeout")
print("Android booted", flush=True)
device("input", "keyevent", "KEYCODE_WAKEUP")
device("wm", "dismiss-keyguard")
device("cmd", "connectivity", "airplane-mode", "enable")
device("svc", "wifi", "disable")
device("svc", "data", "disable")
assert_offline()
install_args = []
build = None
if mode.startswith("web-debug"):
    build = json.loads((INPUTS / "android-build.json").read_bytes())
    assert build["abi"] in {"x86_64", "arm64-v8a"} and build["apk_sha256"] == apk_digest
    guest_abi = device("getprop", "ro.product.cpu.abi")
    supported = device("getprop", "ro.product.cpu.abilist").split(",")
    assert guest_abi == "x86_64" and build["abi"] in supported, "guest does not support the selected app ABI"
    install_args = ["--abi", build["abi"]]
assert adb("install", "--no-streaming", *install_args, INPUTS / "OrganicMaps.apk", timeout=120).endswith("Success")
if build:
    packages = device("dumpsys", "package", PACKAGE)
    assert re.search(r"^\s*primaryCpuAbi=" + re.escape(build["abi"]) + r"\s*$", packages, re.MULTILINE), "installed ABI mismatch"
print("Organic Maps installed", flush=True)
set_adb_identity("root", "0")
# Bootstrap the original app so Android creates its own scoped storage and
# Organic Maps selects its real writable directory before any bytes are staged.
device("am", "start", "-W", "-n", COMPONENT)
for _ in range(22):
    try:
        nodes = layout()
    except (ET.ParseError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        continue
    for node in nodes:
        if node["resource-id"].endswith("/permission_deny_button") and node["package"] == "com.android.permissioncontroller":
            tap(node)
    if any(node["resource-id"].endswith("/btn_download_resources") for node in nodes):
        break
    time.sleep(1)
else:
    raise RuntimeError("stock app did not initialize map storage")
device("am", "force-stop", PACKAGE)
internal = f"/data/user/0/{PACKAGE}/files"
settings = device("cat", internal + "/settings.ini")
configured, = re.findall(r"(?m)^StoragePath=(.+?)\s*$", settings)
configured = configured.rstrip("/")
allowed = {internal, f"/data/data/{PACKAGE}/files", f"/storage/emulated/0/Android/data/{PACKAGE}/files"}
assert configured in allowed, "unexpected Organic Maps storage"
# Stage into real app-owned internal storage. Organic Maps discovers already
# installed maps when its configured directory contains no map files.
base = f"{internal}/{PINS['data_version']}"
uid = device("stat", "-c", "%u", internal)
assert re.fullmatch(r"[1-9]\d{4,5}", uid)
device("mkdir", base)
for name, (_, _, digest) in PINS["maps"].items():
    dest = base + "/" + name + ".mwm"
    adb("push", INPUTS / (name + ".mwm"), dest, timeout=90)
    assert device("sha256sum", dest).split()[0] == digest
device("chown", "-R", f"{uid}:{uid}", base)
device("chmod", "0700", base)
for name in PINS["maps"]:
    device("chmod", "0600", base + "/" + name + ".mwm")
device("restorecon", "-RF", base)
print("Verified the three map files on the device", flush=True)
set_adb_identity("unroot", "2000")
# Both endpoints are explicit. Exercise the app with the ordinary Android
# permission state a user gets by declining access to location and notifications.
for permission in ("ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "POST_NOTIFICATIONS"):
    full_name = "android.permission." + permission
    device("pm", "revoke", PACKAGE, full_name)
    device("pm", "set-permission-flags", PACKAGE, full_name, "user-set", "user-fixed")
    state = device("dumpsys", "package", PACKAGE)
    assert re.search(re.escape(full_name) + r": granted=false, flags=\[[^\]]*USER_FIXED", state)
summaries = []
for start, end in ROUTES:
    device("am", "force-stop", PACKAGE)
    summaries.append(route(start, end, summaries[-1] if summaries else None))
assert len(set(summaries)) == 2 and all(re.search(r"[1-9]", s.split("•")[1]) for s in summaries)
assert_offline()
device("am", "force-stop", PACKAGE)
assert not device("pidof", PACKAGE, check=False)
result = dict(map_commit=os.environ["GITHUB_SHA"], organic_maps_source=PINS["source"], apk_sha256=apk_digest,
              maps_version=PINS["data_version"], monaco_sha256=PINS["maps"]["Monaco"][2], android_api=device("getprop", "ro.build.version.sdk"),
              native_pedestrian_route_summaries=summaries, cold_app_launches=2, host_namespace_interfaces=["lo"],
              guest_airplane_mode=True, app_stopped=True)
if build:
    result.update(android_source_build="WebDebug", application_id=PACKAGE, built_abi=build["abi"],
                  installed_abi=build["abi"], guest_abi=guest_abi, native_translation=guest_abi != build["abi"],
                  emulator_build_fingerprint=device("getprop", "ro.build.fingerprint"))
if mode == "web-debug-quality":
    from my_position import exercise
    result["synthetic_fix_in_main_place_page"] = exercise(INPUTS, PACKAGE, result, adb, device)
    assert_offline()
    assert not device("pidof", PACKAGE, check=False)
(INPUTS / "observations.json").write_text(json.dumps(result, separators=(",", ":")) + "\n")
