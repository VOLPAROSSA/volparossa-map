# SPDX-License-Identifier: GPL-3.0-only
"""Inert Python/Git/archive tests only; none executes Rust/JNI/Android or a controller."""
import copy
import ast
import errno
from contextlib import ExitStack
from types import SimpleNamespace
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
import prepare as P
import tool_inputs as T
import hosted as H
import source_gate as S


def inert_ipv6_route(**changes):
    fields = dict(destination="20010db8000000000000000000000000", destination_prefix="40",
        source="00000000000000000000000000000000", source_prefix="00",
        gateway="00000000000000000000000000000000", metric="00000001", reference="00000001",
        use="00000000", flags="00000001", device="lo")
    fields.update(changes)
    return (" ".join(fields[key] for key in ("destination", "destination_prefix", "source", "source_prefix",
        "gateway", "metric", "reference", "use", "flags")) + " " + fields["device"].rjust(8) + "\n").encode()


def inert_ipv6_renderer():
    return dict(schema=1, profile=H.IPV6_ROUTE_RENDERER_PROFILE,
        kernel=dict(sysname="Linux", release="6.8.0-1001-azure", version="#1-Ubuntu SMP inert", machine="x86_64"))


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

    def test_test_manifest_allows_only_exact_negative_permission_marker(self):
        module = P.HERE / "module"
        source = module / "src/androidTest/AndroidManifest.xml"
        S.manifest_boundary(source, module)
        marker = source.read_text()
        mutations = (marker.replace(' tools:node="remove"', ""),
            marker.replace('node="remove"', 'node="merge"'),
            marker.replace('node="remove"', 'node="removeAll"'),
            marker.replace("REORDER_TASKS", "ACCESS_FINE_LOCATION"),
            marker.replace('tools:node="remove"', 'tools:node="remove" android:maxSdkVersion="21"'),
            marker.replace("</manifest>", "<application /></manifest>"),
            marker.replace("</manifest>", '<uses-permission android:name="android.permission.INTERNET" /></manifest>'),
            marker.replace(" />", "><application /></uses-permission>"),
            marker.replace("<manifest ", '<manifest package="unexpected" '),
            marker.replace("<uses-permission", "PRIVATE<uses-permission"),
            marker.replace("</manifest>", "PRIVATE</manifest>"))
        for raw in mutations:
            with self.subTest(raw=raw), patch.object(Path, "read_text", return_value=raw):
                with self.assertRaisesRegex(ValueError, "TEST_PERMISSION_REMOVAL_ONLY"):
                    S.manifest_boundary(source, module)

    def test_permission_removal_marker_is_not_allowed_in_main_or_debug_manifest(self):
        module = P.HERE / "module"
        raw = (module / "src/androidTest/AndroidManifest.xml").read_text()
        for variant in ("main", "debug", "other"):
            with patch.object(Path, "read_text", return_value=raw):
                with self.assertRaisesRegex(ValueError, "NO_DECLARED_PERMISSION"):
                    S.manifest_boundary(module / "src" / variant / "AndroidManifest.xml", module)

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
            ipv6 = H.ipv6_route_snapshot(inert_ipv6_route())
            baseline = dict(firewall=malformed, ipv6_routes=ipv6["raw"], ipv6_route_metadata=ipv6["metadata"],
                            ipv6_renderer=inert_ipv6_renderer())
            with self.assertRaisesRegex(ValueError, "FIREWALL_METADATA_SHAPE"):
                H.baseline_unchanged(baseline, copy.deepcopy(baseline))

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
             patch.object(H.Path, "read_bytes", return_value=b"inert"), \
             patch.object(H, "read_ipv6_route_snapshot", return_value=H.ipv6_route_snapshot(inert_ipv6_route())), \
             patch.object(H, "ipv6_renderer_snapshot", return_value=inert_ipv6_renderer()), \
             patch.object(H.os, "readlink", return_value="net:[123]"), \
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
            ipv6_routes=H.ipv6_route_snapshot(inert_ipv6_route())["raw"],
            ipv6_route_metadata=H.ipv6_route_snapshot(inert_ipv6_route())["metadata"], ipv6_renderer=inert_ipv6_renderer(),
            resolver=dict(bytes=970, sha256="a" * 64),
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
        self.assertEqual(diagnostic, dict(schema=3, before=self.before, after=self.before,
            changed_components=[], capture_error=None, diagnostic_error=None,
            ipv6_route_comparison=dict(classification="UNCHANGED", raw_changed=False, metadata_valid=True,
                non_refcount_changed=False, reference_metadata_changed=False, row_count_changed=False),
            firewall_comparison=dict(classification="UNCHANGED", raw_changed=False, config_changed=False, counters_changed=False)))
        self.assertEqual(json.loads((evidence / "result.json").read_text()), result)

    def test_every_component_difference_fails_exact_comparison_and_retains_both(self):
        for component in (key for key in self.before if key != "ipv6_route_metadata"):
            with self.subTest(component=component):
                after = copy.deepcopy(self.before)
                if component == "netns": after[component] = "net:[124]"
                elif component == "credentials": after[component]["groups"] = [1001]
                elif component == "kvm": after[component]["device"]["ctime_ns"] += 1
                elif component == "ipv6_renderer": after[component]["kernel"]["version"] = "#2-Ubuntu SMP inert"
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
        self.assertEqual(result["baseline_comparison"], dict(schema=3, before=self.before, after=None,
            changed_components=None, capture_error="BASELINE_CAPTURE_FAILED", diagnostic_error=None,
            firewall_comparison=None, ipv6_route_comparison=None))
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
            with self.assertRaisesRegex(RuntimeError, "CLEANUP_UNCONFIRMED"):
                H.finalize(state, evidence, malformed, True, None)
        result = json.loads((evidence / "result.json").read_text())
        self.assertFalse(result["success"]); self.assertFalse(result["host_routes_dns_firewall_unchanged"])
        self.assertFalse(result["host_configuration_checkpoint_accepted"])
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


