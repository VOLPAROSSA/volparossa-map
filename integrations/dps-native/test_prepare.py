# SPDX-License-Identifier: GPL-3.0-only
"""Inert Python/Git/archive tests only; none executes Rust/JNI/Android or a controller."""
import copy
import errno
from types import SimpleNamespace
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import prepare as P
import tool_inputs as T
import hosted as H


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def repo(self, name, files):
        path = self.root / name
        path.mkdir()
        for filename, raw in files.items():
            target = path / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        subprocess.run(["git", "init", "-q", path], check=True)
        P.git(path, "add", ".")
        P.git(path, "-c", "user.name=Inert test", "-c", "user.email=inert@example.invalid", "commit", "-qm", "synthetic source fixture")
        return path, dict(revision=P.git(path, "rev-parse", "HEAD").decode().strip(),
                          files={n: P.digest(raw) for n, raw in files.items()})

    def fixtures(self):
        # Small non-native text fixtures test preparation mechanics. These are
        # never fed to JNI and never represented as the real core goldens.
        files = {"crates/volparossa-dps-ffi/include/volparossa_dps.h": b"inert header\n"}
        rows = []
        for name in ("relative_consistent.pb", "relative_conflict.pb", "stale.pb", "empty.pb"):
            raw = ("inert:" + name).encode()
            files["crates/volparossa-dps-ffi/tests/fixtures/" + name] = raw
            rows.append(dict(file=name, **P.digest(raw)))
        files["crates/volparossa-dps-ffi/tests/fixtures/manifest.json"] = json.dumps(dict(
            synthetic_only=True, actual_android_acceptance=False, vectors=rows)).encode()
        core, core_pin = self.repo("core", files)
        upstream, upstream_pin = self.repo("upstream", {"android/settings.gradle": b"include ':app'\n",
            "NOTICE": b"unchanged Apache notice\n", "DATA_LICENSE.txt": b"distinct data terms\n",
            "android/build.gradle": b"inert Gradle input, never executed\n"})
        return core, upstream, dict(core=core_pin, organicmaps=upstream_pin)

    def test_preparation_is_additive_preserves_inputs_notices_and_goldens(self):
        core, upstream, pins = self.fixtures()
        output = self.root / "prepared"
        with patch.object(P, "pins", return_value=pins):
            result = P.prepare(upstream, core, output)
        self.assertFalse(result["native_executed"])
        self.assertFalse(result["android_acceptance"])
        self.assertEqual((output / "android/settings.gradle").read_bytes(), b"include ':app'\n" + P.SETTINGS_ADD)
        for name in ("NOTICE", "DATA_LICENSE.txt", "android/build.gradle"):
            self.assertEqual((output / name).read_bytes(), (upstream / name).read_bytes())
        self.assertEqual(P.git(output, "rev-parse", "HEAD"), P.git(upstream, "rev-parse", "HEAD"))
        self.assertEqual(P.git(core, "status", "--porcelain"), b"")
        self.assertEqual(P.git(upstream, "status", "--porcelain"), b"")
        for row in json.loads((core / "crates/volparossa-dps-ffi/tests/fixtures/manifest.json").read_text())["vectors"]:
            asset = output / "android/volparossa_dps_jni/src/androidTest/assets" / row["file"]
            self.assertEqual(P.digest(asset.read_bytes()), {k: row[k] for k in ("bytes", "sha256")})

    def test_wrong_revision_dirty_source_modified_pin_existing_output_reject_before_write(self):
        core, upstream, pins = self.fixtures()
        output = self.root / "prepared"
        wrong = copy.deepcopy(pins); wrong["core"]["revision"] = "0" * 40
        with patch.object(P, "pins", return_value=wrong), self.assertRaisesRegex(ValueError, "SOURCE_REVISION"):
            P.prepare(upstream, core, output)
        self.assertFalse(output.exists())
        (core / "private-unpublished").write_text("preserve")
        with patch.object(P, "pins", return_value=pins), self.assertRaisesRegex(ValueError, "SOURCE_DIRTY"):
            P.prepare(upstream, core, output)
        self.assertEqual((core / "private-unpublished").read_text(), "preserve")
        (core / "private-unpublished").unlink()
        wrong = copy.deepcopy(pins); wrong["core"]["files"]["crates/volparossa-dps-ffi/include/volparossa_dps.h"]["sha256"] = "0" * 64
        with patch.object(P, "pins", return_value=wrong), self.assertRaisesRegex(ValueError, "SOURCE_BYTES"):
            P.prepare(upstream, core, output)
        output.mkdir(); (output / "unique").write_text("preserve")
        with patch.object(P, "pins", return_value=pins), self.assertRaisesRegex(ValueError, "NEW_DISPOSABLE_OUTPUT"):
            P.prepare(upstream, core, output)
        self.assertEqual((output / "unique").read_text(), "preserve")

    def test_source_symlinks_and_shallow_history_rejected(self):
        core, upstream, pins = self.fixtures()
        alias = self.root / "alias"; alias.symlink_to(core, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "SOURCE_PATH"):
            P.verify(alias, pins["core"])
        shallow = self.root / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth=1", "file://" + str(core), shallow], check=True)
        with self.assertRaisesRegex(ValueError, "SOURCE_HISTORY"):
            P.verify(shallow, pins["core"])

    def test_archive_path_escape_and_link_parent_reject(self):
        for name in ("../outside", "/absolute", "a/../b", "a\\b", "a//b", "a/./b"):
            with self.assertRaises(ValueError):
                T.safe(name)
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("root/../escape", b"unsafe")
        with self.assertRaises(ValueError):
            T.extract(archive, self.root / "out", "root")
        self.assertFalse((self.root / "out").exists())
        archive.unlink()
        with zipfile.ZipFile(archive, "w") as z:
            info = zipfile.ZipInfo("root/link"); info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, b"nested")
            z.writestr("root/link/file", b"must not traverse")
        with self.assertRaisesRegex(ValueError, "ARCHIVE_LINK_PARENT"):
            T.extract(archive, self.root / "out", "root")
        self.assertFalse((self.root / "out").exists())

    def test_archive_extracts_internal_links_and_notices_without_executing(self):
        archive = self.root / "good.zip"
        with zipfile.ZipFile(archive, "w") as z:
            info = zipfile.ZipInfo("root/bin/tool"); info.external_attr = (stat.S_IFREG | 0o755) << 16
            z.writestr(info, b"inert text - never executed")
            z.writestr("root/NOTICE", b"all notices retained")
            info = zipfile.ZipInfo("root/tool"); info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, b"bin/tool")
        output = self.root / "tools"
        T.extract(archive, output, "root")
        self.assertEqual((output / "tool").read_bytes(), b"inert text - never executed")
        self.assertEqual((output / "NOTICE").read_bytes(), b"all notices retained")

    def test_download_rejects_wrong_digest_size_and_unapproved_redirect_without_network(self):
        import io
        pin = dict(url="https://dl.google.com/android/repository/inert.zip", bytes=4,
                   algorithm="sha256", digest="0" * 64, checksum_source="inert")
        opener = unittest.mock.Mock()
        opener.open.side_effect = lambda *a, **k: io.BytesIO(b"test")
        with patch.object(T.urllib.request, "build_opener", return_value=opener), self.assertRaisesRegex(ValueError, "ARCHIVE_DIGEST"):
            T.fetch(pin, self.root / "download")
        redirect = T.Redirects(pin["url"])
        with self.assertRaisesRegex(ValueError, "ARCHIVE_REDIRECT"):
            redirect.redirect_request(None, None, 302, "", {}, "https://attacker.invalid/binary")

    def test_native_hosted_guard_rejects_local_and_nonreviewed_context(self):
        with self.assertRaises(ValueError):
            H.guards({})
        # Guard rejection happens before tool acquisition or native execution.
        with patch.object(H, "acquire_build") as build, patch.object(H, "baseline") as baseline:
            with patch.object(H.sys, "argv", ["hosted.py", "--reviewed-hosted-fixture"]), patch.dict(H.os.environ, {}, clear=True):
                with self.assertRaises(ValueError):
                    H.main()
            build.assert_not_called(); baseline.assert_not_called()


