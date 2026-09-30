# SPDX-License-Identifier: GPL-3.0-only
"""Adapter contract tests use synthetic receipts, never claim real peer delivery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import core_traffic as bridge


class TrafficBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.control = socket.socket(socket.AF_UNIX)
        self.control.bind(str(self.root / "core.sock"))
        os.chmod(self.root / "core.sock", 0o600)
        self.args = argparse.Namespace(
            core=str(Path(sys.executable).resolve()), control_socket=str(self.root / "core.sock"),
            publisher_key="a" * 64, name="traffic/synthetic/1", min_revision=1,
            cache=str(self.root / "agent-cache"), output=str(self.root / "accepted.json"),
            region="Synthetic", map_version=260929, map_sha256="b" * 64,
            timeout=30, reuse_cache=False)
        now = int(time.time())
        self.snapshot = {"format": "volparossa-road-traffic-v1", "region": "Synthetic",
                         "map_version": 260929, "map_sha256": "b" * 64,
                         "window_start": now - 60, "window_end": now, "expires": now + 120,
                         "roads": [{"feature_id": 42, "segment_index": 3, "direction": 1,
                                    "speed_group": 2, "observation_count": 8}]}

    def tearDown(self):
        self.control.close()
        self.temp.cleanup()

    def fake_core(self, argv, timeout):
        """Exact CLI contract fixture only; no simulated production network."""
        self.argv = argv
        output = Path(argv[argv.index("--local-output") + 1])
        payload = json.dumps(self.snapshot).encode()
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        value = {"operation": "named_content_download", "publisher_key": self.args.publisher_key,
                 "name": self.args.name, "revision": 3, "local_delivery": True, "output_mode": "0600",
                 "ownership_changed": False, "origin_authenticated": False, "globally_latest": False,
                 "local_output": str(output), "bytes": len(payload),
                 "sha256": hashlib.sha256(payload).hexdigest(), "manifest_id": "c" * 64,
                 "publication_expires_unix_seconds": self.snapshot["expires"]}
        return json.dumps(value).encode()

    def test_verified_contract_publishes_new_private_snapshot(self):
        with patch.object(bridge, "run_core", side_effect=self.fake_core):
            report = bridge.fetch(self.args)
        accepted = json.loads(Path(self.args.output).read_bytes())
        self.assertEqual(accepted["snapshot"]["roads"], self.snapshot["roads"])
        self.assertEqual(accepted["revision"], 3)
        self.assertFalse(report["native_map_applied"])
        self.assertEqual(Path(self.args.output).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.argv[:5], [self.args.core, "--control-socket", self.args.control_socket,
                                      "content", "fetch-name"])
        self.assertFalse(any(self.root.glob(".volparossa-traffic-*")))

    def test_receipt_cannot_relabel_origin_or_publisher(self):
        for key, value in (("origin_authenticated", True), ("publisher_key", "d" * 64),
                           ("revision", 0), ("sha256", "e" * 64), ("local_delivery", False)):
            with self.subTest(key=key):
                def wrong(argv, timeout):
                    receipt = json.loads(self.fake_core(argv, timeout))
                    receipt[key] = value
                    return json.dumps(receipt).encode()
                with patch.object(bridge, "run_core", side_effect=wrong):
                    with self.assertRaises(bridge.TrafficFetchError):
                        bridge.fetch(self.args)
                self.assertFalse(Path(self.args.output).exists())

    def test_duplicate_receipt_rejected_and_temp_cleaned(self):
        def duplicate(argv, timeout):
            result = self.fake_core(argv, timeout)
            return result[:-1] + b', "revision": 99}'
        with patch.object(bridge, "run_core", side_effect=duplicate):
            with self.assertRaisesRegex(bridge.TrafficFetchError, "invalid_core_receipt"):
                bridge.fetch(self.args)
        self.assertFalse(any(self.root.glob(".volparossa-traffic-*")))

    def test_deep_receipt_is_a_closed_error(self):
        with self.assertRaisesRegex(bridge.TrafficFetchError, "^invalid_core_receipt$"):
            bridge.receipt(b'[' * 2000 + b']' * 2000, self.args, b"x", Path("/unused"), 1)

    def test_existing_output_never_overwritten(self):
        Path(self.args.output).write_bytes(b"retained")
        with patch.object(bridge, "run_core") as core:
            with self.assertRaisesRegex(bridge.TrafficFetchError, "output_exists"):
                bridge.fetch(self.args)
            core.assert_not_called()
        self.assertEqual(Path(self.args.output).read_bytes(), b"retained")

    def test_output_race_never_overwritten(self):
        def raced(argv, timeout):
            result = self.fake_core(argv, timeout)
            Path(self.args.output).write_bytes(b"racing owner file")
            return result
        with patch.object(bridge, "run_core", side_effect=raced):
            with self.assertRaisesRegex(bridge.TrafficFetchError, "output_exists"):
                bridge.fetch(self.args)
        self.assertEqual(Path(self.args.output).read_bytes(), b"racing owner file")

    def test_empty_snapshot_is_clear_not_free_flow(self):
        self.snapshot["roads"] = []
        self.args.reuse_cache = True
        with patch.object(bridge, "run_core", side_effect=self.fake_core):
            report = bridge.fetch(self.args)
        self.assertEqual(report["roads"], 0)
        self.assertIn("--reuse-cache", self.argv)

    def test_wrong_dataset_stays_unpublished(self):
        self.snapshot["map_sha256"] = "d" * 64
        with patch.object(bridge, "run_core", side_effect=self.fake_core):
            with self.assertRaises(bridge.TrafficSnapshotError):
                bridge.fetch(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_actual_subprocess_receives_no_shell_or_input(self):
        result = bridge.run_core([sys.executable, "-I", "-c",
                                  'import os,sys; assert not sys.stdin.read(); print("ready")'], 2)
        self.assertEqual(result, b"ready\n")

    def test_actual_subprocess_failure_does_not_expose_stderr(self):
        with self.assertRaisesRegex(bridge.TrafficFetchError, "^core_failed$"):
            bridge.run_core([sys.executable, "-I", "-c",
                             'import sys; print("private-marker",file=sys.stderr); sys.exit(2)'], 2)

    def test_actual_subprocess_timeout_and_output_bound(self):
        with self.assertRaisesRegex(bridge.TrafficFetchError, "^core_timeout$"):
            bridge.run_core([sys.executable, "-I", "-c", "import time; time.sleep(5)"], 0.05)
        with self.assertRaisesRegex(bridge.TrafficFetchError, "^core_output_limit$"):
            bridge.run_core([sys.executable, "-I", "-c", 'print("x"*300000)'], 2)

    def test_timeout_stops_descendant_after_leader_exit(self):
        pidfile = self.root / "child.pid"
        code = ("import os,sys,time; p=os.fork(); "
                "sys.exit(0) if p else None; "
                "open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)")
        with self.assertRaisesRegex(bridge.TrafficFetchError, "^core_timeout$"):
            bridge.run_core([sys.executable, "-I", "-c", code, str(pidfile)], 0.3)
        child = int(pidfile.read_text())
        statefile = Path(f"/proc/{child}/stat")
        deadline = time.monotonic() + 1
        while statefile.exists() and time.monotonic() < deadline:
            try:
                if statefile.read_text().split(") ", 1)[1][0] == "Z":
                    return  # Terminated; the system's parent reaper owns this orphan.
            except FileNotFoundError:
                return
            time.sleep(0.01)
        self.assertFalse(statefile.exists())


if __name__ == "__main__":
    unittest.main()
