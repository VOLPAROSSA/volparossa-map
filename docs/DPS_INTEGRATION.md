# DPS location integration boundary

This document maps the shared VOLPAROSSA positioning service (DPS) to Organic
Maps' pinned native location inputs. A separate Android native-quality fixture
is now authored below; its source checks are not Android execution evidence or
evidence of improved positioning. The core design is
[`DPS.md` at core PR #299 candidate `4e673a5a`](https://github.com/VOLPAROSSA/volparossa/blob/4e673a5a5a9b0c3ad08325999496b72461ba301a/docs/services/DPS.md).
The corrected contract uses request field 64 and response field 64, retaining
local-control envelope version 2 and DPS schema version 1. The initial prototype's
request 46/response 34 overlapped retained Storage interfaces and are superseded
without DPS aliases. Consumers must not implement those prototype assignments.

Earlier source `1fd9397e` passes Quality `39612903195`, including 17 DPS tests,
and CodeQL `39612896634`; those results do not accept the corrected allocation.
The corrected source subsequently passed Quality `39616727891` and CodeQL
`39616721234` and was merged through core PR299. Core main
[`e3b78c6376ccfe6ca9a4af1a60ffe65771ab1feb`](https://github.com/VOLPAROSSA/volparossa/tree/e3b78c6376ccfe6ca9a4af1a60ffe65771ab1feb)
also contains the separately accepted, bounded dormant-daemon proof and the
Storage composition. Its tested non-documentation blobs match composition
source `aa876ea25832efc1cda797b2e4c6be992ed15d14`, whose Quality `39639849031`
and CodeQL `39639840728` pass. These checks establish synthetic assessment and
the recorded daemon lifecycle only; no native Map adapter, location permission,
sensor or positioning improvement is accepted. The
[core evidence ledger](https://github.com/VOLPAROSSA/volparossa/blob/e3b78c6376ccfe6ca9a4af1a60ffe65771ab1feb/docs/IMPLEMENTATION_STATUS.md)
preserves the original build, lockfile and lint failures and exact evidence limits.

## Existing native inputs

All source pointers below are pinned to Organic Maps
[`b20d1417eef1e3c63a447ac8658db4307a066024`](UPSTREAM.md).

- **Android acquisition and lifecycle:**
  [`LocationHelper`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/java/app/organicmaps/sdk/location/LocationHelper.java)
  filters inactive, stale and lower-quality fixes, manages listeners and
  timeout, and forwards ordinary fixes through `LocationState.nativeLocationUpdated`.
  `LocationProviderFactory` selects GMS or `AndroidNativeProvider`; leave that
  OS-provider selection and lifecycle intact. Permission requests remain in
  the app (`MwmActivity.onMyPositionModeChanged`) and must explicitly cover any
  new sensor access.
- **Android JNI conversion:**
  [`LocationState.java`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/java/app/organicmaps/sdk/location/LocationState.java)
  and [`LocationState.cpp`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/cpp/app/organicmaps/sdk/LocationState.cpp)
  convert a platform fix to `GpsInfo`, label it `EAndroidNative`, and send it
  to routing and `GpsTracker`. Do not disguise DPS results as Android
  `Location` objects or send them through this callback. Add a separately typed
  source/quality seam during native implementation.
- **iOS acquisition and lifecycle:**
  [`MWMLocationManager.mm`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/iphone/Maps/Core/Location/MWMLocationManager.mm)
  owns `CLLocationManager`, authorization, app activity and accepted location
  delivery. `location_util::gpsInfoFromLocation` converts accepted
  `CLLocation`s; the manager forwards them to routing, observers and
  `GpsTracker`. Keep this manager and its OS permission/status behavior intact.
- **Common consumer:** `Framework::OnLocationUpdate(GpsInfo)` feeds routing;
  route matching and `MyPositionController` use `horizontalAccuracy` for
  matching and the visible error circle. `Framework::GetCurrentPosition`
  exposes only a projected point and loses quality metadata, so it is not a
  sufficient DPS input/output boundary.

## Data, trust and lifecycle requirements

`GpsInfo` represents latitude/longitude in degrees, altitude in metres,
horizontal/vertical accuracy in metres, bearing clockwise from true north,
speed in m/s, and a Unix-epoch timestamp in seconds. Its `m_source` enum identifies
the existing source class, but it lacks coordinate-frame/altitude-datum and
DPS-specific provenance, correlation, integrity or conflict fields. The core contract
uses WGS84 ECEF XYZ in metres or an explicitly named local Cartesian frame.
Conversion to Map geodetic coordinates therefore needs a tested WGS84
ellipsoid transform and an explicit altitude-datum policy. A local frame needs
a known origin, orientation and epoch plus a tested transform; reject unknown
or incomparable frames. Never relabel ECEF or local coordinates as latitude
and longitude. Caller-supplied frame and source claims are not independently
verified by DPS.

Carry DPS provenance, age, integrity/conflict and its consistency radius in a
separate typed quality result. Do not present the radius as calibrated
probability or silently map it to OS `horizontalAccuracy`. The current core
candidate can return an input position only as explicitly unverified; it does
not fuse or manufacture a more accurate fix. Keep Map's OS provider and
lifecycle available, but select a displayed fix through an explicit policy that
shows unresolved integrity, age and uncertainty. A conflicting GNSS input must not
become a silently trusted OS fallback. If neither source is suitable, report
unavailability rather than silently substituting or retaining a suspect claim.

Any future consumer must be an optional, app-owned, least-authority adapter to
reusable core logic. The core agent's Unix control socket is administrative
and must not be exposed to the mobile app. Do not duplicate the estimator or
override platform location providers. DPS selection, sensing, contribution
and persistence require separate explicit permissions/consent. Do not route a
DPS result to `GpsTracker` or `TrackRecorder`, or retain it as movement history,
unless the existing independent recording choice explicitly authorizes that
recording. Revocation and expiry must clear the app's DPS-selected state.

## App-owned Android quality candidate

Reuse the shared Rust `DpsAssessRequest::assess` implementation through a bounded,
separately typed native bridge. Do not connect the app to the administrative
socket or reimplement the assessor in Java/C++. Keep any necessary JNI/FFI unsafe
boundary separate from the shared Rust code's existing unsafe-code prohibition.
The new [`integrations/dps-native`](../integrations/dps-native/) candidate adds
the bridge and consent controller as a separate module in a disposable source
transformation. It does not patch the existing Organic Maps location pipeline.

The existing `:sdk` module builds the `organicmaps` C++ shared library through
[`android/sdk/src/main/cpp/CMakeLists.txt`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/cpp/CMakeLists.txt)
and [`android/sdk/build.gradle`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/build.gradle).
It currently has no Rust linkage. A real adapter therefore needs explicit
per-ABI Rust compilation and linkage plus a separate JNI quality-result API.
Keep it outside `LocationState.nativeLocationUpdated`, whose input is forwarded
as an Android GPS fix and can reach track recording.

The initial native fixture must display synthetic Cartesian assessment quality
while Android location permission remains denied. It must not subscribe to
`LocationHelper`: `addListener` immediately replays a saved real fix, and
`start()` can start both location polling and `SensorHelper`. A separate
app-owned coordinator needs a consent generation and monotonic expiry, checked
before work and again before publishing a result. Disable, revocation and
background transition must clear selected DPS state and discard pending or late
results. This controls the app's own work; it does not prove OS isolation from
other apps or revoke copies already delivered to a caller.

The native acceptance fixture must exercise unavailable/offline input, expiry,
revocation during work, background/resume and a late completion from an obsolete
generation. Verify actual displayed quality, no permission prompt, no location
provider or sensor start, no network activity or location persistence, and joined
owned work. Unit tests alone cannot accept this native behavior. Coordinate
conversion, real sensors, GNSS integrity and navigation remain later requirements.

The pinned upstream
[`Android workflow`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/.github/workflows/android-check.yaml)
provides a build/test reference, including `sdk:connectedDebugAndroidTest` on an
API 35 x86_64 emulator. Its
[`build configuration`](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/groovy/common-config.gradle)
uses SDK 36 and NDK `29.0.14206865`; Java 17, Gradle `9.6.0` and CMake 3.22.1 or
later are declared in the pinned Gradle configuration. Before an actual build,
bind exact reviewed tool archives and Rust target/linker inputs in the existing
hosted workflow. These upstream declarations do not prove a VOLPAROSSA build
or authorize installation on the development host. Preserve Apache-2.0 and all
separate source/data notices.

## Scope and acceptance stages

Investigate optional supplementary location for weak GNSS, tunnels and indoor
settings, robust positioning and GNSS-manipulation detection. Keep offline
navigation functional, make uncertainty visible, and never share routes or
movement history automatically. This scope is research and staged validation,
not a claim that these environments are already solved.

1. **Synthetic:** test frame conversion, units, altitude handling, bounds,
   stale/expiry, conflict, source provenance, unavailable results and consent
   revocation. Verify OS fixes remain usable and no route/history is published.
2. **Native app:** add an explicitly selected DPS input on Android and iOS
   without changing OS provider or permission behavior. Exercise lifecycle,
   source switching, quality display, expiry and offline navigation in actual
   native builds/devices. Source tests alone do not establish this acceptance.
3. **Hardware evidence:** test named sensor/platform adapters against
   independent reference measurements and controlled fault injection. Report
   simulated results, working hardware trials and demonstrated positioning
   gains separately. Never perform unauthorized radio scans or GNSS
   interference, or change host location/network settings.

Lawful bounded hardware/estimator research may proceed alongside native
integration; the stages distinguish evidence, not a mandatory build order.

## Isolated Android JNI fixture: source status and reproduction

Map base `e8fdcca87575e238894d579797379de9c3516384` now has an authored,
optional `:volparossa_dps_jni` Android library fixture. The production main
manifest requests no permissions and declares no Activity. A nonexported debug
Activity starts disabled, requires an explicit consent button, and assesses
only a fixed public synthetic Cartesian fixture after a separate button click.
It displays claim consistency, conflicts and all seven limitations. No coordinate,
identity, precise observation time, calibrated accuracy or position is returned.

The immutable native dependency is core
[`4d4612200e0a4a46546e0c827a3f3e7f0b73c669`](https://github.com/VOLPAROSSA/volparossa/tree/4d4612200e0a4a46546e0c827a3f3e7f0b73c669),
tree `bac45917af63e152ddb232a885446cb7221c84bb`.
Its source gate [39661011331](https://github.com/VOLPAROSSA/volparossa/actions/runs/39661011331)
was independently audited for 12 Rust tests, 18 C and 18 C++ sanitized cases,
and 36 Memcheck cases. All 14 non-workflow ABI files match corrected predecessor
`aae217d3341d50ea3f925ac58059a705ba215f75`; all 797 main blobs are preserved.
That evidence concerns the core library and its C/C++ callers, not this JNI glue
or Android. Original artifact `12374096078` has SHA-256
`01ceb0c715afadd164ab3f56b02da56066b5e53c9ec6ed9f12a3d3340ebcba57`.
Earlier core source `b97a5d1a467270f40ad368b3055cca6de0d6a341` passed
its bounded native gate, but its combined Quality run failed cargo-deny for a
missing path-dependency version. The corrected successor `aae217d3` and current
composition preserve runtime/header/goldens. Current combined Quality
`39661019902` and CodeQL `39661012097` were still pending when this candidate was
authored; they are not represented as accepted.
`pins.json` binds that core header and all four canonical bare-request goldens,
plus the existing Organic Maps commit and six build/configuration inputs.
`prepare.py` requires clean, exact, full-history source checkouts and a new
output directory. It clones Organic Maps with Git metadata for `version.sh`,
appends one settings include, and copies the new module, header and public
fixtures. Existing modules, source/data licenses, notices, upstream branding,
location callbacks and map files are preserved. No upstream source is vendored.
For source-only preparation, with those exact clean checkouts already available:

```sh
python3 -B integrations/dps-native/prepare.py --upstream /absolute/pinned-organicmaps --core /absolute/pinned-core --output /absolute/new-disposable-output
```

The thin JNI C target links the Rust static library and its Android system runtime
libraries only, without `:sdk`, `organicmaps`, `jni_helper` or `JNI_OnLoad`.
It checks nulls, Java array lengths, time and the 16 KiB input limit before a
bounded stack copy, erases its copy, and returns a fixed closed error status.
Java validates exactly nine fields, schema/ABI versions, status, frame, bounded
claim/pair counts and exactly `0x7f` limitations. Raw errors and requests are not
logged. JNI exceptions are cleared into the closed internal error code; VM
allocation/abort/process faults remain outside a recoverable-result guarantee.

`QualityController` owns one worker for its entire lifetime, with capacity for
one pending, running or unpublished request. Consent generations have a bounded
30-second maximum TTL using Android elapsed realtime. Authority is checked before
native work and again before UI publication. Revoke, disable, background and
expiry clear displayed state, erase queued requests and remove pending callbacks;
a late completion cannot publish under a newer generation. In-flight synchronous
native work is allowed to finish, with its stale result discarded. Closing is
nonblocking on the UI thread. A bounded observer method waits and joins the actual
worker only from another thread. Resume does not silently enable DPS. This
controls this component's ownership, not copies a caller retained or other apps.

The authored eight instrumentation tests use the actual Rust/JNI library for the
four goldens and closed failures, validate contradictory summaries, exercise
in-flight revocation, generation changes, expiry and late publication, display
quality through actual debug buttons, move the Activity's task to the background
and resume it, assert no declared permissions, and join owned workers. Background
coverage now uses a freshly displayed result without prior revoke/expiry, plus
fresh-TTL in-flight work that completes after background/resume. A package-private,
null-in-production observer confirms actual publication posting while a test holds
the UI; the injected clock expires only after posting and before UI delivery.
This separates worker-completion expiry from publication-window expiry. Every
controller is closed and its real worker joined from the instrumentation thread.
Injected clock/delay tests are synthetic lifecycle evidence, not hardware sensing.
All eight tests have **not been run**. Inert local verification is:

```sh
python3 -B integrations/dps-native/source_gate.py
python3 -B -m unittest discover -s integrations/dps-native -p 'test_*.py' -v
git diff --check
```

These pass for the authored source: source bindings plus 13 inert tests (seven
Python/Git/archive preparation tests and six lifetime orchestration shims).
The shims cover deferred signal delivery until child ownership is registered,
unblocked child signals without a pre-exec hook, repeated INT/TERM deadlines,
original-failure precedence, honest incomplete cleanup/state preservation,
state removal after simulated drain, and rejection of local cancellation trials.
They launch no cancellation child, deliver no OS signal, create no namespace and
make no privileged call; passing them is not cancellation runtime acceptance. The test fixtures for preparation mechanics are inert text;
they are never passed to JNI and never represented as real Protobuf goldens.
No Rust, native, Java, Gradle, SDK, Node, emulator or hardware execution occurred
on the development host. No workflow was dispatched for this candidate.

## Hosted candidate and evidence boundaries

`.github/workflows/dps-native.yml` runs only inert source checks automatically.
Its native job is manual and defaults off; the principal must review the source
and independent review before selecting `execute_reviewed_fixture`. `hosted.py`
rejects local/nonreviewed execution and requires GitHub-hosted Ubuntu 24.04,
this repository/workflow, exact clean Map source and a unique run directory.
It does not enable a paid service, publish an APK or create a release.

The candidate explicitly acquires official pinned Gradle 9.6.0, SDK 36 revision 2,
build tools 36.0.0, Organic Maps' NDK 29.0.14206865, CMake 3.22.1,
platform tools 37.0.1, emulator 37.2.12 and the API36 x86_64 default system image
revision 2. Published Google archive sizes/SHA-1 were checked against official
[repository metadata](https://dl.google.com/android/repository/repository2-3.xml)
and [system-image metadata](https://dl.google.com/android/repository/sys-img/android/sys-img2-3.xml).
The [official historical NDK list](https://github.com/android/ndk/wiki/Unsupported-Downloads)
confirms the r29 Linux pin. Gradle has an
[official SHA-256](https://services.gradle.org/distributions/gradle-9.6.0-bin.zip.sha256).
No independent official SHA-256 was located for these Google archives: their
published SHA-1 plus exact HTTPS URL and size is the acquisition trust boundary;
newly observed SHA-256 receipts are provenance, not independent authorization.
Archive paths, links, member counts and expanded sizes are bounded; notices remain.

The runner's preinstalled Java 17 and rustup launcher are runner-image trust
inputs, not newly hash-pinned distributions. The candidate records version output,
uses private Rust/Cargo homes, installs the exact official Rust 1.85.0 target with
rustup's normal checksum verification, requires its exact compiler version, and
compiles the pinned Rust source for Android x86_64/API21. No prebuilt DPS binary
is accepted. The selected Android API36 emulator proves neither API21 compatibility
nor other ABIs/iOS. Upstream AGP 9.2.1 is retained; its
[official compatibility table](https://developer.android.com/build/releases/agp-9-2-0-release-notes)
requires build tools 36.0.0 and Java 17. The root project's other plugins configure,
but this fixture does not build the Organic Maps C++ library or depend on its SDK.

Public Gradle/Maven dependency acquisition is explicit. Generated checksum
verification metadata records acquired dependency consistency, not prior human
review or release authorization. A second clean APK rebuild requires offline,
strict dependency verification inside a new network/PID/mount namespace. The same
namespace runs the installed synthetic test APK on the pinned emulator, with only
namespace loopback active and guest Wi-Fi/data disabled. Source-level absence of
sensor/location/network APIs and the test's permission assertion are distinct from
physical sensor traces, process identity attestation or measured absence of every
socket attempt. There is no admin socket, daemon or host location access.

Privilege is limited to read-only host firewall baselines, creating disposable
namespaces, activating their loopback, and terminating the exact owned supervisor
process group if necessary. Namespace bootstrap drops root UID/GID/groups before
build/emulator work. No host DNS, routes, nftables rules, VPN or sysctls are changed.
Before/after route/resolver/firewall hashes must match. Command output, elapsed
wall time and sampled descendant RSS are bounded; the RSS monitor is not a hard
kernel cgroup memory limit. Commands register and drain their process groups. Runtime PID namespace
termination supplies the final descendant boundary; source checks alone do not
establish actual drain. Failed cleanup or changed baselines fail the job.

Only source/tool/APK hashes, public step receipts, dependency checksums, installed
synthetic test results and cleanup receipts are artifact candidates. Raw logs,
SDKs, Rust/Gradle caches, ADB keys and AVD data are excluded. After confirmed registered-group drain, removal of this run's generated private
source/tool state and retention of fixed public receipts are required for success.
Unconfirmed cleanup preserves remaining state for disposable-runner teardown. No cache reuses test outcomes. Hard workflow termination
may prevent final receipts; the disposable runner teardown is then the external
cleanup boundary and no completed cleanup claim is accepted.

### Catchable cancellation candidate: unrun

Independent source review found that the first candidate lacked catchable TERM
handling, so its `finally` blocks did not establish cancellation cleanup. The
current workflow uses `exec python3` for the actual step entry. The process
lifetime installs INT/TERM handlers before owned work and registers each returned
`Popen` child before a deferred cancellation can raise. Signal delivery remains
unblocked during spawn; no `pthread_sigmask` blocking or unsafe `preexec_fn` is
used. Entry rejects an already blocked INT/TERM mask. Ordinary exec resets caught
handlers to defaults; the hosted control separately verifies an ordinary child's
mask/default TERM handler and actual termination by TERM.

[GitHub's cancellation reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation)
describes INT to the step entry, 7,500 ms grace, then TERM and 2,500 ms before
process-tree termination. Catchable cleanup uses at most nine seconds after INT
and two seconds after TERM, with later TERM able to shorten the deadline. Repeated
signals are recorded and never extend it or interrupt cleanup. Natural failure
cleanup has a 15-second deadline. Original workload/cancellation failure retains
precedence over cleanup errors; incomplete drain/state removal is reported as
incomplete and cannot produce a successful receipt. Group TERM/KILL/reaping,
public receipts and private-state deletion are attempted within those budgets.
Removing a complete SDK/source tree may exceed them: this is a failure boundary,
not a guarantee of graceful runner cancellation.

Before any SDK acquisition, the guarded hosted fixture must pass an actual
cancellation control. A child controller creates a new owned PID/net/mount
namespace containing another owned child in a separate session, plus synthetic
private state. The independent parent records process birth identities and the
new namespace, injects TERM-first plus repeated INT, and separately the documented
INT then 7.5-second-delayed TERM sequence. The control holds its cleanup gate only
to exercise that escalation. Acceptance requires both signals recorded, original
interruption retained, every tracked process extinct, registered groups drained,
private state removed, unchanged host baselines and a complete receipt. Wrapper
exit status alone is insufficient. These controls execute only on the approved
reviewed hosted runner; they have **not been run**. No cancellation acceptance is
claimed by their source or the local shims.

SIGKILL, blocked I/O, workflow killtree and runner destruction can prevent both
cleanup and final receipts. Unconfirmed private state is preserved until the
disposable runner's external teardown; absent/incomplete receipts never prove
cleanup. This control covers process/namespace/state ownership and does not
establish JNI, Android, sensor, privacy-packet or physical-positioning acceptance.

Before accepting Android evidence, review actual acquisition/archive layouts,
upstream root-project configuration, manual AVD compatibility, native runtime
linkage, installed manifest/test results, resource use and complete cleanup from
an exact-source hosted run. JNI-specific Android sanitizer coverage is still open;
the core C ABI sanitizer results do not supply it. Main Map navigation, OS app
identity, real permissions/sensors, private protected execution, coordinate
conversion, GNSS integrity, fusion and positioning improvement all remain open.
The complete DPS goal and all original Map/network requirements remain in force.
