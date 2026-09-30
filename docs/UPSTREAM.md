# Organic Maps source baseline

## Exact release and evidence

Checked **2026-09-30** against the official release API and remote Git tag:

- [Latest stable GitHub release: `2026.09.29-32-android`](https://github.com/organicmaps/organicmaps/releases/tag/2026.09.29-32-android), published 2026-09-29 at 21:16:46 UTC.
- Annotated tag object: `1253338dcd7bc6c1871e4a613468187e1d1e93e6`.
- Peeled source commit: `b20d1417eef1e3c63a447ac8658db4307a066024`.

[upstream.lock.json](../upstream.lock.json) records the exact sizes and SHA-256
hashes of 16 essential source/license files fetched from that commit. This is an
Android release tag. Inspecting its common and iOS source does not establish an
iOS App Store release, Android/iOS build, traffic service or functioning fork.
No upstream checkout, dependency install, application execution or map-data
download was performed for this audit.

## Source license is not the map-data license

The source uses [Apache-2.0](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/LICENSES/Apache-2.0.txt).
The root `LICENSE` is a symbolic link to that file: a downloader must preserve the
target text, not accidentally package only the 23-byte link destination.
Preserve the original license, copyright notices, modification notices and
[NOTICE](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/NOTICE).
The notice requests visible Organic Maps Project attribution and a link in
user-facing locations. Third-party terms remain in `.reuse/dep5`,
`data/copyright.html`, `3party` and `tools/osmctools`; the repository's GPL license
does not replace those upstream licenses.

Compiled `.mwm`, `packed_polygons.bin` and other upstream binary data have
[separate terms](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/DATA_LICENSE.txt).
They require visible Organic Maps and OpenStreetMap attribution with links on
the map and in About/Main Menu. They also state that white-labeling or rebranding
requires explicit written permission. This project has **not** verified such
permission and does not bundle or authorize redistribution of those files.
Do not assume an Apache-licensed code fork permits rebranding upstream binary
maps. Generating maps from independently obtained source data is a separate
option, with the underlying ODbL and other source-data obligations still applying.

## What traffic support really exists

There is genuine reusable traffic code, but the pinned
[`private.h`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/private.h)
sets `TRAFFIC_DATA_BASE_URL` to an empty string. The shipped source therefore does
not supply a working live-traffic source merely because traffic classes or icons
exist. No live backend was exercised by this audit.

[`TrafficInfo`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/traffic/traffic_info.cpp)
loads per-map road keys and retrieves speed buckets through HTTP. Existing
[`TrafficManager`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/map/traffic_manager.cpp)
forwards accepted coloring to both Drape rendering and the routing observer.
[`RoutingSession`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/routing/routing_session.cpp)
updates its cache and rebuilds routes, while the
[`car edge estimator`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/routing/edge_estimator.cpp)
applies congestion weights. These are useful integration points, not proof of
VOLPAROSSA traffic rendering or rerouting. See [INTEGRATION.md](INTEGRATION.md)
for the required production boundary and privacy limits.

## Map-download integrity

The pinned
[`Storage::OnDownloadFinished`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/storage/storage.cpp)
checks `coding::Blake3::CalculateMwmBase64` against the country catalog when
integrity validation is enabled. The catalog digest is the **first 9 bytes of
BLAKE3, encoded as 12 base64 characters**, not SHA-1 or SHA-256.
([Exact hash definition](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/coding/blake3.hpp))

That catalog field cannot be relabeled as the core's verified HTTPS SHA-256 input.
A peer-cache path needs an independently authenticated full-content digest, or
an explicit supported digest-verification extension with its actual assurance
stated. Downloading bytes from an arbitrary peer and hashing them does not prove
their publisher or origin. Preserve catalog/map version and the native integrity
check as well as the core's provenance requirements.