class ReapingShimTests(unittest.TestCase):
    """Inert syscall/child models only; no fork, namespace, signal or workload."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.life = H.Lifetime()
        self.enterContext(patch.object(H, "ACTIVE", self.life))
        self.enterContext(patch.object(H.signal, "setitimer"))
        self.enterContext(patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)))

    def child(self, pid=4321):
        child = unittest.mock.Mock(pid=pid, returncode=None)
        def wait(**kwargs): child.returncode = 31; return 31
        child.wait.side_effect = wait
        self.life.owned.append(child); return child

    def test_private_pid1_requires_exact_nonmissing_namespace_binding_and_private_proc(self):
        self.assertFalse(self.life.private_pid1)
        for outer, current, process, proc_self in ((None, "pid:[2]", 1, "1"), ("pid:[0]", "pid:[2]", 1, "1"),
                ("pid:[1]", "pid:[1]", 1, "1"), ("pid:[1]", "invalid", 1, "1"),
                ("pid:[1]", "pid:[2]", 2, "1"), ("pid:[1]", "pid:[2]", 1, "123")):
            with patch.object(H.os, "getpid", return_value=process), patch.object(H.os, "readlink", side_effect=lambda p: current if p.endswith("/pid") else proc_self):
                with self.assertRaises(ValueError): self.life.enable_private_pid1(outer)
                self.assertFalse(self.life.private_pid1)
        with patch.object(H.os, "getpid", return_value=1), patch.object(H.os, "readlink", side_effect=["pid:[2]", "1"]) as reads:
            self.life.enable_private_pid1("pid:[1]")
        self.assertTrue(self.life.private_pid1)
        self.assertEqual([c.args for c in reads.call_args_list], [("/proc/self/ns/pid",), ("/proc/self",)])

    def test_default_parent_drain_never_uses_group_waitpid(self):
        child = self.child()
        with patch.object(H.os, "killpg", side_effect=[None, ProcessLookupError()]), patch.object(H.os, "waitpid") as reap:
            self.life.drain(child)
        reap.assert_not_called(); self.assertEqual(child.returncode, 31); self.assertEqual(self.life.owned, [])

    def test_reaps_only_owned_group_after_leader_wait_without_consuming_other_status(self):
        target, other = self.child(), self.child(8765); self.life.private_pid1 = True; events = []
        def wait(**kwargs): events.append("leader_wait"); target.returncode = 31; return 31
        target.wait.side_effect = wait
        answers = iter([(5001, 42 << 8), (5002, 9), (0, 0)])
        def reap(*args):
            self.assertEqual(target.returncode, 31); events.append("reap"); return next(answers)
        with patch.object(H.os, "killpg", side_effect=[None, ProcessLookupError()]), patch.object(H.os, "waitpid", side_effect=reap) as calls:
            self.life.drain(target)
        self.assertEqual(events, ["leader_wait", "reap", "reap", "reap"])
        self.assertTrue(all(c.args == (-4321, H.os.WNOHANG) for c in calls.call_args_list))
        self.assertEqual(target.returncode, 31); self.assertEqual(self.life.owned, [other])
        self.assertIsNone(other.returncode); other.wait.assert_not_called()

    def test_echild_or_running_child_never_substitutes_for_group_disappearance(self):
        for error in (ChildProcessError(), None):
            child = self.child(); self.life.private_pid1 = True; now = [0.0]
            def sleep(seconds): now[0] += seconds
            with patch.object(H.time, "monotonic", side_effect=lambda: now[0]), patch.object(H.time, "sleep", side_effect=sleep), \
                 patch.object(H.os, "killpg") as kill, patch.object(H.os, "waitpid", side_effect=error, return_value=(0, 0)):
                with self.assertRaisesRegex(ValueError, "PROCESS_GROUP_DRAIN"): self.life.drain(child)
            self.assertIn(child, self.life.owned); self.assertEqual(child.returncode, 31)
            self.assertTrue(all(call.args[0] == child.pid for call in kill.call_args_list))
            self.assertLessEqual(now[0], 0.8); self.life.owned.clear()

    def test_delayed_adoption_is_reaped_on_existing_next_iteration_with_same_deadline(self):
        child = self.child(); self.life.private_pid1 = True; now = [0.0]; reaped = [False]
        def kill(group, number):
            self.assertEqual(group, child.pid)
            if number == 0 and reaped[0]: raise ProcessLookupError()
        def reap(group, flags):
            if now[0] == 0: return (0, 0)
            if not reaped[0]: reaped[0] = True; return (5001, 9)
            raise ChildProcessError()
        with patch.object(H.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(H.time, "sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)), \
             patch.object(H.os, "killpg", side_effect=kill) as kills, patch.object(H.os, "waitpid", side_effect=reap):
            self.life.drain(child)
        self.assertEqual(now[0], 0.05); self.assertEqual(self.life.owned, [])
        self.assertIn(unittest.mock.call(child.pid, H.signal.SIGKILL), kills.call_args_list)

    def test_total_nonblocking_reap_calls_are_capped_inside_old_budget(self):
        child = self.child(); self.life.private_pid1 = True
        with patch.object(H.time, "monotonic", return_value=0), patch.object(H.os, "killpg"), \
             patch.object(H.os, "waitpid", return_value=(5001, 0)) as reap:
            with self.assertRaisesRegex(ValueError, "PROCESS_GROUP_REAP_BOUND"): self.life.drain(child)
        self.assertEqual(reap.call_count, 256); self.assertIn(child, self.life.owned)

    def test_invalid_or_unregistered_group_never_waits_or_signals(self):
        self.life.private_pid1 = True
        for group in (1, 0, -1, True, "4321", 2**31):
            child = self.child(group)
            with patch.object(H.os, "killpg") as kill, patch.object(H.os, "waitpid") as reap:
                with self.assertRaisesRegex(ValueError, "OWNED_REAP_GROUP"): self.life.drain(child)
                kill.assert_not_called(); reap.assert_not_called()
            self.life.owned.clear()
        child = unittest.mock.Mock(pid=4321)
        with patch.object(H.os, "killpg") as kill:
            with self.assertRaisesRegex(ValueError, "OWNED_REAP_GROUP"): self.life.drain(child)
            kill.assert_not_called()

    def test_reap_error_or_cleanup_deadline_keeps_ownership_and_original_failure(self):
        for failure in (OSError(errno.EIO, "inert"), H.CleanupDeadline("CLEANUP_DEADLINE"), H.Interrupted("HOSTED_INTERRUPTED")):
            child = self.child(); self.life.private_pid1 = True
            with patch.object(H.os, "killpg"), patch.object(H.os, "waitpid", side_effect=failure):
                with self.assertRaises(type(failure)) as caught: self.life.drain(child)
                self.assertIs(caught.exception, failure)
            self.assertIn(child, self.life.owned); self.life.owned.clear()
        with patch.object(self.life, "drain_all", side_effect=H.CleanupDeadline("CLEANUP_DEADLINE")), \
             patch.object(H.signal, "signal"), patch.object(H.signal, "getsignal", return_value=H.signal.SIG_DFL):
            with self.assertRaisesRegex(ValueError, "ORIGINAL_FAILURE"):
                with self.life: raise ValueError("ORIGINAL_FAILURE")

    def control_bootstrap(self):
        owner = self.root.stat(); source = "a" * 40
        values = {"source.json": dict(map=source, parent_pidns="pid:[1]"),
            "baseline.json": dict(netns="net:[host]", credentials=dict(uid=[owner.st_uid] * 3, gid=[owner.st_gid] * 3), kvm=dict(kvm_gid=78)),
            "environment.json": dict(GITHUB_SHA=source)}
        for name, value in values.items(): self.write_json(self.root / name, value)
        self.enterContext(patch.dict(H.os.environ, dict(SUDO_UID=str(owner.st_uid), SUDO_GID=str(owner.st_gid))))
        self.enterContext(patch.object(H.os, "getuid", return_value=0)); self.enterContext(patch.object(H.os, "geteuid", return_value=0))
        self.enterContext(patch.object(H.os, "getpid", return_value=1))
        self.enterContext(patch.object(H.os, "readlink", side_effect=lambda p: {"/proc/self/ns/pid": "pid:[2]", "/proc/self": "1", "/proc/self/ns/net": "net:[private]"}[p]))
        self.enterContext(patch.object(H.socket, "if_nameindex", return_value=[(1, "lo")]))
        self.enterContext(patch.object(H, "drop_credentials")); self.enterContext(patch.object(H, "activate_environment")); self.enterContext(patch.object(H, "guards"))
        return source

    def write_json(self, path, value):
        fd = H.os.open(path, H.os.O_WRONLY | H.os.O_CREAT | H.os.O_EXCL, 0o600)
        try: H.os.write(fd, json.dumps(value).encode())
        finally: H.os.close(fd)

    def test_control_handshake_failure_still_enables_verified_reaper_and_removes_owned_case(self):
        self.control_bootstrap(); now = [0.0]
        def spawn(*args, **kwargs): return self.child(100 + len(self.life.owned))
        def clock(): now[0] += 0.1; return now[0]
        with patch.object(self.life, "spawn", side_effect=spawn), patch.object(self.life, "drain_all", side_effect=self.life.owned.clear), \
             patch.object(H.time, "monotonic", side_effect=clock), patch.object(H.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "REAPING_HANDSHAKE_BOUND"): H.inside_reaping_control(self.root)
        result = json.loads((self.root / "group-reaping-result.json").read_text())
        self.assertFalse(result["success"]); self.assertTrue(self.life.private_pid1)
        self.assertEqual(result["primary_failure"], "REAPING_HANDSHAKE_BOUND")
        self.assertTrue(result["registered_groups_drained"]); self.assertTrue(result["private_state_removed"])
        self.assertFalse((self.root / "group-reaping-private").exists())

    def test_control_negative_positive_and_other_group_status_require_independent_facts(self):
        self.control_bootstrap(); groups = {}; removed = set(); calls = []
        def spawn(argv, **kwargs):
            role = argv[-1]; group = 100 + len(groups); child = self.child(group); groups[role] = child
            case = self.root / "group-reaping-private"
            if role in ("negative", "positive"):
                self.write_json(case / (role + "-family.json"), dict(leader=group, pgid=group, exited=group * 10 + 1, live=group * 10 + 2))
                for kind, child_pid in (("exited", group * 10 + 1), ("live", group * 10 + 2)):
                    self.write_json(case / (role + "-" + kind + ".json"), dict(pid=child_pid, pgid=group, adopted=True))
            else:
                self.write_json(case / (role + ".json"), dict(pid=group, pgid=group))
                if role == "exit-sentinel":
                    def wait(**kwargs): child.returncode = 23; return 23
                    child.wait.side_effect = wait
                else: child.poll.return_value = None
            return child
        def waitid(kind, pid, flags):
            self.assertEqual(kind, H.os.P_PID); self.assertEqual(flags, H.os.WEXITED | H.os.WNOHANG | H.os.WNOWAIT)
            status = 23 if pid == groups["exit-sentinel"].pid else 42 if pid % 10 == 1 and pid > 1000 else None
            return None if status is None else SimpleNamespace(si_pid=pid, si_code=H.os.CLD_EXITED, si_status=status)
        def drain(child):
            calls.append((child.pid, self.life.private_pid1))
            if child is groups.get("negative") and not self.life.private_pid1: raise ValueError("PROCESS_GROUP_DRAIN")
            removed.add(child.pid); self.life.owned.remove(child)
        def kill(group, number):
            self.assertEqual(number, 0)
            if group in removed: raise ProcessLookupError()
        with patch.object(self.life, "spawn", side_effect=spawn), patch.object(self.life, "drain", side_effect=drain), \
             patch.object(H.os, "waitid", side_effect=waitid), patch.object(H.os, "killpg", side_effect=kill):
            H.inside_reaping_control(self.root)
        result = json.loads((self.root / "group-reaping-result.json").read_text())
        self.assertTrue(result["success"]); self.assertEqual(self.life.owned, [])
        self.assertEqual(calls[:3], [(groups["negative"].pid, False), (groups["negative"].pid, True), (groups["positive"].pid, True)])
        self.assertTrue(result["sentinel_live_preserved"]); self.assertTrue(result["sentinel_exit_status_preserved"])
        self.assertNotIn("pid", json.dumps(result).replace("private_pid1_verified", ""))

    def test_parent_control_requires_exact_facts_and_exact_post_control_baseline(self):
        facts = ("success", "private_pid1_verified", "negative_failed_closed", "negative_group_gone", "positive_group_gone",
            "adopted_exited_verified", "adopted_live_verified", "leader_status_preserved", "sentinel_exit_status_preserved",
            "sentinel_live_preserved", "registered_groups_drained", "private_state_removed")
        source = "a" * 40
        result = dict(schema=1, source=source, primary_failure=None, cleanup_error=None, **dict.fromkeys(facts, True))
        self.write_json(self.root / "group-reaping-result.json", result)
        before, after = dict(fixed="before"), dict(fixed="after")
        with patch.object(H, "guards"), patch.object(H, "run") as run, patch.object(H, "baseline", return_value=after) as capture, \
             patch.object(H, "baseline_unchanged", return_value=False) as equal, patch.object(H, "baseline_diagnostic", return_value={"closed": True}):
            with self.assertRaisesRegex(ValueError, "REAPING_CONTROL_BASELINE"):
                H.group_reaping_control(self.root, dict(GITHUB_SHA=source), before)
        capture.assert_called_once_with(); equal.assert_called_once_with(before, after)
        self.assertEqual(run.call_args.args[0], "group-reaping-namespace"); self.assertEqual(run.call_args.kwargs, dict(seconds=17))
        self.assertIn("--net", run.call_args.args[1]); self.assertIn("--pid", run.call_args.args[1])
        self.assertFalse(json.loads((self.root / "group-reaping-control.json").read_text())["success"])
        with patch.object(H, "guards"), patch.object(H, "run"), patch.object(H, "baseline", return_value=after), \
             patch.object(H, "baseline_unchanged", return_value=True), patch.object(H, "baseline_diagnostic", return_value={"closed": True}):
            H.group_reaping_control(self.root, dict(GITHUB_SHA=source), before)
        self.assertTrue(json.loads((self.root / "group-reaping-control.json").read_text())["success"])
        for key in facts:
            result[key] = False
            with patch.object(H, "guards"), patch.object(H, "run"), patch.object(H, "private_document", return_value=result), patch.object(H, "baseline") as capture:
                with self.assertRaisesRegex(ValueError, "REAPING_CONTROL_RESULT"):
                    H.group_reaping_control(self.root, dict(GITHUB_SHA=source), before)
                capture.assert_not_called()
            result[key] = True

    def test_control_entry_rejects_local_context_before_any_process_or_namespace(self):
        with patch.object(H.os, "getpid", return_value=4321), patch.object(self.life, "spawn") as spawn:
            with self.assertRaises(ValueError): H.inside_reaping_control(self.root)
            spawn.assert_not_called()
        with patch.object(H, "run") as run, patch.object(H, "baseline") as baseline:
            with self.assertRaises(ValueError): H.group_reaping_control(self.root, {}, {})
            run.assert_not_called(); baseline.assert_not_called()


class InstrumentationObservationTests(unittest.TestCase):
    """Existing-output protocol fixtures only; no runner or Android execution."""
    methods = ("actualRustJniGoldensAndClosedErrors", "strictSummaryRejectsUnknownOrContradictoryFields",
        "controllerDropsLateRevokedExpiredBackgroundResultsAndJoins", "controllerRechecksAuthorityBeforeUiPublicationAndExpiryClears",
        "obsoleteGenerationNeverPublishesAfterQueuedCompletion", "publicationWindowExpiryDropsPostedResult",
        "backgroundDuringFreshInflightWorkDropsLateResultAndResumeStaysDisabled", "actualDebugActivityDisplayRevokeBackgroundAndPermissionBoundary")

    def record(self, name, current, code, stack=None):
        values = [("id", "AndroidJUnitRunner"), ("class", "org.volparossa.map.dps.QualityInstrumentedTest"),
                  ("test", name), ("numtests", "8"), ("current", str(current)), ("stream", ".")]
        if stack is not None: values.append(("stack", stack))
        return ("".join("INSTRUMENTATION_STATUS: " + key + "=" + value + "\n" for key, value in values)
                + "INSTRUMENTATION_STATUS_CODE: " + str(code) + "\n").encode()

    def transcript(self, code=0, stack=None):
        records = b"".join(self.record(name, i, 1) + self.record(name, i, code if i == 1 else 0, stack if i == 1 else None)
                           for i, name in enumerate(self.methods, 1))
        return records + b"INSTRUMENTATION_RESULT: stream=\nOK (8 tests)\nINSTRUMENTATION_CODE: -1\n"

    def test_eight_fixed_methods_and_lf_crlf_status_inventory_are_distinct_from_acceptance(self):
        for output in (self.transcript(), self.transcript().replace(b"\n", b"\r\n")):
            observed = H.instrumentation_observation(output)
            self.assertEqual(tuple(observed["tests"]), self.methods)
            self.assertTrue(observed["inventory_complete"]); self.assertTrue(observed["all_eight_passed"])
            self.assertEqual(observed["counts"]["PASSED"], 8)
            self.assertTrue(all(row == dict(status="PASSED", failure_family=None) for row in observed["tests"].values()))
        summary_only = H.instrumentation_observation(b"OK (8 tests)\n")
        self.assertTrue(summary_only["acceptance_tokens"]["ok_eight"])
        self.assertFalse(summary_only["inventory_complete"]); self.assertFalse(summary_only["all_eight_passed"])
        failed_summary = H.instrumentation_observation(self.transcript().replace(b"OK (8 tests)", b"FAILURES!!!"))
        self.assertTrue(failed_summary["inventory_complete"]); self.assertFalse(failed_summary["all_eight_passed"])

    def test_failure_statuses_and_exception_families_are_closed_and_private_text_is_omitted(self):
        cases = ((-1, "ERROR", "java.lang.UnsatisfiedLinkError", "JNI_LINK"),
                 (-2, "FAILED", "java.lang.AssertionError", "ASSERTION"),
                 (-4, "ASSUMPTION_FAILED", "java.lang.SecurityException", "SECURITY"),
                 (-2, "FAILED", "private.UnexpectedException", "UNKNOWN"), (-3, "IGNORED", None, None))
        for code, status, exception, family in cases:
            output = self.transcript(code, None if exception is None else exception + ": SECRET_PATH /private/name\n\tat private.Stack.file")
            observed = H.instrumentation_observation(output)
            self.assertEqual(observed["tests"][self.methods[0]], dict(status=status, failure_family=family))
            self.assertFalse(observed["all_eight_passed"])
            encoded = json.dumps(observed)
            for secret in ("SECRET", "/private", "private.", "Stack.file"): self.assertNotIn(secret, encoded)
            self.assertLessEqual(len(encoded.encode()), 16384)

    def test_permission_assertion_is_exact_method_message_and_failed_status_only(self):
        name = self.methods[-1]
        message = "java.lang.AssertionError: fixture declares no permissions"
        good = self.transcript()
        output = good.replace(self.record(name, 8, 0),
            self.record(name, 8, -2, message + "\n\tat PRIVATE_STACK /private/path"))
        for raw in (output, output.replace(b"\n", b"\r\n")):
            observed = H.instrumentation_observation(raw)
            self.assertEqual(observed["tests"][name], dict(status="FAILED", failure_family="PERMISSION_BOUNDARY"))
            self.assertTrue(observed["inventory_complete"]); self.assertFalse(observed["all_eight_passed"])
            self.assertEqual(observed["counts"]["PASSED"], 7)
            for private in (message, "PRIVATE", "/private", "STACK"): self.assertNotIn(private, json.dumps(observed))
            self.assertLessEqual(len(json.dumps(observed).encode()), 16384)

    def test_permission_assertion_near_messages_other_methods_and_statuses_stay_generic(self):
        name = self.methods[-1]
        message = "java.lang.AssertionError: fixture declares no permissions"
        messages = (message.replace("fixture", "Fixture"), message + " extra", message + " ", message.replace("permissions", "permission"),
            "java.lang.AssertionError: other\n" + message,
            "junit.framework.AssertionFailedError: fixture declares no permissions")
        for stack in messages:
            output = self.transcript().replace(self.record(name, 8, 0), self.record(name, 8, -2, stack))
            self.assertEqual(H.instrumentation_observation(output)["tests"][name]["failure_family"], "ASSERTION")
        for other in self.methods[:-1]:
            i = self.methods.index(other) + 1
            output = self.transcript().replace(self.record(other, i, 0), self.record(other, i, -2, message))
            self.assertEqual(H.instrumentation_observation(output)["tests"][other]["failure_family"], "ASSERTION")
        for code in (-1, -4):
            output = self.transcript().replace(self.record(name, 8, 0), self.record(name, 8, code, message))
            self.assertEqual(H.instrumentation_observation(output)["tests"][name]["failure_family"], "ASSERTION")

    def test_unknown_duplicate_mismatched_or_partial_protocol_never_becomes_complete(self):
        good = self.transcript()
        mutations = (good.replace(self.methods[0].encode(), b"PRIVATE_METHOD"),
            good.replace(b"org.volparossa.map.dps.QualityInstrumentedTest", b"PRIVATE_CLASS"),
            good.replace(b"numtests=8", b"numtests=9"), good.replace(b"STATUS_CODE: 0", b"STATUS_CODE: 7", 1),
            good.replace(b"STATUS: current=1", b"STATUS: current=2", 1),
            self.record(self.methods[0], 1, 1) + good,
            good + b"INSTRUMENTATION_CODE: -1\n", good + b"INSTRUMENTATION_STATUS: privateKey=SECRET\n",
            good.replace(b"INSTRUMENTATION_RESULT: stream=", b"INSTRUMENTATION_RESULT: stream=\nINSTRUMENTATION_RESULT: stream="),
            b"INSTRUMENTATION_CODE: -1\n" + good.removesuffix(b"INSTRUMENTATION_CODE: -1\n"),
            good.replace(b"INSTRUMENTATION_STATUS: id=", b"INSTRUMENTATION_STATUS: id=AndroidJUnitRunner\nINSTRUMENTATION_STATUS: id=", 1),
            good.replace(b"INSTRUMENTATION_STATUS_CODE: 0\n", b"", 1),
            good.replace(b"INSTRUMENTATION_CODE: -1", b"INSTRUMENTATION_CODE: 0"), good[:-1])
        for output in mutations:
            with self.subTest(output_bytes=len(output)):
                observed = H.instrumentation_observation(output)
                self.assertFalse(observed["inventory_complete"]); self.assertFalse(observed["all_eight_passed"])
                self.assertTrue(observed["unexpected_records"] or observed["duplicate_records"] or observed["parse_error"])
                self.assertNotIn("PRIVATE", json.dumps(observed)); self.assertNotIn("SECRET", json.dumps(observed))

    def test_byte_line_and_line_length_bounds_stop_before_protocol_decode(self):
        cases = ((None, "OUTPUT_TYPE_OR_BOUND"), (b"x" * (256 * 1024 + 1), "OUTPUT_TYPE_OR_BOUND"),
                 (b"\n" * 4097, "LINE_COUNT_BOUND"), (b"x" * 8193 + b"\n", "LINE_BYTES_BOUND"))
        for output, code in cases:
            observed = H.instrumentation_observation(output)
            self.assertEqual(observed["parse_error"], code); self.assertFalse(observed["all_eight_passed"])
            self.assertTrue(all(row["status"] == "NOT_OBSERVED" for row in observed["tests"].values()))

    def test_result_is_required_after_all_status_records_and_before_final_code(self):
        good = self.transcript(); result_line = b"INSTRUMENTATION_RESULT: stream=\n"
        missing = good.replace(result_line, b"")
        first = self.record(self.methods[0], 1, 1)
        cases = (missing, result_line + missing, first + result_line + missing[len(first):],
                 good.replace(b"INSTRUMENTATION_STATUS_CODE: 1\n",
                              b"INSTRUMENTATION_CODE: -1\nINSTRUMENTATION_STATUS_CODE: 1\n", 1))
        for output in cases:
            with self.subTest(output_bytes=len(output)):
                observed = H.instrumentation_observation(output)
                self.assertTrue(observed["unexpected_records"])
                self.assertFalse(observed["inventory_complete"]); self.assertFalse(observed["all_eight_passed"])
                self.assertEqual(observed["counts"]["PASSED"], 8)
                self.assertEqual(observed["acceptance_tokens"], dict(ok_eight=True, failures=False, instrumentation_failed=False))

    def test_retention_bound_failures_and_cancellation_keep_original_authority(self):
        life = H.Lifetime()
        with tempfile.TemporaryDirectory() as directory, patch.object(H, "ACTIVE", life):
            state = Path(directory); source = "a" * 40
            H.retain_instrumentation_observation(state, source, self.transcript())
            receipt = state / "instrumentation-result-diagnostic.json"
            self.assertEqual(json.loads(receipt.read_text())["source"], source)
            self.assertLessEqual(receipt.stat().st_size, 16384)
            for target, failure in (("instrumentation_observation", ValueError("PRIVATE_DECODE")), ("save", OSError("PRIVATE_SAVE"))):
                with patch.object(H, target, side_effect=failure):
                    H.retain_instrumentation_observation(state, source, b"FAILURES\n")
                    with self.assertRaisesRegex(ValueError, "ACTUAL_INSTRUMENTATION_RESULT"):
                        H.require(False, "ACTUAL_INSTRUMENTATION_RESULT")
            with patch.object(H, "instrumentation_observation", return_value={"private": "x" * 17000}), patch.object(H, "save") as save:
                H.retain_instrumentation_observation(state, source, b""); save.assert_not_called()
            for failure in (H.Interrupted("HOSTED_INTERRUPTED"), H.CleanupDeadline("CLEANUP_DEADLINE")):
                with patch.object(H, "save", side_effect=failure):
                    with self.assertRaises(type(failure)) as caught:
                        H.retain_instrumentation_observation(state, source, b"")
                    self.assertIs(caught.exception, failure)
            def deferred_cancel(output):
                life.first_signal = H.signal.SIGTERM
                raise ValueError("PRIVATE_DECODE")
            with patch.object(H, "instrumentation_observation", side_effect=deferred_cancel):
                with self.assertRaises(H.Interrupted): H.retain_instrumentation_observation(state, source, b"")

    def test_trial_acceptance_failure_wins_after_decode_or_receipt_write_failure(self):
        for mode in ("decode", "save", "ordinary"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                state = Path(directory); source = "a" * 40; life = H.Lifetime()
                apks = state / "prepared/android/volparossa_dps_jni/build/outputs/apk/androidTest/debug"
                apks.mkdir(parents=True); (apks / "fixture.apk").write_bytes(b"inert apk never installed")
                (state / "offline-apk.json").write_text(json.dumps(dict(source=source, apk=P.digest(b"inert apk never installed"))))
                def spawn(*args, **kwargs):
                    child = unittest.mock.Mock(returncode=None); child.poll.return_value = None
                    life.owned.append(child); return child
                def command(label, *args, **kwargs):
                    return {"boot-1": b"1", "guest-api": b"36", "guest-abi": b"x86_64"}.get(label, b"INSTRUMENTATION_FAILED: private failure\n")
                original_save, original_decode = H.save, H.instrumentation_observation
                def save(path, value):
                    if mode == "save" and path.name == "instrumentation-result-diagnostic.json": raise OSError("PRIVATE_SAVE")
                    original_save(path, value)
                def decode(output):
                    if mode == "decode": raise ValueError("PRIVATE_DECODE")
                    return original_decode(output)
                with patch.object(H, "ACTIVE", life), patch.object(life, "spawn", side_effect=spawn), \
                     patch.object(life, "drain_all", side_effect=life.owned.clear), patch.object(H, "run", side_effect=command) as run, \
                     patch.object(H, "save", side_effect=save), patch.object(H, "instrumentation_observation", side_effect=decode), \
                     patch.object(H.time, "monotonic", return_value=10), patch.object(H.signal, "setitimer"), \
                     patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)):
                    with self.assertRaisesRegex(ValueError, "ACTUAL_INSTRUMENTATION_RESULT"):
                        H.emulator_trial(state, dict(GITHUB_SHA=source))
                self.assertEqual(run.call_args_list[-1].args[0], "instrumentation")
                self.assertEqual(run.call_args_list[-1].kwargs, dict(seconds=180))
                self.assertFalse((state / "native-result.json").exists())
                self.assertTrue(json.loads((state / "namespace-cleanup.json").read_text())["direct_children_reaped"])

    def test_existing_finalizer_copies_only_closed_json_and_removes_owned_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"; state.mkdir(); evidence = Path(directory) / "evidence"
            life = H.Lifetime(); before = dict(fixed="synthetic")
            with patch.object(H, "ACTIVE", life), patch.object(H, "baseline", return_value=before), \
                 patch.object(H, "baseline_unchanged", return_value=True), patch.object(life, "drain_all"), \
                 patch.object(H.signal, "setitimer"), patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)):
                H.retain_instrumentation_observation(state, "a" * 40, self.transcript(-2, "java.lang.AssertionError: PRIVATE"))
                (state / "instrumentation.private.log").write_bytes(b"PRIVATE raw stack")
                H.finalize(state, evidence, before, False, ValueError("ACTUAL_INSTRUMENTATION_RESULT"))
            self.assertFalse(state.exists()); self.assertFalse((evidence / "instrumentation.private.log").exists())
            receipt = json.loads((evidence / "instrumentation-result-diagnostic.json").read_text())
            self.assertEqual(receipt["tests"][self.methods[0]]["failure_family"], "ASSERTION")
            self.assertNotIn("PRIVATE", json.dumps(receipt))
            self.assertEqual(json.loads((evidence / "result.json").read_text())["primary_failure"], "ACTUAL_INSTRUMENTATION_RESULT")


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

    def test_adb_listener_uses_upstream_loopback_form_and_preserves_client_arguments(self):
        children = iter([self.child([None]), self.child([None])])
        def spawn(*args, **kwargs):
            child = next(children); self.life.owned.append(child); return child
        def command(label, *args, **kwargs):
            if label == "boot-1": return b"1\n"
            raise ValueError("STOP_AT_GUEST_IDENTITY")
        with patch.object(self.life, "spawn", side_effect=spawn) as launch, \
             patch.object(self.life, "drain_all", side_effect=self.life.owned.clear), \
             patch.object(H, "run", side_effect=command) as run, patch.object(H.time, "monotonic", return_value=10):
            with self.assertRaisesRegex(ValueError, "STOP_AT_GUEST_IDENTITY"):
                H.emulator_trial(self.root, dict(GITHUB_SHA=self.source))
        adb = self.root / "tools/sdk/platform-tools/adb"
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(launch.call_args_list[0].args[0],
                         [str(adb), "-L", "tcp:localhost:5037", "nodaemon", "server"])
        self.assertEqual(run.call_args_list[0].args,
                         ("boot-1", [adb, "-s", H.SERIAL, "shell", "getprop", "sys.boot_completed"],
                          self.root, dict(GITHUB_SHA=self.source)))
        self.assertEqual(run.call_args_list[0].kwargs, dict(seconds=3))

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

    def test_adb_listener_literals_match_exact_case_with_raw_newline_fixtures(self):
        literals = (("ADB_LISTEN_HOST_UNSUPPORTED", b"listening on specified hostname currently unsupported"),
                    ("ADB_SMARTSOCKET_LISTENER_FATAL", b"could not install *smartsocket* listener:"))
        for marker, literal in literals:
            for newline in (b"\n", b"\r\n"):
                with self.subTest(marker=marker, newline=newline):
                    self.terminal_files(b"", b"private-prefix " + literal + newline + b"private-suffix")
                    observed = H.emulator_failure_diagnostic(self.root, self.source)
                    self.assertIsNone(observed["capture_error"])
                    self.assertEqual(observed["logs"][1]["observed_markers"], [marker])
                    self.assertEqual(observed["logs"][0]["observed_markers"], [])
                    self.assertNotIn("private-", json.dumps(observed))

    def test_adb_listener_literals_reject_changed_case_truncation_and_nearby_messages(self):
        for literal in (b"listening on specified hostname currently unsupported",
                        b"could not install *smartsocket* listener:"):
            for raw in (literal.upper(), literal[:1].upper() + literal[1:], literal[:-1],
                        literal.replace(b" ", b"\n", 1), b"listener unsupported; could not install socket"):
                with self.subTest(raw=raw):
                    self.terminal_files(b"", raw)
                    observed = H.emulator_failure_diagnostic(self.root, self.source)
                    self.assertIsNone(observed["capture_error"])
                    self.assertEqual(observed["logs"][1]["observed_markers"], [])

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


class KvmConfigurationTests(unittest.TestCase):
    """Pure replay and mocked phase/file orchestration; no actual device or namespace."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.next_state = 0
        self.life = H.Lifetime()
        self.enterContext(patch.object(H, "ACTIVE", self.life))
        self.enterContext(patch.object(H.signal, "setitimer"))
        self.enterContext(patch.object(H.signal, "getitimer", return_value=(0.0, 0.0)))
        self.before = dict(netns="net:[123]", routes=dict(bytes=896, sha256="a"*64),
            ipv6_routes=H.ipv6_route_snapshot(inert_ipv6_route())["raw"],
            ipv6_route_metadata=H.ipv6_route_snapshot(inert_ipv6_route())["metadata"], ipv6_renderer=inert_ipv6_renderer(),
            resolver=dict(bytes=970, sha256="a"*64),
            firewall=H.firewall_snapshot(json.dumps(inert_firewall()).encode()),
            credentials=dict(uid=[1001]*3, gid=[1001]*3, groups=[4,1001]),
            kvm=dict(kvm_gid=993, acl=dict(present=False), device=dict(dev=6, ino=435, rdev=2792,
                mode=8624, uid=0, gid=993, nlink=1, mtime_ns=123, ctime_ns=123)))
        self.expected = self.before["kvm"]
        self.after = copy.deepcopy(self.before)
        self.after["kvm"]["device"].update(mtime_ns=456, ctime_ns=789)

    def state(self):
        self.next_state += 1
        state = self.root / str(self.next_state); state.mkdir()
        return state, self.root / (str(self.next_state) + "-evidence")

    def finalize(self, *, before=None, after=None, complete=True, original=None, control_mode=None):
        state, evidence = self.state()
        before = self.before if before is None else before
        after = self.after if after is None else after
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", return_value=after):
            try: result = H.finalize(state, evidence, before, complete, original, control_mode)
            except RuntimeError:
                result = json.loads((evidence / "result.json").read_text())
        return result

    def test_timestamp_replay_preserves_raw_values_and_strict_mode_rejects(self):
        saved = copy.deepcopy((self.expected, self.after["kvm"]))
        comparison = H.kvm_comparison(*saved)
        self.assertEqual(comparison, dict(classification="TIMESTAMPS_ONLY", raw_unchanged=False, configuration_unchanged=True))
        self.assertEqual(H.kvm_comparison(self.expected, self.expected)["classification"], "UNCHANGED")
        self.assertFalse(H.baseline_unchanged(self.before, self.after))
        self.assertTrue(H.baseline_unchanged(self.before, self.after, kvm_configuration=True))
        self.assertEqual(saved, (self.expected, self.after["kvm"]))
        self.assertEqual(self.after["kvm"]["device"]["mtime_ns"], 456)
        self.assertEqual(self.after["kvm"]["device"]["ctime_ns"], 789)

    def test_every_authority_field_and_acl_digest_change_fails_even_with_timestamps(self):
        for timestamps in (False, True):
            for key in ("dev", "ino", "rdev", "mode", "uid", "gid", "nlink", "acl_present", "acl_bytes", "acl_sha", "named_gid"):
                before = copy.deepcopy(self.before)
                before["kvm"]["acl"] = dict(present=True, bytes=4, sha256="a"*64)
                after = copy.deepcopy(before)
                if timestamps: after["kvm"]["device"].update(mtime_ns=456, ctime_ns=789)
                if key == "acl_present": after["kvm"]["acl"] = dict(present=False)
                elif key == "acl_bytes": after["kvm"]["acl"]["bytes"] += 1
                elif key == "acl_sha": after["kvm"]["acl"]["sha256"] = "b"*64
                elif key == "named_gid": after["kvm"]["kvm_gid"] += 1
                else: after["kvm"]["device"][key] += 1
                with self.subTest(key=key, timestamps=timestamps):
                    if key in ("gid", "named_gid", "rdev"):
                        with self.assertRaisesRegex(ValueError, "KVM_METADATA_SHAPE"):
                            H.kvm_comparison(before["kvm"], after["kvm"])
                        with self.assertRaises(ValueError): H.baseline_unchanged(before, after, kvm_configuration=True)
                    else:
                        self.assertEqual(H.kvm_comparison(before["kvm"], after["kvm"])["classification"], "CONFIGURATION_DIFFERENCE")
                        self.assertFalse(H.baseline_unchanged(before, after, kvm_configuration=True))
            after = copy.deepcopy(self.after if timestamps else self.before)
            after["kvm"]["device"]["gid"] = after["kvm"]["kvm_gid"] = 994
            self.assertFalse(H.baseline_unchanged(self.before, after, kvm_configuration=True))

    def test_missing_extra_and_wrong_types_validate_before_identical_snapshot_equality(self):
        malformed = [None, [], {}, dict(self.expected, unknown="PRIVATE")]
        for key in self.expected:
            item = copy.deepcopy(self.expected); item.pop(key); malformed.append(item)
        for key in self.expected["device"]:
            for value in (None, True, "1", 1.0, 2**64):
                item = copy.deepcopy(self.expected); item["device"][key] = value; malformed.append(item)
            item = copy.deepcopy(self.expected); item["device"].pop(key); malformed.append(item)
        for change in (dict(present=0), dict(present=1), dict(present=False, extra=1),
            dict(present=True, bytes=True, sha256="a"*64), dict(present=True, bytes=4097, sha256="a"*64),
            dict(present=True, bytes=4, sha256="PRIVATE"), dict(present=True, bytes=4, sha256="a"*64, extra=1)):
            item = copy.deepcopy(self.expected); item["acl"] = change; malformed.append(item)
        item = copy.deepcopy(self.expected); item["device"]["extra"] = 1; malformed.append(item)
        for key, value in (("mode", stat.S_IFREG | 0o660), ("mode", (1 << 32) | stat.S_IFCHR),
                           ("rdev", H.os.makedev(1, 3)), ("uid", 2**32), ("nlink", 0)):
            item = copy.deepcopy(self.expected); item["device"][key] = value; malformed.append(item)
        for value in (0, True, "993", 993.0):
            item = copy.deepcopy(self.expected); item["kvm_gid"] = value; malformed.append(item)
        for item in malformed:
            with self.subTest(shape=type(item).__name__), self.assertRaisesRegex(ValueError, "KVM_METADATA_SHAPE"):
                H.kvm_comparison(item, copy.deepcopy(item))
            with self.assertRaises(ValueError): H.kvm_comparison(self.expected, item)
            with self.assertRaises(ValueError): H.kvm_comparison(item, self.expected)

    def test_configuration_baseline_requires_complete_typed_metadata_and_preserves_other_components(self):
        for component in ("netns", "routes", "ipv6_routes", "resolver", "credentials", "firewall"):
            after = copy.deepcopy(self.after)
            if component == "netns": after[component] = "net:[124]"
            elif component == "credentials": after[component]["groups"] = [1001]
            elif component == "firewall":
                document = inert_firewall(); document["nftables"][2]["chain"]["policy"] = "accept"
                after[component] = H.firewall_snapshot(json.dumps(document).encode())
            else: after[component]["sha256"] = "b"*64
            self.assertFalse(H.baseline_unchanged(self.before, after, kvm_configuration=True))
        for component in self.before:
            missing = copy.deepcopy(self.before); missing.pop(component)
            with self.assertRaises(ValueError): H.baseline_unchanged(missing, missing, kvm_configuration=True)
            wrong = copy.deepcopy(self.before); wrong[component] = "PRIVATE"
            with self.assertRaises(ValueError): H.baseline_unchanged(wrong, wrong, kvm_configuration=True)
        unknown = dict(self.before, extra=1)
        with self.assertRaises(ValueError): H.baseline_unchanged(unknown, unknown, kvm_configuration=True)
        for mode in (1, None, "true"):
            with self.assertRaises(ValueError): H.baseline_unchanged(self.before, self.after, kvm_configuration=mode)
        document = inert_firewall(); document["nftables"][4]["rule"]["expr"][1]["counter"]["packets"] += 1
        after = copy.deepcopy(self.after); after["firewall"] = H.firewall_snapshot(json.dumps(document).encode())
        self.assertTrue(H.baseline_unchanged(self.before, after, kvm_configuration=True))

    def test_completed_finalizer_versions_raw_and_configuration_verdicts_separately(self):
        result = self.finalize()
        self.assertEqual(result["schema"], 3); self.assertTrue(result["success"])
        self.assertEqual(result["baseline_acceptance_mode"], "COMPLETED_EMULATOR_CONFIGURATION")
        self.assertFalse(result["host_routes_dns_firewall_unchanged"])
        self.assertTrue(result["host_configuration_checkpoint_accepted"])
        self.assertEqual(result["kvm_comparison"]["classification"], "TIMESTAMPS_ONLY")
        self.assertEqual(result["baseline_comparison"]["before"], self.before)
        self.assertEqual(result["baseline_comparison"]["after"], self.after)
        self.assertEqual(result["baseline_comparison"]["changed_components"], ["kvm"])
        self.assertEqual(result["cleanup_errors"], [])
        self.assertTrue(result["owned_state_removed"] and result["registered_groups_drained"])
        self.assertLessEqual(len(json.dumps(result, indent=2).encode()), 16384)

    def test_incomplete_or_failed_instrumentation_and_controls_never_select_configuration_mode(self):
        for complete, original, mode in ((False, None, None), (False, ValueError("ACTUAL_INSTRUMENTATION_RESULT"), None),
                (True, ValueError("ORIGINAL_FAILURE"), None), (True, None, "control")):
            result = self.finalize(complete=complete, original=original, control_mode=mode)
            self.assertFalse(result["success"]); self.assertFalse(result["host_configuration_checkpoint_accepted"])
            self.assertEqual(result["baseline_acceptance_mode"], "STRICT_KVM_METADATA")
            self.assertIn("HOST_BASELINE_UNCONFIRMED", result["cleanup_errors"])
            self.assertEqual(result["primary_failure"], H.failure_code(original))

    def test_capture_and_cleanup_failures_never_gain_success_or_replace_original(self):
        state, evidence = self.state(); original = ValueError("ORIGINAL_FAILURE")
        with patch.object(self.life, "drain_all"), patch.object(H, "baseline", side_effect=OSError(errno.EIO, "PRIVATE capture error")):
            result = H.finalize(state, evidence, self.before, True, original)
        self.assertFalse(result["success"]); self.assertFalse(result["host_configuration_checkpoint_accepted"])
        self.assertEqual(result["primary_failure"], "ORIGINAL_FAILURE")
        self.assertIsNone(result["kvm_comparison"]); self.assertNotIn("PRIVATE", json.dumps(result))
        self.life.cleanup_errors = ["DRAIN_UNCONFIRMED"]
        result = self.finalize()
        self.assertFalse(result["success"]); self.assertIn("DRAIN_UNCONFIRMED", result["cleanup_errors"])

    def test_cancellation_during_capture_or_after_comparison_revokes_configuration_mode(self):
        reference = H.ipv6_route_snapshot(inert_ipv6_route(reference="00000002"))
        self.after.update(ipv6_routes=reference["raw"], ipv6_route_metadata=reference["metadata"])
        for moment in ("capture", "comparison", "diagnostic"):
            self.life.first_signal = None
            state, evidence = self.state()
            real = H.baseline_unchanged; config_calls = []
            diagnostic_real = H.baseline_diagnostic; diagnostic_calls = []
            def capture():
                if moment == "capture": self.life.first_signal = H.signal.SIGTERM
                return self.after
            def compare(*args, **kwargs):
                result = real(*args, **kwargs)
                if kwargs.get("kvm_configuration"):
                    config_calls.append(True)
                    if moment == "comparison": self.life.first_signal = H.signal.SIGTERM
                return result
            def diagnostic(*args):
                result = diagnostic_real(*args); diagnostic_calls.append(True)
                if moment == "diagnostic" and len(diagnostic_calls) == 2:
                    self.life.first_signal = H.signal.SIGTERM
                return result
            with patch.object(self.life, "drain_all"), patch.object(H, "baseline", side_effect=capture), \
                 patch.object(H, "baseline_unchanged", side_effect=compare), patch.object(H, "baseline_diagnostic", side_effect=diagnostic):
                with self.assertRaises(RuntimeError): H.finalize(state, evidence, self.before, True, None)
            result = json.loads((evidence / "result.json").read_text())
            self.assertFalse(result["success"]); self.assertFalse(result["host_configuration_checkpoint_accepted"])
            self.assertEqual(result["baseline_acceptance_mode"], "STRICT_KVM_METADATA")
            self.assertEqual(config_calls, [] if moment == "capture" else [True])
            self.assertEqual(result["baseline_comparison"]["ipv6_route_comparison"]["classification"], "REFERENCE_METADATA_ONLY")


    def child_phase(self, phase, observed, workload_error=None):
        owner = SimpleNamespace(st_dev=1, st_ino=2, st_rdev=0, st_mode=stat.S_IFDIR | 0o700,
            st_uid=1001, st_gid=1001, st_nlink=1, st_mtime_ns=3, st_ctime_ns=4)
        receipt = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1001, st_gid=1001, st_nlink=1, st_size=0)
        state = unittest.mock.Mock(); state.lstat.return_value = owner; state.resolve.return_value = state
        selected = [993] if phase in H.KVM_PHASES else []
        creds = dict(uid=[1001]*3, gid=[1001]*3, groups=selected)
        caps = dict(CapEff=0, CapPrm=0, CapAmb=0)
        outer = dict(netns="net:[1]", credentials=self.before["credentials"], kvm=self.expected)
        outer["credentials"] = dict(uid=[1001]*3, gid=[1001]*3, groups=[4,1001])
        net = "net:[1]" if phase == "public-build" else "net:[2]"
        writes = []
        with ExitStack() as stack:
            stack.enter_context(patch.dict(H.os.environ, dict(SUDO_UID="1001", SUDO_GID="1001")))
            for name, value in (("getuid", 0), ("geteuid", 0), ("getpid", 1)):
                stack.enter_context(patch.object(H.os, name, return_value=value))
            stack.enter_context(patch.object(H.os, "open", side_effect=[22,23]))
            stack.enter_context(patch.object(H.os, "fstat", side_effect=[owner,receipt]))
            close = stack.enter_context(patch.object(H.os, "close"))
            stack.enter_context(patch.object(H.os, "write", side_effect=lambda fd, raw: writes.append(raw) or len(raw)))
            stack.enter_context(patch.object(H.os, "readlink", side_effect=lambda p: {"/proc/self/ns/net": net,
                "/proc/self/ns/pid": "pid:[2]", "/proc/self": "1"}[p]))
            stack.enter_context(patch.object(H.socket, "if_nameindex", return_value=[(1,"lo")]))
            stack.enter_context(patch.object(H, "private_document", side_effect=[outer,
                dict(map="a"*40,parent_pidns="pid:[1]"), dict(GITHUB_SHA="a"*40)]))
            for name, value in (("drop_credentials", creds), ("capability_bits", caps), ("credentials", creds)):
                stack.enter_context(patch.object(H, name, return_value=value))
            for name in ("guards", "activate_environment", "acquire_build", "offline_build"):
                stack.enter_context(patch.object(H, name))
            snapshot = stack.enter_context(patch.object(H, "kvm_snapshot", side_effect=[self.expected, observed]))
            query = stack.enter_context(patch.object(H, "kvm_open_query", return_value=dict(api_version=12, fd_closed=True, vm_created=False)))
            stack.enter_context(patch.object(H, "emulator_trial", side_effect=workload_error))
            stack.enter_context(patch.object(H.subprocess, "run"))
            error = None
            try: H.phase_child(state, phase)
            except BaseException as caught: error = caught
            self.assertEqual(close.call_args_list, [unittest.mock.call(23), unittest.mock.call(22)])
            self.assertEqual(query.call_count, int(phase in H.KVM_PHASES))
            self.assertEqual(snapshot.call_count, 1 if workload_error else 2)
        self.assertEqual(len(writes), 1); self.assertLessEqual(len(writes[0]), 16384)
        return json.loads(writes[0]), error

    def test_emulator_final_checkpoint_accepts_only_timestamp_differences_and_retains_raw(self):
        receipt, error = self.child_phase("emulator", self.after["kvm"])
        self.assertIsNone(error); self.assertEqual(receipt["schema"], 2)
        self.assertTrue(receipt["success"]); self.assertFalse(receipt["device_unchanged"])
        self.assertTrue(receipt["device_configuration_unchanged"])
        self.assertEqual(receipt["device_comparison"]["classification"], "TIMESTAMPS_ONLY")
        self.assertEqual(receipt["device_checkpoint"], dict(before=self.expected, after=self.after["kvm"]))
        for key in ("ino", "mode", "uid", "dev", "rdev", "nlink"):
            changed = copy.deepcopy(self.after["kvm"]); changed["device"][key] += 1
            receipt, error = self.child_phase("emulator", changed)
            self.assertIsInstance(error, ValueError)
            self.assertEqual(str(error), "KVM_METADATA_SHAPE" if key == "rdev" else "KVM_DEVICE_CHANGED")
            self.assertFalse(receipt["success"]); self.assertFalse(receipt["device_configuration_unchanged"])

    def test_quiet_phase_checkpoints_stay_strict_and_missing_workload_cannot_gain_comparison(self):
        for phase in ("kvm-probe", "public-build", "offline-build"):
            receipt, error = self.child_phase(phase, self.after["kvm"])
            self.assertIsInstance(error, ValueError); self.assertEqual(str(error), "KVM_DEVICE_CHANGED")
            self.assertFalse(receipt["success"]); self.assertFalse(receipt["device_unchanged"])
            self.assertTrue(receipt["device_configuration_unchanged"])
            receipt, error = self.child_phase(phase, self.expected)
            self.assertIsNone(error); self.assertTrue(receipt["success"] and receipt["device_unchanged"])
        original = ValueError("ACTUAL_INSTRUMENTATION_RESULT")
        receipt, error = self.child_phase("emulator", self.after["kvm"], original)
        self.assertIs(error, original); self.assertFalse(receipt["success"])
        self.assertFalse(receipt["device_unchanged"] or receipt["device_configuration_unchanged"])
        self.assertIsNone(receipt["device_checkpoint"]); self.assertIsNone(receipt["device_comparison"])

    def parent_receipt(self, phase, observed):
        comparison = H.kvm_comparison(self.expected, observed)
        return dict(schema=2, kind="hosted-phase", phase=phase, source="a"*40, success=True,
            credentials=dict(uid=[1001]*3,gid=[1001]*3,groups=[993] if phase in H.KVM_PHASES else []),
            capabilities=dict(CapEff=0,CapPrm=0,CapAmb=0),
            probe=dict(api_version=12,fd_closed=True,vm_created=False) if phase in H.KVM_PHASES else None,
            device_unchanged=comparison["raw_unchanged"], device_configuration_unchanged=comparison["configuration_unchanged"],
            device_comparison=comparison, device_checkpoint=dict(before=copy.deepcopy(self.expected),after=copy.deepcopy(observed)))

    def parent_phase(self, phase, receipt):
        state, _ = self.state()
        with patch.object(H, "run") as run, patch.object(H, "private_document", side_effect=[receipt,self.before]):
            H.run_phase(state, phase, dict(GITHUB_SHA="a"*40), 10)
            run.assert_called_once()

    def test_parent_recomputes_source_bound_checkpoint_and_quiet_phase_rejection(self):
        self.parent_phase("emulator", self.parent_receipt("emulator", self.after["kvm"]))
        for phase in H.PHASES:
            self.parent_phase(phase, self.parent_receipt(phase, self.expected))
            if phase != "emulator":
                with self.assertRaisesRegex(ValueError, "PHASE_RECEIPT_CONTRACT"):
                    self.parent_phase(phase, self.parent_receipt(phase, self.after["kvm"]))
        for mode in ("origin", "after", "before_extra", "after_missing", "classification", "extra_comparison", "raw_int", "config_int", "recorded_raw_int", "recorded_config_int", "missing", "old_schema", "schema_float"):
            receipt = self.parent_receipt("emulator", self.after["kvm"])
            if mode == "origin": receipt["device_checkpoint"]["before"]["device"]["mtime_ns"] += 1
            elif mode == "after": receipt["device_checkpoint"]["after"]["device"]["mode"] += 1
            elif mode == "before_extra": receipt["device_checkpoint"]["before"]["extra"] = 1
            elif mode == "after_missing": receipt["device_checkpoint"]["after"]["device"].pop("ctime_ns")
            elif mode == "classification": receipt["device_comparison"]["classification"] = "UNCHANGED"
            elif mode == "extra_comparison": receipt["device_comparison"]["extra"] = 1
            elif mode == "raw_int": receipt["device_unchanged"] = 0
            elif mode == "config_int": receipt["device_configuration_unchanged"] = 1
            elif mode == "recorded_raw_int": receipt["device_comparison"]["raw_unchanged"] = 0
            elif mode == "recorded_config_int": receipt["device_comparison"]["configuration_unchanged"] = 1
            elif mode == "missing": receipt.pop("device_checkpoint")
            elif mode == "old_schema": receipt["schema"] = 1
            elif mode == "schema_float": receipt["schema"] = 2.0
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.parent_phase("emulator", receipt)

    def test_configuration_callsite_gate_keeps_controls_strict_and_no_other_mode_authority(self):
        source = (P.HERE / "hosted.py").read_text()
        S.kvm_configuration_bindings(ast.parse(source))
        marker = 'unchanged = baseline_unchanged(before, after)'
        mutations = (source.replace(marker, marker[:-1] + ', kvm_configuration=True)'),
            source.replace('kvm_configuration=True) if configuration_mode', 'kvm_configuration=1) if configuration_mode'),
            source.replace('kvm_configuration=True) if configuration_mode', 'unknown_mode=True) if configuration_mode'),
            source + '\ndef extra():\n    baseline_unchanged(before, after, kvm_configuration=True)\n')
        for mutation in mutations:
            with self.assertRaises(ValueError): S.kvm_configuration_bindings(ast.parse(mutation))


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
             patch.object(H.os, "readlink", side_effect=lambda p: {"/proc/self/ns/net": namespace,
                 "/proc/self/ns/pid": "pid:[2]", "/proc/self": "1"}[p]), patch.object(H.socket, "if_nameindex", return_value=links), \
             patch.object(H, "private_document", side_effect=[outer, dict(map="a" * 40, parent_pidns="pid:[1]")]), \
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
        before = dict(kvm=self.expected, ipv6_routes=H.ipv6_route_snapshot(inert_ipv6_route())["raw"],
            ipv6_route_metadata=H.ipv6_route_snapshot(inert_ipv6_route())["metadata"])
        with patch.dict(H.os.environ, env, clear=True), patch.object(H.sys, "argv", ["hosted.py", "--reviewed-hosted-fixture"]), \
             patch.object(H, "guards"), patch.object(H, "environment", return_value=env), \
             patch.object(H, "kvm_admission", return_value=self.expected), patch.object(H, "baseline", return_value=before), \
             patch.object(H, "ipv6_renderer_admission", return_value=inert_ipv6_renderer()), \
             patch.object(H, "run_phase", side_effect=lambda state, phase, *_: observed.append(phase)), \
             patch.object(H, "cancellation_control", side_effect=lambda *_: observed.append("cancellation")), \
             patch.object(H, "group_reaping_control", side_effect=lambda *_: observed.append("group-reaping")), \
             patch.object(H, "acquire_build") as build, patch.object(H, "finalize") as finalizer:
            H.main()
            build.assert_not_called(); finalizer.assert_called_once()
        self.assertEqual(observed, ["kvm-probe", "cancellation", "group-reaping", "public-build", "offline-build", "emulator"])


