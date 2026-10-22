#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Guarded, manual public hosted synthetic JNI proof candidate. Never a host installer.

Public source/dependency acquisition precedes an offline rebuild and installed
API36/x86_64 test in a disposable network/PID/mount namespace. Only namespace
loopback is activated. No core daemon, network participation or sensor trial.
"""
import json
import errno
import fcntl
import grp
import stat
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
KVM = Path("/dev/kvm")
KVM_GET_API_VERSION = 0xAE00  # Linux UAPI _IO(KVMIO, 0x00), system query only.
PHASES = ("kvm-probe", "public-build", "offline-build", "emulator")
KVM_PHASES = ("kvm-probe", "emulator")
IPV6_ROUTE_BYTES = 1024**2
IPV6_ROUTE_ROWS = 4096
IPV6_ROUTE_RENDERER_PROFILE = "linux-6.8-native-seq-v1"
IPV6_ROUTE_RENDERER_PROFILES = {
    "linux-6.8-native-seq-v1": r"6\.8\.0-[0-9]{1,6}-(?:generic|azure)",
    "linux-7.0-azure-native-seq-v1": r"7\.0\.0-[0-9]{1,6}-azure",
}


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
    require(Path(env.get("RUNNER_TEMP", "")).is_absolute()
            and all(re.fullmatch("[1-9][0-9]{0,19}", env.get(k, "")) for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")), "RUNNER_TEMP")


def save(path, value):
    temporary = path.with_name(path.name + ".part")
    with temporary.open("x") as output:
        output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


class KvmFailure(ValueError):
    def __init__(self, code, number=None):
        super().__init__(code)
        self.errno = number


def credentials():
    groups = sorted(os.getgroups())
    require(len(groups) <= 64 and len(groups) == len(set(groups)), "CREDENTIAL_GROUP_BOUND")
    return dict(uid=list(os.getresuid()), gid=list(os.getresgid()), groups=groups)


def capability_bits():
    with Path("/proc/self/status").open() as source:
        text = source.read(16385)
    require(len(text) <= 16384, "CAPABILITY_STATUS_BOUND")
    rows = dict(row.split(":", 1) for row in text.splitlines() if ":" in row)
    result = {}
    for name in ("CapEff", "CapPrm", "CapAmb"):
        value = rows.get(name, "").strip()
        require(re.fullmatch("[0-9a-fA-F]{1,16}", value), "CAPABILITY_STATUS_SHAPE")
        result[name] = int(value, 16)
    return result


def device_identity(info):
    return {name: getattr(info, "st_" + name) for name in
            ("dev", "ino", "rdev", "mode", "uid", "gid", "nlink", "mtime_ns", "ctime_ns")}


def kvm_snapshot():
    """Read only the fixed device metadata and existing named group, never members."""
    try:
        info = KVM.lstat()
    except OSError as error:
        raise KvmFailure("KVM_MISSING" if error.errno == errno.ENOENT else "KVM_STAT_FAILED", error.errno) from None
    if not stat.S_ISCHR(info.st_mode):
        raise KvmFailure("KVM_WRONG_TYPE")
    if (os.major(info.st_rdev), os.minor(info.st_rdev)) != (10, 232):
        raise KvmFailure("KVM_WRONG_DEVICE")
    try:
        gid = grp.getgrnam("kvm").gr_gid
    except KeyError:
        raise KvmFailure("KVM_GROUP_MISSING") from None
    if gid <= 0 or gid != info.st_gid:
        raise KvmFailure("KVM_GROUP_MISMATCH")
    try:
        raw_acl = os.getxattr(KVM, "system.posix_acl_access", follow_symlinks=False)
        if len(raw_acl) > 4096:
            raise KvmFailure("KVM_ACL_BOUND")
        acl = dict(present=True, **digest(raw_acl))
    except OSError as error:
        if error.errno != errno.ENODATA:
            raise KvmFailure("KVM_ACL_UNREADABLE", error.errno) from None
        acl = dict(present=False)
    result = dict(device=device_identity(info), acl=acl, kvm_gid=gid)
    try:
        if device_identity(KVM.lstat()) != result["device"]:
            raise KvmFailure("KVM_DEVICE_CHANGED")
    except OSError as error:
        raise KvmFailure("KVM_DEVICE_CHANGED", error.errno) from None
    return result


def kvm_comparison(before, after):
    """Closed checkpoint comparison; raw snapshots are never rewritten."""
    device_fields = {"dev", "ino", "rdev", "mode", "uid", "gid", "nlink", "mtime_ns", "ctime_ns"}
    for value in (before, after):
        require(type(value) is dict and set(value) == {"device", "acl", "kvm_gid"}, "KVM_METADATA_SHAPE")
        device, acl = value["device"], value["acl"]
        require(type(device) is dict and set(device) == device_fields, "KVM_METADATA_SHAPE")
        require(all(type(device[key]) is int and 0 <= device[key] < 2**64 for key in device_fields - {"mtime_ns", "ctime_ns"})
                and all(type(device[key]) is int and -2**63 <= device[key] < 2**64 for key in ("mtime_ns", "ctime_ns")),
                "KVM_METADATA_SHAPE")
        require(device["mode"] < 2**16 and stat.S_ISCHR(device["mode"])
                and (os.major(device["rdev"]), os.minor(device["rdev"])) == (10, 232)
                and device["uid"] < 2**32 and device["gid"] < 2**32 and device["nlink"] > 0,
                "KVM_METADATA_SHAPE")
        require(type(value["kvm_gid"]) is int and 0 < value["kvm_gid"] < 2**32
                and value["kvm_gid"] == device["gid"], "KVM_METADATA_SHAPE")
        require(type(acl) is dict and ((set(acl) == {"present"} and acl["present"] is False) or
                (set(acl) == {"present", "bytes", "sha256"} and acl["present"] is True
                 and type(acl["bytes"]) is int and 0 <= acl["bytes"] <= 4096
                 and type(acl["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", acl["sha256"]))), "KVM_METADATA_SHAPE")
    raw_unchanged = before == after
    configuration_unchanged = (before["acl"] == after["acl"] and before["kvm_gid"] == after["kvm_gid"]
        and all(before["device"][key] == after["device"][key] for key in device_fields - {"mtime_ns", "ctime_ns"}))
    return dict(classification="UNCHANGED" if raw_unchanged else "TIMESTAMPS_ONLY" if configuration_unchanged
                else "CONFIGURATION_DIFFERENCE", raw_unchanged=raw_unchanged, configuration_unchanged=configuration_unchanged)


def kvm_admission(state):
    """Metadata receipt exists even if the fixed device/group cannot be used."""
    result = dict(schema=1, kind="kvm-metadata-only", success=False,
                  source=os.environ["GITHUB_SHA"], credentials=credentials(), device=None,
                  actual_open=False, vm_created=False)
    try:
        try:
            result["observed_metadata"] = device_identity(KVM.lstat())
        except OSError as error:
            raise KvmFailure("KVM_MISSING" if error.errno == errno.ENOENT else "KVM_STAT_FAILED", error.errno) from None
        result["device"] = kvm_snapshot()
        require(result["observed_metadata"] == result["device"]["device"], "KVM_DEVICE_CHANGED")
        require(result["credentials"]["uid"] == [result["credentials"]["uid"][0]] * 3
                and result["credentials"]["uid"][0] > 0, "PARENT_UID_AUTHORITY")
        require(result["credentials"]["gid"] == [result["credentials"]["gid"][0]] * 3
                and result["credentials"]["gid"][0] != result["device"]["kvm_gid"], "KVM_PRIMARY_GID_OVERLAP")
        result["outer_access"] = os.access(KVM, os.R_OK | os.W_OK)
        result["success"] = True  # Metadata admission, not KVM execution acceptance.
    except (ValueError, OSError) as error:
        result.update(code=failure_code(error), errno=getattr(error, "errno", None))
        raise
    finally:
        save(state / "kvm-admission.json", result)
    return result["device"]


def drop_credentials(uid, gid, phase, snapshot):
    """Only probe/emulator receive the sole device-derived supplementary group."""
    require(os.getuid() == os.geteuid() == 0, "CREDENTIAL_DROP_ROOT")
    require(phase in PHASES and uid > 0 and gid > 0 and snapshot["kvm_gid"] > 0
            and gid != snapshot["kvm_gid"], "CREDENTIAL_TARGET")
    selected = [snapshot["kvm_gid"]] if phase in KVM_PHASES else []
    try:
        os.setgroups(selected)
        os.setresgid(gid, gid, gid)
        os.setresuid(uid, uid, uid)
    except OSError as error:
        raise KvmFailure("CREDENTIAL_DROP_FAILED", error.errno) from None
    observed = credentials()
    require(observed == dict(uid=[uid] * 3, gid=[gid] * 3, groups=selected), "CREDENTIAL_DROP_MISMATCH")
    require(all(value == 0 for value in capability_bits().values()), "CREDENTIAL_CAPABILITIES_REMAIN")
    return observed


def activate_environment(env):
    # sudo resets the launcher environment. Only after permanent privilege drop
    # restore the existing bounded build environment for nested guards/rustup.
    require(os.getuid() == os.geteuid() != 0, "PHASE_ENV_UNPRIVILEGED")
    require(type(env) is dict and len(env) <= 64 and all(
        isinstance(k, str) and 0 < len(k) <= 128 and "=" not in k and "\x00" not in k
        and isinstance(v, str) and len(v) <= 8192 and "\x00" not in v for k, v in env.items()), "PHASE_ENV_BOUND")
    os.environ.clear(); os.environ.update(env)


def kvm_open_query(expected):
    """Unprivileged system query only. No VM creation or transferred probe FD."""
    require(os.getuid() == os.geteuid() != 0, "KVM_PROBE_UNPRIVILEGED")
    fd, original = None, None
    result = dict(api_version=None, fd_closed=False, vm_created=False)
    try:
        require(kvm_snapshot() == expected, "KVM_DEVICE_CHANGED")
        with ACTIVE.registration():
            try:
                fd = os.open(KVM, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            except OSError as error:
                code = "KVM_OPEN_DENIED" if error.errno in (errno.EACCES, errno.EPERM) else "KVM_OPEN_FAILED"
                raise KvmFailure(code, error.errno) from None
        require(device_identity(os.fstat(fd)) == expected["device"], "KVM_DEVICE_CHANGED")
        try:
            result["api_version"] = fcntl.ioctl(fd, KVM_GET_API_VERSION, 0)
        except OSError as error:
            raise KvmFailure("KVM_API_UNAVAILABLE", error.errno) from None
        require(result["api_version"] == 12, "KVM_API_VERSION_UNSUPPORTED")
    except BaseException as error:
        original = error
        raise
    finally:
        if fd is not None:
            try:
                with ACTIVE.cleanup():
                    os.close(fd); result["fd_closed"] = True
            except BaseException:
                if original is None:
                    raise
    require(result["fd_closed"] and kvm_snapshot() == expected, "KVM_DEVICE_CHANGED")
    return result


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


def private_pid1_identity(parent_pidns):
    require(type(parent_pidns) is str and re.fullmatch(r"pid:\[[1-9][0-9]{0,19}\]", parent_pidns), "PRIVATE_PIDNS_BINDING")
    current = os.readlink("/proc/self/ns/pid")
    require(re.fullmatch(r"pid:\[[1-9][0-9]{0,19}\]", current) and current != parent_pidns
            and os.getpid() == 1 and os.readlink("/proc/self") == "1", "PRIVATE_PID1_REQUIRED")
    return current


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
        self.private_pid1 = False

    def enable_private_pid1(self, parent_pidns):
        private_pid1_identity(parent_pidns)
        self.private_pid1 = True

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
        if self.private_pid1:
            require(child in self.owned and type(child.pid) is int and 1 < child.pid <= 2**31 - 1,
                    "OWNED_REAP_GROUP")
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
        reap_calls = 0
        while True:
            if self.private_pid1:
                require(type(child.returncode) is int, "REAP_AFTER_LEADER_WAIT")
                while reap_calls < 256:
                    require(time.monotonic() < stop, "PROCESS_GROUP_DRAIN")
                    reap_calls += 1
                    try:
                        waited, _status = os.waitpid(-child.pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if waited == 0:
                        break
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                pass
            require(time.monotonic() < stop, "PROCESS_GROUP_DRAIN")
            if self.private_pid1:
                require(reap_calls < 256, "PROCESS_GROUP_REAP_BOUND")
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


def firewall_snapshot(raw):
    """Schema-1 listing: omit only validated counter packet/byte statistics."""
    require(type(raw) is bytes and 0 < len(raw) <= 1024**2, "FIREWALL_JSON_BYTES")
    # Bound nesting before json.loads; quoted braces and escapes are not nesting.
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped: escaped = False
            elif byte == 92: escaped = True
            elif byte == 34: quoted = False
        elif byte == 34: quoted = True
        elif byte in (91, 123):
            depth += 1
            require(depth <= 32, "FIREWALL_JSON_DEPTH")
        elif byte in (93, 125):
            depth -= 1
            require(depth >= 0, "FIREWALL_JSON_DEPTH")
    require(depth == 0 and not quoted, "FIREWALL_JSON_SHAPE")

    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "FIREWALL_JSON_DUPLICATE")
            value[key] = item
        return value

    def unsupported_number(_):
        raise ValueError("FIREWALL_JSON_NUMBER")

    def integer_number(value):
        require(len(value) <= 21, "FIREWALL_JSON_NUMBER")
        return int(value)

    try:
        tree = json.loads(raw, object_pairs_hook=object_pairs, parse_float=unsupported_number,
                          parse_constant=unsupported_number, parse_int=integer_number)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("FIREWALL_JSON_PARSE") from None
    pending, nodes = [tree], 0
    while pending:
        value = pending.pop(); nodes += 1
        require(nodes <= 65536, "FIREWALL_JSON_NODES")
        if type(value) is dict: pending.extend(value.values())
        elif type(value) is list: pending.extend(value)
        elif type(value) is int:
            require(-2**63 <= value < 2**64, "FIREWALL_JSON_NUMBER")
    require(type(tree) is dict and set(tree) == {"nftables"} and type(tree["nftables"]) is list
            and len(tree["nftables"]) > 0, "FIREWALL_JSON_ENVELOPE")
    listing = tree["nftables"]
    metadata = listing[0].get("metainfo") if type(listing[0]) is dict else None
    require(type(metadata) is dict and set(metadata) == {"version", "release_name", "json_schema_version"}
            and type(metadata["version"]) is str and type(metadata["release_name"]) is str
            and type(metadata["json_schema_version"]) is int and metadata["json_schema_version"] == 1,
            "FIREWALL_JSON_SCHEMA")
    accepted, statistics = set(), []
    kinds = {"table", "chain", "rule", "set", "map", "element", "flowtable", "counter", "quota",
             "ct helper", "limit", "ct timeout", "ct expectation"}

    def uint64(value):
        return type(value) is int and 0 <= value < 2**64

    def strip_counter(wrapper, named):
        counter = wrapper["counter"]
        if not named and type(counter) is str:
            require(0 < len(counter) <= 256, "FIREWALL_COUNTER_REFERENCE")
        else:
            required = {"family", "table", "name", "packets", "bytes"} if named else {"packets", "bytes"}
            require(type(counter) is dict and required <= set(counter)
                    and set(counter) <= required | ({"handle"} if named else set()), "FIREWALL_COUNTER_SHAPE")
            if named:
                require(counter["family"] in ("ip", "ip6", "inet", "arp", "bridge", "netdev")
                        and all(type(counter[key]) is str and 0 < len(counter[key]) <= 256 for key in ("table", "name"))
                        and ("handle" not in counter or uint64(counter["handle"])), "FIREWALL_COUNTER_SHAPE")
            require(uint64(counter["packets"]) and uint64(counter["bytes"]), "FIREWALL_COUNTER_VALUE")
            statistics.append([counter.pop("packets"), counter.pop("bytes")])
        accepted.add(id(wrapper))

    for index, item in enumerate(listing):
        require(type(item) is dict and len(item) == 1, "FIREWALL_LIST_SHAPE")
        kind = next(iter(item))
        if index == 0:
            require(kind == "metainfo", "FIREWALL_JSON_SCHEMA")
            continue
        require(kind in kinds and type(item[kind]) is dict, "FIREWALL_LIST_SHAPE")
        if kind == "counter": strip_counter(item, True)
        elif kind == "rule":
            rule = item[kind]
            require(type(rule.get("expr")) is list and len(rule["expr"]) > 0, "FIREWALL_RULE_SHAPE")
            for statement in rule["expr"]:
                require(type(statement) is dict and len(statement) == 1, "FIREWALL_RULE_SHAPE")
                if "counter" in statement: strip_counter(statement, False)
    # Unsupported nested counter contexts fail closed rather than losing policy.
    pending = [tree]
    while pending:
        value = pending.pop()
        if type(value) is dict:
            require("counter" not in value or id(value) in accepted, "FIREWALL_COUNTER_CONTEXT")
            pending.extend(value.values())
        elif type(value) is list: pending.extend(value)
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return dict(schema=1, raw=digest(raw), config=digest(canonical(tree)),
                counters=digest(canonical(statistics)), counter_count=len(statistics))


def firewall_comparison(before, after):
    for value in (before, after):
        require(type(value) is dict and set(value) == {"schema", "raw", "config", "counters", "counter_count"}
                and type(value["schema"]) is int and value["schema"] == 1
                and type(value["counter_count"]) is int and 0 <= value["counter_count"] <= 65536,
                "FIREWALL_METADATA_SHAPE")
        for key in ("raw", "config", "counters"):
            item = value[key]
            require(type(item) is dict and set(item) == {"bytes", "sha256"}
                    and type(item["bytes"]) is int and 0 <= item["bytes"] < 2**64
                    and type(item["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", item["sha256"]),
                    "FIREWALL_METADATA_SHAPE")
    config_changed = before["config"] != after["config"] or before["counter_count"] != after["counter_count"]
    counters_changed = before["counters"] != after["counters"]
    raw_changed = before["raw"] != after["raw"]
    classification = ("CONFIG_CHANGED" if config_changed else "COUNTERS_ONLY" if counters_changed
                      else "REPRESENTATION_ONLY" if raw_changed else "UNCHANGED")
    return dict(classification=classification, raw_changed=raw_changed,
                config_changed=config_changed, counters_changed=counters_changed)


def validate_ipv6_renderer(value, *, supported=True):
    """Reviewed layout identity, not proof of the running kernel's source tree."""
    require(type(supported) is bool, "IPV6_RENDERER_METADATA_SHAPE")
    require(type(value) is dict and set(value) == {"schema", "profile", "kernel"}
            and type(value["schema"]) is int and value["schema"] == 1
            and type(value["profile"]) is str and value["profile"] in IPV6_ROUTE_RENDERER_PROFILES,
            "IPV6_RENDERER_METADATA_SHAPE")
    kernel = value["kernel"]
    require(type(kernel) is dict and set(kernel) == {"sysname", "release", "version", "machine"}
            and all(type(kernel[key]) is str and 0 < len(kernel[key]) <= bound
                    and re.fullmatch(r"[ -~]+", kernel[key])
                    for key, bound in (("sysname", 16), ("release", 128), ("version", 256), ("machine", 32))),
            "IPV6_RENDERER_METADATA_SHAPE")
    if supported:
        require(kernel["sysname"] == "Linux" and kernel["machine"] == "x86_64"
                and re.fullmatch(IPV6_ROUTE_RENDERER_PROFILES[value["profile"]], kernel["release"]),
                "IPV6_RENDERER_UNSUPPORTED")


