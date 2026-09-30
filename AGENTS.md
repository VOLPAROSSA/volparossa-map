# VOLPAROSSA Map integration

- Integrate Organic Maps with the shared VOLPAROSSA core. Keep offline maps, search and
  navigation functional without VOLPAROSSA connectivity. Do not build another peer network.
- Pin exact upstream sources and preserve Apache-2.0, third-party, OpenStreetMap and
  Organic Maps notices. Prebuilt map data has its own terms, not the code license.
  Do not remove branding or redistribute map files assuming code licensing permits it.
- No silent location collection: traffic contribution requires explicit, revocable consent.
  Do not publish raw GPS tracks, trip endpoints, permanent node IDs or precise per-user
  observation times. Aggregation is not a guarantee of anonymity or Sybil resistance.
- Traffic is time-sensitive advisory information, not navigation authority. Expired, sparse,
  incompatible or conflicting evidence is unknown, never a fabricated free-flow result.
  Bind road identifiers to the exact map dataset/version/direction before use.
- Private favorites, tracks, locations and AI prompts are not public cache or training data.
  Keep public map data, short-lived traffic aggregates and private backups separate.
- Preserve core relay/exit policy and privacy boundaries. Do not bypass a denied request
  through an implicit direct-origin fallback. Plaintext public content still needs provenance.
- No host network changes, global installations, automatic downloads or background services.
  Use synthetic fixtures for development, narrow checks and truthful integration status.
- Do not claim Android/iOS support, live traffic rendering or rerouting from a desktop
  component test. Track these actual application paths until exercised.