class Ipv6RouteDiagnosticTests(unittest.TestCase):
    """Pure proc-byte fixtures and owned-file orchestration; never host routes."""
    def test_reference_only_projection_preserves_raw_and_every_other_byte(self):
        raw = inert_ipv6_route()
        before = H.ipv6_route_snapshot(raw)
        after = H.ipv6_route_snapshot(inert_ipv6_route(reference="00000002"))
        self.assertEqual(before["raw"], P.digest(raw))
        self.assertEqual(before["metadata"]["row_count"], 1)
        self.assertEqual(before["metadata"]["non_refcount"], after["metadata"]["non_refcount"])
        self.assertNotEqual(before["metadata"]["reference_metadata"], after["metadata"]["reference_metadata"])
        self.assertEqual(H.ipv6_route_comparison(before, after), dict(classification="REFERENCE_METADATA_ONLY",
            raw_changed=True, metadata_valid=True, non_refcount_changed=False,
            reference_metadata_changed=True, row_count_changed=False))
        self.assertEqual(H.ipv6_route_comparison(before, before)["classification"], "UNCHANGED")
        self.assertEqual(before, H.ipv6_route_snapshot(raw))
        self.assertNotIn(raw[:32].decode(), json.dumps([before, after]))

    def test_every_other_field_order_and_multiplicity_remain_configuration_differences(self):
        KvmConfigurationTests.setUp(self)
        raw = inert_ipv6_route()
        before = H.ipv6_route_snapshot(raw)
        changes = dict(destination="20010db8000000000000000000000001", destination_prefix="41",
            source="20010db8000000000000000000000002", source_prefix="01",
            gateway="20010db8000000000000000000000003", metric="00000002", use="00000001",
            flags="00000002", device="eth0")
        for key, value in changes.items():
            with self.subTest(field=key):
                after = H.ipv6_route_snapshot(inert_ipv6_route(**{key: value}))
                result = H.ipv6_route_comparison(before, after)
                self.assertTrue(result["metadata_valid"])
                self.assertEqual(result["classification"], "NON_REFCOUNT_DIFFERENCE")
                self.assertTrue(result["non_refcount_changed"])
                self.assertFalse(result["reference_metadata_changed"])
                changed = dict(self.before, ipv6_routes=after["raw"], ipv6_route_metadata=after["metadata"])
                self.assertFalse(H.baseline_unchanged(self.before, changed, kvm_configuration=True))
        other = inert_ipv6_route(metric="00000002")
        for left, right in ((raw + other, other + raw), (raw, raw + raw), (raw + raw, raw)):
            result = H.ipv6_route_comparison(H.ipv6_route_snapshot(left), H.ipv6_route_snapshot(right))
            self.assertEqual(result["classification"], "NON_REFCOUNT_DIFFERENCE")
            self.assertTrue(result["non_refcount_changed"])
            before_rows, after_rows = H.ipv6_route_snapshot(left), H.ipv6_route_snapshot(right)
            first = dict(self.before, ipv6_routes=before_rows["raw"], ipv6_route_metadata=before_rows["metadata"])
            last = dict(self.before, ipv6_routes=after_rows["raw"], ipv6_route_metadata=after_rows["metadata"])
            self.assertFalse(H.baseline_unchanged(first, last, kvm_configuration=True))

    def test_empty_and_duplicate_rows_are_valid_without_sorting_or_deduplication(self):
        empty = H.ipv6_route_snapshot(b"")
        self.assertEqual(empty["raw"], P.digest(b""))
        self.assertEqual(empty["metadata"]["row_count"], 0)
        self.assertEqual(H.ipv6_route_comparison(empty, empty)["classification"], "UNCHANGED")
        row = inert_ipv6_route()
        duplicate = H.ipv6_route_snapshot(row + row)
        self.assertEqual(duplicate["metadata"]["row_count"], 2)
        self.assertEqual(duplicate["raw"], P.digest(row + row))
        self.assertNotEqual(duplicate["metadata"]["non_refcount"], H.ipv6_route_snapshot(row)["metadata"]["non_refcount"])
        # Same non-reference rows remain positional: swapping reference values is
        # reference metadata only, without identifying an actor or distinct route.
        changed = inert_ipv6_route(reference="00000002")
        comparison = H.ipv6_route_comparison(H.ipv6_route_snapshot(row + changed), H.ipv6_route_snapshot(changed + row))
        self.assertEqual(comparison["classification"], "REFERENCE_METADATA_ONLY")
        self.assertIsNone(H.ipv6_route_snapshot(inert_ipv6_route(device=""))["metadata"]["error"])
        self.assertIsNone(H.ipv6_route_snapshot(inert_ipv6_route(device="a" * 15))["metadata"]["error"])

    def test_unknown_or_malformed_rows_retain_only_raw_digest_and_closed_rejection(self):
        raw = inert_ipv6_route()
        cases = [b"\n", raw.rstrip(b"\n"), raw + b"unknown PRIVATE row\n", raw.replace(b" ", b"\t", 1),
            raw.replace(b"\n", b"\r\n"), raw.replace(b"00000001", b"0000000g", 1),
            raw.replace(b"20010db8", b"20010DB8"), raw.replace(b"      lo", b"     lo"),
            inert_ipv6_route(destination_prefix="81"), inert_ipv6_route(source_prefix="ff"),
            inert_ipv6_route(device="."), inert_ipv6_route(device=".."), inert_ipv6_route(device="a" * 16),
            inert_ipv6_route(device="../PRIVATE"), raw.rstrip(b"\n") + b" extra\n"]
        for key in ("destination", "destination_prefix", "source", "source_prefix", "gateway", "metric", "reference", "use", "flags"):
            tokens = raw.split()
            index = ("destination", "destination_prefix", "source", "source_prefix", "gateway", "metric", "reference", "use", "flags").index(key)
            # Widths are fixed, even when the numerical value is unchanged.
            cases.append(inert_ipv6_route(**{key: tokens[index].decode() + "0"}))
        for malformed in cases:
            with self.subTest(length=len(malformed)):
                snapshot = H.ipv6_route_snapshot(malformed)
                self.assertEqual(snapshot["raw"], P.digest(malformed))
                self.assertEqual(snapshot["metadata"], dict(schema=1, error="IPV6_ROUTE_ROW_SHAPE",
                    non_refcount=None, reference_metadata=None, row_count=None))
                self.assertEqual(H.ipv6_route_comparison(snapshot, snapshot)["classification"], "PARSE_REJECTED")
                self.assertNotIn("PRIVATE", json.dumps(snapshot))

    def test_byte_row_and_type_bounds_fail_closed(self):
        for raw in (None, True, False, "PRIVATE", bytearray(b"PRIVATE"), 1, b"x" * (H.IPV6_ROUTE_BYTES + 1)):
            with self.subTest(type=type(raw).__name__), self.assertRaisesRegex(ValueError, "^IPV6_ROUTE_BYTES$"):
                H.ipv6_route_snapshot(raw)
        row = inert_ipv6_route()
        maximum = H.ipv6_route_snapshot(row * H.IPV6_ROUTE_ROWS)
        self.assertIsNone(maximum["metadata"]["error"])
        self.assertEqual(maximum["metadata"]["row_count"], H.IPV6_ROUTE_ROWS)
        excess = row * (H.IPV6_ROUTE_ROWS + 1)
        rejected = H.ipv6_route_snapshot(excess)
        self.assertEqual(rejected["raw"], P.digest(excess))
        self.assertEqual(rejected["metadata"]["error"], "IPV6_ROUTE_ROW_BOUND")
        self.assertEqual(H.ipv6_route_snapshot(b"x" * H.IPV6_ROUTE_BYTES)["metadata"]["error"], "IPV6_ROUTE_ROW_SHAPE")

    def test_closed_metadata_rejects_missing_extra_bool_and_identical_invalid_shapes(self):
        valid = H.ipv6_route_snapshot(inert_ipv6_route())
        cases = [None, {}, [], dict(valid, extra="PRIVATE")]
        for outer in ("raw", "metadata"):
            missing = copy.deepcopy(valid); missing.pop(outer); cases.append(missing)
            unknown = copy.deepcopy(valid); unknown[outer]["extra"] = "PRIVATE"; cases.append(unknown)
        for outer, keys in (("raw", ("bytes", "sha256")), ("metadata", ("schema", "error", "row_count", "non_refcount", "reference_metadata"))):
            for key in keys:
                missing = copy.deepcopy(valid); missing[outer].pop(key); cases.append(missing)
                for value in (True, False, None, "PRIVATE", -1, 1.0):
                    # error=None is the one existing nullable success field.
                    if key == "error" and value is None: continue
                    malformed = copy.deepcopy(valid); malformed[outer][key] = value; cases.append(malformed)
        for key in ("non_refcount", "reference_metadata"):
            for item, value in (("bytes", True), ("bytes", 2**64), ("sha256", "PRIVATE"), ("extra", 1)):
                malformed = copy.deepcopy(valid); malformed["metadata"][key][item] = value; cases.append(malformed)
        for malformed in cases:
            with self.subTest(shape=type(malformed).__name__), self.assertRaisesRegex(ValueError, "^IPV6_ROUTE_METADATA_SHAPE$"):
                H.ipv6_route_comparison(malformed, copy.deepcopy(malformed))

    def test_one_bounded_read_supplies_raw_and_metadata_for_each_checkpoint(self):
        rows = [inert_ipv6_route(), inert_ipv6_route(reference="00000002")]
        snapshots = []
        for raw in rows:
            stream = SimpleNamespace(read=Mock(return_value=raw))
            with patch.object(H.Path, "open") as opened:
                opened.return_value.__enter__.return_value = stream
                snapshots.append(H.read_ipv6_route_snapshot())
                opened.assert_called_once_with("rb")
                stream.read.assert_called_once_with(H.IPV6_ROUTE_BYTES + 1)
            self.assertEqual(snapshots[-1], H.ipv6_route_snapshot(raw))
        self.assertEqual(H.ipv6_route_comparison(*snapshots)["classification"], "REFERENCE_METADATA_ONLY")
        with patch.object(H.Path, "open") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"x" * (H.IPV6_ROUTE_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "^IPV6_ROUTE_BYTES$"): H.read_ipv6_route_snapshot()

    def test_only_completed_configuration_accepts_reference_only_and_all_reject_bad_layout(self):
        # Reuse only inert setup/finalizer helpers, without inheriting test methods.
        KvmConfigurationTests.setUp(self)
        self.state = lambda: KvmConfigurationTests.state(self)
        ref = H.ipv6_route_snapshot(inert_ipv6_route(reference="00000002"))
        rejected = H.ipv6_route_snapshot(b"PRIVATE unknown row\n")
        for case in ("reference", "rejected"):
            before = copy.deepcopy(self.before); after = copy.deepcopy(self.before)
            if case == "rejected": before.update(ipv6_routes=rejected["raw"], ipv6_route_metadata=rejected["metadata"])
            observed = ref if case == "reference" else rejected
            after.update(ipv6_routes=observed["raw"], ipv6_route_metadata=observed["metadata"])
            for configuration in (False, True):
                self.assertEqual(H.baseline_unchanged(before, after, kvm_configuration=configuration),
                                 case == "reference" and configuration)
            for mode in ("normal", "quiet", "cancel", "incomplete", "completed", "failed"):
                self.life.first_signal = H.signal.SIGTERM if mode == "cancel" else None
                result = KvmConfigurationTests.finalize(self, before=before, after=after,
                    complete=mode not in ("normal", "incomplete"),
                    original=ValueError("ORIGINAL_FAILURE") if mode == "failed" else None,
                    control_mode="quiet" if mode == "quiet" else None)
                accepted = case == "reference" and mode == "completed"
                self.assertEqual(result["success"], accepted, (case, mode))
                self.assertFalse(result["host_routes_dns_firewall_unchanged"])
                self.assertEqual(result["host_configuration_checkpoint_accepted"], accepted)
                self.assertEqual("HOST_BASELINE_UNCONFIRMED" in result["cleanup_errors"], not accepted)
                diagnostic = result["baseline_comparison"]
                self.assertEqual(diagnostic["before"], before); self.assertEqual(diagnostic["after"], after)
                self.assertEqual(diagnostic["ipv6_route_comparison"]["classification"],
                    "REFERENCE_METADATA_ONLY" if case == "reference" else "PARSE_REJECTED")
                self.assertNotIn("PRIVATE", json.dumps(result))
                self.assertLessEqual(len(json.dumps(result, indent=2).encode()), 16384)
        for key in ("ipv6_routes", "ipv6_route_metadata"):
            missing = copy.deepcopy(self.before); missing.pop(key)
            for configuration in (False, True):
                with self.assertRaisesRegex(ValueError, "IPV6_ROUTE_METADATA_SHAPE"):
                    H.baseline_unchanged(missing, missing, kvm_configuration=configuration)
        inconsistent = copy.deepcopy(self.before)
        inconsistent["ipv6_route_metadata"]["non_refcount"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "IPV6_ROUTE_METADATA_SHAPE"):
            H.baseline_unchanged(self.before, inconsistent)
        diagnostic = H.baseline_diagnostic(self.before, inconsistent, None)
        self.assertEqual(diagnostic["diagnostic_error"], "BASELINE_METADATA_SHAPE")
        self.assertEqual(diagnostic["after"]["ipv6_routes"], self.before["ipv6_routes"])

    def test_missing_both_ipv6_fields_never_accepts_matching_or_partial_baselines(self):
        KvmConfigurationTests.setUp(self)
        self.state = lambda: KvmConfigurationTests.state(self)
        missing = copy.deepcopy(self.before)
        missing.pop("ipv6_routes"); missing.pop("ipv6_route_metadata")
        for before, after in ((missing, copy.deepcopy(missing)), (missing, self.before), (self.before, missing)):
            for configuration in (False, True):
                with self.subTest(configuration=configuration), self.assertRaisesRegex(ValueError, "^IPV6_ROUTE_METADATA_SHAPE$"):
                    H.baseline_unchanged(before, after, kvm_configuration=configuration)
        for mode in ("normal", "quiet", "cancel", "incomplete", "completed", "failed"):
            self.life.first_signal = H.signal.SIGTERM if mode == "cancel" else None
            result = KvmConfigurationTests.finalize(self, before=missing, after=copy.deepcopy(missing),
                complete=mode not in ("normal", "incomplete"),
                original=ValueError("ORIGINAL_FAILURE") if mode == "failed" else None,
                control_mode="quiet" if mode == "quiet" else None)
            self.assertFalse(result["success"], mode)
            self.assertFalse(result["host_routes_dns_firewall_unchanged"])
            self.assertFalse(result["host_configuration_checkpoint_accepted"])
            self.assertIn("HOST_BASELINE_UNCONFIRMED", result["cleanup_errors"])
            self.assertTrue(result["registered_groups_drained"] and result["owned_state_removed"])
            if mode == "failed": self.assertEqual(result["primary_failure"], "ORIGINAL_FAILURE")

    def test_renderer_capture_excludes_hostname_and_admission_retains_unsupported_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            for release, compatible in (("6.8.0-1001-azure", True), ("6.8.0-99-generic", True),
                    ("6.17.0-1022-azure", True), ("6.17.0-1022-generic", False),
                    ("6.17.1-1022-azure", False), ("6.18.0-1022-azure", False),
                    ("7.0.0-1017-azure", True), ("7.0.0-1017-generic", False),
                    ("7.0.1-1017-azure", False), ("7.1.0-1017-azure", False),
                    ("6.14.0-1018-azure", False), ("6.8.1-custom", False), ("6.8.0-99-lowlatency", False)):
                identity = SimpleNamespace(**dict(inert_ipv6_renderer()["kernel"], release=release))
                with patch.object(H.os, "uname", return_value=identity) as uname:
                    if compatible:
                        observed = H.ipv6_renderer_admission(state)
                        self.assertEqual(observed["kernel"]["release"], release)
                    else:
                        with self.assertRaisesRegex(ValueError, "^IPV6_RENDERER_UNSUPPORTED$"):
                            H.ipv6_renderer_admission(state)
                    uname.assert_called_once_with()
                receipt = json.loads((state / "ipv6-renderer-admission.json").read_text())
                self.assertEqual(receipt["compatible"], compatible)
                self.assertEqual(receipt["snapshot"]["kernel"], vars(identity))
                self.assertEqual(receipt["snapshot"]["profile"], {
                    "6.17.0-1022-azure": "linux-6.17-azure-native-seq-v1",
                    "7.0.0-1017-azure": "linux-7.0-azure-native-seq-v1",
                }.get(release, H.IPV6_ROUTE_RENDERER_PROFILE))
                self.assertEqual(receipt["code"], None if compatible else "IPV6_RENDERER_UNSUPPORTED")
                self.assertEqual(set(receipt["snapshot"]["kernel"]), {"sysname", "release", "version", "machine"})
                self.assertLess(len(json.dumps(receipt)), 1024)

    def test_separate_reviewed_renderer_profiles_reject_cross_family_and_near_releases(self):
        legacy = inert_ipv6_renderer()
        azure = copy.deepcopy(legacy)
        azure.update(profile="linux-7.0-azure-native-seq-v1")
        azure["kernel"].update(release="7.0.0-1017-azure",
            version="#17~24.04.1-Ubuntu SMP PREEMPT Thu Sep 24 18:46:57 UTC 2026")
        for metadata in (legacy, azure):
            H.validate_ipv6_renderer(metadata)
        for profile, release in ((legacy["profile"], azure["kernel"]["release"]),
                (azure["profile"], legacy["kernel"]["release"]),
                (azure["profile"], "7.0.0-1017-generic"), (azure["profile"], "7.0.1-1017-azure"),
                (azure["profile"], "7.1.0-1017-azure"), (azure["profile"], "7.0.0-1017-azure-extra"),
                (azure["profile"], "7.0.0-1000000-azure"), (azure["profile"], "7.0.0-azure")):
            metadata = copy.deepcopy(azure); metadata["profile"] = profile; metadata["kernel"]["release"] = release
            with self.subTest(profile=profile, release=release), self.assertRaisesRegex(ValueError, "^IPV6_RENDERER_UNSUPPORTED$"):
                H.validate_ipv6_renderer(metadata)
        for key, value in (("machine", "aarch64"), ("sysname", "FreeBSD"), ("release", "7.0.0-1017-azure\n"),
                           ("version", "x" * 257)):
            metadata = copy.deepcopy(azure); metadata["kernel"][key] = value
            with self.assertRaises(ValueError): H.validate_ipv6_renderer(metadata)
        for profile in ("linux-7-native-seq-v1", "linux-7.0-native-seq-v1", None, True):
            metadata = dict(azure, profile=profile)
            with self.assertRaisesRegex(ValueError, "^IPV6_RENDERER_METADATA_SHAPE$"):
                H.validate_ipv6_renderer(metadata)

    def test_linux7_profile_preserves_raw_strict_and_completed_only_cancellation_boundaries(self):
        KvmConfigurationTests.setUp(self)
        self.state = lambda: KvmConfigurationTests.state(self)
        for value in (self.before, self.after):
            value["ipv6_renderer"]["profile"] = "linux-7.0-azure-native-seq-v1"
            value["ipv6_renderer"]["kernel"]["release"] = "7.0.0-1017-azure"
        reference = H.ipv6_route_snapshot(inert_ipv6_route(reference="00000002"))
        self.after.update(ipv6_routes=reference["raw"], ipv6_route_metadata=reference["metadata"])
        for mode in ("completed", "quiet", "cancel", "incomplete", "failed"):
            self.life.first_signal = H.signal.SIGTERM if mode == "cancel" else None
            result = KvmConfigurationTests.finalize(self, complete=mode != "incomplete",
                original=ValueError("ORIGINAL_FAILURE") if mode == "failed" else None,
                control_mode="quiet" if mode == "quiet" else None)
            accepted = mode == "completed"
            self.assertEqual(result["success"], accepted)
            self.assertFalse(result["host_routes_dns_firewall_unchanged"])
            self.assertEqual(result["host_configuration_checkpoint_accepted"], accepted)
            self.assertEqual(result["baseline_comparison"]["before"], self.before)
            self.assertEqual(result["baseline_comparison"]["after"], self.after)
        # Reuse the existing cancellation timing matrix under the new identity:
        # capture, configuration comparison and final diagnostic comparison.
        self.life.first_signal = None
        KvmConfigurationTests.test_cancellation_during_capture_or_after_comparison_revokes_configuration_mode(self)

    def test_linux617_profile_rejects_crossed_families_near_releases_and_invalid_identity(self):
        azure = inert_ipv6_renderer()
        azure["profile"] = "linux-6.17-azure-native-seq-v1"
        for release in ("6.17.0-1-azure", "6.17.0-1022-azure", "6.17.0-999999-azure"):
            azure["kernel"]["release"] = release
            H.validate_ipv6_renderer(azure)
        for profile, release in ((azure["profile"], "6.8.0-1001-azure"),
                (azure["profile"], "7.0.0-1017-azure"),
                (H.IPV6_ROUTE_RENDERER_PROFILE, "6.17.0-1022-azure"),
                ("linux-7.0-azure-native-seq-v1", "6.17.0-1022-azure")):
            metadata = copy.deepcopy(azure); metadata["profile"] = profile; metadata["kernel"]["release"] = release
            with self.subTest(profile=profile, release=release), self.assertRaisesRegex(ValueError, "^IPV6_RENDERER_UNSUPPORTED$"):
                H.validate_ipv6_renderer(metadata)
        for release in ("6.17.0-1022-generic", "6.17.0-1022-lowlatency", "6.17.1-1022-azure",
                "6.16.0-1022-azure", "6.18.0-1022-azure", "6.17.0-1022-azure-extra",
                "6.17.0-1000000-azure", "6.17.0--azure", "6.17.0-azure", "6.17.0-１２３-azure"):
            metadata = copy.deepcopy(azure); metadata["kernel"]["release"] = release
            with self.subTest(release=release), self.assertRaises(ValueError): H.validate_ipv6_renderer(metadata)
        for key, value in (("machine", "aarch64"), ("sysname", "FreeBSD"),
                ("release", "6.17.0-1022-azure\n"), ("version", "x" * 257), ("machine", True)):
            metadata = copy.deepcopy(azure); metadata["kernel"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): H.validate_ipv6_renderer(metadata)
        for profile in ("linux-6-native-seq-v1", "linux-6.17-native-seq-v1", None, True):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "^IPV6_RENDERER_METADATA_SHAPE$"):
                H.validate_ipv6_renderer(dict(azure, profile=profile))

    def test_linux617_profile_preserves_raw_strict_and_completed_only_cancellation_boundaries(self):
        KvmConfigurationTests.setUp(self)
        self.state = lambda: KvmConfigurationTests.state(self)
        for value in (self.before, self.after):
            value["ipv6_renderer"]["profile"] = "linux-6.17-azure-native-seq-v1"
            value["ipv6_renderer"]["kernel"]["release"] = "6.17.0-1022-azure"
        reference = H.ipv6_route_snapshot(inert_ipv6_route(reference="00000002"))
        self.after.update(ipv6_routes=reference["raw"], ipv6_route_metadata=reference["metadata"])
        for mode in ("completed", "quiet", "cancel", "incomplete", "failed"):
            self.life.first_signal = H.signal.SIGTERM if mode == "cancel" else None
            result = KvmConfigurationTests.finalize(self, complete=mode != "incomplete",
                original=ValueError("ORIGINAL_FAILURE") if mode == "failed" else None,
                control_mode="quiet" if mode == "quiet" else None)
            accepted = mode == "completed"
            self.assertEqual(result["success"], accepted)
            self.assertFalse(result["host_routes_dns_firewall_unchanged"])
            self.assertEqual(result["host_configuration_checkpoint_accepted"], accepted)
            self.assertEqual(result["baseline_comparison"]["before"], self.before)
            self.assertEqual(result["baseline_comparison"]["after"], self.after)
        self.life.first_signal = None
        KvmConfigurationTests.test_cancellation_during_capture_or_after_comparison_revokes_configuration_mode(self)

    def test_missing_malformed_or_unsupported_renderer_never_accepts_identical_baselines(self):
        KvmConfigurationTests.setUp(self)
        valid = inert_ipv6_renderer()
        cases = [None, {}, [], dict(valid, schema=True), dict(valid, schema=1.0),
            dict(valid, profile="unreviewed"), dict(valid, unknown="PRIVATE")]
        for key in valid:
            missing = copy.deepcopy(valid); missing.pop(key); cases.append(missing)
        for key, bound in (("sysname", 16), ("release", 128), ("version", 256), ("machine", 32)):
            for value in (None, True, 1, "", "x" * (bound + 1), "PRIVATE\n", "é"):
                bad = copy.deepcopy(valid); bad["kernel"][key] = value; cases.append(bad)
            missing = copy.deepcopy(valid); missing["kernel"].pop(key); cases.append(missing)
        for key, value in (("sysname", "FreeBSD"), ("machine", "aarch64"), ("release", "6.14.0-1018-azure")):
            bad = copy.deepcopy(valid); bad["kernel"][key] = value; cases.append(bad)
        bad = copy.deepcopy(valid); bad["kernel"]["nodename"] = "PRIVATE"; cases.append(bad)
        for metadata in cases:
            first = dict(self.before, ipv6_renderer=metadata)
            for configuration in (False, True):
                with self.subTest(metadata=metadata, configuration=configuration), self.assertRaises(ValueError):
                    H.baseline_unchanged(first, copy.deepcopy(first), kvm_configuration=configuration)
        missing = dict(self.before); missing.pop("ipv6_renderer")
        for first, last in ((missing, copy.deepcopy(missing)), (missing, self.before), (self.before, missing)):
            for configuration in (False, True):
                with self.assertRaisesRegex(ValueError, "^IPV6_RENDERER_METADATA_SHAPE$"):
                    H.baseline_unchanged(first, last, kvm_configuration=configuration)
        changed = copy.deepcopy(self.before); changed["ipv6_renderer"]["kernel"]["version"] = "#2-Ubuntu SMP inert"
        self.assertFalse(H.baseline_unchanged(self.before, changed, kvm_configuration=True))

    def test_unsupported_kernel_or_layout_refuses_before_phase_and_retains_identity(self):
        KvmConfigurationTests.setUp(self)
        env = dict(RUNNER_TEMP=str(self.root), GITHUB_RUN_ID="1", GITHUB_RUN_ATTEMPT="1", GITHUB_SHA="a"*40)
        for kind in ("kernel", "layout"):
            env["GITHUB_RUN_ATTEMPT"] = "1" if kind == "kernel" else "2"
            identity = SimpleNamespace(**inert_ipv6_renderer()["kernel"])
            if kind == "kernel": identity.release = "6.14.0-1018-azure"
            before = copy.deepcopy(self.before)
            rejected = H.ipv6_route_snapshot(b"PRIVATE unsupported row\n")
            before.update(ipv6_routes=rejected["raw"], ipv6_route_metadata=rejected["metadata"])
            code = "IPV6_RENDERER_UNSUPPORTED" if kind == "kernel" else "IPV6_ROUTE_LAYOUT_UNSUPPORTED"
            with patch.dict(H.os.environ, env, clear=True), patch.object(H.sys, "argv", ["hosted.py", "--reviewed-hosted-fixture"]), \
                 patch.object(H, "guards"), patch.object(H, "kvm_admission", return_value=self.expected), \
                 patch.object(H.os, "uname", return_value=identity), patch.object(H, "run_phase") as phase, \
                 patch.object(H, "baseline", return_value=before), patch.object(H, "finalize") as finalizer:
                with self.assertRaisesRegex(ValueError, "^" + code + "$"): H.main()
                phase.assert_not_called(); finalizer.assert_called_once()
                state = finalizer.call_args.args[0]
                receipt = json.loads((state / "ipv6-renderer-admission.json").read_text())
                self.assertEqual(receipt["compatible"], kind != "kernel")
                self.assertEqual(receipt["snapshot"]["kernel"]["release"], identity.release)

    def test_ambiguous_or_forged_classification_cannot_authorize_configuration(self):
        KvmConfigurationTests.setUp(self)
        ambiguous = copy.deepcopy(self.before)
        ambiguous["ipv6_routes"]["sha256"] = "b" * 64
        result = H.ipv6_route_comparison(*[dict(raw=value["ipv6_routes"], metadata=value["ipv6_route_metadata"])
                                        for value in (self.before, ambiguous)])
        self.assertEqual(result["classification"], "RAW_DIFFERENCE")
        self.assertFalse(H.baseline_unchanged(self.before, ambiguous, kvm_configuration=True))
        for cached in (dict(classification="REFERENCE_METADATA_ONLY"),
                       dict(classification="UNCHANGED", metadata_valid=True)):
            extra = dict(ambiguous, ipv6_route_comparison=cached)
            with self.assertRaisesRegex(ValueError, "KVM_CONFIGURATION_BASELINE_SHAPE"):
                H.baseline_unchanged(extra, copy.deepcopy(extra), kvm_configuration=True)
            extra = copy.deepcopy(ambiguous); extra["ipv6_route_metadata"].update(cached)
            with self.assertRaisesRegex(ValueError, "IPV6_ROUTE_METADATA_SHAPE"):
                H.baseline_unchanged(extra, copy.deepcopy(extra), kvm_configuration=True)

    def test_cleanup_failures_revoke_reference_only_configuration_after_comparison(self):
        KvmConfigurationTests.setUp(self)
        reference = H.ipv6_route_snapshot(inert_ipv6_route(reference="00000002"))
        after = dict(self.before, ipv6_routes=reference["raw"], ipv6_route_metadata=reference["metadata"])
        for mode in ("undrained", "removal", "late_cleanup", "capture_signal"):
            self.life.first_signal = None; self.life.cleanup_errors = []; self.life.owned = []
            state, evidence = KvmConfigurationTests.state(self)
            def capture():
                if mode == "capture_signal": self.life.first_signal = H.signal.SIGTERM
                return after
            def remove(path):
                if mode == "removal": raise OSError("PRIVATE")
                if mode == "late_cleanup": self.life.cleanup_errors.append("LATE_CLEANUP_FAILURE")
                real_remove(path)
            if mode == "undrained": self.life.owned.append(SimpleNamespace(pid=123))
            real_remove = H.shutil.rmtree
            with patch.object(self.life, "drain_all"), patch.object(H, "baseline", side_effect=capture), \
                 patch.object(H.shutil, "rmtree", side_effect=remove):
                try: H.finalize(state, evidence, self.before, True, None)
                except RuntimeError: pass
            result = json.loads((evidence / "result.json").read_text())
            self.assertFalse(result["success"]); self.assertFalse(result["host_routes_dns_firewall_unchanged"])
            self.assertFalse(result["host_configuration_checkpoint_accepted"])
            self.assertEqual(result["baseline_acceptance_mode"], "STRICT_KVM_METADATA")
            self.assertIn("HOST_BASELINE_UNCONFIRMED", result["cleanup_errors"])
            self.assertEqual(result["baseline_comparison"]["ipv6_route_comparison"]["classification"], "REFERENCE_METADATA_ONLY")
        self.life.owned = []; self.life.first_signal = None

    def test_source_binding_rejects_route_exceptions_or_missing_early_validation(self):
        source = (P.HERE / "hosted.py").read_text()
        S.ipv6_diagnostic_bindings(ast.parse(source))
        for mutation in (source.replace('{"firewall", "kvm", "ipv6_routes", "ipv6_route_metadata"} if kvm_configuration',
                                       '{"firewall", "kvm", "ipv6_routes", "ipv6_route_metadata", "ipv6_renderer"} if kvm_configuration'),
            source.replace('else {"firewall"}', 'else {"firewall", "ipv6_route_metadata"}'),
            source.replace('ipv6 = ipv6_route_comparison(*[', 'ipv6 = ignored_comparison(*['),
            source.replace('ipv6 = read_ipv6_route_snapshot()', 'ipv6 = ignored_capture()'),
            source.replace('validate_ipv6_renderer(value["ipv6_renderer"])', 'ignored_validate(value["ipv6_renderer"])'),
            source.replace('("UNCHANGED", "REFERENCE_METADATA_ONLY")', '("UNCHANGED", "REFERENCE_METADATA_ONLY", "RAW_DIFFERENCE")'),
            source.replace('renderer = ipv6_renderer_admission(state)', 'renderer = ignored_admission(state)'),
            source.replace('validate_ipv6_renderer(value["ipv6_renderer"])', 'validate_ipv6_renderer(value["ipv6_renderer"], supported=False)'),
            source.replace('r"6\\.17\\.0-[0-9]{1,6}-azure"', 'r".*"'),
            source.replace('r"6\\.17\\.0-[0-9]{1,6}-azure"', 'r"6\\.17\\.0-[0-9]{1,6}-(?:generic|azure)"'),
            source.replace('r"6\\.17\\.0-[0-9]{1,6}-azure"', 'r"6\\.17\\.[0-9]+-[0-9]{1,6}-azure"'),
            source.replace('r"6\\.17\\.0-[0-9]{1,6}-azure"', 'r"6\\.[0-9]+\\.0-[0-9]{1,6}-azure"'),
            source.replace('r"7\\.0\\.0-[0-9]{1,6}-azure"', 'r".*"'),
            source.replace('r"7\\.0\\.0-[0-9]{1,6}-azure"', 'r"7\\.0\\.0-[0-9]{1,6}-(?:generic|azure)"')):
            with self.assertRaises(ValueError): S.ipv6_diagnostic_bindings(ast.parse(mutation))


if __name__ == "__main__":
    unittest.main()