class LifetimeShimTests(unittest.TestCase):
    """Local orchestration shims only: no child, signal, namespace or privilege execution."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.life = H.Lifetime()
        self.enterContext(patch.object(H, "ACTIVE", self.life))
        self.timer = self.enterContext(patch.object(H.signal, "setitimer"))
        self.enterContext(patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)))
        self.enterContext(patch.object(H.signal, "signal"))
        self.enterContext(patch.object(H.signal, "getsignal", return_value=H.signal.SIG_DFL))

    def test_spawn_defers_cancellation_until_owner_registered_without_blocking_child_signals(self):
        child = unittest.mock.Mock(pid=4321)
        def spawn(*args, **kwargs):
            self.assertTrue(self.life.spawning)
            self.assertTrue(kwargs["start_new_session"])
            self.assertNotIn("preexec_fn", kwargs)
            self.life.interrupted(H.signal.SIGTERM, None)
            self.assertEqual(self.life.owned, [])
            return child
        with patch.object(H.subprocess, "Popen", side_effect=spawn), patch.object(H.signal, "pthread_sigmask") as mask:
            with self.assertRaises(H.Interrupted):
                self.life.spawn(["inert-never-executed"])
            mask.assert_not_called()
        self.assertEqual(self.life.owned, [child])
        self.assertFalse(self.life.spawning)

    def test_repeated_int_term_signals_never_extend_cleanup_deadline(self):
        now = [10.0]
        with patch.object(H.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(H.Interrupted):
                self.life.interrupted(H.signal.SIGINT, None)
            self.assertEqual(self.life.stop, 19.0)
            with self.life.cleanup():
                now[0] = 17.5
                self.life.interrupted(H.signal.SIGTERM, None)
                self.life.interrupted(H.signal.SIGINT, None)
                self.assertEqual(self.life.signal_count, 3)
                self.assertEqual(self.life.stop, 19.0)
                self.assertLessEqual(self.timer.call_args.args[1], 1.5)
            now[0] = 20
            with self.assertRaises(H.CleanupDeadline):
                with self.life.cleanup():
                    self.fail("expired cleanup budget cannot restart")

    def test_context_cleanup_failure_preserves_original_failure_and_restores_handlers(self):
        with patch.object(self.life, "drain_all", side_effect=H.CleanupDeadline("CLEANUP_DEADLINE")):
            with self.assertRaisesRegex(ValueError, "ORIGINAL_FAILURE"):
                with self.life:
                    raise ValueError("ORIGINAL_FAILURE")
        self.assertEqual(H.signal.signal.call_count, 6)
        self.assertEqual(self.timer.call_args.args[1], 0)

    def test_incomplete_cleanup_preserves_state_and_reports_original_failure(self):
        state = self.root / "owned"; state.mkdir(); (state / "private").write_bytes(b"inert")
        evidence = self.root / "evidence"
        original = ValueError("ORIGINAL_FAILURE")
        with patch.object(self.life, "drain_all", side_effect=H.CleanupDeadline("CLEANUP_DEADLINE")), patch.object(H, "baseline") as baseline:
            result = H.finalize(state, evidence, {}, False, original)
            baseline.assert_not_called()
        self.assertEqual((state / "private").read_bytes(), b"inert")
        self.assertFalse(result["success"])
        self.assertFalse(result["registered_groups_drained"])
        self.assertFalse(result["owned_state_removed"])
        self.assertEqual(result["primary_failure"], "ORIGINAL_FAILURE")
        self.assertIn("CleanupDeadline", result["cleanup_errors"])
        self.assertEqual(json.loads((evidence / "result.json").read_text()), result)

    def test_cleanup_signal_is_recorded_state_removed_and_original_failure_kept(self):
        state = self.root / "owned"; state.mkdir(); (state / "private").write_bytes(b"inert")
        def drain():
            self.life.interrupted(H.signal.SIGTERM, None)
            self.life.interrupted(H.signal.SIGINT, None)
        with patch.object(self.life, "drain_all", side_effect=drain), patch.object(H, "baseline", return_value={"inert": True}):
            result = H.finalize(state, self.root / "evidence", {"inert": True}, False, ValueError("ORIGINAL_FAILURE"))
        self.assertFalse(state.exists())
        self.assertTrue(result["owned_state_removed"])
        self.assertEqual(result["primary_failure"], "ORIGINAL_FAILURE")
        self.assertEqual(result["signal_count"], 2)
        self.assertFalse(result["success"])

    def test_hosted_cancellation_control_rejects_before_any_child_or_namespace(self):
        with patch.object(H, "guards", side_effect=ValueError("HOSTED_ONLY")), patch.object(self.life, "spawn") as spawn:
            with self.assertRaisesRegex(ValueError, "HOSTED_ONLY"):
                H.cancellation_control(self.root, {})
            spawn.assert_not_called()


def inert_firewall():
    return dict(nftables=[dict(metainfo=dict(version="1.0.9", release_name="inert", json_schema_version=1)),
        dict(table=dict(family="inet", name="fixture", handle=1)),
        dict(chain=dict(family="inet", table="fixture", name="input", handle=2, type="filter", hook="input", prio=0, policy="drop")),
        dict(counter=dict(family="inet", table="fixture", name="named", packets=3, bytes=100)),
        dict(rule=dict(family="inet", table="fixture", chain="input", handle=3, expr=[
            dict(match=dict(op="==", left=dict(payload=dict(protocol="tcp", field="dport")), right=443)),
            dict(counter=dict(packets=4, bytes=200)), dict(accept=None)])),
        dict(rule=dict(family="inet", table="fixture", chain="input", handle=4, expr=[dict(counter="named"), dict(drop=None)])),
        dict(quota=dict(family="inet", table="fixture", name="budget", bytes=4096, used=100, inv=False)),
        dict(set=dict(family="inet", table="fixture", name="members", type="ipv4_addr", timeout=100,
            elem=[dict(elem=dict(val="192.0.2.1", timeout=100, expires=90))]))])


class FirewallSnapshotTests(unittest.TestCase):
    """Synthetic JSON mutation controls only; no nft, sudo or host reads."""
    def snapshot(self, value):
        return H.firewall_snapshot(json.dumps(value, separators=(",", ":")).encode())

    def test_only_actual_anonymous_and_named_statistics_are_excluded(self):
        before = inert_firewall(); after = copy.deepcopy(before)
        after["nftables"][3]["counter"].update(packets=2**64 - 1, bytes=2**64 - 1)
        after["nftables"][4]["rule"]["expr"][1]["counter"].update(packets=0, bytes=0)
        # Representation may change alongside statistics; configuration must not.
        left = self.snapshot(before)
        right = H.firewall_snapshot(json.dumps(after, indent=2, sort_keys=True).encode())
        self.assertEqual(left["counter_count"], 2); self.assertEqual(left["config"], right["config"])
        self.assertNotEqual(left["counters"], right["counters"]); self.assertNotEqual(left["raw"], right["raw"])
        self.assertEqual(H.firewall_comparison(left, right), dict(classification="COUNTERS_ONLY",
            raw_changed=True, config_changed=False, counters_changed=True))
        self.assertNotIn("192.0.2.1", json.dumps(left))

    def test_named_handle_is_optional_and_every_identity_field_is_preserved(self):
        before = inert_firewall(); original = self.snapshot(before)
        for key, value in (("handle", 9), ("family", "ip"), ("table", "changed"), ("name", "changed")):
            with self.subTest(key=key):
                changed = copy.deepcopy(before); changed["nftables"][3]["counter"][key] = value
                self.assertEqual(H.firewall_comparison(original, self.snapshot(changed))["classification"], "CONFIG_CHANGED")

    def test_policy_mutations_including_other_stateful_fields_never_compare_equal(self):
        before = inert_firewall(); original = self.snapshot(before)
        mutations = [
            lambda x: x[2]["chain"].update(policy="accept"),
            lambda x: x[4]["rule"]["expr"][0]["match"].update(right=80),
            lambda x: x[4]["rule"]["expr"][2].update(accept="changed"),
            lambda x: x[4]["rule"]["expr"].reverse(),
            lambda x: x.reverse(),
            lambda x: x[6]["quota"].update(bytes=8192),
            lambda x: x[6]["quota"].update(used=101),
            lambda x: x[7]["set"].update(timeout=101),
            lambda x: x[7]["set"]["elem"][0]["elem"].update(expires=89),
            lambda x: x[7]["set"]["elem"].append("192.0.2.2"),
            lambda x: x[4]["rule"].update(future_policy_field=dict(bytes=7, packets=8)),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                changed = copy.deepcopy(before)
                # Keep metainfo first while changing the order of real objects.
                if index == 4: changed["nftables"][1:] = list(reversed(changed["nftables"][1:]))
                else: mutate(changed["nftables"])
                result = H.firewall_comparison(original, self.snapshot(changed))
                self.assertTrue(result["config_changed"]); self.assertEqual(result["classification"], "CONFIG_CHANGED")

    def test_malformed_or_unknown_counter_shapes_and_contexts_fail_closed(self):
        bad_values = [None, {}, {"packets": 1}, {"packets": True, "bytes": 1},
            {"packets": -1, "bytes": 1}, {"packets": 2**64, "bytes": 1},
            {"packets": 1, "bytes": 1, "quota": 1}, ""]
        for value in bad_values:
            with self.subTest(value=value):
                changed = inert_firewall(); changed["nftables"][4]["rule"]["expr"][1]["counter"] = value
                code = "FIREWALL_JSON_NUMBER" if type(value) is dict and value.get("packets") == 2**64 else "FIREWALL_COUNTER_"
                with self.assertRaisesRegex(ValueError, code): self.snapshot(changed)
        for mutation in (lambda x: x[3]["counter"].update(comment="unsupported"),
                         lambda x: x[4]["rule"]["expr"].append(dict(meter=dict(stmt=dict(counter=dict(packets=1, bytes=2)))))):
            changed = inert_firewall(); mutation(changed["nftables"])
            with self.assertRaisesRegex(ValueError, "FIREWALL_COUNTER_"): self.snapshot(changed)

    def test_duplicate_keys_schema_and_malformed_json_reject(self):
        for raw in (b'{"nftables":[],"nftables":[]}', b'{"nftables":[]}', b'{}', b'[]', b'{', b'\xff'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "FIREWALL_JSON_"):
                H.firewall_snapshot(raw)
        for schema in (True, 0, 2, "1"):
            changed = inert_firewall(); changed["nftables"][0]["metainfo"]["json_schema_version"] = schema
            with self.assertRaisesRegex(ValueError, "FIREWALL_JSON_SCHEMA"): self.snapshot(changed)
        valid = self.snapshot(inert_firewall())
        for mutation in (lambda x: x.update(schema=2), lambda x: x.update(unknown=1),
                         lambda x: x["raw"].update(sha256="raw policy")):
            malformed = copy.deepcopy(valid); mutation(malformed)
            with self.assertRaisesRegex(ValueError, "FIREWALL_METADATA_SHAPE"):
                H.baseline_unchanged(dict(firewall=malformed), dict(firewall=malformed))

    def test_parser_byte_depth_node_and_numeric_bounds(self):
        with patch.object(H.json, "loads") as loads:
            with self.assertRaisesRegex(ValueError, "FIREWALL_JSON_BYTES"): H.firewall_snapshot(b" " * (1024**2 + 1))
            loads.assert_not_called()
            with self.assertRaisesRegex(ValueError, "FIREWALL_JSON_DEPTH"): H.firewall_snapshot(b"[" * 33 + b"]" * 33)
            loads.assert_not_called()
        with self.assertRaisesRegex(ValueError, "FIREWALL_JSON_NODES"):
            H.firewall_snapshot(b"[" + b"0," * 65536 + b"0]")
        for value in ("NaN", "Infinity", "1.5", str(2**64), "9" * 100):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "FIREWALL_JSON_NUMBER"):
                H.firewall_snapshot(('{"nftables":[' + value + ']}').encode())

    def test_representation_only_requires_identical_canonical_configuration_and_counters(self):
        document = inert_firewall()
        before = self.snapshot(document)
        after = H.firewall_snapshot(json.dumps(document, indent=2, sort_keys=True).encode())
        self.assertEqual(H.firewall_comparison(before, after), dict(classification="REPRESENTATION_ONLY",
            raw_changed=True, config_changed=False, counters_changed=False))
        self.assertEqual(H.firewall_comparison(before, before)["classification"], "UNCHANGED")

    def test_counter_presence_placement_and_named_reference_are_configuration(self):
        original = self.snapshot(inert_firewall())
        for mode in ("remove", "add", "move", "reference"):
            changed = inert_firewall(); expressions = changed["nftables"][4]["rule"]["expr"]
            if mode == "remove": expressions.pop(1)
            elif mode == "add": expressions.insert(1, dict(counter=dict(packets=0, bytes=0)))
            elif mode == "move": expressions.insert(0, expressions.pop(1))
            else: changed["nftables"][5]["rule"]["expr"][0]["counter"] = "another"
            with self.subTest(mode=mode):
                self.assertTrue(H.firewall_comparison(original, self.snapshot(changed))["config_changed"])

    def test_baseline_uses_single_existing_read_with_json_flag_and_same_timeout(self):
        raw = json.dumps(inert_firewall()).encode()
        with patch.object(H.subprocess, "check_output", return_value=raw) as command, \
             patch.object(H.Path, "read_bytes", return_value=b"inert"), patch.object(H.os, "readlink", return_value="net:[123]"), \
             patch.object(H, "credentials", return_value={}), patch.object(H, "kvm_snapshot", return_value={}):
            snapshot = H.baseline()
        command.assert_called_once_with(["sudo", "-n", "nft", "-j", "list", "ruleset"], timeout=10)
        self.assertEqual(snapshot["firewall"], H.firewall_snapshot(raw))


class BaselineDiagnosticShimTests(unittest.TestCase):
    """Fixed inert snapshots and ordinary owned files; no host baseline reads."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.life = H.Lifetime()
        self.enterContext(patch.object(H, "ACTIVE", self.life))
        self.enterContext(patch.object(H.signal, "setitimer"))
        self.enterContext(patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)))
        self.before = dict(netns="net:[123]", routes=dict(bytes=896, sha256="a" * 64),
            ipv6_routes=dict(bytes=900, sha256="a" * 64), resolver=dict(bytes=970, sha256="a" * 64),
            firewall=H.firewall_snapshot(json.dumps(inert_firewall()).encode()),
            credentials=dict(uid=[1001] * 3, gid=[1001] * 3, groups=[4, 1001]),
            kvm=dict(kvm_gid=993, acl=dict(present=False), device=dict(dev=6, ino=435, rdev=2792,
                mode=8624, uid=0, gid=993, nlink=1, mtime_ns=123, ctime_ns=123)))

    def owned(self, name):
        state = self.root / name; state.mkdir(); (state / "private").write_bytes(b"inert")
        return state, self.root / (name + "-evidence")

    def test_matching_snapshots_preserve_success_and_capture_exactly_once(self):
        state, evidence = self.owned("match")
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=copy.deepcopy(self.before)) as capture:
            result = H.finalize(state, evidence, self.before, True, None)
        capture.assert_called_once_with()
        self.assertTrue(result["success"]); self.assertFalse(state.exists())
        diagnostic = result["baseline_comparison"]
        self.assertEqual(diagnostic, dict(schema=2, before=self.before, after=self.before,
            changed_components=[], capture_error=None, diagnostic_error=None,
            firewall_comparison=dict(classification="UNCHANGED", raw_changed=False, config_changed=False, counters_changed=False)))
        self.assertEqual(json.loads((evidence / "result.json").read_text()), result)

    def test_every_component_difference_fails_exact_comparison_and_retains_both(self):
        for component in self.before:
            with self.subTest(component=component):
                after = copy.deepcopy(self.before)
                if component == "netns": after[component] = "net:[124]"
                elif component == "credentials": after[component]["groups"] = [1001]
                elif component == "kvm": after[component]["device"]["ctime_ns"] += 1
                elif component == "firewall":
                    document = inert_firewall(); document["nftables"][2]["chain"]["policy"] = "accept"
                    after[component] = H.firewall_snapshot(json.dumps(document).encode())
                else: after[component]["sha256"] = "b" * 64
                state, evidence = self.owned(component)
                with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=after) as capture:
                    result = H.finalize(state, evidence, self.before, True, ValueError("ORIGINAL_FAILURE"))
                capture.assert_called_once_with()
                self.assertFalse(result["success"]); self.assertFalse(result["host_routes_dns_firewall_unchanged"])
                self.assertEqual(result["primary_failure"], "ORIGINAL_FAILURE")
                self.assertEqual(result["cleanup_errors"], ["HOST_BASELINE_UNCONFIRMED"])
                self.assertEqual(result["baseline_comparison"]["changed_components"], [component])
                self.assertEqual(result["baseline_comparison"]["before"], self.before)
                self.assertEqual(result["baseline_comparison"]["after"], after)
                self.assertTrue(result["registered_groups_drained"]); self.assertFalse(state.exists())

    def test_capture_failure_retains_closed_code_and_original_failure(self):
        state, evidence = self.owned("capture-failure")
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", side_effect=OSError("private error detail")) as capture:
            result = H.finalize(state, evidence, self.before, False, ValueError("ORIGINAL_FAILURE"))
        capture.assert_called_once_with()
        self.assertEqual(result["cleanup_errors"], ["OSError", "HOST_BASELINE_UNCONFIRMED"])
        self.assertEqual(result["primary_failure"], "ORIGINAL_FAILURE")
        self.assertEqual(result["baseline_comparison"], dict(schema=2, before=self.before, after=None,
            changed_components=None, capture_error="BASELINE_CAPTURE_FAILED", diagnostic_error=None, firewall_comparison=None))
        self.assertNotIn("private error detail", (evidence / "result.json").read_text())
        self.assertFalse(state.exists()); self.assertFalse(result["success"])

    def test_unavailable_before_does_not_add_a_host_read(self):
        state, evidence = self.owned("no-before")
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline") as capture:
            result = H.finalize(state, evidence, None, False, ValueError("ORIGINAL_FAILURE"))
        capture.assert_not_called()
        self.assertEqual(result["baseline_comparison"]["capture_error"], "BASELINE_BEFORE_UNAVAILABLE")
        self.assertIsNone(result["baseline_comparison"]["before"])
        self.assertIsNone(result["baseline_comparison"]["changed_components"])
        self.assertFalse(result["success"])

    def test_unknown_or_raw_metadata_is_omitted_without_changing_equality(self):
        for component in ("raw_routes", "resolver", "credentials", "kvm"):
            with self.subTest(component=component):
                malformed = copy.deepcopy(self.before)
                malformed[component] = "PRIVATE_RAW_CONTENT" * 2000
                diagnostic = H.baseline_diagnostic(malformed, malformed, None)
                self.assertEqual(diagnostic["diagnostic_error"], "BASELINE_METADATA_SHAPE")
                self.assertIsNone(diagnostic["before"]); self.assertIsNone(diagnostic["after"])
                self.assertNotIn("PRIVATE_RAW_CONTENT", json.dumps(diagnostic))
        state, evidence = self.owned("malformed")
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=malformed):
            result = H.finalize(state, evidence, malformed, True, None)
        self.assertTrue(result["success"]); self.assertTrue(result["host_routes_dns_firewall_unchanged"])
        self.assertEqual(result["baseline_comparison"]["diagnostic_error"], "BASELINE_METADATA_SHAPE")

    def test_complete_receipt_bound_omits_snapshots_preserving_existing_errors(self):
        state, evidence = self.owned("bounded")
        self.life.cleanup_errors = ["X" * 14000]
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=self.before):
            result = H.finalize(state, evidence, self.before, True, ValueError("ORIGINAL_FAILURE"))
        self.assertLessEqual((evidence / "result.json").stat().st_size, 16384)
        self.assertEqual(result["baseline_comparison"]["diagnostic_error"], "BASELINE_RECEIPT_BOUND")
        self.assertIsNone(result["baseline_comparison"]["before"]); self.assertIsNone(result["baseline_comparison"]["after"])
        self.assertEqual(result["cleanup_errors"], ["X" * 14000])
        self.assertEqual(result["primary_failure"], "ORIGINAL_FAILURE")
        self.assertTrue(result["host_routes_dns_firewall_unchanged"]); self.assertFalse(result["success"])

    def test_existing_nested_copy_retains_both_control_diagnostics_after_removal(self):
        state, evidence = self.owned("outer")
        nested = {}
        for mode in ("term-first", "int-term"):
            case = state / ("control-" + mode); case.mkdir()
            with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=self.before):
                nested[mode] = H.finalize(case, state / ("control-evidence-" + mode), self.before,
                    False, H.Interrupted("HOSTED_INTERRUPTED"))
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=self.before):
            result = H.finalize(state, evidence, self.before, True, None)
        self.assertTrue(result["success"]); self.assertFalse(state.exists())
        for mode in nested:
            self.assertEqual(json.loads((evidence / ("control-" + mode + "-result.json")).read_text()), nested[mode])
            self.assertEqual(nested[mode]["baseline_comparison"]["before"], self.before)
            self.assertEqual(nested[mode]["baseline_comparison"]["after"], self.before)

    def test_counter_changes_can_pass_config_check_while_raw_difference_is_retained(self):
        after = copy.deepcopy(self.before); document = inert_firewall()
        document["nftables"][3]["counter"]["packets"] += 1
        after["firewall"] = H.firewall_snapshot(json.dumps(document).encode())
        state, evidence = self.owned("counter-only")
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=after):
            result = H.finalize(state, evidence, self.before, True, None)
        self.assertTrue(result["success"]); self.assertTrue(result["host_routes_dns_firewall_unchanged"])
        self.assertEqual(result["baseline_comparison"]["changed_components"], ["firewall"])
        self.assertEqual(result["baseline_comparison"]["firewall_comparison"]["classification"], "COUNTERS_ONLY")
        self.assertNotEqual(result["baseline_comparison"]["before"]["firewall"]["raw"],
                            result["baseline_comparison"]["after"]["firewall"]["raw"])