def ipv6_renderer_snapshot():
    # Uname's hostname is deliberately neither selected nor retained. No command,
    # network request, module load or host configuration write is performed.
    identity = os.uname()
    value = dict(schema=1, profile=IPV6_ROUTE_RENDERER_PROFILE,
                 kernel={key: getattr(identity, key) for key in ("sysname", "release", "version", "machine")})
    validate_ipv6_renderer(value, supported=False)
    # Select only an explicitly reviewed release/profile pairing. Unknown
    # releases retain the original requested profile and still fail admission.
    value["profile"] = next((profile for profile, release in IPV6_ROUTE_RENDERER_PROFILES.items()
                             if re.fullmatch(release, value["kernel"]["release"])), IPV6_ROUTE_RENDERER_PROFILE)
    return value


def ipv6_renderer_admission(state):
    """Retain bounded identity on refusal, before acquisition or Android starts."""
    receipt = dict(schema=1, snapshot=None, compatible=False, code=None)
    try:
        receipt["snapshot"] = ipv6_renderer_snapshot()
        validate_ipv6_renderer(receipt["snapshot"])
        receipt["compatible"] = True
    except (ValueError, OSError) as error:
        receipt["code"] = (str(error) if isinstance(error, ValueError) and str(error) in
            ("IPV6_RENDERER_METADATA_SHAPE", "IPV6_RENDERER_UNSUPPORTED") else "IPV6_RENDERER_CAPTURE_FAILED")
        raise
    finally:
        save(state / "ipv6-renderer-admission.json", receipt)
    return receipt["snapshot"]


