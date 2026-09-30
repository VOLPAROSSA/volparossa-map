# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic validator tests, not native rendering, aggregation or routing proof."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from traffic_snapshot import (  # noqa: E402
    MAX_PAYLOAD_BYTES,
    MAX_ROADS,
    TrafficSnapshotError,
    validate_snapshot,
)

UNSET = object()


class TrafficSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "region": "Synthetic Region",
            "map_version": 260930,
            "map_sha256": bytes(range(32)).hex(),
            "now": 1000,
            "publication_expires": 1200,
        }
        self.road = {"feature_id": 12, "segment_index": 3, "direction": 0, "speed_group": 2, "observation_count": 5}
        self.snapshot = {
            "format": "volparossa-road-traffic-v1",
            **{key: self.binding[key] for key in ("region", "map_version", "map_sha256")},
            "window_start": 900,
            "window_end": 1000,
            "expires": 1200,
            "roads": [dict(self.road)],
        }

    def check(self, snapshot=UNSET, **binding):
        return validate_snapshot(json.dumps(self.snapshot if snapshot is UNSET else snapshot).encode(), **(self.binding | binding))

    def rejects(self, expected, snapshot=UNSET, **binding):
        with self.assertRaises(TrafficSnapshotError) as caught:
            self.check(snapshot, **binding)
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected)

    def test_valid_and_scope(self):
        actual = self.check()
        self.assertEqual(actual["roads"], [self.road])
        self.assertEqual(actual["scope"]["observation_counts"], "publisher_assertions")
        self.assertFalse(actual["scope"]["independent_users_verified"])
        self.assertFalse(actual["scope"]["anonymity_guaranteed"])
        self.assertTrue(actual["scope"]["advisory_only"])
        self.assertFalse(actual["scope"]["unknown_is_free_flow"])

    def test_empty_valid_clears_traffic_without_free_flow_entries(self):
        self.snapshot["roads"] = []
        self.assertEqual(self.check()["roads"], [])

    def test_all_supported_speed_groups_and_unknown_counts(self):
        for speed in (0, 1, 2, 3, 4, 5, 7):
            self.snapshot["roads"][0]["speed_group"] = speed
            self.assertEqual(self.check()["roads"][0]["speed_group"], speed)
        self.snapshot["roads"][0]["speed_group"] = 7
        for count in (0, 1, 4, 5, (1 << 32) - 1):
            self.snapshot["roads"][0]["observation_count"] = count
            self.assertEqual(self.check()["roads"][0]["speed_group"], 7)

    def test_sparse_known_road_rejects_whole_snapshot_consistently(self):
        for speed in range(6):
            for count in (0, 1, 4):
                self.snapshot["roads"][0].update(speed_group=speed, observation_count=count)
                self.rejects("sparse_known_traffic")

    def test_unsupported_temporary_block_group(self):
        self.snapshot["roads"][0]["speed_group"] = 6
        self.rejects("unsupported_speed_group")

    def test_duplicate_directional_segment_rejected_but_both_directions_valid(self):
        self.snapshot["roads"].append(dict(self.road))
        self.rejects("duplicate_road")
        self.snapshot["roads"][1]["direction"] = 1
        self.assertEqual(len(self.check()["roads"]), 2)
        self.snapshot["roads"].reverse()
        self.assertEqual([road["direction"] for road in self.check()["roads"]], [0, 1])

    def test_u32_feature_and_fifteen_bit_segment_boundaries(self):
        self.snapshot["roads"][0].update(feature_id=(1 << 32) - 1, segment_index=32767, direction=1)
        self.check()
        for field, value in (("feature_id", 1 << 32), ("feature_id", -1),
                             ("segment_index", 32768), ("segment_index", -1),
                             ("direction", 2), ("direction", -1),
                             ("speed_group", 8), ("observation_count", -1),
                             ("observation_count", 1 << 32)):
            snapshot = copy.deepcopy(self.snapshot)
            snapshot["roads"][0][field] = value
            self.rejects("invalid_road_value", snapshot)

    def test_bool_string_null_and_float_are_not_road_integers(self):
        for field in self.road:
            for value in (False, True, "1", None, 1.0):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["roads"][0][field] = value
                self.rejects("invalid_json_number" if type(value) is float else "invalid_road_value", snapshot)

    def test_exact_dataset_binding_and_canonical_hash(self):
        for field, value in (("region", "Other Region"), ("map_version", 260929),
                             ("map_sha256", "00" * 32)):
            self.rejects("wrong_dataset", self.snapshot | {field: value})
        digest = self.binding["map_sha256"]
        # A truncated upstream BLAKE3 value is not this full local SHA-256 binding.
        for value in (digest.upper(), digest[:-1], digest + "0", "\n" + digest,
                      "AAECAwQFBgcI", "g" * 64, True):
            self.rejects("invalid_dataset_binding", self.snapshot | {"map_sha256": value})
            self.rejects("invalid_expected_binding", map_sha256=value)
        renamed = dict(self.snapshot)
        renamed["map_blake3_base64"] = renamed.pop("map_sha256")
        self.rejects("invalid_snapshot_schema", renamed)

    def test_bad_region_and_version_types(self):
        for region in ("", "x" * 257, "bad\x00region", "region\n", " leading", "\ud800", True):
            self.rejects("invalid_dataset_binding", self.snapshot | {"region": region})
            self.rejects("invalid_expected_binding", region=region)
        for version in (False, 0, -1, 1 << 32, "260930", None):
            self.rejects("invalid_dataset_binding", self.snapshot | {"map_version": version})
            self.rejects("invalid_expected_binding", map_version=version)

    def test_no_postdating_zero_reversed_or_overlong_windows(self):
        for start, end in ((900, 1001), (1000, 1000), (1000, 999), (699, 1000), (-1, 1000)):
            self.rejects("invalid_window", self.snapshot | {"window_start": start, "window_end": end})
        self.check(self.snapshot | {"window_start": 700})

    def test_expiry_and_publisher_ceiling_are_strict(self):
        for expiry in (999, 1000):
            self.rejects("snapshot_expired", self.snapshot | {"expires": expiry})
        self.rejects("invalid_expiry", self.snapshot | {"expires": 1201})
        self.rejects("invalid_expiry", self.snapshot | {"expires": 1301}, publication_expires=1400)
        self.check(self.snapshot | {"expires": 1300}, publication_expires=1300)
        self.rejects("publication_expired", publication_expires=1000)
        self.rejects("invalid_window", now=999)
        self.rejects("invalid_expiry", self.snapshot | {"window_start": 500, "window_end": 700, "expires": 1001})

    def test_bad_time_types_and_ranges(self):
        for field in ("window_start", "window_end", "expires"):
            for value in (True, "1000", None, -1, 1 << 64):
                self.rejects("invalid_window", self.snapshot | {field: value})
        for field in ("now", "publication_expires"):
            for value in (False, "1000", -1, 1 << 64):
                self.rejects("invalid_expected_binding", **{field: value})

    def test_schema_rejects_extra_ids_gps_unknown_fields_and_missing_fields(self):
        for field in ("gps", "node_id", "lat", "lon", "user_id", "observation_time"):
            self.rejects("invalid_snapshot_schema", self.snapshot | {field: "NEVER-ECHO-THIS"})
            snapshot = copy.deepcopy(self.snapshot)
            snapshot["roads"][0][field] = "NEVER-ECHO-THIS"
            self.rejects("invalid_road_schema", snapshot)
        for field in self.snapshot:
            snapshot = dict(self.snapshot)
            del snapshot[field]
            self.rejects("invalid_snapshot_schema", snapshot)
        for field in self.road:
            snapshot = copy.deepcopy(self.snapshot)
            del snapshot["roads"][0][field]
            self.rejects("invalid_road_schema", snapshot)
        self.rejects("unsupported_format", self.snapshot | {"format": "v2"})

    def test_road_container_and_cardinality(self):
        for roads in (None, {}, "roads", 1):
            self.rejects("invalid_roads", self.snapshot | {"roads": roads})
        for road in (None, [], "road", True):
            self.rejects("invalid_road_schema", self.snapshot | {"roads": [road]})
        self.snapshot["roads"] = [self.road | {"feature_id": index} for index in range(MAX_ROADS)]
        self.assertEqual(len(self.check()["roads"]), MAX_ROADS)
        self.snapshot["roads"].append(self.road | {"feature_id": MAX_ROADS})
        self.rejects("invalid_roads")

    def test_duplicate_json_keys_including_escaped_keys_rejected(self):
        valid = json.dumps(self.snapshot)
        samples = [valid.replace('"format":', '"format":"old","format":', 1),
                   valid.replace('"feature_id":', '"feature_id":0,"feature_id":', 1),
                   valid.replace('"region":', '"r\\u0065gion":"old","region":', 1)]
        for text in samples:
            with self.assertRaises(TrafficSnapshotError) as caught:
                validate_snapshot(text.encode(), **self.binding)
            self.assertEqual(caught.exception.code, "duplicate_json_key")

    def test_nonfinite_numbers_exponents_and_non_json_are_closed_errors(self):
        valid = json.dumps(self.snapshot)
        for value in ("NaN", "Infinity", "-Infinity", "1e1000", "5.0"):
            text = valid.replace('"observation_count": 5', f'"observation_count": {value}')
            with self.assertRaises(TrafficSnapshotError) as caught:
                validate_snapshot(text.encode(), **self.binding)
            self.assertEqual(caught.exception.code, "invalid_json_number")
        for payload in (b"not-json-secret", b"\xff", b"{} trailing-secret", b"\xef\xbb\xbf{}"):
            with self.assertRaises(TrafficSnapshotError) as caught:
                validate_snapshot(payload, **self.binding)
            self.assertEqual(caught.exception.code, "invalid_json")
        # Python versions differ in whether JSON nesting first exceeds a parser limit.
        with self.assertRaises(TrafficSnapshotError) as caught:
            validate_snapshot(b"[" * 2000 + b"]" * 2000, **self.binding)
        self.assertIn(caught.exception.code, ("invalid_json", "invalid_snapshot_schema"))
        for value in ([], 1, None, True):
            self.rejects("invalid_snapshot_schema", value)

    def test_payload_size_and_type(self):
        for payload, code in ((bytearray(b"{}"), "invalid_payload_type"), ("{}", "invalid_payload_type"),
                              (b"", "invalid_payload_size"), (b" " * (MAX_PAYLOAD_BYTES + 1), "invalid_payload_size")):
            with self.assertRaises(TrafficSnapshotError) as caught:
                validate_snapshot(payload, **self.binding)
            self.assertEqual(caught.exception.code, code)
        valid = json.dumps(self.snapshot).encode()
        padded = valid + b" " * (MAX_PAYLOAD_BYTES - len(valid))
        self.assertEqual(validate_snapshot(padded, **self.binding)["roads"], [self.road])

    def test_unexpired_replay_requires_caller_revision_state_not_an_invented_claim(self):
        first = self.check()
        second = self.check()
        self.assertEqual(first, second)
        self.assertFalse(first["scope"]["unexpired_replay_detection"])
        # A normalized report is not the closed signed wire payload.
        self.rejects("invalid_snapshot_schema", first)


if __name__ == "__main__":
    unittest.main()
