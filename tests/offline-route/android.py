#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise only the released app and its normal URL/UI on a disconnected test emulator."""
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

ADB, INPUTS = sys.argv[1], Path(sys.argv[2])
PINS = json.loads(Path(__file__).with_name("inputs.json").read_bytes())
PACKAGE = "app.organicmaps.web"
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


def route(start, end):
    assert_offline()
    open_route(start, end)
    deadline = time.monotonic() + 115
    retried = False
    controls = set()
    summaries = set()
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
                    if re.search(r"\d", duration) and re.fullmatch(r"[\d.,\s]+(?:km|m|mi|ft)", distance):
                        summaries.add(label)
            if not node.get("enabled") == "true":
                continue
            if pkg == "com.android.permissioncontroller" and rid == "permission_deny_button":
                tap(node)
                break
            if pkg == PACKAGE and label.casefold() in {"accept", "continue", "next", "done", "got it", "ok", "close", "understood"}:
                tap(node)
                break
        if summaries:
            assert_offline()
            return sorted(summaries)[0]
        if not retried and time.monotonic() > deadline - 55:
            open_route(start, end)
            retried = True
        time.sleep(1)
    print("Observed app controls", sorted(controls)[:75], file=sys.stderr)
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
device("input", "keyevent", "KEYCODE_WAKEUP")
device("wm", "dismiss-keyguard")
device("cmd", "connectivity", "airplane-mode", "enable")
device("svc", "wifi", "disable")
device("svc", "data", "disable")
assert_offline()
assert adb("install", "--no-streaming", INPUTS / "OrganicMaps.apk", timeout=120).endswith("Success")
assert adb("root").startswith(("restarting adbd as root", "adbd is already running as root"))
adb("wait-for-device")
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
adb("unroot")
adb("wait-for-device")
assert device("id", "-u") == "2000"
summaries = []
for start, end in ROUTES:
    device("am", "force-stop", PACKAGE)
    summaries.append(route(start, end))
assert len(set(summaries)) == 2 and all(re.search(r"[1-9]", s.split("•")[1]) for s in summaries)
assert_offline()
device("am", "force-stop", PACKAGE)
assert not device("pidof", PACKAGE, check=False)
result = dict(map_commit=os.environ["GITHUB_SHA"], organic_maps_source=PINS["source"], apk_sha256=PINS["apk_sha256"],
              maps_version=PINS["data_version"], monaco_sha256=PINS["maps"]["Monaco"][2], android_api=device("getprop", "ro.build.version.sdk"),
              native_pedestrian_route_summaries=summaries, cold_app_launches=2, host_namespace_interfaces=["lo"],
              guest_airplane_mode=True, app_stopped=True)
(INPUTS / "receipt.json").write_text(json.dumps(result, separators=(",", ":")) + "\n")