def ipv6_route_snapshot(raw):
    """Hash one bounded proc snapshot; only the reference-count bytes are split.

    Layout: reviewed Linux v6.8/v7.0 ipv6_route_native_seq_show, byte-identical.
    Projections alone grant no acceptance; the finalizer also requires the
    independently captured reviewed renderer identity and completed workload.
    Empty tables are valid. Duplicate rows and their order remain significant.
    """
    require(type(raw) is bytes and len(raw) <= IPV6_ROUTE_BYTES, "IPV6_ROUTE_BYTES")
    metadata = dict(schema=1, error=None, non_refcount=None, reference_metadata=None, row_count=None)
    snapshot = dict(raw=digest(raw), metadata=metadata)
    try:
        rows = raw.splitlines(keepends=True)
        require(len(rows) <= IPV6_ROUTE_ROWS, "IPV6_ROUTE_ROW_BOUND")
        non_refcount, references = [], []
        layout = (rb"[0-9a-f]{32} ([0-9a-f]{2}) [0-9a-f]{32} ([0-9a-f]{2}) "
                  rb"[0-9a-f]{32} [0-9a-f]{8} (?P<reference>[0-9a-f]{8}) "
                  rb"[0-9a-f]{8} [0-9a-f]{8} (?P<device> *[A-Za-z0-9_.-]{0,15})\n")
        for row in rows:
            match = re.fullmatch(layout, row)
            require(match is not None, "IPV6_ROUTE_ROW_SHAPE")
            require(all(int(match[index], 16) <= 128 for index in (1, 2)), "IPV6_ROUTE_ROW_SHAPE")
            device = match["device"].lstrip(b" ")
            require(device not in (b".", b"..") and match["device"] == device.rjust(8, b" "),
                    "IPV6_ROUTE_ROW_SHAPE")
            start, end = match.span("reference")
            non_refcount.append(row[:start] + row[end:])
            references.append(match["reference"] + b"\n")
        metadata.update(non_refcount=digest(b"".join(non_refcount)),
                        reference_metadata=digest(b"".join(references)), row_count=len(rows))
    except ValueError as error:
        # Fixed internal codes only; preserve the raw fingerprint on row rejection.
        metadata["error"] = "IPV6_ROUTE_ROW_BOUND" if str(error) == "IPV6_ROUTE_ROW_BOUND" else "IPV6_ROUTE_ROW_SHAPE"
    return snapshot


def read_ipv6_route_snapshot():
    # One bounded read provides both raw and projected fingerprints. No reread,
    # sorting, deduplication, host route output or unbounded read_bytes allocation.
    with Path("/proc/net/ipv6_route").open("rb") as source:
        raw = source.read(IPV6_ROUTE_BYTES + 1)
    return ipv6_route_snapshot(raw)


def ipv6_route_comparison(before, after):
    """Closed fingerprints only; rejected parses never become valid baselines."""
    def fingerprint(value, bound):
        return (type(value) is dict and set(value) == {"bytes", "sha256"}
                and type(value["bytes"]) is int and 0 <= value["bytes"] <= bound
                and type(value["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", value["sha256"]) is not None)
    for value in (before, after):
        require(type(value) is dict and set(value) == {"raw", "metadata"}
                and fingerprint(value["raw"], IPV6_ROUTE_BYTES), "IPV6_ROUTE_METADATA_SHAPE")
        meta = value["metadata"]
        require(type(meta) is dict and set(meta) == {"schema", "error", "non_refcount", "reference_metadata", "row_count"}
                and type(meta["schema"]) is int and meta["schema"] == 1, "IPV6_ROUTE_METADATA_SHAPE")
        if meta["error"] is not None:
            require(type(meta["error"]) is str and meta["error"] in ("IPV6_ROUTE_ROW_BOUND", "IPV6_ROUTE_ROW_SHAPE")
                    and all(meta[key] is None for key in ("non_refcount", "reference_metadata", "row_count")),
                    "IPV6_ROUTE_METADATA_SHAPE")
        else:
            require(type(meta["row_count"]) is int and 0 <= meta["row_count"] <= IPV6_ROUTE_ROWS
                    and fingerprint(meta["non_refcount"], IPV6_ROUTE_BYTES)
                    and fingerprint(meta["reference_metadata"], IPV6_ROUTE_ROWS * 9)
                    and meta["reference_metadata"]["bytes"] == meta["row_count"] * 9
                    and meta["non_refcount"]["bytes"] + meta["row_count"] * 8 == value["raw"]["bytes"],
                    "IPV6_ROUTE_METADATA_SHAPE")
    raw_changed = before["raw"] != after["raw"]
    valid = before["metadata"]["error"] is None and after["metadata"]["error"] is None
    fields = {key + "_changed": before["metadata"][key] != after["metadata"][key] if valid else None
              for key in ("non_refcount", "reference_metadata", "row_count")}
    require(not valid or raw_changed or not any(fields.values()), "IPV6_ROUTE_METADATA_SHAPE")
    classification = ("PARSE_REJECTED" if not valid else "NON_REFCOUNT_DIFFERENCE"
        if fields["non_refcount_changed"] or fields["row_count_changed"] else "REFERENCE_METADATA_ONLY"
        if fields["reference_metadata_changed"] else "RAW_DIFFERENCE" if raw_changed else "UNCHANGED")
    return dict(classification=classification, raw_changed=raw_changed, metadata_valid=valid, **fields)


def baseline_unchanged(before, after, *, kvm_configuration=False):
    require(type(kvm_configuration) is bool, "KVM_COMPARISON_MODE")
    require(all(type(value) is dict and "ipv6_routes" in value and "ipv6_route_metadata" in value
                for value in (before, after)), "IPV6_ROUTE_METADATA_SHAPE")
    ipv6 = ipv6_route_comparison(*[dict(raw=value["ipv6_routes"], metadata=value["ipv6_route_metadata"])
                                   for value in (before, after)])
    if not ipv6["metadata_valid"]: return False
    for value in (before, after):
        require("ipv6_renderer" in value, "IPV6_RENDERER_METADATA_SHAPE")
        validate_ipv6_renderer(value["ipv6_renderer"])
    comparison = None
    if type(before) is dict and type(after) is dict and ("kvm" in before or "kvm" in after):
        require("kvm" in before and "kvm" in after, "KVM_METADATA_SHAPE")
        comparison = kvm_comparison(before["kvm"], after["kvm"])
    if kvm_configuration:
        components = {"netns", "routes", "ipv6_routes", "ipv6_route_metadata", "ipv6_renderer", "resolver", "firewall", "credentials", "kvm"}
        require(type(before) is dict and type(after) is dict and set(before) == set(after) == components,
                "KVM_CONFIGURATION_BASELINE_SHAPE")
        diagnostic = baseline_diagnostic(before, after, None)
        require(diagnostic["diagnostic_error"] is None and diagnostic["before"] is not None
                and diagnostic["after"] is not None, "KVM_CONFIGURATION_BASELINE_SHAPE")
    if before == after:
        if type(before) is dict and "firewall" in before:
            firewall_comparison(before["firewall"], after["firewall"])
        return True
    ignored = {"firewall", "kvm", "ipv6_routes", "ipv6_route_metadata"} if kvm_configuration else {"firewall"}
    return (type(before) is dict and type(after) is dict and set(before) == set(after) and "firewall" in before
            and {key: value for key, value in before.items() if key not in ignored}
                == {key: value for key, value in after.items() if key not in ignored}
            and (not kvm_configuration or comparison["configuration_unchanged"])
            and (not kvm_configuration or ipv6["classification"] in ("UNCHANGED", "REFERENCE_METADATA_ONLY"))
            and not firewall_comparison(before["firewall"], after["firewall"])["config_changed"])


def baseline(*, renderer=None):
    renderer = ipv6_renderer_snapshot() if renderer is None else renderer
    validate_ipv6_renderer(renderer)
    rules = subprocess.check_output(["sudo", "-n", "nft", "-j", "list", "ruleset"], timeout=10)
    ipv6 = read_ipv6_route_snapshot()
    return dict(netns=os.readlink("/proc/self/ns/net"), routes=digest(Path("/proc/net/route").read_bytes()),
                ipv6_routes=ipv6["raw"], ipv6_route_metadata=ipv6["metadata"], ipv6_renderer=renderer,
                resolver=digest(Path("/etc/resolv.conf").read_bytes()), firewall=firewall_snapshot(rules),
                credentials=credentials(), kvm=kvm_snapshot())


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


def offline_failure_diagnostic(state, source):
    """Hash/marker observations only, after the fixed failed command drained."""
    result = dict(schema=1, source=None, phase="offline-build", stage="android-offline-rebuild",
                  capture_error=None, coverage=None, log_bytes=None, read_bytes=0,
                  windows=[], complete_log_fingerprint=None, observed_markers=[])
    descriptors = []
    codes = {"BUILD_DIAGNOSTIC_SOURCE", "BUILD_DIAGNOSTIC_STATE", "BUILD_DIAGNOSTIC_FILE",
             "BUILD_DIAGNOSTIC_CHANGED", "BUILD_DIAGNOSTIC_SUMMARY", "BUILD_DIAGNOSTIC_NOT_DRAINED"}

    def closed_error(error):
        if isinstance(error, CleanupDeadline): return "BUILD_DIAGNOSTIC_DEADLINE"
        if isinstance(error, OSError): return "BUILD_DIAGNOSTIC_IO"
        value = error.args[0] if error.args else None
        return value if type(value) is str and value in codes else "BUILD_DIAGNOSTIC_FAILED"

    try:
        with ACTIVE.cleanup():
            try:
                require(type(source) is str and re.fullmatch("[0-9a-f]{40}", source), "BUILD_DIAGNOSTIC_SOURCE")
                result["source"] = source
                with ACTIVE.registration():
                    directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
                    descriptors.append(directory)
                owner = os.fstat(directory)
                require(stat.S_ISDIR(owner.st_mode) and stat.S_IMODE(owner.st_mode) == 0o700
                        and owner.st_uid == os.getuid() == os.geteuid() != 0 and owner.st_gid == os.getgid(),
                        "BUILD_DIAGNOSTIC_STATE")

                def owned_file(name, minimum, maximum):
                    # Only the two fixed leaves below reach this helper. A held
                    # directory FD plus no-follow/nonblocking opens prevents a
                    # replaced symlink/FIFO from redirecting or blocking reads.
                    with ACTIVE.registration():
                        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                        descriptors.append(fd)
                    info = os.fstat(fd)
                    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                            and info.st_uid == owner.st_uid and info.st_gid == owner.st_gid
                            and info.st_nlink == 1 and minimum <= info.st_size <= maximum, "BUILD_DIAGNOSTIC_FILE")
                    return fd, info

                def stable(fd, info):
                    after = os.fstat(fd)
                    require(device_identity(after) == device_identity(info) and after.st_size == info.st_size,
                            "BUILD_DIAGNOSTIC_CHANGED")

                summary_fd, summary_info = owned_file("android-offline-rebuild.json", 1, 4096)
                raw = os.read(summary_fd, summary_info.st_size)
                require(len(raw) == summary_info.st_size, "BUILD_DIAGNOSTIC_CHANGED")
                stable(summary_fd, summary_info)
                def summary_object(pairs):
                    require(len(pairs) == len(dict(pairs)), "BUILD_DIAGNOSTIC_SUMMARY")
                    return dict(pairs)
                try:
                    summary = json.loads(raw, object_pairs_hook=summary_object)
                except (ValueError, UnicodeError, RecursionError):
                    raise ValueError("BUILD_DIAGNOSTIC_SUMMARY") from None
                require(type(summary) is dict and set(summary) == {"exit", "peak_observed_rss", "wall_bound_seconds",
                        "owned_process_group_drained", "signal_count", "cleanup_error"}, "BUILD_DIAGNOSTIC_SUMMARY")
                require(summary["owned_process_group_drained"] is True and summary["cleanup_error"] is None and not ACTIVE.owned
                        and type(summary["exit"]) is int and summary["exit"] != 0, "BUILD_DIAGNOSTIC_NOT_DRAINED")
                log_fd, log_info = owned_file("android-offline-rebuild.private.log", 0, 64 * 1024**2)
                size, windows, samples = log_info.st_size, [], []
                for offset, length in [(0, min(size, 32768))] + (
                        [(max(32768, size - 32768), min(size - 32768, 32768))] if size > 32768 else []):
                    os.lseek(log_fd, offset, os.SEEK_SET)
                    sample = os.read(log_fd, length)
                    require(len(sample) == length, "BUILD_DIAGNOSTIC_CHANGED")
                    samples.append(sample); windows.append(dict(offset=offset, **digest(sample)))
                stable(log_fd, log_info)
                complete = size <= 65536
                # Never join disjoint windows for marker matching: that could
                # invent a phrase spanning the unobserved middle of a log.
                searchable = [b"".join(samples)] if complete else samples
                markers = {
                    "GRADLE_FAILURE_HEADER": (b"FAILURE: Build failed with an exception",),
                    "GRADLE_DAEMON_START": (b"Unable to start the daemon process",),
                    "GRADLE_DAEMON_CONNECT": (b"Could not connect to the Gradle daemon",),
                    "LOCAL_ADDRESS_SELECTION": (b"Could not determine a usable wildcard IP", b"Unable to determine a usable wildcard IP"),
                    "LOCAL_SOCKET_BIND": (b"java.net.BindException", b"Cannot assign requested address"),
                    "NETWORK_UNREACHABLE": (b"Network is unreachable",),
                    "OFFLINE_CACHE_MESSAGE": (b"No cached version", b"available for offline mode"),
                    "DEPENDENCY_VERIFICATION_MESSAGE": (b"Dependency verification failed",),
                    "SDK_COMPONENT_MESSAGE": (b"SDK location not found", b"Failed to find Build Tools", b"NDK not configured"),
                    "NATIVE_BUILD_MESSAGE": (b"CMake Error", b"ninja: build stopped"),
                    "JAVA_START_MESSAGE": (b"Could not create the Java Virtual Machine", b"JAVA_HOME is set to an invalid directory"),
                }
                result.update(coverage="COMPLETE" if complete else "PREFIX_TAIL", log_bytes=size,
                    read_bytes=sum(len(sample) for sample in samples), windows=windows,
                    complete_log_fingerprint=digest(searchable[0]) if complete else None,
                    observed_markers=[name for name, phrases in markers.items()
                                      if any(phrase in sample for sample in searchable for phrase in phrases)])
            except BaseException as error:
                result["capture_error"] = closed_error(error)
            finally:
                for fd in reversed(descriptors):
                    try:
                        os.close(fd)
                    except BaseException:
                        if result["capture_error"] is None: result["capture_error"] = "BUILD_DIAGNOSTIC_CLOSE"
    except BaseException as error:
        if result["capture_error"] is None: result["capture_error"] = closed_error(error)
    return result


