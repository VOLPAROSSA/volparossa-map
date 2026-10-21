#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Guarded, manual public hosted synthetic JNI proof candidate. Never a host installer.

Public source/dependency acquisition precedes an offline rebuild and installed
API36/x86_64 test in a disposable network/PID/mount namespace. Only namespace
loopback is activated. No core daemon, network participation or sensor trial.
"""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from prepare import HERE, digest, git, pins, prepare, require, verify

ROOT = HERE.parents[1]
WORKFLOW = "Map native DPS quality"
SERIAL = "emulator-5554"
PACKAGE = "org.volparossa.map.dps.test"
RUNNER = PACKAGE + "/androidx.test.runner.AndroidJUnitRunner"


def guards(env):
    require(os.getuid() == os.geteuid() != 0, "HOSTED_UNPRIVILEGED")
    required = dict(GITHUB_ACTIONS="true", RUNNER_OS="Linux", RUNNER_ENVIRONMENT="github-hosted",
                    GITHUB_REPOSITORY="VOLPAROSSA/volparossa-map", GITHUB_WORKFLOW=WORKFLOW,
                    GITHUB_EVENT_NAME="workflow_dispatch", VOLPAROSSA_DPS_REVIEWED="true")
    require(all(env.get(k) == v for k, v in required.items()), "REVIEWED_DISPOSABLE_HOSTED_ONLY")
    require(env.get("ImageOS") == "ubuntu24" and Path("/etc/os-release").read_text().find('VERSION_ID="24.04"') >= 0,
            "UBUNTU_24_ONLY")
    require(env.get("GITHUB_WORKSPACE") == str(ROOT)
            and re.fullmatch("[0-9a-f]{40}", env.get("GITHUB_SHA", ""))
            and git(ROOT, "rev-parse", "HEAD").decode().strip() == env["GITHUB_SHA"]
            and not git(ROOT, "status", "--porcelain", "--untracked-files=all"), "EXACT_CLEAN_MAP_SOURCE")
    require(os.access("/dev/kvm", os.R_OK | os.W_OK), "PREEXISTING_KVM_ACCESS_REQUIRED")
    require(Path(env.get("RUNNER_TEMP", "")).is_absolute()
            and all(re.fullmatch("[1-9][0-9]{0,19}", env.get(k, "")) for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")), "RUNNER_TEMP")


def save(path, value):
    temporary = path.with_name(path.name + ".part")
    with temporary.open("x") as output:
        output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def children(pid):
    """One process tree's current descendants and RSS, never unrelated runner jobs."""
    rows = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            fields = (proc / "stat").read_text().rpartition(") ")[2].split()
            rows[int(proc.name)] = (int(fields[1]), int(fields[21]) * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            pass
    owned = {pid}
    while True:
        updated = owned | {p for p, (parent, _) in rows.items() if parent in owned}
        if updated == owned:
            break
        owned = updated
    return owned, sum(rows.get(p, (0, 0))[1] for p in owned)


class Interrupted(RuntimeError):
    """Catchable cancellation, never decoder/input text."""


class CleanupDeadline(RuntimeError):
    pass


ACTIVE = None


class Lifetime:
    """One process's registered children and catchable-signal cleanup budget.

    Signal *delivery* stays unblocked across spawn. The Python handler defers
    raising while Popen returns and ownership is registered; exec resets caught
    handlers to defaults. No pthread mask or unsafe preexec_fn is inherited.
    """
    def __init__(self):
        self.owned = []
        self.spawning = False
        self.cleaning = 0
        self.first_signal = None
        self.signal_count = 0
        self.signal_time = None
        self.handlers = {}
        self.stop = None
        self.cleanup_errors = []

    def __enter__(self):
        blocked = int(Path("/proc/self/status").read_text().split("SigBlk:")[1].split()[0], 16)
        require(not blocked & ((1 << (signal.SIGTERM - 1)) | (1 << (signal.SIGINT - 1))), "UNBLOCKED_ENTRY_SIGNALS")
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "NO_OUTER_ALARM")
        self.handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
        signal.signal(signal.SIGTERM, self.interrupted)
        signal.signal(signal.SIGINT, self.interrupted)
        signal.signal(signal.SIGALRM, self.expired)
        return self

    def interrupted(self, number, _frame):
        self.signal_count += 1
        now = time.monotonic()
        first = self.first_signal is None
        if first:
            self.first_signal, self.signal_time = number, now
            self.stop = now + (2.0 if number == signal.SIGTERM else 9.0)
        elif number == signal.SIGTERM:
            self.stop = min(self.stop, now + 2.0)
        if self.cleaning and self.stop is not None:
            remaining = max(0.001, self.stop - now)
            armed = signal.getitimer(signal.ITIMER_REAL)[0]
            signal.setitimer(signal.ITIMER_REAL, min(remaining, armed) if armed > 0 else remaining)
        if first and not self.spawning and not self.cleaning and sys.exc_info()[1] is None:
            raise Interrupted("HOSTED_INTERRUPTED")

    def expired(self, _number, _frame):
        raise CleanupDeadline("CLEANUP_DEADLINE")

    def check(self):
        if self.first_signal is not None and not self.cleaning:
            raise Interrupted("HOSTED_INTERRUPTED")

    @contextmanager
    def registration(self):
        self.check()
        require(not self.spawning, "NO_NESTED_REGISTRATION")
        self.spawning = True
        try:
            yield
        finally:
            self.spawning = False
        self.check()

    def spawn(self, argv, **kwargs):
        with self.registration():
            child = subprocess.Popen([str(a) for a in argv], start_new_session=True, **kwargs)
            self.owned.append(child)
        return child

    @contextmanager
    def cleanup(self):
        outer = self.cleaning == 0
        self.cleaning += 1
        try:
            if outer:
                # GitHub gives INT 7.5s then TERM 2.5s before killtree. Use
                # <=9s total after INT and <=2s after TERM, without extension.
                remaining = 15 if self.stop is None else min(15, self.stop - time.monotonic())
                if remaining <= 0:
                    raise CleanupDeadline("CLEANUP_DEADLINE")
                signal.setitimer(signal.ITIMER_REAL, remaining)
            yield
        finally:
            self.cleaning -= 1
            if outer:
                signal.setitimer(signal.ITIMER_REAL, 0)

    def drain(self, child):
        def terminate(sig):
            try:
                os.killpg(child.pid, sig)
            except PermissionError:
                # Only a registered start_new_session group, including the
                # exact root-created namespace supervisor; never arbitrary PIDs.
                subprocess.run(["sudo", "-n", "/bin/kill", "-" + str(int(sig)), "--", "-" + str(child.pid)],
                    check=True, timeout=0.75, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except ProcessLookupError:
                pass
        terminate(signal.SIGTERM)
        try:
            child.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            terminate(signal.SIGKILL)
            child.wait(timeout=0.75)
        stop = time.monotonic() + 0.75
        while True:
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                pass
            require(time.monotonic() < stop, "PROCESS_GROUP_DRAIN")
            terminate(signal.SIGKILL)
            time.sleep(0.05)
        self.owned.remove(child)

    def drain_all(self):
        for child in list(reversed(self.owned)):
            self.drain(child)

    def __exit__(self, kind, error, traceback):
        cleanup_error = None
        try:
            with self.cleanup():
                self.drain_all()
        except BaseException as failure:
            cleanup_error = failure
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for number, previous in self.handlers.items():
                signal.signal(number, previous)
        # Never replace the original workload/cancellation error with cleanup.
        if error is None:
            if cleanup_error is not None:
                raise cleanup_error
            self.check()
        return False


def run(label, argv, state, env, cwd=None, seconds=900):
    """Bound commands and always attempt registered group cleanup on TERM/INT."""
    ACTIVE.check()
    log = state / (label + ".private.log")
    stop, peak, child = time.monotonic() + seconds, 0, None
    with log.open("xb") as output:
        try:
            child = ACTIVE.spawn(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT)
            while child.poll() is None:
                _, rss = children(child.pid); peak = max(peak, rss)
                require(time.monotonic() < stop and rss <= 6 * 1024**3
                        and all(p.stat().st_size <= 64 * 1024**2 for p in state.glob("*.private.log")), "STEP_RESOURCE_BOUND")
                time.sleep(0.2)
            require(child.returncode == 0 and log.stat().st_size <= 64 * 1024**2, "STEP_" + label.upper())
        finally:
            original = sys.exc_info()[1]
            cleanup_error = None
            try:
                with ACTIVE.cleanup():
                    # A signal deferred by spawn can raise before assignment;
                    # that child is still in ACTIVE.owned for outer cleanup.
                    if child is not None:
                        ACTIVE.drain(child)
            except BaseException as error:
                cleanup_error = error
            try:
                with ACTIVE.cleanup():
                    save(state / (label + ".json"), dict(exit=None if child is None else child.returncode,
                        peak_observed_rss=peak, wall_bound_seconds=seconds,
                        owned_process_group_drained=child is not None and child not in ACTIVE.owned,
                        signal_count=ACTIVE.signal_count, cleanup_error=None if cleanup_error is None else type(cleanup_error).__name__))
            except BaseException as error:
                ACTIVE.cleanup_errors.append(type(error).__name__)
                if original is None and cleanup_error is None:
                    raise
            if original is None and cleanup_error is not None:
                raise cleanup_error
    ACTIVE.check()
    return log.read_bytes()


def baseline():
    rules = subprocess.check_output(["sudo", "-n", "nft", "list", "ruleset"], timeout=10)
    return dict(netns=os.readlink("/proc/self/ns/net"), routes=digest(Path("/proc/net/route").read_bytes()),
                ipv6_routes=digest(Path("/proc/net/ipv6_route").read_bytes()),
                resolver=digest(Path("/etc/resolv.conf").read_bytes()), firewall=digest(rules))


def environment(state, tools):
    java = Path(os.environ["JAVA_HOME_17_X64"]).resolve(strict=True)
    env = {k: v for k, v in os.environ.items() if k in
           {"PATH", "LANG", "GITHUB_ACTIONS", "RUNNER_OS", "RUNNER_ENVIRONMENT", "GITHUB_REPOSITORY",
            "GITHUB_WORKFLOW", "GITHUB_EVENT_NAME", "VOLPAROSSA_DPS_REVIEWED", "GITHUB_WORKSPACE", "GITHUB_SHA",
            "ImageOS", "RUNNER_TEMP", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"}}
    env.update(HOME=str(state / "home"), JAVA_HOME=str(java), JAVA_HOME_17_X64=str(java),
               ANDROID_HOME=str(tools / "sdk"), ANDROID_SDK_ROOT=str(tools / "sdk"),
               ANDROID_USER_HOME=str(state / "android-home"), ANDROID_AVD_HOME=str(state / "avd"),
               GRADLE_USER_HOME=str(state / "gradle-home"), RUSTUP_HOME=str(state / "rustup"),
               CARGO_HOME=str(state / "cargo"), CARGO_TARGET_DIR=str(state / "rust-target"),
               CI="true", ANDROID_EMULATOR_USE_SYSTEM_LIBS="1", QT_QPA_PLATFORM="offscreen",
               GRADLE_OPTS="-Dorg.gradle.daemon=false", TZ="UTC")
    return env


def gradle(tools, *tasks):
    return [tools / "gradle/bin/gradle", "--no-daemon", "--max-workers=2", "--console=plain",
            "-Dorg.gradle.jvmargs=-Xmx1536m -XX:MaxMetaspaceSize=512m", "-Pfirebase=false",
            "-Pandroid.builder.sdkDownload=false", *tasks]


def acquire_build(state, env):
    import tool_inputs
    locked = pins()
    tools = state / "tools"
    save(state / "archives.json", tool_inputs.materialize(tools))
    for name in ("organicmaps", "core"):
        pin, source = locked[name], state / name
        run("clone-" + name, ["git", "clone", "--filter=blob:none", "--no-checkout", pin["repository"], source], state, env, seconds=600)
        run("checkout-" + name, ["git", "-C", source, "checkout", "--detach", pin["revision"]], state, env, seconds=600)
        verify(source, pin)
    prepared = state / "prepared"
    save(state / "preparation.json", prepare(state / "organicmaps", state / "core", prepared))
    java_version = run("java-version", [Path(env["JAVA_HOME"]) / "bin/java", "-version"], state, env, seconds=20)
    require(re.search(rb'version "17\.', java_version), "JAVA17_REQUIRED")
    rustup = shutil.which("rustup")
    require(rustup is not None, "RUNNER_RUSTUP_REQUIRED")
    run("rustup-version", [rustup, "--version"], state, env, seconds=20)
    run("rust-toolchain", [rustup, "toolchain", "install", locked["rust"], "--profile", "minimal",
                          "--target", locked["rust_target"]], state, env, seconds=600)
    rust_bin = state / "rustup/toolchains" / (locked["rust"] + "-x86_64-unknown-linux-gnu/bin")
    rust_version = run("rust-version", [rust_bin / "rustc", "--version"], state, env, seconds=20)
    require(rust_version.strip() == b"rustc 1.85.0 (4d91de4e4 2025-02-17)", "RUST_VERSION")
    env["PATH"] = str(rust_bin) + ":" + str(Path(env["JAVA_HOME"]) / "bin") + ":/usr/bin:/bin"
    ndk = tools / "sdk/ndk" / locked["ndk"] / "toolchains/llvm/prebuilt/linux-x86_64/bin"
    env["CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER"] = str(ndk / "x86_64-linux-android21-clang")
    env["CARGO_TARGET_X86_64_LINUX_ANDROID_AR"] = str(ndk / "llvm-ar")
    run("rust-staticlib", [rust_bin / "cargo", "build", "--locked", "--release", "-p", "volparossa-dps-ffi",
                          "--target", locked["rust_target"]], state, env, state / "core", seconds=900)
    library = state / "rust-target" / locked["rust_target"] / "release/libvolparossa_dps_ffi.a"
    native = prepared / "android/volparossa_dps_jni/rust/x86_64"
    native.mkdir(); shutil.copyfile(library, native / library.name)
    save(state / "rust-library.json", dict(source=locked["core"]["revision"], library=digest(library.read_bytes()),
                                          target=locked["rust_target"], api=21, rust=rust_version.decode().strip()))
    android = prepared / "android"
    (android / "local.properties").write_text("sdk.dir=" + str(tools / "sdk") + "\n")
    run("android-acquisition-build", gradle(tools, "--write-verification-metadata", "sha256",
                    ":volparossa_dps_jni:assembleDebug", ":volparossa_dps_jni:assembleDebugAndroidTest"), state, env, android, seconds=1200)
    metadata = android / "gradle/verification-metadata.xml"
    require(metadata.is_file(), "DEPENDENCY_METADATA")
    shutil.copyfile(metadata, state / "dependency-verification.xml")
    save(state / "environment.json", env)
    verify(state / "core", locked["core"])
    verify(state / "organicmaps", locked["organicmaps"])


def offline(state):
    """PID1 executes only inside the newly owned root-created namespaces."""
    require(os.getuid() == 0 and os.geteuid() == 0, "NAMESPACE_BOOTSTRAP_ROOT")
    outer = json.loads((state / "baseline.json").read_text())
    require(os.readlink("/proc/self/ns/net") != outer["netns"] and os.getpid() == 1, "DISPOSABLE_NAMESPACES")
    print("Disposable namespace only: activate its loopback, drop all root groups/UID, run offline synthetic tests.", flush=True)
    subprocess.run(["/usr/sbin/ip", "link", "set", "lo", "up"], check=True, timeout=10)
    require(sorted(name for _, name in socket.if_nameindex()) == ["lo"], "OFFLINE_LINKS")
    owner = state.stat()
    require(owner.st_uid != 0 and state.resolve(strict=True) == state, "OWNED_STATE")
    os.setgroups([]); os.setgid(owner.st_gid); os.setuid(owner.st_uid)
    env = json.loads((state / "environment.json").read_text())
    guards(env)
    tools, prepared = state / "tools", state / "prepared"
    android = prepared / "android"
    success, cleanup = False, False
    adb = tools / "sdk/platform-tools/adb"
    emulator = None
    adb_server = None
    def command(label, *args, seconds=30):
        return run(label, [adb, "-s", SERIAL, *args], state, env, seconds=seconds)
    try:
        run("android-offline-rebuild", gradle(tools, "--offline", "--dependency-verification=strict",
            ":volparossa_dps_jni:clean", ":volparossa_dps_jni:assembleDebug", ":volparossa_dps_jni:assembleDebugAndroidTest"),
            state, env, android, seconds=1200)
        # AVD is an owned, hand-written public API36 fixture; no SDK manager,
        # fabricated package metadata or user/global AVD is consulted.
        avd = state / "avd/trial.avd"
        avd.mkdir(parents=True)
        (state / "avd/trial.ini").write_text("avd.ini.encoding=UTF-8\npath=" + str(avd) + "\ntarget=android-36\n")
        (avd / "config.ini").write_text("AvdId=trial\nabi.type=x86_64\nhw.cpu.arch=x86_64\n"
            + "image.sysdir.1=" + str(tools / "sdk/system-images/android-36/default/x86_64")
            + "\nhw.ramSize=1536\nhw.cpu.ncore=2\nhw.lcd.width=1080\nhw.lcd.height=1920\nhw.lcd.density=420\n"
            + "hw.keyboard=yes\nhw.audioInput=no\nhw.audioOutput=no\nhw.gps=no\nshowDeviceFrame=no\n")
        with (state / "adb.private.log").open("xb") as log:
            adb_server = ACTIVE.spawn([str(adb), "-L", "tcp:127.0.0.1:5037", "nodaemon", "server"],
                                          env=env, stdout=log, stderr=log)
        with (state / "emulator.private.log").open("xb") as log:
            emulator = ACTIVE.spawn([str(tools / "sdk/emulator/emulator"), "-avd", "trial", "-port", "5554",
                "-accel", "on", "-no-window", "-no-audio", "-no-snapshot", "-no-boot-anim", "-no-metrics",
                "-gpu", "swiftshader_indirect", "-camera-back", "none", "-camera-front", "none"],
                env=env, stdout=log, stderr=log)
        stop = time.monotonic() + 180
        attempt = 0
        while True:
            require(emulator.poll() is None and adb_server.poll() is None and time.monotonic() < stop, "EMULATOR_BOOT")
            attempt += 1
            try:
                if command("boot-" + str(attempt), "shell", "getprop", "sys.boot_completed", seconds=3).strip() == b"1":
                    break
            except ValueError:
                pass
            time.sleep(1)
        require(command("guest-api", "shell", "getprop", "ro.build.version.sdk").strip() == b"36", "GUEST_API")
        require(command("guest-abi", "shell", "getprop", "ro.product.cpu.abi").strip() == b"x86_64", "GUEST_ABI")
        command("guest-wifi-off", "shell", "svc", "wifi", "disable")
        command("guest-data-off", "shell", "svc", "data", "disable")
        apks = list((android / "volparossa_dps_jni/build/outputs/apk/androidTest/debug").glob("*.apk"))
        require(len(apks) == 1, "ONE_TEST_APK")
        save(state / "tested-apk.json", digest(apks[0].read_bytes()))
        command("install-test", "install", "--no-streaming", apks[0], seconds=60)
        output = command("instrumentation", "shell", "am", "instrument", "-w", "-r", RUNNER, seconds=180)
        require(b"OK (8 tests)" in output and b"FAILURES" not in output
                and b"INSTRUMENTATION_FAILED" not in output, "ACTUAL_INSTRUMENTATION_RESULT")
        command("stop-test", "shell", "am", "force-stop", PACKAGE)
        command("uninstall-test", "uninstall", PACKAGE)
        save(state / "native-result.json", dict(schema=1, success=True, actual_rust_jni=True,
            actual_debug_activity=True, synthetic_quality_only=True, main_map_navigation=False,
            sensors_exercised=False, physical_accuracy_proven=False, confidential_execution=False,
            permissionless_asserted_by_installed_test=True, external_network_prevented_by_netns=True,
            os_app_identity_proven=False, instrumentation=digest(output)))
        success = True
    finally:
        original, cleanup_error = sys.exc_info()[1], None
        try:
            with ACTIVE.cleanup():
                ACTIVE.drain_all()
                for name in ("avd", "android-home"):
                    path = state / name
                    if path.exists():
                        shutil.rmtree(path)
        except BaseException as error:
            cleanup_error = error
        try:
            with ACTIVE.cleanup():
                save(state / "namespace-cleanup.json", dict(direct_children_reaped=not ACTIVE.owned,
                    avd_removed=not (state / "avd").exists(), android_home_removed=not (state / "android-home").exists(),
                    namespace_parent_must_confirm_exit=True, trial_returned=success, signal_count=ACTIVE.signal_count,
                    cleanup_error=None if cleanup_error is None else type(cleanup_error).__name__))
        except BaseException as error:
            ACTIVE.cleanup_errors.append(type(error).__name__)
            if original is None and cleanup_error is None:
                raise
        if original is None and cleanup_error is not None:
            raise cleanup_error


def failure_code(error):
    if error is None:
        return None
    value = error.args[0] if error.args else ""
    return value if isinstance(value, str) and re.fullmatch("[A-Z_]+", value) else type(error).__name__


def finalize(state, evidence, before, complete, original, control_mode=None):
    """Best-effort bounded finalization; original failure always keeps precedence."""
    errors, removed, unchanged, drained = [], False, None, False
    result = dict(schema=2, source=os.environ.get("GITHUB_SHA"), success=False,
                  primary_failure=failure_code(original), synthetic_only=True, hardware_acceptance=False)
    try:
        with ACTIVE.cleanup():
            evidence.mkdir(parents=True, exist_ok=False)
            if control_mode and ACTIVE.first_signal is not None:
                save(evidence / "cleanup-entered.json", dict(mode=control_mode))
                # Only the explicit hosted cancellation control waits here. This
                # leaves cleanup active for the independently timed second signal.
                while not (evidence / "second-signal-sent").exists():
                    time.sleep(0.01)
            ACTIVE.drain_all()
            drained = not ACTIVE.owned
            unchanged = before is not None and baseline() == before
            if not unchanged:
                errors.append("HOST_BASELINE_UNCONFIRMED")
            for path in state.glob("*.json"):
                if path.name != "environment.json":
                    shutil.copyfile(path, evidence / path.name)
            for mode in ("term-first", "int-term"):
                for name in ("result.json", "cleanup-entered.json"):
                    receipt = state / ("control-evidence-" + mode) / name
                    if receipt.exists():
                        shutil.copyfile(receipt, evidence / ("control-" + mode + "-" + name))
            metadata = state / "dependency-verification.xml"
            if metadata.exists():
                shutil.copyfile(metadata, evidence / metadata.name)
            require(state.resolve(strict=True) == state and state.stat().st_uid == os.getuid(), "OWNED_FINAL_CLEANUP")
            # Only after all registered groups drain: never erase live child state.
            shutil.rmtree(state)
            removed = not state.exists()
    except BaseException as error:
        errors.append(type(error).__name__)
    finally:
        # Tiny fixed receipts remain best effort after a cleanup deadline. A hard
        # kill, blocked filesystem or runner destruction can prevent even these.
        result.update(host_routes_dns_firewall_unchanged=unchanged, registered_groups_drained=drained,
            owned_state_removed=removed, completed_workload=complete, first_signal=ACTIVE.first_signal,
            signal_count=ACTIVE.signal_count, cleanup_errors=ACTIVE.cleanup_errors + errors,
            success=complete and unchanged is True and drained and removed and not errors
                    and not ACTIVE.cleanup_errors and ACTIVE.first_signal is None)
        try:
            evidence.mkdir(parents=True, exist_ok=True)
            save(evidence / "result.json", result)
        except BaseException as error:
            errors.append(type(error).__name__)
    if original is None:
        if errors or ACTIVE.cleanup_errors:
            raise RuntimeError("CLEANUP_UNCONFIRMED")
        ACTIVE.check()
    return result


def namespace_command(mode, state, seconds):
    return ["sudo", "-n", "timeout", "--signal=TERM", "--kill-after=1s", str(seconds) + "s",
            "unshare", "--net", "--pid", "--fork", "--mount-proc", "--kill-child=KILL",
            "/usr/bin/python3", "-B", HERE / "hosted.py", mode, state]


def birth(pid):
    try:
        return Path("/proc", str(pid), "stat").read_text().rpartition(") ")[2].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def wait_for(test, child, seconds):
    stop = time.monotonic() + seconds
    while not test():
        require(child.poll() is None and time.monotonic() < stop, "CANCELLATION_CONTROL_DEADLINE")
        time.sleep(0.01)


def inside_cancellation(state):
    require(os.getuid() == 0 and os.getpid() == 1 and state.resolve(strict=True) == state, "CANCELLATION_NAMESPACE")
    outer = json.loads((state / "baseline.json").read_text())
    require(os.readlink("/proc/self/ns/net") != outer["netns"]
            and sorted(n for _, n in socket.if_nameindex()) == ["lo"], "CANCELLATION_NETNS")
    owner = state.stat()
    require(owner.st_uid != 0, "CANCELLATION_OWNER")
    os.setgroups([]); os.setgid(owner.st_gid); os.setuid(owner.st_uid)
    # This child creates a separate session, proving the PID namespace supplies
    # cleanup beyond the supervisor group. No VM/SDK/network/sensors are involved.
    with (state / "escaped.private.log").open("xb") as log:
        ACTIVE.spawn(["/usr/bin/python3", "-B", "-c", "import time; time.sleep(300)"],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    save(state / "namespace-ready.json", dict(netns=os.readlink("/proc/self/ns/net"), escaped_session_started=True))
    while True:
        time.sleep(0.1)


def cancellation_child(state, evidence, mode):
    guards(os.environ)
    require(mode in ("term-first", "int-term") and state.parent.resolve(strict=True) == state.parent
            and not state.exists() and evidence.parent == state.parent, "CANCELLATION_CHILD_SCOPE")
    created, before, complete = False, None, False
    try:
        with ACTIVE.registration():
            state.mkdir(mode=0o700); created = True
        before = baseline(); save(state / "baseline.json", before)
        (state / "private-payload").write_bytes(b"synthetic cancellation control state")
        run("cancel-namespace", namespace_command("--inside-cancellation", state, 20), state, dict(os.environ), seconds=22)
        complete = True
        raise RuntimeError("CANCELLATION_NOT_DELIVERED")
    finally:
        original = sys.exc_info()[1]
        if created:
            finalize(state, evidence, before, complete, original, control_mode=mode)


def cancellation_control(state, env):
    """Actual hosted-only control, independent of a wrapper's exit status."""
    guards(os.environ)
    reports = []
    # Verify an ordinary exec child inherits unblocked TERM and really exits on
    # TERM, separately from namespace/root-kill cleanup.
    ready = state / "term-default-ready.json"
    code = ("import json,signal,time; from pathlib import Path; "
            "status=Path('/proc/self/status').read_text(); "
            "mask=int(status.split('SigBlk:')[1].split()[0],16); "
            "Path(__import__('sys').argv[1]).write_text(json.dumps(dict("
            "term_default=signal.getsignal(signal.SIGTERM)==signal.SIG_DFL,"
            "term_unblocked=not bool(mask & (1 << (signal.SIGTERM-1))),"
            "int_unblocked=not bool(mask & (1 << (signal.SIGINT-1)))))); time.sleep(30)")
    with (state / "term-default.private.log").open("xb") as log:
        child = ACTIVE.spawn(["/usr/bin/python3", "-B", "-c", code, ready], env=env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    wait_for(ready.exists, child, 5)
    require(json.loads(ready.read_text()) == dict(term_default=True, term_unblocked=True, int_unblocked=True), "CHILD_SIGNAL_STATE")
    os.kill(child.pid, signal.SIGTERM)
    require(child.wait(timeout=2) == -signal.SIGTERM, "CHILD_REAL_TERM_DELIVERY")
    with ACTIVE.cleanup():
        ACTIVE.drain(child)
    for mode in ("term-first", "int-term"):
        case = state / ("control-" + mode)
        evidence = state / ("control-evidence-" + mode)
        with (state / ("control-" + mode + ".private.log")).open("xb") as log:
            child = ACTIVE.spawn(["/usr/bin/python3", "-B", HERE / "hosted.py", "--cancellation-child", case, evidence, mode],
                                 env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        wait_for(lambda: (case / "namespace-ready.json").exists(), child, 10)
        namespace = json.loads((case / "namespace-ready.json").read_text())
        tracked = {pid: birth(pid) for pid in children(child.pid)[0]}
        require(len(tracked) >= 4 and namespace["netns"] != os.readlink("/proc/self/ns/net"), "REAL_NAMESPACE_CHILDREN")
        first = signal.SIGTERM if mode == "term-first" else signal.SIGINT
        started = time.monotonic(); os.kill(child.pid, first)
        wait_for(lambda: (evidence / "cleanup-entered.json").exists(), child, 1)
        if mode == "int-term":
            # Match the documented GitHub runner INT -> 7500ms -> TERM sequence.
            while time.monotonic() < started + 7.5:
                require(child.poll() is None, "CANCELLATION_CONTROL_EARLY_EXIT")
                time.sleep(0.01)
        os.kill(child.pid, signal.SIGINT if mode == "term-first" else signal.SIGTERM)
        (evidence / "second-signal-sent").write_bytes(b"sent")
        status = child.wait(timeout=2)
        with ACTIVE.cleanup():
            ACTIVE.drain(child)
        receipt = json.loads((evidence / "result.json").read_text())
        extinct = all(token is not None and birth(pid) != token for pid, token in tracked.items())
        require(status == 128 + first and receipt["primary_failure"] == "HOSTED_INTERRUPTED"
                and receipt["signal_count"] >= 2 and receipt["registered_groups_drained"]
                and receipt["owned_state_removed"] and receipt["host_routes_dns_firewall_unchanged"]
                and not receipt["cleanup_errors"] and extinct and not case.exists(), "CANCELLATION_CONTROL_FAILED")
        reports.append(dict(mode=mode, signal_sequence=[int(first), int(signal.SIGINT if mode == "term-first" else signal.SIGTERM)],
            tracked_processes=len(tracked), tracked_processes_extinct=extinct, private_state_removed=not case.exists(),
            namespace=namespace["netns"], elapsed_seconds=time.monotonic() - started, receipt=receipt))
    save(state / "cancellation-control.json", dict(schema=1, actual_hosted_processes=True,
        actual_disposable_namespaces=True, real_default_child_term_verified=True, success=True, cases=reports,
        android_executed=False, hard_kill_cleanup_guaranteed=False))


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] in ("--inside-offline", "--inside-cancellation"):
        (offline if sys.argv[1] == "--inside-offline" else inside_cancellation)(Path(sys.argv[2])); return
    if len(sys.argv) == 5 and sys.argv[1] == "--cancellation-child":
        cancellation_child(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]); return
    require(sys.argv[1:] == ["--reviewed-hosted-fixture"], "NO_ARBITRARY_COMMAND")
    guards(os.environ)
    state = Path(os.environ["RUNNER_TEMP"]) / ("map-dps-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"])
    require(not state.exists(), "NEW_RUN_STATE")
    before, complete, created = None, False, False
    try:
        with ACTIVE.registration():
            state.mkdir(mode=0o700); created = True
        before = baseline(); save(state / "baseline.json", before)
        save(state / "source.json", dict(map=os.environ["GITHUB_SHA"], pins=pins(),
            workflow=WORKFLOW, run=os.environ["GITHUB_RUN_ID"], attempt=os.environ["GITHUB_RUN_ATTEMPT"]))
        env = environment(state, state / "tools")
        for directory in ("home", "android-home", "gradle-home", "rustup", "cargo"):
            (state / directory).mkdir(mode=0o700)
        cancellation_control(state, env)
        acquire_build(state, env)
        print("Reviewed hosted fixture: disposable namespaces and namespace loopback only.", flush=True)
        run("offline-namespace", namespace_command("--inside-offline", state, 1590), state, env, seconds=1600)
        ACTIVE.check(); complete = True
    finally:
        original = sys.exc_info()[1]
        if created:
            finalize(state, ROOT / "build/dps-native-evidence", before, complete, original)


if __name__ == "__main__":
    try:
        ACTIVE = Lifetime()
        with ACTIVE:
            main()
    except Interrupted:
        print("HOSTED_INTERRUPTED; cleanup receipt required", file=sys.stderr)
        sys.exit(128 + (ACTIVE.first_signal or signal.SIGTERM))
