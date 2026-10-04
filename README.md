![VOLPAROSSA Map banner with a fox formed from golden topographic contours and mountain trails](docs/assets/banner-volparossa-map.png)

# Project VOLPAROSSA Map

**Organic Maps connected to the VOLPAROSSA cooperative network.**

Offline navigation remains the foundation. The integration adds shared map delivery,
short-lived traffic information and private synchronization without turning your journeys
into a public location history.

> Integration in development. This repository is not yet a modified Android/iOS application
> with working decentralized traffic. Component checks are not mobile or live-network proof.

The first executable component now receives a **signed, short-lived traffic snapshot through
the core's existing content interface**. It checks the selected publisher, exact map binding,
revision floor, file hash and expiry before handing data to the application. Its focused
tests cover validation and the process contract; an actual peer-to-map proof is still open.
[Run the development traffic adapter →](docs/TRAFFIC_ADAPTER.md)

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