def offline_build(state, env):
    tools = state / "tools"
    android = state / "prepared/android"
    try:
        run("android-offline-rebuild", gradle(tools, "--offline", "--dependency-verification=strict",
            ":volparossa_dps_jni:clean", ":volparossa_dps_jni:assembleDebug", ":volparossa_dps_jni:assembleDebugAndroidTest"),
            state, env, android, seconds=1200)
    except BaseException:
        try:
            diagnostic = offline_failure_diagnostic(state, env["GITHUB_SHA"])
            with ACTIVE.cleanup():
                require(len((json.dumps(diagnostic, indent=2, sort_keys=True) + "\n").encode()) <= 16384,
                        "BUILD_DIAGNOSTIC_RECEIPT_BOUND")
                save(state / "offline-build-diagnostic.json", diagnostic)
        except BaseException:
            pass  # A diagnostic must never replace the original command failure.
        raise
    apks = list((android / "volparossa_dps_jni/build/outputs/apk/androidTest/debug").glob("*.apk"))
    require(len(apks) == 1, "ONE_TEST_APK")
    save(state / "offline-apk.json", dict(source=env["GITHUB_SHA"], apk=digest(apks[0].read_bytes())))


def emulator_boot_observation(emulator, adb_server, stop, attempt):
    """Preserve the boot predicate's short-circuit order; export no process ID."""
    observation = dict(reason=None, emulator_observed=True, emulator_returncode=None,
        adb_observed=False, adb_returncode=None, deadline_observed=False, deadline_reached=None,
        attempts=min(attempt, 255), attempts_truncated=attempt > 255, elapsed_ms=None, elapsed_truncated=False)
    def status(value):
        return value if type(value) is int and -255 <= value <= 255 else None
    emulator_status = emulator.poll()
    observation["emulator_returncode"] = status(emulator_status)
    if emulator_status is not None:
        observation["reason"] = "EMULATOR_EXITED"
    else:
        adb_status = adb_server.poll()
        observation.update(adb_observed=True, adb_returncode=status(adb_status))
        if adb_status is not None:
            observation["reason"] = "ADB_SERVER_EXITED"
        else:
            now = time.monotonic()
            within_deadline = now < stop
            observation.update(deadline_observed=True, deadline_reached=not within_deadline)
            if not within_deadline: observation["reason"] = "BOOT_DEADLINE"
    if not observation["deadline_observed"]: now = time.monotonic()
    elapsed = max(0, int((now - (stop - 180)) * 1000))
    observation.update(elapsed_ms=min(elapsed, 420000), elapsed_truncated=elapsed > 420000)
    return observation


def emulator_cleanup_observation(before, remaining, emulator, adb_server, stage, error):
    """Observe existing drain bookkeeping without adding waits, polls or signals."""
    result = dict(stage=stage if stage in {"ENTER_CLEANUP", "DRAIN_GROUPS", "REMOVE_AVD", "REMOVE_ANDROID_HOME", "COMPLETE"} else "UNKNOWN",
                  code=None, order_proven=False, failed_group_index=None,
                  remaining_roles=[], observation_error=None)
    try:
        code = failure_code(error)
        result["code"] = code if code in {None, "PROCESS_GROUP_DRAIN", "CLEANUP_DEADLINE", "ValueError",
            "TimeoutExpired", "CalledProcessError", "OSError", "Interrupted"} else "OTHER_CLEANUP_FAILURE"
        if len(before) > 8 or len(remaining) > 8:
            result["observation_error"] = "OWNERSHIP_OBSERVATION_BOUND"
            return result
        ordered, live = [id(child) for child in before], [id(child) for child in remaining]
        completed = 0
        while completed < len(ordered) and ordered[completed] not in live: completed += 1
        proven = (len(ordered) == len(set(ordered)) and len(live) == len(set(live))
                  and live == list(reversed(ordered[completed:])))
        result["order_proven"] = proven
        if proven and stage == "DRAIN_GROUPS" and code == "PROCESS_GROUP_DRAIN" and completed < len(ordered):
            result["failed_group_index"] = completed
        for child in remaining:
            value = child.returncode
            result["remaining_roles"].append(dict(
                role="emulator" if child is emulator else "adb_server" if child is adb_server else "other_registered",
                cached_returncode=value if type(value) is int and -255 <= value <= 255 else None))
    except BaseException:
        result.update(order_proven=False, failed_group_index=None, remaining_roles=[], observation_error="OWNERSHIP_OBSERVATION_FAILED")
    return result


def emulator_failure_diagnostic(state, source):
    """Observe two fixed logs only after their enclosing namespace drained."""
    result = dict(schema=1, source=None, phase="emulator", namespace_exit=None, capture_error=None, logs=[])
    descriptors = []
    codes = {"EMULATOR_DIAGNOSTIC_SOURCE", "EMULATOR_DIAGNOSTIC_STATE", "EMULATOR_DIAGNOSTIC_FILE",
             "EMULATOR_DIAGNOSTIC_CHANGED", "EMULATOR_DIAGNOSTIC_SUMMARY", "EMULATOR_DIAGNOSTIC_NOT_DRAINED"}
    def closed_error(error):
        if isinstance(error, CleanupDeadline): return "EMULATOR_DIAGNOSTIC_DEADLINE"
        if isinstance(error, OSError): return "EMULATOR_DIAGNOSTIC_IO"
        value = error.args[0] if error.args else None
        return value if type(value) is str and value in codes else "EMULATOR_DIAGNOSTIC_FAILED"
    try:
        with ACTIVE.cleanup():
            try:
                require(type(source) is str and re.fullmatch("[0-9a-f]{40}", source), "EMULATOR_DIAGNOSTIC_SOURCE")
                result["source"] = source
                with ACTIVE.registration():
                    directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
                    descriptors.append(directory)
                owner = os.fstat(directory)
                require(stat.S_ISDIR(owner.st_mode) and stat.S_IMODE(owner.st_mode) == 0o700
                        and owner.st_uid == os.getuid() == os.geteuid() != 0 and owner.st_gid == os.getgid(), "EMULATOR_DIAGNOSTIC_STATE")
                def owned_file(name, maximum):
                    with ACTIVE.registration():
                        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                        descriptors.append(fd)
                    info = os.fstat(fd)
                    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                            and info.st_uid == owner.st_uid and info.st_gid == owner.st_gid and info.st_nlink == 1
                            and 0 <= info.st_size <= maximum, "EMULATOR_DIAGNOSTIC_FILE")
                    return fd, info
                def stable(fd, info):
                    after = os.fstat(fd)
                    require(device_identity(after) == device_identity(info) and after.st_size == info.st_size,
                            "EMULATOR_DIAGNOSTIC_CHANGED")
                def summary_object(pairs):
                    require(len(pairs) == len(dict(pairs)), "EMULATOR_DIAGNOSTIC_SUMMARY")
                    return dict(pairs)
                fd, info = owned_file("emulator-namespace.json", 4096)
                raw = os.read(fd, info.st_size)
                require(len(raw) == info.st_size, "EMULATOR_DIAGNOSTIC_CHANGED"); stable(fd, info)
                try: summary = json.loads(raw, object_pairs_hook=summary_object)
                except (ValueError, UnicodeError, RecursionError): raise ValueError("EMULATOR_DIAGNOSTIC_SUMMARY") from None
                require(type(summary) is dict and set(summary) == {"exit", "peak_observed_rss", "wall_bound_seconds",
                    "owned_process_group_drained", "signal_count", "cleanup_error"}, "EMULATOR_DIAGNOSTIC_SUMMARY")
                require(summary["owned_process_group_drained"] is True and summary["cleanup_error"] is None and not ACTIVE.owned
                        and type(summary["exit"]) is int and -255 <= summary["exit"] <= 255 and summary["exit"] != 0,
                        "EMULATOR_DIAGNOSTIC_NOT_DRAINED")
                result["namespace_exit"] = summary["exit"]
                markers = {"ERROR_TOKEN": (b"ERROR",), "FATAL_TOKEN": (b"FATAL",), "KVM_TOKEN": (b"KVM",),
                           "AVD_TOKEN": (b"AVD",), "VULKAN_TOKEN": (b"Vulkan", b"VULKAN"), "QT_PLATFORM_TOKEN": (b"Qt platform",),
                           "ADB_LISTEN_HOST_UNSUPPORTED": (b"listening on specified hostname currently unsupported",),
                           "ADB_SMARTSOCKET_LISTENER_FATAL": (b"could not install *smartsocket* listener:",)}
                for role, name in (("emulator", "emulator.private.log"), ("adb_server", "adb.private.log")):
                    fd, info = owned_file(name, 64 * 1024**2)
                    size, samples, windows = info.st_size, [], []
                    for offset, length in [(0, min(size, 16384))] + (
                            [(max(16384, size - 16384), min(size - 16384, 16384))] if size > 16384 else []):
                        os.lseek(fd, offset, os.SEEK_SET); sample = os.read(fd, length)
                        require(len(sample) == length, "EMULATOR_DIAGNOSTIC_CHANGED")
                        samples.append(sample); windows.append(dict(offset=offset, **digest(sample)))
                    stable(fd, info)
                    complete = size <= 32768
                    searchable = [b"".join(samples)] if complete else samples
                    result["logs"].append(dict(role=role, coverage="COMPLETE" if complete else "PREFIX_TAIL",
                        log_bytes=size, read_bytes=sum(len(sample) for sample in samples), windows=windows,
                        complete_log_fingerprint=digest(searchable[0]) if complete else None,
                        observed_markers=[key for key, phrases in markers.items()
                            if any(phrase in sample for sample in searchable for phrase in phrases)]))
            except BaseException as error:
                result["capture_error"] = closed_error(error)
            finally:
                for fd in reversed(descriptors):
                    try: os.close(fd)
                    except BaseException:
                        if result["capture_error"] is None: result["capture_error"] = "EMULATOR_DIAGNOSTIC_CLOSE"
    except BaseException as error:
        if result["capture_error"] is None: result["capture_error"] = closed_error(error)
    return result