class OfflineDiagnosticTests(unittest.TestCase):
    """Inert text files and mocked build calls; no Gradle or namespace execution."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.life = H.Lifetime()
        self.enterContext(patch.object(H, "ACTIVE", self.life))
        self.enterContext(patch.object(H.signal, "setitimer"))
        self.enterContext(patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)))
        self.source = "a" * 40
        self.summary = dict(exit=1, peak_observed_rss=100, wall_bound_seconds=1200,
            owned_process_group_drained=True, signal_count=0, cleanup_error=None)

    def fixtures(self, raw):
        for name, value in (("android-offline-rebuild.json", json.dumps(self.summary).encode()),
                            ("android-offline-rebuild.private.log", raw)):
            fd = H.os.open(self.root / name, H.os.O_CREAT | H.os.O_TRUNC | H.os.O_WRONLY, 0o600)
            try: H.os.write(fd, value)
            finally: H.os.close(fd)

    def test_complete_capture_is_bounded_private_and_retained_after_original_failure(self):
        raw = b"SECRET_URL https://private.invalid/path\nUnable to start the daemon process\njava.net.BindException"
        self.fixtures(raw)
        original = ValueError("STEP_ANDROID-OFFLINE-REBUILD")
        with patch.object(H, "run", side_effect=original):
            with self.assertRaises(ValueError) as caught:
                H.offline_build(self.root, dict(GITHUB_SHA=self.source))
        self.assertIs(caught.exception, original)
        diagnostic = json.loads((self.root / "offline-build-diagnostic.json").read_text())
        self.assertEqual(diagnostic["source"], self.source); self.assertIsNone(diagnostic["capture_error"])
        self.assertEqual(diagnostic["coverage"], "COMPLETE")
        self.assertEqual(diagnostic["log_bytes"], len(raw)); self.assertEqual(diagnostic["read_bytes"], len(raw))
        self.assertEqual(diagnostic["complete_log_fingerprint"], P.digest(raw))
        self.assertEqual(diagnostic["observed_markers"], ["GRADLE_DAEMON_START", "LOCAL_SOCKET_BIND"])
        self.assertLessEqual((self.root / "offline-build-diagnostic.json").stat().st_size, 16384)
        self.assertNotIn("SECRET_URL", json.dumps(diagnostic)); self.assertNotIn("https://", json.dumps(diagnostic))
        evidence = self.root.parent / (self.root.name + "-evidence")
        self.addCleanup(lambda: __import__("shutil").rmtree(evidence, ignore_errors=True))
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=dict(inert=True)):
            result = H.finalize(self.root, evidence, dict(inert=True), False, original)
        self.assertEqual(result["primary_failure"], "ValueError"); self.assertFalse(result["success"])
        self.assertEqual(json.loads((evidence / "offline-build-diagnostic.json").read_text()), diagnostic)
        self.assertFalse((evidence / "android-offline-rebuild.private.log").exists())
        self.assertFalse(self.root.exists())

    def test_prefix_tail_coverage_does_not_invent_markers_across_unread_middle(self):
        raw = (b"x" * (32768 - len(b"Dependency verifi")) + b"Dependency verifi"
               + b"No cached version SECRET_MIDDLE" * 100 + b"cation failed" + b"x" * (32768 - len(b"cation failed")))
        self.fixtures(raw)
        diagnostic = H.offline_failure_diagnostic(self.root, self.source)
        self.assertIsNone(diagnostic["capture_error"]); self.assertEqual(diagnostic["coverage"], "PREFIX_TAIL")
        self.assertEqual(diagnostic["read_bytes"], 65536); self.assertEqual(diagnostic["log_bytes"], len(raw))
        self.assertIsNone(diagnostic["complete_log_fingerprint"]); self.assertEqual(diagnostic["observed_markers"], [])
        self.assertEqual(diagnostic["windows"], [dict(offset=0, **P.digest(raw[:32768])),
            dict(offset=len(raw) - 32768, **P.digest(raw[-32768:]))])

    def test_empty_and_window_boundaries_have_exact_read_counts_and_full_hash_only_when_complete(self):
        for size in (0, 32768, 32769, 65536, 65537):
            with self.subTest(size=size):
                raw = b"x" * size; self.fixtures(raw)
                with patch.object(H.os, "read", wraps=H.os.read) as read:
                    diagnostic = H.offline_failure_diagnostic(self.root, self.source)
                self.assertIsNone(diagnostic["capture_error"])
                self.assertEqual(diagnostic["read_bytes"], min(size, 65536))
                self.assertTrue(all(call.args[1] <= 32768 for call in read.call_args_list))
                self.assertEqual(diagnostic["complete_log_fingerprint"], P.digest(raw) if size <= 65536 else None)

    def test_undrained_failed_command_never_opens_private_log(self):
        for mode in ("summary", "owned"):
            with self.subTest(mode=mode):
                self.summary["owned_process_group_drained"] = mode != "summary"
                self.fixtures(b"private")
                self.life.owned = [unittest.mock.Mock()] if mode == "owned" else []
                with patch.object(H.os, "open", wraps=H.os.open) as opened:
                    diagnostic = H.offline_failure_diagnostic(self.root, self.source)
                self.assertEqual(diagnostic["capture_error"], "BUILD_DIAGNOSTIC_NOT_DRAINED")
                self.assertNotIn("android-offline-rebuild.private.log", [call.args[0] for call in opened.call_args_list])
                self.life.owned = []

    def test_nonblocking_nofollow_owned_type_size_and_link_guards_close_fds_before_read(self):
        self.fixtures(b"private")
        fake = 987654
        real_open, real_stat, real_close = H.os.open, H.os.fstat, H.os.close
        for change in (dict(st_mode=stat.S_IFIFO | 0o600), dict(st_size=64 * 1024**2 + 1), dict(st_nlink=2),
                       dict(st_mode=stat.S_IFREG | 0o644), dict(st_uid=0)):
            with self.subTest(change=change):
                info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=7, st_nlink=1,
                    st_uid=H.os.getuid(), st_gid=H.os.getgid())
                vars(info).update(change)
                def opened(name, flags, **kwargs):
                    if name == "android-offline-rebuild.private.log":
                        self.assertEqual(flags, H.os.O_RDONLY | H.os.O_CLOEXEC | H.os.O_NOFOLLOW | H.os.O_NONBLOCK)
                        return fake
                    return real_open(name, flags, **kwargs)
                with patch.object(H.os, "open", side_effect=opened), \
                     patch.object(H.os, "fstat", side_effect=lambda fd: info if fd == fake else real_stat(fd)), \
                     patch.object(H.os, "close", side_effect=lambda fd: None if fd == fake else real_close(fd)) as close, \
                     patch.object(H.os, "read", wraps=H.os.read) as read:
                    diagnostic = H.offline_failure_diagnostic(self.root, self.source)
                self.assertEqual(diagnostic["capture_error"], "BUILD_DIAGNOSTIC_FILE")
                self.assertTrue(any(call.args == (fake,) for call in close.call_args_list))
                self.assertFalse(any(call.args[0] == fake for call in read.call_args_list))
        path = self.root / "android-offline-rebuild.private.log"
        path.unlink(); path.symlink_to(self.root / "android-offline-rebuild.json")
        self.assertEqual(H.offline_failure_diagnostic(self.root, self.source)["capture_error"], "BUILD_DIAGNOSTIC_IO")

    def test_invalid_source_and_changed_or_short_files_emit_only_closed_capture_codes(self):
        self.fixtures(b"private")
        with patch.object(H.os, "open") as opened:
            diagnostic = H.offline_failure_diagnostic(self.root, "PRIVATE_SOURCE")
        opened.assert_not_called(); self.assertIsNone(diagnostic["source"])
        self.assertEqual(diagnostic["capture_error"], "BUILD_DIAGNOSTIC_SOURCE")
        with patch.object(H.os, "read", return_value=b""):
            self.assertEqual(H.offline_failure_diagnostic(self.root, self.source)["capture_error"], "BUILD_DIAGNOSTIC_CHANGED")
        real_stat = H.os.fstat
        calls = [0]
        def changed(fd):
            calls[0] += 1; info = real_stat(fd)
            if calls[0] != 3: return info
            fields = {name: getattr(info, name) for name in ("st_dev", "st_ino", "st_rdev", "st_mode", "st_uid", "st_gid",
                "st_nlink", "st_mtime_ns", "st_ctime_ns", "st_size")}
            fields["st_ino"] += 1
            return SimpleNamespace(**fields)
        with patch.object(H.os, "fstat", side_effect=changed):
            self.assertEqual(H.offline_failure_diagnostic(self.root, self.source)["capture_error"], "BUILD_DIAGNOSTIC_CHANGED")
        duplicate = json.dumps(self.summary).replace('"exit": 1', '"exit": 0, "exit": 1').encode()
        path = self.root / "android-offline-rebuild.json"
        path.write_bytes(duplicate)
        self.assertEqual(H.offline_failure_diagnostic(self.root, self.source)["capture_error"], "BUILD_DIAGNOSTIC_SUMMARY")

    def test_diagnostic_capture_or_save_failure_never_replaces_original_command_error(self):
        original = ValueError("ORIGINAL_COMMAND_FAILURE")
        for failure in ("capture", "save", "bound"):
            with self.subTest(failure=failure), patch.object(H, "run", side_effect=original), \
                 patch.object(H, "offline_failure_diagnostic", return_value={}) as capture, patch.object(H, "save") as save:
                if failure == "capture": capture.side_effect = OSError("PRIVATE_CAPTURE_ERROR")
                elif failure == "save": save.side_effect = OSError("PRIVATE_SAVE_ERROR")
                else: capture.return_value = dict(private="x" * 16384)
                with self.assertRaises(ValueError) as caught:
                    H.offline_build(self.root, dict(GITHUB_SHA=self.source))
                self.assertIs(caught.exception, original)
                if failure == "bound": save.assert_not_called()

    def test_successful_offline_build_keeps_exact_command_and_skips_failure_capture(self):
        output = self.root / "prepared/android/volparossa_dps_jni/build/outputs/apk/androidTest/debug"
        output.mkdir(parents=True); (output / "inert.apk").write_bytes(b"inert nonexecutable fixture")
        env = dict(GITHUB_SHA=self.source)
        with patch.object(H, "run") as run, patch.object(H, "offline_failure_diagnostic") as capture:
            H.offline_build(self.root, env)
        capture.assert_not_called()
        run.assert_called_once_with("android-offline-rebuild", H.gradle(self.root / "tools", "--offline", "--dependency-verification=strict",
            ":volparossa_dps_jni:clean", ":volparossa_dps_jni:assembleDebug", ":volparossa_dps_jni:assembleDebugAndroidTest"),
            self.root, env, self.root / "prepared/android", seconds=1200)
        self.assertEqual(json.loads((self.root / "offline-apk.json").read_text())["source"], self.source)


class EmulatorObservationTests(unittest.TestCase):
    """Mocked child/clock/drain behavior and inert files only; no emulator runs."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.life = H.Lifetime()
        self.enterContext(patch.object(H, "ACTIVE", self.life))
        self.enterContext(patch.object(H.signal, "setitimer"))
        self.enterContext(patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)))
        self.source = "a" * 40

    def child(self, statuses):
        child = unittest.mock.Mock(returncode=None)
        values = iter(statuses)
        def poll():
            child.returncode = next(values)
            return child.returncode
        child.poll.side_effect = poll
        return child

    def terminal_files(self, emulator=b"ERROR KVM SECRET_URL https://private.invalid", adb=b"FATAL SECRET_ADB"):
        summary = dict(exit=1, peak_observed_rss=100, wall_bound_seconds=420,
            owned_process_group_drained=True, signal_count=0, cleanup_error=None)
        for name, raw in (("emulator-namespace.json", json.dumps(summary).encode()),
                          ("emulator.private.log", emulator), ("adb.private.log", adb)):
            fd = H.os.open(self.root / name, H.os.O_CREAT | H.os.O_TRUNC | H.os.O_WRONLY, 0o600)
            try: H.os.write(fd, raw)
            finally: H.os.close(fd)
        return summary

    def test_boot_predicate_has_independent_shortcircuit_terminal_reasons_and_bounded_values(self):
        for emulator_status, adb_status, now, reason in ((2, 1, 10, "EMULATOR_EXITED"),
                (None, 1, 10, "ADB_SERVER_EXITED"), (None, None, 180, "BOOT_DEADLINE"), (None, None, 10, None)):
            with self.subTest(reason=reason):
                emulator, adb = self.child([emulator_status]), self.child([adb_status])
                with patch.object(H.time, "monotonic", return_value=now):
                    observation = H.emulator_boot_observation(emulator, adb, 180, 1)
                self.assertEqual(observation["reason"], reason)
                self.assertEqual(observation["emulator_returncode"], emulator_status)
                self.assertEqual(observation["adb_observed"], emulator_status is None)
                self.assertEqual(adb.poll.call_count, int(emulator_status is None))
                self.assertEqual(observation["deadline_observed"], emulator_status is None and adb_status is None)
        with patch.object(H.time, "monotonic", return_value=1000):
            observed = H.emulator_boot_observation(self.child(["PRIVATE_STATUS"]), self.child([None]), 180, 1000)
        self.assertEqual(observed["attempts"], 255); self.assertTrue(observed["attempts_truncated"])
        self.assertEqual(observed["elapsed_ms"], 420000); self.assertTrue(observed["elapsed_truncated"])
        self.assertNotIn("PRIVATE_STATUS", json.dumps(observed))

    def trial_mocks(self, emulator, adb, command, cleanup):
        children = iter([adb, emulator])
        def spawn(*args, **kwargs):
            child = next(children); self.life.owned.append(child); return child
        with patch.object(self.life, "spawn", side_effect=spawn), patch.object(self.life, "drain_all", side_effect=cleanup), \
             patch.object(H, "run", side_effect=command), patch.object(H.time, "monotonic", return_value=10), patch.object(H.time, "sleep"):
            with self.assertRaises(ValueError) as caught:
                H.emulator_trial(self.root, dict(GITHUB_SHA=self.source))
        return caught.exception, json.loads((self.root / "namespace-cleanup.json").read_text())

    def test_not_ready_adb_poll_continues_until_ready_and_is_not_later_failure_cause(self):
        labels = []
        def command(label, *args, **kwargs):
            labels.append(label)
            if label == "boot-1": raise ValueError("STEP_BOOT-1")
            if label == "boot-2": return b"1\n"
            if label == "guest-api": raise ValueError("STOP_AT_GUEST_IDENTITY")
            self.fail("unexpected command")
        error, receipt = self.trial_mocks(self.child([None, None]), self.child([None, None]), command, self.life.owned.clear)
        self.assertEqual(str(error), "STOP_AT_GUEST_IDENTITY"); self.assertEqual(labels, ["boot-1", "boot-2", "guest-api"])
        self.assertFalse(receipt["primary_boot_failure"]); self.assertIsNone(receipt["boot_observation"]["reason"])
        self.assertEqual(receipt["last_nonfatal_boot_poll"], dict(attempt=1, attempts_truncated=False, code="VALUE_ERROR"))
        self.assertTrue(receipt["direct_children_reaped"]); self.assertEqual(receipt["cleanup_observation"]["stage"], "COMPLETE")

    def test_primary_boot_failure_and_cleanup_group_failure_remain_separate(self):
        def command(*args, **kwargs): raise ValueError("STEP_BOOT-1")
        def cleanup(): raise ValueError("PROCESS_GROUP_DRAIN")
        error, receipt = self.trial_mocks(self.child([None, 2]), self.child([None]), command, cleanup)
        self.assertEqual(str(error), "EMULATOR_BOOT"); self.assertTrue(receipt["primary_boot_failure"])
        self.assertEqual(receipt["boot_observation"]["reason"], "EMULATOR_EXITED")
        self.assertEqual(receipt["last_nonfatal_boot_poll"]["code"], "VALUE_ERROR")
        self.assertEqual(receipt["cleanup_error"], "ValueError")
        diagnostic = receipt["cleanup_observation"]
        self.assertEqual(diagnostic["stage"], "DRAIN_GROUPS"); self.assertEqual(diagnostic["code"], "PROCESS_GROUP_DRAIN")
        self.assertTrue(diagnostic["order_proven"]); self.assertEqual(diagnostic["failed_group_index"], 0)
        self.assertEqual(diagnostic["remaining_roles"][-1], dict(role="emulator", cached_returncode=2))
        self.assertFalse(receipt["direct_children_reaped"]); self.assertFalse(receipt["avd_removed"])
        self.assertLessEqual((self.root / "namespace-cleanup.json").stat().st_size, 16384)

    def test_cleanup_index_requires_exact_ownership_prefix_and_known_drain_error(self):
        emulator, adb = self.child([]), self.child([])
        error = ValueError("PROCESS_GROUP_DRAIN")
        known = H.emulator_cleanup_observation([emulator, adb], [adb], emulator, adb, "DRAIN_GROUPS", error)
        self.assertTrue(known["order_proven"]); self.assertEqual(known["failed_group_index"], 1)
        for remaining, failure in (([emulator, adb], error), ([adb], ValueError("PRIVATE_ERROR")), ([adb, adb], error)):
            observed = H.emulator_cleanup_observation([emulator, adb], remaining, emulator, adb, "DRAIN_GROUPS", failure)
            self.assertIsNone(observed["failed_group_index"]); self.assertNotIn("PRIVATE_ERROR", json.dumps(observed))
        bounded = H.emulator_cleanup_observation([adb] * 9, [], emulator, adb, "DRAIN_GROUPS", error)
        self.assertEqual(bounded["observation_error"], "OWNERSHIP_OBSERVATION_BOUND")
        emulator.poll.assert_not_called(); adb.poll.assert_not_called()

    def test_terminal_logs_publish_only_bounded_fingerprints_and_finite_tokens(self):
        self.terminal_files()
        observed = H.emulator_failure_diagnostic(self.root, self.source)
        self.assertIsNone(observed["capture_error"]); self.assertEqual(observed["namespace_exit"], 1)
        self.assertEqual([entry["role"] for entry in observed["logs"]], ["emulator", "adb_server"])
        self.assertEqual(observed["logs"][0]["observed_markers"], ["ERROR_TOKEN", "KVM_TOKEN"])
        self.assertEqual(observed["logs"][1]["observed_markers"], ["FATAL_TOKEN"])
        self.assertTrue(all(entry["coverage"] == "COMPLETE" for entry in observed["logs"]))
        encoded = json.dumps(observed)
        self.assertNotIn("SECRET", encoded); self.assertNotIn("https://", encoded); self.assertNotIn(str(self.root), encoded)
        self.assertLessEqual(len(encoded.encode()), 16384)

    def test_live_or_undrained_namespace_rejects_before_any_log_open(self):
        for mode in ("summary", "owned", "zero-exit"):
            summary = self.terminal_files()
            if mode == "summary": summary["owned_process_group_drained"] = False
            elif mode == "zero-exit": summary["exit"] = 0
            else: self.life.owned = [self.child([])]
            (self.root / "emulator-namespace.json").write_text(json.dumps(summary))
            with patch.object(H.os, "open", wraps=H.os.open) as opened:
                observed = H.emulator_failure_diagnostic(self.root, self.source)
            self.assertEqual(observed["capture_error"], "EMULATOR_DIAGNOSTIC_NOT_DRAINED")
            self.assertFalse(observed["logs"])
            self.assertTrue(all(not str(call.args[0]).endswith(".private.log") for call in opened.call_args_list))
            self.life.owned = []

    def test_terminal_log_fd_type_size_nofollow_nonblocking_and_read_races_reject(self):
        self.terminal_files()
        real_open, real_stat, real_close = H.os.open, H.os.fstat, H.os.close
        fake = 987654
        for change in (dict(st_mode=stat.S_IFIFO | 0o600), dict(st_size=64 * 1024**2 + 1), dict(st_nlink=2),
                       dict(st_mode=stat.S_IFREG | 0o644), dict(st_uid=0)):
            info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=7, st_nlink=1,
                st_uid=H.os.getuid(), st_gid=H.os.getgid())
            vars(info).update(change)
            def opened(name, flags, **kwargs):
                if name == "emulator.private.log":
                    self.assertEqual(flags, H.os.O_RDONLY | H.os.O_CLOEXEC | H.os.O_NOFOLLOW | H.os.O_NONBLOCK)
                    return fake
                return real_open(name, flags, **kwargs)
            with patch.object(H.os, "open", side_effect=opened), \
                 patch.object(H.os, "fstat", side_effect=lambda fd: info if fd == fake else real_stat(fd)), \
                 patch.object(H.os, "close", side_effect=lambda fd: None if fd == fake else real_close(fd)) as close, \
                 patch.object(H.os, "read", wraps=H.os.read) as read:
                observed = H.emulator_failure_diagnostic(self.root, self.source)
            self.assertEqual(observed["capture_error"], "EMULATOR_DIAGNOSTIC_FILE")
            self.assertTrue(any(call.args == (fake,) for call in close.call_args_list))
            self.assertFalse(any(call.args[0] == fake for call in read.call_args_list))
        log = self.root / "emulator.private.log"; log.unlink(); log.symlink_to(self.root / "emulator-namespace.json")
        self.assertEqual(H.emulator_failure_diagnostic(self.root, self.source)["capture_error"], "EMULATOR_DIAGNOSTIC_IO")
        with patch.object(H.os, "read", return_value=b""):
            self.assertEqual(H.emulator_failure_diagnostic(self.root, self.source)["capture_error"], "EMULATOR_DIAGNOSTIC_CHANGED")
        calls = [0]
        def changed(fd):
            calls[0] += 1; info = real_stat(fd)
            if calls[0] != 3: return info
            fields = {name: getattr(info, name) for name in ("st_dev", "st_ino", "st_rdev", "st_mode", "st_uid", "st_gid",
                "st_nlink", "st_mtime_ns", "st_ctime_ns", "st_size")}
            fields["st_ino"] += 1
            return SimpleNamespace(**fields)
        with patch.object(H.os, "fstat", side_effect=changed):
            self.assertEqual(H.emulator_failure_diagnostic(self.root, self.source)["capture_error"], "EMULATOR_DIAGNOSTIC_CHANGED")

    def test_two_log_prefix_tail_windows_never_join_gaps_and_total_at_most_64k(self):
        raw = b"x" * 16382 + b"ER" + b"Qt platform SECRET_MIDDLE" * 100 + b"ROR" + b"x" * 16381
        self.terminal_files(raw, raw)
        with patch.object(H.os, "read", wraps=H.os.read) as read:
            observed = H.emulator_failure_diagnostic(self.root, self.source)
        self.assertIsNone(observed["capture_error"])
        self.assertEqual(sum(entry["read_bytes"] for entry in observed["logs"]), 65536)
        self.assertTrue(all(call.args[1] <= 16384 for call in read.call_args_list))
        for entry in observed["logs"]:
            self.assertEqual(entry["coverage"], "PREFIX_TAIL"); self.assertEqual(entry["log_bytes"], len(raw))
            self.assertIsNone(entry["complete_log_fingerprint"]); self.assertEqual(entry["observed_markers"], [])
            self.assertEqual(entry["windows"], [dict(offset=0, **P.digest(raw[:16384])),
                dict(offset=len(raw) - 16384, **P.digest(raw[-16384:]))])

    def test_phase_capture_and_save_errors_or_bounds_never_replace_namespace_failure(self):
        original = ValueError("STEP_EMULATOR-NAMESPACE")
        for mode in ("capture", "save", "bound", "public"):
            state = self.root / mode; state.mkdir(mode=0o700)
            phase = "public-build" if mode == "public" else "emulator"
            with patch.object(H, "run", side_effect=original), patch.object(H, "emulator_failure_diagnostic", return_value={}) as capture, \
                 patch.object(H, "save") as save:
                if mode == "capture": capture.side_effect = OSError("PRIVATE_CAPTURE")
                elif mode == "save": save.side_effect = OSError("PRIVATE_SAVE")
                elif mode == "bound": capture.return_value = dict(private="x" * 16384)
                with self.assertRaises(ValueError) as caught: H.run_phase(state, phase, dict(GITHUB_SHA=self.source), 420)
                self.assertIs(caught.exception, original)
                if mode in ("public", "bound"): save.assert_not_called()
                if mode == "public": capture.assert_not_called()

    def test_existing_finalizer_retains_terminal_diagnostics_and_excludes_raw_logs(self):
        self.terminal_files()
        original = ValueError("STEP_EMULATOR-NAMESPACE")
        with patch.object(H, "run", side_effect=original):
            with self.assertRaises(ValueError) as caught: H.run_phase(self.root, "emulator", dict(GITHUB_SHA=self.source), 420)
        self.assertIs(caught.exception, original)
        diagnostic = json.loads((self.root / "emulator-failure-diagnostic.json").read_text())
        self.assertIsNone(diagnostic["capture_error"])
        evidence = self.root.parent / (self.root.name + "-evidence")
        self.addCleanup(lambda: __import__("shutil").rmtree(evidence, ignore_errors=True))
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=dict(inert=True)):
            result = H.finalize(self.root, evidence, dict(inert=True), False, original)
        self.assertFalse(result["success"])
        self.assertEqual(json.loads((evidence / "emulator-failure-diagnostic.json").read_text()), diagnostic)
        self.assertFalse((evidence / "emulator.private.log").exists()); self.assertFalse((evidence / "adb.private.log").exists())


