# SPDX-License-Identifier: GPL-3.0-only
"""Inert Python/Git/archive tests only; none executes Rust/JNI/Android or a controller."""
import copy
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


if __name__ == "__main__":
    unittest.main()