def instrumentation_observation(output):
    """Closed diagnostic of existing output, never test or workload authority."""
    methods = ("actualRustJniGoldensAndClosedErrors", "strictSummaryRejectsUnknownOrContradictoryFields",
        "controllerDropsLateRevokedExpiredBackgroundResultsAndJoins", "controllerRechecksAuthorityBeforeUiPublicationAndExpiryClears",
        "obsoleteGenerationNeverPublishesAfterQueuedCompletion", "publicationWindowExpiryDropsPostedResult",
        "backgroundDuringFreshInflightWorkDropsLateResultAndResumeStaysDisabled", "actualDebugActivityDisplayRevokeBackgroundAndPermissionBoundary")
    rows = {name: dict(status="NOT_OBSERVED", failure_family=None) for name in methods}
    result = dict(schema=1, kind="instrumentation-output-observation", tests=rows, inventory_complete=False,
        all_eight_passed=False, parse_error=None, unexpected_records=False, duplicate_records=False,
        final_code="NOT_OBSERVED", acceptance_tokens=dict(ok_eight=None, failures=None, instrumentation_failed=None),
        counts={status: 8 if status == "NOT_OBSERVED" else 0 for status in
                ("NOT_OBSERVED", "STARTED", "UNKNOWN", "PASSED", "ERROR", "FAILED", "IGNORED", "ASSUMPTION_FAILED")})
    if type(output) is not bytes or len(output) > 256 * 1024:
        result["parse_error"] = "OUTPUT_TYPE_OR_BOUND"; return result
    result["acceptance_tokens"] = dict(ok_eight=b"OK (8 tests)" in output, failures=b"FAILURES" in output,
                                      instrumentation_failed=b"INSTRUMENTATION_FAILED" in output)
    if output.count(b"\n") > 4096:
        result["parse_error"] = "LINE_COUNT_BOUND"; return result
    lines = output.split(b"\n")
    if any(len(line) > 8192 for line in lines):
        result["parse_error"] = "LINE_BYTES_BOUND"; return result
    if output and not output.endswith(b"\n"):
        result["parse_error"] = "TRUNCATED_FINAL_LINE"; return result
    families = {b"java.lang.AssertionError": "ASSERTION", b"junit.framework.AssertionFailedError": "ASSERTION",
        b"java.lang.UnsatisfiedLinkError": "JNI_LINK", b"java.lang.NoClassDefFoundError": "CLASS_LOAD",
        b"java.lang.ClassNotFoundException": "CLASS_LOAD", b"java.lang.ExceptionInInitializerError": "INITIALIZER",
        b"java.lang.SecurityException": "SECURITY", b"java.lang.IllegalArgumentException": "ARGUMENT",
        b"java.lang.IllegalStateException": "STATE", b"java.io.FileNotFoundException": "IO"}
    terminal = {b"0": "PASSED", b"-1": "ERROR", b"-2": "FAILED", b"-3": "IGNORED", b"-4": "ASSUMPTION_FAILED"}
    names = {name.encode(): name for name in methods}
    bundle, started, finished, ordinals, result_seen, valid_result = {}, set(), set(), {}, False, False
    for raw in lines:
        line = raw.removesuffix(b"\r")
        if result["final_code"] != "NOT_OBSERVED" and line.startswith(
                (b"INSTRUMENTATION_STATUS:", b"INSTRUMENTATION_STATUS_CODE:", b"INSTRUMENTATION_RESULT:")):
            result["unexpected_records"] = True
        if result_seen and line.startswith((b"INSTRUMENTATION_STATUS:", b"INSTRUMENTATION_STATUS_CODE:")):
            result["unexpected_records"] = True
        if line.startswith(b"INSTRUMENTATION_STATUS: "):
            key, separator, value = line[24:].partition(b"=")
            if not separator or key not in {b"id", b"class", b"test", b"numtests", b"current", b"stack", b"stream"}:
                result["unexpected_records"] = True
            elif key in bundle:
                result["duplicate_records"] = True
            else: bundle[key] = value
        elif line.startswith(b"INSTRUMENTATION_STATUS_CODE: "):
            code = line[29:]
            name = names.get(bundle.get(b"test"))
            current = bundle.get(b"current", b"")
            valid = (name is not None and bundle.get(b"class") == b"org.volparossa.map.dps.QualityInstrumentedTest"
                and bundle.get(b"id") == b"AndroidJUnitRunner" and bundle.get(b"numtests") == b"8"
                and current in {b"1", b"2", b"3", b"4", b"5", b"6", b"7", b"8"})
            if not valid or code not in {b"1", *terminal}:
                result["unexpected_records"] = True
                if name is not None: rows[name]["status"] = "UNKNOWN"
            elif code == b"1":
                if name in started or current in ordinals:
                    result["duplicate_records"] = True; rows[name]["status"] = "UNKNOWN"
                else:
                    started.add(name); ordinals[current] = name; rows[name]["status"] = "STARTED"
            elif ordinals.get(current) != name:
                result["unexpected_records"] = True; rows[name]["status"] = "UNKNOWN"
            elif name not in started or name in finished or rows[name]["status"] != "STARTED":
                result["duplicate_records"] = True; rows[name]["status"] = "UNKNOWN"
            else:
                finished.add(name); rows[name]["status"] = terminal[code]
                if code in {b"-1", b"-2", b"-4"}:
                    exception = bundle.get(b"stack", b"").partition(b":")[0]
                    rows[name]["failure_family"] = families.get(exception, "UNKNOWN")
                    if (code == b"-2" and name == methods[-1] and bundle.get(b"stack")
                            == b"java.lang.AssertionError: fixture declares no permissions"):
                        rows[name]["failure_family"] = "PERMISSION_BOUNDARY"
                elif b"stack" in bundle: result["unexpected_records"] = True
            bundle = {}
        elif line.startswith(b"INSTRUMENTATION_CODE: "):
            if not valid_result or bundle: result["unexpected_records"] = True
            if result["final_code"] != "NOT_OBSERVED": result["duplicate_records"] = True
            result["final_code"] = "RESULT_OK" if line[22:] == b"-1" else "OTHER"
            if result["final_code"] == "OTHER": result["unexpected_records"] = True
        elif line.startswith(b"INSTRUMENTATION_RESULT: "):
            if result_seen: result["duplicate_records"] = True
            result_seen = True
            if not line.startswith(b"INSTRUMENTATION_RESULT: stream=") or bundle or result["final_code"] != "NOT_OBSERVED":
                result["unexpected_records"] = True
            else: valid_result = True
        elif line.startswith((b"INSTRUMENTATION_", b"INSTRUMENTATION_FAILED")):
            result["unexpected_records"] = True
    if bundle: result["parse_error"] = "UNTERMINATED_STATUS_RECORD"
    clean = not result["parse_error"] and not result["unexpected_records"] and not result["duplicate_records"]
    result["inventory_complete"] = bool(clean and valid_result and len(finished) == 8 and result["final_code"] == "RESULT_OK")
    result["all_eight_passed"] = bool(result["inventory_complete"] and all(row["status"] == "PASSED" for row in rows.values())
        and result["acceptance_tokens"] == dict(ok_eight=True, failures=False, instrumentation_failed=False))
    result["counts"] = {status: sum(row["status"] == status for row in rows.values())
        for status in ("NOT_OBSERVED", "STARTED", "UNKNOWN", "PASSED", "ERROR", "FAILED", "IGNORED", "ASSUMPTION_FAILED")}
    return result


def retain_instrumentation_observation(state, source, output):
    """No new capture; diagnostics cannot replace acceptance or cancellation."""
    ACTIVE.check()
    try:
        result = instrumentation_observation(output)
        require(type(source) is str and re.fullmatch("[0-9a-f]{40}", source), "INSTRUMENTATION_OBSERVATION_SOURCE")
        result["source"] = source
        require(len((json.dumps(result, indent=2, sort_keys=True) + "\n").encode()) <= 16384,
                "INSTRUMENTATION_OBSERVATION_BOUND")
        save(state / "instrumentation-result-diagnostic.json", result)
    except (Interrupted, CleanupDeadline):
        raise
    except Exception:
        pass
    ACTIVE.check()