class KvmShimTests(unittest.TestCase):
    """No local KVM device, ioctl, credentials, namespace, signal or workload execution."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.life = H.Lifetime()
        self.enterContext(patch.object(H, "ACTIVE", self.life))
        self.enterContext(patch.object(H.signal, "setitimer"))
        self.enterContext(patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)))
        self.info = SimpleNamespace(st_dev=1, st_ino=2, st_rdev=H.os.makedev(10, 232),
            st_mode=stat.S_IFCHR | 0o660, st_uid=0, st_gid=78, st_nlink=1, st_mtime_ns=3, st_ctime_ns=4)
        self.kvm = self.enterContext(patch.object(H, "KVM", unittest.mock.Mock()))
        self.kvm.lstat.return_value = self.info
        self.acl = self.enterContext(patch.object(H.os, "getxattr", side_effect=OSError(errno.ENODATA, "inert")))
        self.group = self.enterContext(patch.object(H.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=78)))
        self.expected = dict(device=H.device_identity(self.info), acl=dict(present=False), kvm_gid=78)

    def phase_prefix(self, phase, namespace, links):
        """Mock the root bootstrap through its first credential-drop call."""
        owner = SimpleNamespace(st_dev=1, st_ino=2, st_rdev=0, st_mode=stat.S_IFDIR | 0o700,
            st_uid=1001, st_gid=1001, st_nlink=1, st_mtime_ns=3, st_ctime_ns=4)
        receipt = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1001, st_gid=1001, st_nlink=1, st_size=0)
        state = unittest.mock.Mock(); state.lstat.return_value = owner; state.resolve.return_value = state
        outer = dict(netns="net:[host]", credentials=dict(uid=[1001] * 3, gid=[1001] * 3), kvm=self.expected)
        events = []
        def drop(*args):
            events.append("drop")
            raise ValueError("STOP_AT_CREDENTIAL_DROP")
        with patch.dict(H.os.environ, dict(SUDO_UID="1001", SUDO_GID="1001")), \
             patch.object(H.os, "getuid", return_value=0), patch.object(H.os, "geteuid", return_value=0), \
             patch.object(H.os, "getpid", return_value=1), patch.object(H.os, "open", side_effect=[22, 23]), \
             patch.object(H.os, "fstat", side_effect=[owner, receipt]), patch.object(H.os, "close"), \
             patch.object(H.os, "write", side_effect=lambda fd, raw: len(raw)), \
             patch.object(H.os, "readlink", return_value=namespace), patch.object(H.socket, "if_nameindex", return_value=links), \
             patch.object(H, "private_document", side_effect=[outer, dict(map="a" * 40)]), \
             patch.object(H, "kvm_snapshot", return_value=self.expected), \
             patch.object(H, "drop_credentials", side_effect=drop) as dropped, \
             patch.object(H.subprocess, "run", side_effect=lambda *args, **kwargs: events.append("loopback")) as command:
            with self.assertRaises(ValueError) as caught:
                H.phase_child(state, phase)
        return str(caught.exception), events, command, dropped

    def test_private_offline_and_emulator_enable_only_loopback_once_before_drop(self):
        for phase in H.PHASES:
            with self.subTest(phase=phase):
                namespace = "net:[host]" if phase == "public-build" else "net:[private]"
                code, events, command, dropped = self.phase_prefix(phase, namespace, [(1, "lo")])
                self.assertEqual(code, "STOP_AT_CREDENTIAL_DROP"); self.assertEqual(dropped.call_count, 1)
                if phase in ("offline-build", "emulator"):
                    self.assertEqual(events, ["loopback", "drop"])
                    command.assert_called_once_with(["/usr/sbin/ip", "link", "set", "lo", "up"], check=True, timeout=5)
                else:
                    self.assertEqual(events, ["drop"]); command.assert_not_called()

    def test_wrong_phase_namespace_rejects_before_loopback_or_drop(self):
        for phase in H.PHASES:
            with self.subTest(phase=phase):
                namespace = "net:[private]" if phase == "public-build" else "net:[host]"
                code, events, command, dropped = self.phase_prefix(phase, namespace, [(1, "lo")])
                self.assertEqual(code, "PHASE_NETWORK_NAMESPACE"); self.assertEqual(events, [])
                command.assert_not_called(); dropped.assert_not_called()

    def test_unexpected_private_links_reject_before_loopback_or_drop(self):
        for phase in ("kvm-probe", "offline-build", "emulator"):
            for links in ([], [(1, "lo"), (2, "eth0")]):
                with self.subTest(phase=phase, links=links):
                    code, events, command, dropped = self.phase_prefix(phase, "net:[private]", links)
                    self.assertEqual(code, "PHASE_PRIVATE_LINKS"); self.assertEqual(events, [])
                    command.assert_not_called(); dropped.assert_not_called()

    def test_snapshot_binds_named_group_device_acl_and_rejects_changed_identity(self):
        self.assertEqual(H.kvm_snapshot(), self.expected)
        self.group.assert_called_with("kvm")
        self.acl.assert_called_with(self.kvm, "system.posix_acl_access", follow_symlinks=False)
        self.acl.side_effect = None; self.acl.return_value = b"inert ACL"
        self.assertEqual(H.kvm_snapshot()["acl"], dict(present=True, **P.digest(b"inert ACL")))
        changed = SimpleNamespace(**vars(self.info)); changed.st_ino = 99
        self.kvm.lstat.side_effect = [self.info, changed]
        with self.assertRaisesRegex(ValueError, "KVM_DEVICE_CHANGED"):
            H.kvm_snapshot()

    def test_missing_wrong_type_wrong_device_group_acl_fail_closed_without_open(self):
        cases = [("missing", "KVM_MISSING"), ("type", "KVM_WRONG_TYPE"), ("device", "KVM_WRONG_DEVICE"),
                 ("no_group", "KVM_GROUP_MISSING"), ("root_group", "KVM_GROUP_MISMATCH"),
                 ("mismatch", "KVM_GROUP_MISMATCH"), ("acl", "KVM_ACL_UNREADABLE"), ("large_acl", "KVM_ACL_BOUND")]
        for case, code in cases:
            with self.subTest(case=case), patch.object(H.os, "open") as opened:
                self.kvm.lstat.side_effect = None; self.kvm.lstat.return_value = self.info
                self.group.side_effect = None; self.group.return_value = SimpleNamespace(gr_gid=78)
                self.acl.side_effect = OSError(errno.ENODATA, "inert")
                if case == "missing": self.kvm.lstat.side_effect = FileNotFoundError(errno.ENOENT, "inert")
                elif case in ("type", "device"):
                    changed = SimpleNamespace(**vars(self.info))
                    if case == "type": changed.st_mode = stat.S_IFLNK | 0o777
                    else: changed.st_rdev = H.os.makedev(1, 3)
                    self.kvm.lstat.return_value = changed
                elif case == "no_group": self.group.side_effect = KeyError("inert")
                elif case == "root_group": self.group.return_value = SimpleNamespace(gr_gid=0)
                elif case == "mismatch": self.group.return_value = SimpleNamespace(gr_gid=79)
                elif case == "acl": self.acl.side_effect = OSError(errno.EOPNOTSUPP, "inert")
                elif case == "large_acl": self.acl.side_effect = None; self.acl.return_value = b"x" * 4097
                with self.assertRaisesRegex(ValueError, code): H.kvm_snapshot()
                opened.assert_not_called()

    def test_admission_failure_keeps_bounded_private_metadata_receipt(self):
        creds = dict(uid=[1001] * 3, gid=[1001] * 3, groups=[27, 1001])
        with patch.object(H, "credentials", return_value=creds), patch.dict(H.os.environ, {"GITHUB_SHA": "a" * 40}):
            self.group.side_effect = KeyError("inert")
            with self.assertRaisesRegex(ValueError, "KVM_GROUP_MISSING"): H.kvm_admission(self.root)
        result = json.loads((self.root / "kvm-admission.json").read_text())
        self.assertFalse(result["success"]); self.assertFalse(result["actual_open"])
        self.assertEqual(result["observed_metadata"], self.expected["device"])
        self.assertEqual(result["credentials"], creds)
        self.assertEqual(result["code"], "KVM_GROUP_MISSING")
        self.assertLess((self.root / "kvm-admission.json").stat().st_size, 4096)

    def test_drop_phase_matrix_has_permanent_ids_exact_groups_zero_caps_and_order(self):
        for phase in H.PHASES:
            selected = [78] if phase in H.KVM_PHASES else []
            expected = dict(uid=[1001] * 3, gid=[1001] * 3, groups=selected)
            calls = []
            with self.subTest(phase=phase), patch.object(H.os, "getuid", return_value=0), patch.object(H.os, "geteuid", return_value=0), \
                 patch.object(H.os, "setgroups", side_effect=lambda v: calls.append(("groups", v))), \
                 patch.object(H.os, "setresgid", side_effect=lambda *v: calls.append(("gid", v))), \
                 patch.object(H.os, "setresuid", side_effect=lambda *v: calls.append(("uid", v))), \
                 patch.object(H, "credentials", return_value=expected), \
                 patch.object(H, "capability_bits", return_value=dict(CapEff=0, CapPrm=0, CapAmb=0)):
                self.assertEqual(H.drop_credentials(1001, 1001, phase, self.expected), expected)
            self.assertEqual(calls, [("groups", selected), ("gid", (1001,1001,1001)), ("uid", (1001,1001,1001))])

    def test_wrong_credential_target_or_residual_groups_caps_rejected(self):
        with patch.object(H.os, "getuid", return_value=0), patch.object(H.os, "geteuid", return_value=0), \
             patch.object(H.os, "setgroups") as groups, patch.object(H.os, "setresgid"), patch.object(H.os, "setresuid"), \
             patch.object(H, "credentials", return_value=dict(uid=[1001]*3, gid=[1001]*3, groups=[0,78])), \
             patch.object(H, "capability_bits", return_value=dict(CapEff=1, CapPrm=0, CapAmb=0)):
            with self.assertRaisesRegex(ValueError, "CREDENTIAL_TARGET"): H.drop_credentials(1001, 78, "emulator", self.expected)
            groups.assert_not_called()
            with self.assertRaisesRegex(ValueError, "CREDENTIAL_DROP_MISMATCH"):
                H.drop_credentials(1001, 1001, "emulator", self.expected)
            with patch.object(H, "credentials", return_value=dict(uid=[1001]*3, gid=[1001]*3, groups=[78])):
                with self.assertRaisesRegex(ValueError, "CREDENTIAL_CAPABILITIES_REMAIN"):
                    H.drop_credentials(1001, 1001, "emulator", self.expected)

    def test_existing_build_environment_is_restored_only_after_nonroot_drop(self):
        env = dict(PATH="/inert", GITHUB_SHA="a" * 40, ImageOS="ubuntu24")
        with patch.dict(H.os.environ, {"SUDO_UID": "1001"}, clear=True), patch.object(H.os, "getuid", return_value=0), patch.object(H.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "PHASE_ENV_UNPRIVILEGED"):
                H.activate_environment(env)
            self.assertEqual(dict(H.os.environ), {"SUDO_UID": "1001"})
            with patch.object(H.os, "getuid", return_value=1001), patch.object(H.os, "geteuid", return_value=1001):
                H.activate_environment(env)
                self.assertEqual(dict(H.os.environ), env)
                with self.assertRaisesRegex(ValueError, "PHASE_ENV_BOUND"):
                    H.activate_environment(dict(PATH="x" * 8193))
                self.assertEqual(dict(H.os.environ), env)

    def test_actual_probe_call_contract_is_mocked_query_only_and_fd_closed(self):
        with patch.object(H.os, "getuid", return_value=1001), patch.object(H.os, "geteuid", return_value=1001), \
             patch.object(H, "kvm_snapshot", return_value=self.expected), patch.object(H.os, "open", return_value=23) as opened, \
             patch.object(H.os, "fstat", return_value=self.info), patch.object(H.os, "close") as close, \
             patch.object(H.fcntl, "ioctl", return_value=12) as query, patch.object(self.life, "spawn") as spawn:
            self.assertEqual(H.kvm_open_query(self.expected), dict(api_version=12, fd_closed=True, vm_created=False))
            opened.assert_called_once_with(self.kvm, H.os.O_RDWR | H.os.O_CLOEXEC | H.os.O_NOFOLLOW)
            query.assert_called_once_with(23, 0xAE00, 0)
            close.assert_called_once_with(23); spawn.assert_not_called()

    def test_probe_denied_bad_api_or_changed_fd_closes_owned_descriptor(self):
        for case, code, closed in [("denied", "KVM_OPEN_DENIED", False), ("ioctl", "KVM_API_UNAVAILABLE", True),
                                   ("version", "KVM_API_VERSION_UNSUPPORTED", True), ("changed", "KVM_DEVICE_CHANGED", True)]:
            with self.subTest(case=case), patch.object(H.os, "getuid", return_value=1001), patch.object(H.os, "geteuid", return_value=1001), \
                 patch.object(H, "kvm_snapshot", return_value=self.expected), patch.object(H.os, "open", return_value=23) as opened, \
                 patch.object(H.os, "fstat", return_value=self.info) as fstat, patch.object(H.os, "close") as close, \
                 patch.object(H.fcntl, "ioctl", return_value=12) as query:
                if case == "denied": opened.side_effect = PermissionError(errno.EACCES, "inert")
                elif case == "ioctl": query.side_effect = OSError(errno.ENOTTY, "inert")
                elif case == "version": query.return_value = 11
                elif case == "changed":
                    changed = SimpleNamespace(**vars(self.info)); changed.st_ino = 99; fstat.return_value = changed
                with self.assertRaisesRegex(ValueError, code): H.kvm_open_query(self.expected)
                self.assertEqual(close.call_count, int(closed))

    def test_deferred_cancel_after_probe_open_still_closes_fd(self):
        with patch.object(H.os, "getuid", return_value=1001), patch.object(H.os, "geteuid", return_value=1001), \
             patch.object(H, "kvm_snapshot", return_value=self.expected), patch.object(H.os, "open", return_value=23), \
             patch.object(H.os, "close") as close, patch.object(self.life, "check", side_effect=[None, H.Interrupted("HOSTED_INTERRUPTED")]), \
             patch.object(H.fcntl, "ioctl") as query:
            with self.assertRaisesRegex(H.Interrupted, "HOSTED_INTERRUPTED"): H.kvm_open_query(self.expected)
            close.assert_called_once_with(23); query.assert_not_called()

    def test_private_bootstrap_input_is_bounded_owned_nofollow_json(self):
        path = self.root / "baseline.json"
        fd = H.os.open(path, H.os.O_CREAT | H.os.O_EXCL | H.os.O_WRONLY, 0o600)
        H.os.write(fd, b'{"inert":true}'); H.os.close(fd)
        directory = H.os.open(self.root, H.os.O_RDONLY | H.os.O_DIRECTORY)
        try:
            self.assertEqual(H.private_document(directory, "baseline.json", self.root.stat()), {"inert": True})
            path.unlink(); path.symlink_to(self.root / "not-admitted")
            with self.assertRaisesRegex(ValueError, "PHASE_INPUT_OPEN"):
                H.private_document(directory, "baseline.json", self.root.stat())
            path.unlink()
            oversized = H.os.open(path, H.os.O_CREAT | H.os.O_EXCL | H.os.O_WRONLY, 0o600)
            H.os.write(oversized, b"x" * 16385); H.os.close(oversized)
            with self.assertRaisesRegex(ValueError, "PHASE_INPUT_OWNER_BOUND"):
                H.private_document(directory, "baseline.json", self.root.stat())
        finally: H.os.close(directory)

    def test_bootstrap_input_allowlist_hardlinks_and_nonobject_json_reject(self):
        path = self.root / "source.json"
        fd = H.os.open(path, H.os.O_CREAT | H.os.O_EXCL | H.os.O_WRONLY, 0o600)
        H.os.write(fd, b'[]'); H.os.close(fd)
        directory = H.os.open(self.root, H.os.O_RDONLY | H.os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(ValueError, "PHASE_INPUT_ALLOWLIST"):
                H.private_document(directory, "arbitrary.json", self.root.stat())
            with self.assertRaisesRegex(ValueError, "PHASE_INPUT_SHAPE"):
                H.private_document(directory, "source.json", self.root.stat())
            H.os.link(path, self.root / "second-name")
            with self.assertRaisesRegex(ValueError, "PHASE_INPUT_OWNER_BOUND"):
                H.private_document(directory, "source.json", self.root.stat())
        finally: H.os.close(directory)

    def test_mocked_fifo_input_opens_nonblocking_and_rejects_before_read(self):
        owner = SimpleNamespace(st_uid=1001, st_gid=1001)
        fifo = SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, st_uid=1001, st_gid=1001,
                               st_nlink=1, st_size=0)
        with patch.object(H.os, "open", return_value=23) as opened, \
             patch.object(H.os, "fstat", return_value=fifo), patch.object(H.os, "read") as read, \
             patch.object(H.os, "close") as close:
            with self.assertRaisesRegex(ValueError, "PHASE_INPUT_OWNER_BOUND"):
                H.private_document(22, "baseline.json", owner)
            opened.assert_called_once_with("baseline.json",
                H.os.O_RDONLY | H.os.O_CLOEXEC | H.os.O_NOFOLLOW | H.os.O_NONBLOCK, dir_fd=22)
            read.assert_not_called(); close.assert_called_once_with(23)

    def test_mocked_fifo_receipt_opens_nonblocking_and_rejects_before_work(self):
        owner = SimpleNamespace(st_dev=1, st_ino=2, st_rdev=0, st_mode=stat.S_IFDIR | 0o700,
            st_uid=1001, st_gid=1001, st_nlink=1, st_mtime_ns=3, st_ctime_ns=4)
        fifo = SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, st_uid=1001, st_gid=1001,
                               st_nlink=1, st_size=0)
        state = unittest.mock.Mock()
        state.lstat.return_value = owner; state.resolve.return_value = state
        with patch.dict(H.os.environ, dict(SUDO_UID="1001", SUDO_GID="1001")), \
             patch.object(H.os, "getuid", return_value=0), patch.object(H.os, "geteuid", return_value=0), \
             patch.object(H.os, "getpid", return_value=1), patch.object(H.os, "open", side_effect=[22, 23]) as opened, \
             patch.object(H.os, "fstat", side_effect=[owner, fifo]), patch.object(H.os, "close") as close, \
             patch.object(H.os, "write") as write, patch.object(H, "private_document") as document, \
             patch.object(H, "drop_credentials") as drop, patch.object(H, "acquire_build") as build:
            with self.assertRaisesRegex(ValueError, "PHASE_RECEIPT_OWNER"):
                H.phase_child(state, "public-build")
            self.assertEqual(opened.call_args_list, [
                unittest.mock.call(state, H.os.O_RDONLY | H.os.O_DIRECTORY | H.os.O_CLOEXEC | H.os.O_NOFOLLOW),
                unittest.mock.call("phase-public-build.json",
                    H.os.O_WRONLY | H.os.O_CLOEXEC | H.os.O_NOFOLLOW | H.os.O_NONBLOCK, dir_fd=22)])
            self.assertEqual(close.call_args_list, [unittest.mock.call(23), unittest.mock.call(22)])
            write.assert_not_called(); document.assert_not_called(); drop.assert_not_called(); build.assert_not_called()

    def test_namespace_profiles_preserve_public_network_only_for_build(self):
        for phase in H.PHASES:
            command = H.namespace_command("--inside-" + phase, self.root, 10)
            self.assertEqual("--net" in command, phase != "public-build")
            self.assertIn("--pid", command); self.assertIn("--mount-proc", command)
        with self.assertRaisesRegex(ValueError, "NAMESPACE_MODE"):
            H.namespace_command("--arbitrary", self.root, 10)

    def test_outer_orchestration_probes_before_acquisition_and_separates_builds(self):
        env = dict(RUNNER_TEMP=str(self.root), GITHUB_RUN_ID="1", GITHUB_RUN_ATTEMPT="1", GITHUB_SHA="a"*40)
        observed = []
        before = dict(kvm=self.expected)
        with patch.dict(H.os.environ, env, clear=True), patch.object(H.sys, "argv", ["hosted.py", "--reviewed-hosted-fixture"]), \
             patch.object(H, "guards"), patch.object(H, "environment", return_value=env), \
             patch.object(H, "kvm_admission", return_value=self.expected), patch.object(H, "baseline", return_value=before), \
             patch.object(H, "run_phase", side_effect=lambda state, phase, *_: observed.append(phase)), \
             patch.object(H, "cancellation_control", side_effect=lambda *_: observed.append("cancellation")), \
             patch.object(H, "acquire_build") as build, patch.object(H, "finalize") as finalizer:
            H.main()
            build.assert_not_called(); finalizer.assert_called_once()
        self.assertEqual(observed, ["kvm-probe", "cancellation", "public-build", "offline-build", "emulator"])


if __name__ == "__main__":
    unittest.main()
