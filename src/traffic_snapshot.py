# SPDX-License-Identifier: GPL-3.0-only
"""Strict validation of public, dataset-bound aggregated road traffic.

This module neither collects observations nor authenticates publishers. Counts are
publisher assertions, not a proof of independent contributors or anonymity. The
caller verifies the core publication and supplies its authenticated expiry and map
binding. A stateless validator cannot identify an unexpired duplicate or rollback:
the caller must retain its accepted core revision/window floor separately.
"""

import json
import re


FORMAT = "volparossa-road-traffic-v1"
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_ROADS = 4096
MAX_WINDOW_SECONDS = 300
MAX_TTL_SECONDS = 300
MIN_OBSERVATIONS = 5
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1
SNAPSHOT_FIELDS = frozenset(
    ("format", "region", "map_version", "map_sha256", "window_start", "window_end", "expires", "roads")
)
ROAD_FIELDS = frozenset(("feature_id", "segment_index", "direction", "speed_group", "observation_count"))


class TrafficSnapshotError(ValueError):
    """Closed error code only: do not echo payloads, region names or parser text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require(condition, code):
    if not condition:
        raise TrafficSnapshotError(code)


def _integer(value, low, high):
    # bool is an int subclass in Python, but never a valid traffic number.
    return type(value) is int and low <= value <= high


def _region(value):
    if type(value) is not str or not value or value.strip() != value:
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return len(encoded) <= 256 and all(ord(char) >= 32 and not 127 <= ord(char) <= 159 for char in value)


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _reject_number(_value):
    # Every numeric field is an integer; reject floats and nonfinite extensions
    # at parsing time instead of normalizing 1.0/1e0 into an accepted integer.
    raise TrafficSnapshotError("invalid_json_number")


def validate_snapshot(
    payload: bytes,
    *,
    region: str,
    map_version: int,
    map_sha256: str,
    now: int,
    publication_expires: int,
) -> dict:
    """Return a normalized snapshot plus an explicit, non-wire ``scope`` object.

    Known speed groups require at least five asserted observations. Sparse known
    roads reject the whole snapshot; unknown group 7 may have zero observations.
    An empty roads array is valid and means clearing traffic, never free flow.
    The expected map_sha256 is the caller's independently computed FULL local
    .mwm-file SHA-256. It is this adapter's binding, NOT the upstream catalogue's
    truncated 9-byte BLAKE3 hash, and is not an upstream signature/provenance proof.
    No map file is read here; the supplied lowercase hexadecimal digest must match.
    """
    _require(type(payload) is bytes, "invalid_payload_type")
    _require(0 < len(payload) <= MAX_PAYLOAD_BYTES, "invalid_payload_size")
    _require(
        _region(region)
        and _integer(map_version, 1, UINT32_MAX)
        and _hash(map_sha256)
        and _integer(now, 0, UINT64_MAX)
        and _integer(publication_expires, 1, UINT64_MAX),
        "invalid_expected_binding",
    )
    _require(publication_expires > now, "publication_expired")
    try:
        raw = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except TrafficSnapshotError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise TrafficSnapshotError("invalid_json") from None
    _require(type(raw) is dict and raw.keys() == SNAPSHOT_FIELDS, "invalid_snapshot_schema")
    _require(raw["format"] == FORMAT, "unsupported_format")
    _require(
        _region(raw["region"])
        and _integer(raw["map_version"], 1, UINT32_MAX)
        and _hash(raw["map_sha256"]),
        "invalid_dataset_binding",
    )
    _require(
        raw["region"] == region
        and raw["map_version"] == map_version
        and raw["map_sha256"] == map_sha256,
        "wrong_dataset",
    )
    start, end, expires = raw["window_start"], raw["window_end"], raw["expires"]
    _require(
        all(_integer(value, 0, UINT64_MAX) for value in (start, end, expires))
        and 0 < end - start <= MAX_WINDOW_SECONDS
        and end <= now,
        "invalid_window",
    )
    _require(expires > now, "snapshot_expired")
    _require(expires <= end + MAX_TTL_SECONDS and expires <= publication_expires, "invalid_expiry")
    _require(type(raw["roads"]) is list and len(raw["roads"]) <= MAX_ROADS, "invalid_roads")
    seen = set()
    roads = []
    for road in raw["roads"]:
        _require(type(road) is dict and road.keys() == ROAD_FIELDS, "invalid_road_schema")
        _require(
            _integer(road["feature_id"], 0, UINT32_MAX)
            and _integer(road["segment_index"], 0, 32767)
            and _integer(road["direction"], 0, 1)
            and _integer(road["speed_group"], 0, 7)
            and _integer(road["observation_count"], 0, UINT32_MAX),
            "invalid_road_value",
        )
        _require(road["speed_group"] != 6, "unsupported_speed_group")
        _require(
            road["speed_group"] == 7 or road["observation_count"] >= MIN_OBSERVATIONS,
            "sparse_known_traffic",
        )
        key = (road["feature_id"], road["segment_index"], road["direction"])
        _require(key not in seen, "duplicate_road")
        seen.add(key)
        roads.append({field: road[field] for field in (
            "feature_id", "segment_index", "direction", "speed_group", "observation_count"
        )})
    roads.sort(key=lambda road: (road["feature_id"], road["segment_index"], road["direction"]))
    return {
        "format": FORMAT,
        "region": region,
        "map_version": map_version,
        "map_sha256": map_sha256,
        "window_start": start,
        "window_end": end,
        "expires": expires,
        "roads": roads,
        "scope": {
            "observation_counts": "publisher_assertions",
            "independent_users_verified": False,
            "anonymity_guaranteed": False,
            "advisory_only": True,
            "unknown_is_free_flow": False,
            "unexpired_replay_detection": False,
        },
    }