def emulator_trial(state, env):
    tools, android = state / "tools", state / "prepared/android"
    success, cleanup = False, False
    adb = tools / "sdk/platform-tools/adb"
    emulator = None
    adb_server = None
    boot_observation, nonfatal_poll = None, None
    def command(label, *args, seconds=30):
        return run(label, [adb, "-s", SERIAL, *args], state, env, seconds=seconds)
    try:
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
            adb_server = ACTIVE.spawn([str(adb), "-L", "tcp:localhost:5037", "nodaemon", "server"],
                                          env=env, stdout=log, stderr=log)
        with (state / "emulator.private.log").open("xb") as log:
            emulator = ACTIVE.spawn([str(tools / "sdk/emulator/emulator"), "-avd", "trial", "-port", "5554",
                "-accel", "on", "-no-window", "-no-audio", "-no-snapshot", "-no-boot-anim", "-no-metrics",
                "-gpu", "swiftshader_indirect", "-camera-back", "none", "-camera-front", "none"],
                env=env, stdout=log, stderr=log)
        stop = time.monotonic() + 180
        attempt = 0
        while True:
            boot_observation = emulator_boot_observation(emulator, adb_server, stop, attempt)
            require(boot_observation["reason"] is None, "EMULATOR_BOOT")
            attempt += 1
            try:
                if command("boot-" + str(attempt), "shell", "getprop", "sys.boot_completed", seconds=3).strip() == b"1":
                    break
            except ValueError as error:
                code = failure_code(error)
                nonfatal_poll = dict(attempt=min(attempt, 255), attempts_truncated=attempt > 255,
                    code=code if code in {"STEP_RESOURCE_BOUND", "PROCESS_GROUP_DRAIN"} else "VALUE_ERROR")
            time.sleep(1)
        require(command("guest-api", "shell", "getprop", "ro.build.version.sdk").strip() == b"36", "GUEST_API")
        require(command("guest-abi", "shell", "getprop", "ro.product.cpu.abi").strip() == b"x86_64", "GUEST_ABI")
        command("guest-wifi-off", "shell", "svc", "wifi", "disable")
        command("guest-data-off", "shell", "svc", "data", "disable")
        apks = list((android / "volparossa_dps_jni/build/outputs/apk/androidTest/debug").glob("*.apk"))
        require(len(apks) == 1, "ONE_TEST_APK")
        binding = json.loads((state / "offline-apk.json").read_text())
        require(binding == dict(source=env["GITHUB_SHA"], apk=digest(apks[0].read_bytes())), "OFFLINE_APK_CHANGED")
        save(state / "tested-apk.json", binding["apk"])
        command("install-test", "install", "--no-streaming", apks[0], seconds=60)
        output = command("instrumentation", "shell", "am", "instrument", "-w", "-r", RUNNER, seconds=180)
        retain_instrumentation_observation(state, env["GITHUB_SHA"], output)
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
        cleanup_stage, prior_owned = "ENTER_CLEANUP", []
        try:
            with ACTIVE.cleanup():
                cleanup_stage, prior_owned = "DRAIN_GROUPS", list(reversed(ACTIVE.owned))
                ACTIVE.drain_all()
                for name in ("avd", "android-home"):
                    cleanup_stage = "REMOVE_AVD" if name == "avd" else "REMOVE_ANDROID_HOME"
                    path = state / name
                    if path.exists():
                        shutil.rmtree(path)
                cleanup_stage = "COMPLETE"
        except BaseException as error:
            cleanup_error = error
        try:
            with ACTIVE.cleanup():
                receipt = dict(direct_children_reaped=not ACTIVE.owned,
                    avd_removed=not (state / "avd").exists(), android_home_removed=not (state / "android-home").exists(),
                    namespace_parent_must_confirm_exit=True, trial_returned=success, signal_count=ACTIVE.signal_count,
                    cleanup_error=None if cleanup_error is None else type(cleanup_error).__name__,
                    source=env["GITHUB_SHA"], boot_observation=boot_observation, last_nonfatal_boot_poll=nonfatal_poll,
                    primary_boot_failure=isinstance(original, ValueError) and original.args == ("EMULATOR_BOOT",),
                    cleanup_observation=emulator_cleanup_observation(prior_owned, list(ACTIVE.owned), emulator, adb_server,
                        cleanup_stage, cleanup_error))
                require(len((json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()) <= 16384,
                        "EMULATOR_OBSERVATION_RECEIPT_BOUND")
                save(state / "namespace-cleanup.json", receipt)
        except BaseException as error:
            ACTIVE.cleanup_errors.append(type(error).__name__)
            if original is None and cleanup_error is None:
                raise
        if original is None and cleanup_error is not None:
            raise cleanup_error


def private_document(directory_fd, name, owner, *, receipt_phase=None):
    allowed = name in ("baseline.json", "source.json", "environment.json", "group-reaping-result.json",
        "negative-family.json", "positive-family.json", "exit-sentinel.json", "live-sentinel.json",
        "negative-exited.json", "negative-live.json", "positive-exited.json", "positive-live.json") if receipt_phase is None else (
        receipt_phase in PHASES and name == "phase-" + receipt_phase + ".json")
    require(allowed, "PHASE_INPUT_ALLOWLIST")
    fd, original = None, None
    try:
        with ACTIVE.registration():
            try:
                fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            except OSError as error:
                raise KvmFailure("PHASE_INPUT_OPEN", error.errno) from None
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner.st_uid and info.st_gid == owner.st_gid
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                and 0 < info.st_size <= 16384, "PHASE_INPUT_OWNER_BOUND")
        raw = os.read(fd, 16385)
        require(len(raw) == info.st_size and device_identity(os.fstat(fd)) == device_identity(info), "PHASE_INPUT_CHANGED")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise KvmFailure("PHASE_INPUT_SHAPE") from None
        require(type(value) is dict, "PHASE_INPUT_SHAPE")
        return value
    except BaseException as error:
        original = error
        raise
    finally:
        if fd is not None:
            try:
                with ACTIVE.cleanup(): os.close(fd)
            except BaseException:
                if original is None: raise


