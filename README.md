![VOLPAROSSA Map banner with a fox formed from golden topographic contours and mountain trails](docs/assets/banner-volparossa-map.png)

# VOLPAROSSA Map

**Keep the offline map. Add useful road conditions, without publishing your trip.**

A downloaded map helps you find the way; fresh traffic information can help you
decide when to leave or which road to take. VOLPAROSSA Map builds on Organic Maps,
keeping offline search and navigation while working toward shared map downloads,
short-lived road-condition reports and private synchronization.

The [VOLPAROSSA core](https://github.com/VOLPAROSSA/volparossa) supplies shared
content delivery so this app can focus on maps and journeys. The Linux desktop can display a publisher’s signed road conditions and use them when planning a drive. Users opt in with a private local Core handoff. [Traffic adapter](docs/TRAFFIC_ADAPTER.md)

The Linux traffic adapter received a signed 325-byte aggregate for a synthetic road graph from one provider in the twelve-node Core trial. After the route disconnected, the adapter reopened the same private handoff from cache with zero peer bytes. One second after signed expiry, Core refused the next handoff. [Peer trial](https://github.com/VOLPAROSSA/volparossa/actions/runs/44438057954) · [Run the adapter](docs/TRAFFIC_ADAPTER.md)

On the unmodified Linux desktop, the regular **Offline Search** found the Oceanographic Museum of Monaco and the Monte-Carlo Casino and Opera House from the downloaded Monaco map. Selecting each first result opened its place page with the museum or casino feature category. The process had only loopback available. [Repeat the two searches and inspect the result](docs/evidence/offline-place-search-20261119/README.md)

The published Organic Maps Android app calculated two pedestrian routes on the Monaco map after separate cold starts with airplane mode enabled and only loopback available to the emulator. The native route planner reported 390 m in 6 minutes and 490 m in 7 minutes. The separate patched Linux desktop applied 719 synthetic observations to the actual Monaco graph. Its car route changed from 1.08 km to 3.75 km and returned to the same 1.08 km route at expiry. [Desktop traffic trial](docs/NATIVE_TRAFFIC.md) [Android trial](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/44444481182) · [Native integration](docs/INTEGRATION.md)

In the VOLPAROSSA Android WebDebug build, **My Position** can check the latest available fix on the device after an explicit tap. It displays a short-lived quality summary derived from Android’s horizontal and vertical error estimates. The installed x86-64 emulator trial used fictional fixes and preserved both offline routes. [My Position and its limits](docs/DPS_INTEGRATION.md)

## What the network can add

- **Maps and updates:** reuse authenticated public map chunks from nearby or remote peers,
  subject to map-data distribution rights. Keep ordinary authorized downloads and already
  downloaded offline maps usable when cooperative delivery is unavailable.
- **Traffic conditions:** share recent conditions for directed road segments, bound to the
  matching map version. Display congestion and, later, use credible fresh observations in
  ETA and route selection. Unknown or expired conditions must remain visibly unknown.
- **Private synchronization:** encrypted favorites, saved places and routes across your own
  devices, using private storage rather than the public cache. Private tracks stay private.
- **Useful compute:** optional natural-language place search and route explanations grounded
  in map data. Do not upload a private location, itinerary or travel history to arbitrary
  compute peers. Existing deterministic offline navigation remains available.

## Traffic without publishing your trip

Reading traffic information and contributing observations are separate permissions. Sending
observations must be explicitly enabled and can be stopped. The intended shared representation
is a short-lived road-condition aggregate, not raw GPS coordinates, a sequence of individual
movements or a permanent identity tied to a trip.

Aggregation, freshness checks, limited contribution rates and independent corroboration are
needed before this becomes a live service. Several reports do not necessarily mean several
independent people, and an authenticated publisher can still be wrong. Sparse or conflicting
data must not turn into an authoritative congestion claim. Transport privacy alone does not
make location reports anonymous.

## Integration boundaries

The reusable networking, authenticated content transport, private storage and resource
coordination belong in [VOLPAROSSA core](https://github.com/VOLPAROSSA/volparossa).
This repository owns the Organic Maps application adapters. Android and iOS integration,
mobile background limits and real map rendering/routing must be implemented and checked;
the existing Linux core cannot simply be installed unchanged on either mobile platform.

Organic Maps code and prebuilt map data have different license terms. Preserve upstream
branding and OpenStreetMap attribution; do not treat the code's Apache-2.0 license as
permission to rebrand or redistribute every map asset. No upstream map files are bundled here.

Project reference: [Organic Maps](https://organicmaps.app/).

Development references: [pinned upstream and licenses](docs/UPSTREAM.md) ·
[native and mobile integration points](docs/INTEGRATION.md).
