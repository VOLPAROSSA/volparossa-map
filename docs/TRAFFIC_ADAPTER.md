# Core-backed traffic input

`src/core_traffic.py` is a Linux development consumer of the **existing**
`volparossa content fetch-name` interface. No new peer network, HTTP service,
location collector, identity authority or privileged helper operation is added.

The producer must already publish the bounded public JSON below through an
authorized core content service. A publisher's signature establishes provenance,
not truth, independent contributors, anonymity or authority over navigation.
No production traffic publisher is configured or implicitly trusted here.

## Run

Prepare an owner-only output directory (mode `0700`), an authorized running core
and its control socket, and an agent-owned cache location. Use a publisher key
obtained independently, not one supplied by an arbitrary cache peer. The map
binding is a full SHA-256 computed from the exact installed `.mwm` bytes by the
application; it is **not** Organic Maps' truncated BLAKE3 catalog field.

```sh
python3 -B src/core_traffic.py \
  --core /absolute/path/to/volparossa \
  --control-socket /run/volparossa/control/agent.sock \
  --publisher-key TRUSTED_64_HEX_PUBLIC_KEY \
  --name traffic/SELECTED_REGION/SELECTED_VERSION \
  --min-revision SELECTED_REVISION \
  --cache /absolute/agent-owned/traffic-cache \
  --region SELECTED_REGION --map-version SELECTED_VERSION \
  --map-sha256 FULL_INSTALLED_MAP_SHA256 \
  --output /absolute/private-directory/new-traffic.json
```

Replace the uppercase placeholders; they are not working production defaults.
Use `--reuse-cache` only to reopen this operation's existing agent-owned cache;
that retains the core's observed revision floor. This is not a global latest-version
oracle. The consumer never enables participation, changes the host network or
uses direct HTTPS to work around an unavailable/denied core request.

The output is a **local, private handoff**, not an independently signed portable
proof. It contains the accepted publisher/revision/manifest reference and a
normalized snapshot. Existing outputs are never overwritten. Application code
must adopt new snapshots explicitly, reject rollback against its last accepted
state, and clear stale traffic even when subsequent downloads fail.

## Public payload v1

```json
{
  "format": "volparossa-road-traffic-v1",
  "region": "Synthetic",
  "map_version": 260929,
  "map_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "window_start": 1790784000,
  "window_end": 1790784060,
  "expires": 1790784240,
  "roads": [
    {"feature_id": 42, "segment_index": 3, "direction": 1,
     "speed_group": 2, "observation_count": 8}
  ]
}
```

This is synthetic illustrative data, not a usable road or current traffic report.
Road IDs are map-local feature/segment/direction tuples, **not OSM way IDs**.
The segment index is 15-bit. Known speed groups `0..5` follow the pinned native
Organic Maps buckets, not km/h; `7` is unknown. `6` (temporary block) is deliberately
not admitted by this observation-only contract. Actual local road geometry must
still be validated before native application.

- At most 2 MiB and 4,096 directed segments per accepted payload.
- Observation window at most five minutes, never in the future; expiry no later
  than five minutes after that window and no later than the signed publication.
- Known groups require five asserted observations. Sparse known entries reject
  the snapshot. Counts are publisher assertions, not an independent-user check.
- An empty snapshot explicitly clears traffic; omitted, unknown or expired
  information never implies a clear road. Duplicate fields/roads, unknown fields,
  GPS/user IDs, unsupported versions and invalid types are rejected.

The adapter supplies a 2 MiB cache quota. The core's named-content network path
checks authenticated object length against this quota before body retrieval;
the adapter also checks the received file length independently. This bounds the
accepted object, not all transport overhead or retry bytes. It is not a claim
that mobile resource scheduling is implemented.

## Verification and remaining application work

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

The 31 current checks cover strict traffic parsing, dataset/time binding, empty
clears, receipt correlation, hash readback, non-overwrite handoff and real child
process timeout/output handling. Core receipt/file fixtures in adapter tests are
explicitly synthetic. They do not prove a live peer transfer, native rendering,
GPS aggregation, Android/iOS execution or traffic-aware navigation.

Next: a real core-publication/peer-delivery trial, then the native manager input
described in [INTEGRATION.md](INTEGRATION.md), including rendering **and** route-cache
expiry. Consent-based local observations, privacy-preserving multi-contributor
aggregation, abuse resistance, and independent source corroboration remain open.