def phase_child(state, phase):
    """Fixed root bootstrap: device-derived group only, then permanent UID/GID drop."""
    require(phase in PHASES and os.getuid() == os.geteuid() == 0 and os.getpid() == 1, "PHASE_BOOTSTRAP_ROOT")
    owner = state.lstat()
    require(state.resolve(strict=True) == state and stat.S_ISDIR(owner.st_mode)
            and stat.S_IMODE(owner.st_mode) == 0o700 and owner.st_uid > 0 and owner.st_gid > 0,
            "PHASE_OWNED_STATE")
    require(os.environ.get("SUDO_UID") == str(owner.st_uid) and os.environ.get("SUDO_GID") == str(owner.st_gid),
            "PHASE_SUDO_OWNER")
    result = dict(schema=2, kind="hosted-phase", phase=phase, source=None, success=False,
                  credentials=None, capabilities=None, probe=None, device_unchanged=False,
                  device_configuration_unchanged=False, device_comparison=None, device_checkpoint=None)
    directory_fd, receipt_fd, writable, original = None, None, False, None
    try:
        # Bind a non-symlink owned directory before privileged file access.
        with ACTIVE.registration():
            directory_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        require(device_identity(os.fstat(directory_fd)) == device_identity(owner), "PHASE_STATE_CHANGED")
        # Caller preallocates a private regular output file. It stays caller-owned
        # even for root-bootstrap failures; no chown or permission changes.
        with ACTIVE.registration():
            receipt_fd = os.open("phase-" + phase + ".json", os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        info = os.fstat(receipt_fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner.st_uid and info.st_gid == owner.st_gid
                and info.st_nlink == 1 and info.st_size == 0 and stat.S_IMODE(info.st_mode) == 0o600, "PHASE_RECEIPT_OWNER")
        writable = True
        outer = private_document(directory_fd, "baseline.json", owner)
        require(outer["credentials"]["uid"] == [owner.st_uid] * 3
                and outer["credentials"]["gid"] == [owner.st_gid] * 3, "PHASE_ORIGINAL_AUTHORITY")
        source_document = private_document(directory_fd, "source.json", owner)
        source = source_document.get("map")
        require(isinstance(source, str) and re.fullmatch("[0-9a-f]{40}", source), "PHASE_SOURCE_SHAPE")
        result["source"] = source
        same_network = os.readlink("/proc/self/ns/net") == outer["netns"]
        require(same_network == (phase == "public-build"), "PHASE_NETWORK_NAMESPACE")
        ACTIVE.enable_private_pid1(source_document.get("parent_pidns"))
        if not same_network:
            require(sorted(n for _, n in socket.if_nameindex()) == ["lo"], "PHASE_PRIVATE_LINKS")
            if phase in ("offline-build", "emulator"):
                subprocess.run(["/usr/sbin/ip", "link", "set", "lo", "up"], check=True, timeout=5)
        expected = outer["kvm"]
        require(kvm_snapshot() == expected, "KVM_DEVICE_CHANGED")
        result["credentials"] = drop_credentials(owner.st_uid, owner.st_gid, phase, expected)
        result["capabilities"] = capability_bits()
        env = private_document(directory_fd, "environment.json", owner)
        require(env["GITHUB_SHA"] == source, "PHASE_SOURCE")
        guards(env)
        activate_environment(env)
        if phase in KVM_PHASES:
            result["probe"] = kvm_open_query(expected)
        if phase == "public-build":
            acquire_build(state, env)
        elif phase == "offline-build":
            offline_build(state, env)
        elif phase == "emulator":
            emulator_trial(state, env)
        require(credentials() == result["credentials"] and capability_bits() == result["capabilities"], "PHASE_CREDENTIAL_CHANGED")
        observed = kvm_snapshot()
        comparison = kvm_comparison(expected, observed)
        result.update(device_unchanged=comparison["raw_unchanged"],
            device_configuration_unchanged=comparison["configuration_unchanged"], device_comparison=comparison,
            device_checkpoint=dict(before=expected, after=observed))
        require(comparison["raw_unchanged"] or (phase == "emulator" and comparison["configuration_unchanged"]), "KVM_DEVICE_CHANGED")
        result["success"] = True
    except BaseException as error:
        original = error
        result.update(code=failure_code(error), errno=getattr(error, "errno", None))
        raise
    finally:
        finish_error = None
        if receipt_fd is not None and writable:
            try:
                with ACTIVE.cleanup():
                    raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
                    require(len(raw) <= 16384, "PHASE_RECEIPT_BOUND")
                    require(os.write(receipt_fd, raw) == len(raw), "PHASE_RECEIPT_WRITE")
            except BaseException as error:
                ACTIVE.cleanup_errors.append(type(error).__name__)
                finish_error = error
        # Close both FDs even if writing fails. Neither is transferred to a
        # workload; an original workload error keeps precedence over cleanup.
        for fd in (receipt_fd, directory_fd):
            if fd is None: continue
            try:
                with ACTIVE.cleanup(): os.close(fd)
            except BaseException as error:
                ACTIVE.cleanup_errors.append(type(error).__name__)
                if finish_error is None: finish_error = error
        if original is None and finish_error is not None:
            raise finish_error


def run_phase(state, phase, env, seconds):
    require(phase in PHASES, "PHASE_ALLOWLIST")
    path = state / ("phase-" + phase + ".json")
    # Explicit creation mode, independent of a caller's ambient umask.
    with ACTIVE.registration():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    try:
        run(phase + "-namespace", namespace_command("--inside-" + phase, state, seconds - 2), state, env, seconds=seconds)
    except BaseException:
        if phase == "emulator":
            try:
                with ACTIVE.cleanup():
                    diagnostic = emulator_failure_diagnostic(state, env["GITHUB_SHA"])
                    require(len((json.dumps(diagnostic, indent=2, sort_keys=True) + "\n").encode()) <= 16384,
                            "EMULATOR_DIAGNOSTIC_RECEIPT_BOUND")
                    save(state / "emulator-failure-diagnostic.json", diagnostic)
            except BaseException:
                pass  # The enclosing namespace failure retains precedence.
        raise
    directory, original = None, None
    try:
        with ACTIVE.registration():
            directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        owner = os.fstat(directory)
        receipt = private_document(directory, path.name, owner, receipt_phase=phase)
        outer = private_document(directory, "baseline.json", owner)
    except BaseException as error:
        original = error
        raise
    finally:
        if directory is not None:
            try:
                with ACTIVE.cleanup(): os.close(directory)
            except BaseException:
                if original is None: raise
    selected = [outer["kvm"]["kvm_gid"]] if phase in KVM_PHASES else []
    checkpoint = receipt.get("device_checkpoint")
    require(type(checkpoint) is dict and set(checkpoint) == {"before", "after"}, "PHASE_KVM_CHECKPOINT")
    require(kvm_comparison(outer["kvm"], checkpoint["before"])["raw_unchanged"], "PHASE_KVM_CHECKPOINT_ORIGIN")
    comparison = kvm_comparison(outer["kvm"], checkpoint["after"])
    recorded = receipt.get("device_comparison")
    require(type(recorded) is dict and set(recorded) == set(comparison)
            and type(recorded["raw_unchanged"]) is bool and type(recorded["configuration_unchanged"]) is bool
            and recorded == comparison and receipt.get("device_unchanged") is comparison["raw_unchanged"]
            and receipt.get("device_configuration_unchanged") is comparison["configuration_unchanged"], "PHASE_KVM_CHECKPOINT")
    require(type(receipt["schema"]) is int and receipt["schema"] == 2 and receipt["kind"] == "hosted-phase" and receipt["phase"] == phase
            and receipt["source"] == env["GITHUB_SHA"] and receipt["success"] is True
            and (comparison["raw_unchanged"] or (phase == "emulator" and comparison["configuration_unchanged"]))
            and receipt["credentials"] == dict(uid=outer["credentials"]["uid"], gid=outer["credentials"]["gid"], groups=selected)
            and receipt["capabilities"] == dict(CapEff=0, CapPrm=0, CapAmb=0), "PHASE_RECEIPT_CONTRACT")
    require(receipt["probe"] == (dict(api_version=12, fd_closed=True, vm_created=False) if phase in KVM_PHASES else None),
            "PHASE_KVM_CONTRACT")


def failure_code(error):
    if error is None:
        return None
    value = error.args[0] if error.args else ""
    return value if isinstance(value, str) and re.fullmatch("[A-Z_]+", value) else type(error).__name__


def baseline_diagnostic(before, after, capture_error):
    """Only fixed metadata/hash snapshots; never raw host configuration or errors."""
    components = ("netns", "routes", "ipv6_routes", "ipv6_route_metadata", "ipv6_renderer", "resolver", "firewall", "credentials", "kvm")
    require(capture_error in (None, "BASELINE_NOT_CAPTURED", "BASELINE_BEFORE_UNAVAILABLE", "BASELINE_CAPTURE_FAILED"),
            "BASELINE_CAPTURE_CODE")
    result = dict(schema=3, before=None, after=None, changed_components=None, firewall_comparison=None,
                  ipv6_route_comparison=None,
                  capture_error=capture_error, diagnostic_error=None)

    def integer(value):
        return type(value) is int and 0 <= value < 2**64

    def fingerprint(value):
        return (type(value) is dict and set(value) == {"bytes", "sha256"} and integer(value["bytes"])
                and type(value["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", value["sha256"]) is not None)

    def snapshot(value):
        if type(value) is not dict or set(value) != set(components): return False
        if type(value["netns"]) is not str or re.fullmatch(r"net:\[[0-9]{1,20}\]", value["netns"]) is None: return False
        if not all(fingerprint(value[key]) for key in ("routes", "ipv6_routes", "resolver")): return False
        try:
            ipv6 = dict(raw=value["ipv6_routes"], metadata=value["ipv6_route_metadata"])
            ipv6_route_comparison(ipv6, ipv6)
            validate_ipv6_renderer(value["ipv6_renderer"])
        except ValueError:
            return False
        firewall = value["firewall"]
        if (type(firewall) is not dict or set(firewall) != {"schema", "raw", "config", "counters", "counter_count"}
                or type(firewall["schema"]) is not int or firewall["schema"] != 1
                or not all(fingerprint(firewall[key]) for key in ("raw", "config", "counters"))
                or not integer(firewall["counter_count"]) or firewall["counter_count"] > 65536): return False
        creds = value["credentials"]
        if type(creds) is not dict or set(creds) != {"uid", "gid", "groups"}: return False
        if not all(type(creds[key]) is list and len(creds[key]) == 3
                   and all(integer(item) for item in creds[key]) for key in ("uid", "gid")): return False
        if (type(creds["groups"]) is not list or len(creds["groups"]) > 64
                or not all(integer(item) for item in creds["groups"])
                or creds["groups"] != sorted(set(creds["groups"]))): return False
        kvm = value["kvm"]
        if type(kvm) is not dict or set(kvm) != {"device", "acl", "kvm_gid"}: return False
        device = kvm["device"]
        if (type(device) is not dict or set(device) != {"dev", "ino", "rdev", "mode", "uid", "gid", "nlink", "mtime_ns", "ctime_ns"}
                or not all(type(item) is int and -2**63 <= item < 2**64 for item in device.values())): return False
        if not integer(kvm["kvm_gid"]) or kvm["kvm_gid"] == 0 or kvm["kvm_gid"] != device["gid"]: return False
        acl = kvm["acl"]
        return (type(acl) is dict and ((set(acl) == {"present"} and acl["present"] is False) or (
            set(acl) == {"present", "bytes", "sha256"} and acl["present"] is True
            and fingerprint({key: acl[key] for key in ("bytes", "sha256")}))))

    for name, value in (("before", before), ("after", after)):
        if value is None: continue
        if snapshot(value): result[name] = value
        else: result["diagnostic_error"] = "BASELINE_METADATA_SHAPE"
    if result["before"] is not None and result["after"] is not None:
        result["changed_components"] = [key for key in components if before[key] != after[key]]
        result["firewall_comparison"] = firewall_comparison(before["firewall"], after["firewall"])
        try:
            result["ipv6_route_comparison"] = ipv6_route_comparison(*[
                dict(raw=value["ipv6_routes"], metadata=value["ipv6_route_metadata"]) for value in (before, after)])
        except ValueError:
            result["diagnostic_error"] = "BASELINE_METADATA_SHAPE"
    return result


def finalize(state, evidence, before, complete, original, control_mode=None):
    """Best-effort bounded finalization; original failure always keeps precedence."""
    errors, removed, unchanged, drained = [], False, None, False
    configuration_accepted, kvm_checkpoint = None, None
    configuration_mode = False
    after, capture_error = None, "BASELINE_NOT_CAPTURED"
    result = dict(schema=3, source=os.environ.get("GITHUB_SHA"), success=False,
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
            try:
                if before is None:
                    unchanged, configuration_accepted, capture_error = False, False, "BASELINE_BEFORE_UNAVAILABLE"
                else:
                    after = baseline()
                    unchanged, capture_error = baseline_unchanged(before, after), None
                    configuration_mode = (complete is True and original is None and control_mode is None
                                          and ACTIVE.first_signal is None and drained and not ACTIVE.cleanup_errors)
                    configuration_accepted = baseline_unchanged(before, after, kvm_configuration=True) if configuration_mode else unchanged
                    if type(before) is dict and type(after) is dict and "kvm" in before and "kvm" in after:
                        kvm_checkpoint = kvm_comparison(before["kvm"], after["kvm"])
            except BaseException as error:
                errors.append(failure_code(error)); unchanged, configuration_accepted = False, False
                capture_error = "BASELINE_CAPTURE_FAILED"
            if not configuration_accepted:
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
        try:
            result["baseline_comparison"] = baseline_diagnostic(before, after, capture_error)
            # A deferred cleanup signal revokes the configuration-only criterion too.
            if ACTIVE.first_signal is not None or not drained or not removed or errors or ACTIVE.cleanup_errors:
                configuration_mode, configuration_accepted = False, unchanged
                if unchanged is False and "HOST_BASELINE_UNCONFIRMED" not in errors:
                    errors.append("HOST_BASELINE_UNCONFIRMED")
            result.update(host_routes_dns_firewall_unchanged=unchanged,
                host_configuration_checkpoint_accepted=configuration_accepted,
                baseline_acceptance_mode="COMPLETED_EMULATOR_CONFIGURATION" if configuration_mode else "STRICT_KVM_METADATA",
                kvm_comparison=kvm_checkpoint, registered_groups_drained=drained,
                owned_state_removed=removed, completed_workload=complete, first_signal=ACTIVE.first_signal,
                signal_count=ACTIVE.signal_count, cleanup_errors=ACTIVE.cleanup_errors + errors,
                success=complete is True and original is None and configuration_accepted is True and drained and removed and not errors
                        and not ACTIVE.cleanup_errors and ACTIVE.first_signal is None)
            # Keep the existing receipt bounded even at the diagnostic limit.
            # Omitting diagnostic snapshots never changes the equality verdict.
            if len((json.dumps(result, indent=2, sort_keys=True) + "\n").encode()) > 16384:
                result["baseline_comparison"].update(before=None, after=None, diagnostic_error="BASELINE_RECEIPT_BOUND")
            require(len((json.dumps(result, indent=2, sort_keys=True) + "\n").encode()) <= 16384, "BASELINE_RECEIPT_BOUND")
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
    require(mode in ("--inside-cancellation", "--inside-reaping-control", *("--inside-" + phase for phase in PHASES)), "NAMESPACE_MODE")
    network = [] if mode == "--inside-public-build" else ["--net"]
    return ["sudo", "-n", "timeout", "--signal=TERM", "--kill-after=1s", str(seconds) + "s",
            "unshare", *network, "--pid", "--fork", "--mount-proc", "--kill-child=KILL",
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


REAPING_FIXTURE = r'''
import json, os, signal, sys, time
from pathlib import Path
case, role = Path(sys.argv[1]), sys.argv[2]
def publish(name, value):
    raw = json.dumps(value).encode()
    fd = os.open(case / (name + ".part"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if os.write(fd, raw) != len(raw): raise RuntimeError("FIXTURE_WRITE")
    finally: os.close(fd)
    os.rename(case / (name + ".part"), case / name)
if role in ("negative", "positive"):
    descendants = {}
    for kind in ("exited", "live"):
        pid = os.fork()
        if pid == 0:
            if kind == "live": signal.signal(signal.SIGTERM, signal.SIG_IGN)
            stop = time.monotonic() + 2
            while os.getppid() != 1:
                if time.monotonic() >= stop: os._exit(90)
                time.sleep(0.005)
            publish(role + "-" + kind + ".json", dict(pid=os.getpid(), pgid=os.getpgrp(), adopted=True))
            if kind == "exited": os._exit(42)
            time.sleep(30); os._exit(91)
        descendants[kind] = pid
    publish(role + "-family.json", dict(leader=os.getpid(), pgid=os.getpgrp(), **descendants))
    os._exit(31)
elif role in ("exit-sentinel", "live-sentinel"):
    publish(role + ".json", dict(pid=os.getpid(), pgid=os.getpgrp()))
    if role == "exit-sentinel": os._exit(23)
    time.sleep(30); os._exit(92)
else:
    os._exit(93)
'''


def inside_reaping_control(state):
    """Hosted-only kernel control, with private PID values never exported."""
    require(os.getuid() == os.geteuid() == 0 and os.getpid() == 1, "REAPING_BOOTSTRAP_ROOT")
    owner = state.lstat()
    require(state.resolve(strict=True) == state and stat.S_ISDIR(owner.st_mode)
            and stat.S_IMODE(owner.st_mode) == 0o700 and owner.st_uid > 0 and owner.st_gid > 0
            and os.environ.get("SUDO_UID") == str(owner.st_uid) and os.environ.get("SUDO_GID") == str(owner.st_gid), "REAPING_STATE_OWNER")
    directory, case_fd, case_created, verified, unprivileged = None, None, False, False, False
    source, parent_pidns, cleanup_error = None, None, None
    case = state / "group-reaping-private"
    result = dict(schema=1, source=None, success=False, private_pid1_verified=False, negative_failed_closed=False,
        negative_group_gone=False, positive_group_gone=False, adopted_exited_verified=False, adopted_live_verified=False,
        leader_status_preserved=False, sentinel_exit_status_preserved=False, sentinel_live_preserved=False,
        registered_groups_drained=False, private_state_removed=False, primary_failure=None, cleanup_error=None)
    try:
        with ACTIVE.registration():
            directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        require(device_identity(os.fstat(directory)) == device_identity(owner), "REAPING_STATE_CHANGED")
        binding = private_document(directory, "source.json", owner)
        source, parent_pidns = binding.get("map"), binding.get("parent_pidns")
        require(type(source) is str and re.fullmatch("[0-9a-f]{40}", source), "REAPING_SOURCE")
        result["source"] = source
        private_pid1_identity(parent_pidns); verified = True; result["private_pid1_verified"] = True
        require(not ACTIVE.private_pid1, "REAPING_NEGATIVE_DEFAULT")
        outer = private_document(directory, "baseline.json", owner)
        require(outer["credentials"]["uid"] == [owner.st_uid] * 3 and outer["credentials"]["gid"] == [owner.st_gid] * 3
                and os.readlink("/proc/self/ns/net") != outer["netns"]
                and sorted(n for _, n in socket.if_nameindex()) == ["lo"], "REAPING_PRIVATE_NAMESPACE")
        env = private_document(directory, "environment.json", owner)
        require(env["GITHUB_SHA"] == source, "REAPING_SOURCE")
        drop_credentials(owner.st_uid, owner.st_gid, "offline-build", outer["kvm"]); unprivileged = True
        activate_environment(env); guards(env)
        case.mkdir(mode=0o700); case_created = True
        with ACTIVE.registration():
            case_fd = os.open(case, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        case_owner = os.fstat(case_fd)
        require(stat.S_ISDIR(case_owner.st_mode) and stat.S_IMODE(case_owner.st_mode) == 0o700
                and case_owner.st_uid == owner.st_uid and case_owner.st_gid == owner.st_gid, "REAPING_CASE_OWNER")
        def document(name):
            stop = time.monotonic() + 2
            while not (case / name).exists():
                ACTIVE.check(); require(time.monotonic() < stop, "REAPING_HANDSHAKE_BOUND"); time.sleep(0.005)
            return private_document(case_fd, name, case_owner)
        def fixture(role):
            return ACTIVE.spawn(["/usr/bin/python3", "-B", "-c", REAPING_FIXTURE, case, role], env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def pid(value):
            require(type(value) is int and 1 < value <= 2**31 - 1, "REAPING_FIXTURE_PID"); return value
        def observation(child_pid):
            return os.waitid(os.P_PID, pid(child_pid), os.WEXITED | os.WNOHANG | os.WNOWAIT)
        def exited(child_pid, status):
            stop = time.monotonic() + 2
            while True:
                value = observation(child_pid)
                if value is not None:
                    require(value.si_pid == child_pid and value.si_code == os.CLD_EXITED and value.si_status == status,
                            "REAPING_EXIT_STATUS"); return
                ACTIVE.check(); require(time.monotonic() < stop, "REAPING_HANDSHAKE_BOUND"); time.sleep(0.005)
        def gone(group):
            try: os.killpg(group, 0)
            except ProcessLookupError: return True
            return False
        def family(role):
            child = fixture(role)
            details = document(role + "-family.json")
            require(set(details) == {"leader", "pgid", "exited", "live"}
                    and details["leader"] == details["pgid"] == child.pid
                    and len({pid(details[key]) for key in ("leader", "exited", "live")}) == 3, "REAPING_FAMILY_BINDING")
            require(child.wait(timeout=2) == 31, "REAPING_LEADER_STATUS")
            for kind in ("exited", "live"):
                child_pid = pid(details[kind]); ready = document(role + "-" + kind + ".json")
                require(ready == dict(pid=child_pid, pgid=child.pid, adopted=True), "REAPING_ADOPTION_BINDING")
            exited(details["exited"], 42)
            require(observation(details["live"]) is None, "REAPING_LIVE_REQUIRED")
            return child
        sentinel_exit, sentinel_live = fixture("exit-sentinel"), fixture("live-sentinel")
        for role, child in (("exit-sentinel", sentinel_exit), ("live-sentinel", sentinel_live)):
            require(document(role + ".json") == dict(pid=child.pid, pgid=child.pid), "REAPING_SENTINEL_BINDING")
        exited(sentinel_exit.pid, 23)
        negative = family("negative")
        result.update(adopted_exited_verified=True, adopted_live_verified=True)
        with ACTIVE.cleanup():
            try: ACTIVE.drain(negative)
            except ValueError as error:
                require(error.args == ("PROCESS_GROUP_DRAIN",) and negative in ACTIVE.owned and not gone(negative.pid),
                        "REAPING_NEGATIVE_FAILURE")
                result["negative_failed_closed"] = True
            require(result["negative_failed_closed"], "REAPING_NEGATIVE_REQUIRED")
            ACTIVE.enable_private_pid1(parent_pidns)
            ACTIVE.drain(negative)
        result["negative_group_gone"] = gone(negative.pid)
        positive = family("positive")
        with ACTIVE.cleanup(): ACTIVE.drain(positive)
        result.update(positive_group_gone=gone(positive.pid), leader_status_preserved=negative.returncode == positive.returncode == 31,
                      sentinel_live_preserved=sentinel_live.poll() is None)
        # WNOWAIT above must not have consumed this other group's actual status.
        result["sentinel_exit_status_preserved"] = sentinel_exit.returncode is None and sentinel_exit.wait(timeout=2) == 23
        require(all(result[key] is True for key in ("negative_group_gone", "positive_group_gone", "leader_status_preserved",
                "sentinel_live_preserved", "sentinel_exit_status_preserved")), "REAPING_CONTROL_FACTS")
        result["success"] = True
    finally:
        original = sys.exc_info()[1]
        try:
            with ACTIVE.cleanup():
                # Also reaps adopted descendants after setup/negative failures.
                if verified and not ACTIVE.private_pid1: ACTIVE.enable_private_pid1(parent_pidns)
                ACTIVE.drain_all(); result["registered_groups_drained"] = not ACTIVE.owned
                if case_fd is not None:
                    os.close(case_fd); case_fd = None
                if case_created: shutil.rmtree(case)
                result["private_state_removed"] = not case.exists()
        except BaseException as error:
            cleanup_error = error
        for fd in (case_fd, directory):
            if fd is not None:
                try:
                    with ACTIVE.cleanup(): os.close(fd)
                except BaseException as error:
                    if cleanup_error is None: cleanup_error = error
        result.update(primary_failure=failure_code(original), cleanup_error=failure_code(cleanup_error),
            success=result["success"] and original is None and cleanup_error is None
                    and result["registered_groups_drained"] and result["private_state_removed"] and ACTIVE.first_signal is None)
        if unprivileged:
            try:
                with ACTIVE.cleanup():
                    require(len((json.dumps(result, indent=2, sort_keys=True) + "\n").encode()) <= 16384, "REAPING_RECEIPT_BOUND")
                    save(state / "group-reaping-result.json", result)
            except BaseException:
                if original is None and cleanup_error is None: raise
        if original is None and cleanup_error is not None: raise cleanup_error


def group_reaping_control(state, env, before):
    guards(env)
    run("group-reaping-namespace", namespace_command("--inside-reaping-control", state, 15), state, env, seconds=17)
    with ACTIVE.registration():
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    original = None
    try:
        result = private_document(directory, "group-reaping-result.json", os.fstat(directory))
    except BaseException as error:
        original = error
        raise
    finally:
        try:
            with ACTIVE.cleanup(): os.close(directory)
        except BaseException:
            if original is None: raise
    facts = ("success", "private_pid1_verified", "negative_failed_closed", "negative_group_gone", "positive_group_gone",
        "adopted_exited_verified", "adopted_live_verified", "leader_status_preserved", "sentinel_exit_status_preserved",
        "sentinel_live_preserved", "registered_groups_drained", "private_state_removed")
    require(set(result) == {"schema", "source", "primary_failure", "cleanup_error", *facts}
            and type(result["schema"]) is int and result["schema"] == 1 and result["source"] == env["GITHUB_SHA"]
            and result["primary_failure"] is None and result["cleanup_error"] is None
            and all(result[key] is True for key in facts) and not (state / "group-reaping-private").exists(), "REAPING_CONTROL_RESULT")
    after = baseline()
    unchanged = baseline_unchanged(before, after)
    receipt = dict(schema=1, source=env["GITHUB_SHA"], success=unchanged, actual_hosted_processes=True,
        namespace_result=result, baseline_comparison=baseline_diagnostic(before, after, None), android_executed=False)
    require(len((json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()) <= 16384, "REAPING_RECEIPT_BOUND")
    save(state / "group-reaping-control.json", receipt)
    require(unchanged, "REAPING_CONTROL_BASELINE")


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == "--inside-cancellation":
        inside_cancellation(Path(sys.argv[2])); return
    if len(sys.argv) == 3 and sys.argv[1] == "--inside-reaping-control":
        inside_reaping_control(Path(sys.argv[2])); return
    if len(sys.argv) == 3 and sys.argv[1] in tuple("--inside-" + phase for phase in PHASES):
        phase_child(Path(sys.argv[2]), sys.argv[1].removeprefix("--inside-")); return
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
        save(state / "source.json", dict(map=os.environ["GITHUB_SHA"], pins=pins(),
            workflow=WORKFLOW, run=os.environ["GITHUB_RUN_ID"], attempt=os.environ["GITHUB_RUN_ATTEMPT"],
            parent_pidns=os.readlink("/proc/self/ns/pid")))
        admitted = kvm_admission(state)
        renderer = ipv6_renderer_admission(state)
        before = baseline(renderer=renderer); require(before["kvm"] == admitted, "KVM_DEVICE_CHANGED")
        save(state / "baseline.json", before)
        require(ipv6_route_comparison(dict(raw=before["ipv6_routes"], metadata=before["ipv6_route_metadata"]),
                dict(raw=before["ipv6_routes"], metadata=before["ipv6_route_metadata"]))["metadata_valid"],
                "IPV6_ROUTE_LAYOUT_UNSUPPORTED")
        env = environment(state, state / "tools")
        for directory in ("home", "android-home", "gradle-home", "rustup", "cargo"):
            (state / directory).mkdir(mode=0o700)
        save(state / "environment.json", env)
        run_phase(state, "kvm-probe", env, 10)  # Before any SDK acquisition/build.
        cancellation_control(state, env)
        group_reaping_control(state, env, before)
        run_phase(state, "public-build", env, 2700)
        env = json.loads((state / "environment.json").read_text())
        run_phase(state, "offline-build", env, 1220)
        run_phase(state, "emulator", env, 420)
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
