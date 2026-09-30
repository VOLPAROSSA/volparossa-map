# Connecting Organic Maps to VOLPAROSSA

This map describes concrete seams at the [pinned upstream](UPSTREAM.md).
The first component under development consumes core-delivered, signed named
traffic aggregates. Its validation is not yet an Android/iOS application hook,
a live traffic aggregation service or a rendering/rerouting proof.

## Keep three kinds of data separate

- Public, properly licensed map packages can use provenance-checked cache and
  origin retrieval. Keep offline navigation independent of network availability.
- Short-lived traffic aggregates are advisory public observations with explicit
  freshness, dataset compatibility and provenance, not permanent trip records.
- Favorites, saved routes, tracks, live locations and AI prompts remain private.
  Private backup or explicitly selected encrypted sharing must not publish them
  to public cache or model-training peers.

Traffic contribution requires explicit, revocable consent. Perform GPS-to-road
matching locally. Do not export raw traces, origin/destination, permanent device
identifiers or precise per-person observation times. Even a road segment and
coarse time window may identify someone in a sparse area; signatures, minimum
sample counts and aggregation alone do not establish anonymity or Sybil
resistance. The observation protocol, privacy mechanism and resistance to fake
contributors remain required work. Unknown, expired or conflicting evidence must
not be displayed as known free-flow traffic.

## Next native traffic seam

Upstream's
[`TrafficInfo::RoadSegmentId`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/traffic/traffic_info.hpp)
uses a 32-bit feature ordinal, a **15-bit** segment index (`0..32767`) and a
one-bit direction (`0` forward, `1` reverse). It is not an OpenStreetMap way ID.
Identifiers are valid only for the exact map data that produced them. Validate
numeric limits before constructing the bitfields, then check actual local road
geometry, segment existence and direction.

The native
[`SpeedGroup`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/traffic/speed_groups.hpp)
values are `G0..G5 = 0..5`, `TempBlock = 6`, `Unknown = 7`. G0..G5 represent speed
ratios against local free-road speed, with thresholds 8, 16, 33, 58, 83 and 100
percent; these are not km/h values.
([Thresholds](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/traffic/speed_groups.cpp))
`TempBlock` currently adds a large routing cost, not a legally authoritative or
absolute road closure. Do not let one uncorroborated report become a closure.

Add a deliberately named production entrypoint such as
`TrafficManager::ApplyVerifiedAggregate` **in a future native patch**. It should:

1. Accept only bounded, already authenticated aggregate data bound to the loaded
   map identity, dataset version, full-content digest and a short expiry. Unknown
   map versions and invalid geometry fail before publishing a coloring.
2. Construct a real `TrafficInfo` through a new validated production factory;
   do not reuse `BuildForTesting` or fabricate `MwmId`/road IDs. Keep peer transport
   and core scheduling outside Organic Maps' routing engine.
3. Update manager cache/state and dispatch the same accepted coloring to Drape
   and the routing observer. The current
   [`OnTrafficDataResponse`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/map/traffic_manager.cpp)
   is private, requires an existing map cache entry and skips empty colorings;
   simply calling it is not a complete new input path.
4. Explicitly remove both render and routing state on empty replacement, expiry,
   map replacement, disable and revocation. Upstream's HTTP poll/outdated timers
   do not enforce a core-signed aggregate's expiry. Bound/coalesce updates so
   repeated arrivals do not continually restart route building.
5. Prove actual native route geometry/ETA changes and actual layer rendering
   using synthetic lawful observations and a rights-cleared map. Existing
   [`applying_traffic_test.cpp`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/routing/routing_tests/applying_traffic_test.cpp)
   is useful for route-weight test patterns; mocked coloring alone is not
   end-to-end peer delivery or real-world traffic accuracy evidence.

A first real vertical is: signed aggregate delivered through the existing core
→ bounded application validation → native coloring on an exact loaded map →
route change → expiry restoring unknown/base routing. Public traffic collection,
multi-contributor aggregation, dispute handling and real-world accuracy are
additional work, not implied by a successful consumer test.

## Map downloads and updates

The common
[`MapFilesDownloader`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/storage/map_files_downloader.hpp)
is a real download abstraction. It manages server lists, pending requests,
cancellation and metadata fetches. A production core-backed implementation should
preserve `QueuedCountry` progress/completion, exact catalog/data versions,
download sizes and native file validation. A debug server-URL override is useful
for testing but is not a secure production core bridge.

- Android's
  [`sdk/downloader/MapManager.java`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/java/app/organicmaps/sdk/downloader/MapManager.java)
  invokes native download/update operations; common
  [`HttpMapFilesDownloader`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/storage/http_map_files_downloader.cpp)
  provides queue, progress and resumable-transfer behavior.
- iOS's
  [`MWMStorage`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/iphone/CoreApi/CoreApi/Storage/MWMStorage.mm)
  calls the same storage layer, but
  [`BackgroundDownloaderAdapter`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/libs/storage/background_downloading/downloader_adapter_ios.mm)
  uses native background downloads. A desktop Unix-socket or foreground-only
  bridge is not proof of working iOS suspension/resumption.

Map-relative URLs are `maps/<data-version>/<encoded-filename>`; updates also use
the common metadata/catalog path. Preserve no-network offline use. A denied core
request must not trigger silent direct-origin bypass. Confirm data redistribution
rights before advertising any actual upstream binary map in the public cache.

## Reusable APIs and other useful connections

The repository has a real
[`android/sdk` library module](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/build.gradle)
with JNI/C++ builds, and iOS `CoreApi` wrappers for storage/overlays. These are
useful source seams, not evidence of a stable portable SDK or a mobile VOLPAROSSA
core port. Mobile ownership, key storage, background limits and lifecycle still
need implementation and native execution tests.

The separate official [URL-scheme API](https://github.com/organicmaps/api-ios)
can hand selected locations to an installed Organic Maps app. It cannot inject
a custom traffic backend or replace map downloads. Do not send private routes
through external URL schemes without an explicit user action.

Useful follow-on integrations are private favorites/track backup through core
storage, explicitly encrypted location sharing, and local/private compute for
questions about a selected map area. Keep route computation and location matching
local; never silently feed live location or travel history into cooperative
public compute. Each feature needs its own actual app integration proof.
