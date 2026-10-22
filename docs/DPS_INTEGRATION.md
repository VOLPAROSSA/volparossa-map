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
All eight tests lack accepted passing execution evidence. Inert local verification is:

```sh
python3 -B integrations/dps-native/source_gate.py
python3 -B -m unittest discover -s integrations/dps-native -p 'test_*.py' -v
git diff --check
```

The preceding source has 69 existing inert tests (seven Python/Git/archive
preparation tests, six lifetime orchestration
shims, 18 KVM/phase/input-file shims, eight baseline-diagnostic shims, and nine
synthetic firewall JSON tests, eight offline-failure diagnostic tests, and 13
emulator-observation/listener tests).
The instrumentation diagnostic correction added eight focused inert tests: **69
existing + 8 = 77**. Published source `65f048d5e573677d126403cd049ef88f7ec9d212`
passed source gate `39688444556` with 77 inert tests and Components `39688444607`
with 31 tests, against merge `95d57b03` and tree
`225d0f6fc994e9714391aecf08fcfa63ecc85d87`. These gates were independently audited
by the principal; they establish no namespace or Android execution.
The cleanup candidate adds 12 inert reaping/controller tests: **77 + 12 = 89**.
The source gate and complete 89-test inert suite pass locally. Of the original
77 test methods, 76 retain identical ASTs; the orchestration test necessarily
adds the new control before acquisition. Its bootstrap fixture helper also
supplies the new PID-namespace identity. No original assertions were removed.
The test-permission correction adds four focused inert tests: **89 existing + 4
= 93**. All 89 existing test-method ASTs remain identical to published
`b64fba86d56ef1ac9e8d1afe89274c009bb7fffe`. The four additions exercise the exact
negative manifest marker, rejection of grants/extra attributes/elements/other
source sets, and method/status/message-scoped assertion classification with
newline and negative controls. The 93-test whole inert suite remains required on
the hosted source gate; these tests do not execute Android manifest merging.
No Android tests ran locally. Focused reproduction is:

```sh
cd integrations/dps-native
python3 -B -m unittest test_prepare.InstrumentationObservationTests -v
python3 -B -m unittest test_prepare.ReapingShimTests -v
```

The shims cover deferred signal delivery until child ownership is registered,
unblocked child signals without a pre-exec hook, repeated INT/TERM deadlines,
original-failure precedence, honest incomplete cleanup/state preservation,
state removal after simulated drain, and rejection of local cancellation trials.
They launch no cancellation child, deliver no OS signal, create no namespace and
make no privileged call; passing them is not cancellation runtime acceptance. The test fixtures for preparation mechanics are inert text;
they are never passed to JNI and never represented as real Protobuf goldens.
No Rust, native, Java, Gradle, SDK, Node, emulator or hardware execution occurred
on the development host. The published candidate's failed hosted attempt is
preserved below; the current three-file emulator-observation correction has not been dispatched.

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
strict dependency verification inside a new network/PID/mount namespace without
KVM group access. A separate namespace runs the installed synthetic test APK on
the pinned emulator, with only namespace loopback active and guest Wi-Fi/data
disabled. The exact offline APK hash is checked again before installation. Source-level absence of
sensor/location/network APIs and the test's permission assertion are distinct from
physical sensor traces, process identity attestation or measured absence of every
socket attempt. There is no admin socket, daemon or host location access.

Privilege is limited to read-only host firewall baselines, creating disposable
namespaces, activating their loopback, and terminating the exact owned supervisor
process group if necessary. Namespace bootstrap permanently drops all UID/GID
identities before workloads and clears supplementary groups for all builds.
Only the KVM probe and emulator receive the one validated device-derived KVM
group. No host DNS, routes, nftables rules, VPN or sysctls are changed.
Before/after route/resolver, parent credentials and KVM metadata/ACL observations
must match exactly. Firewall configuration must match under the counter-only
projection documented below; raw fingerprints and classification remain visible.
Workload command output, elapsed
wall time and sampled descendant RSS are bounded; the RSS monitor is not a hard
kernel cgroup memory limit. The existing firewall snapshot command captures into
an unbounded buffer; only its subsequent parser input is bounded to 1 MiB.
Commands register and drain their process groups. Runtime PID namespace
termination supplies the final descendant boundary; source checks alone do not
establish actual drain. Failed cleanup or changed baselines fail the job.

Only source/tool/APK hashes, public step receipts, dependency checksums, installed
synthetic test results and cleanup receipts are artifact candidates. Raw logs,
SDKs, Rust/Gradle caches, ADB keys and AVD data are excluded. After confirmed registered-group drain, removal of this run's generated private
source/tool state and retention of fixed public receipts are required for success.
Unconfirmed cleanup preserves remaining state for disposable-runner teardown. No cache reuses test outcomes. Hard workflow termination
may prevent final receipts; the disposable runner teardown is then the external
cleanup boundary and no completed cleanup claim is accepted.

### Preserved first failed attempt and KVM correction

Published Map source `3abb91fa56a5bdf1b27337c689a409986caed793`, tree
`58fa25a8e89c6d08897d4ba9c06eaf35c66dee85`, passed source run `39664119379`
with 13 inert tests; its test-merge source was `02857cad`. Actual hosted run
[39664284521](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39664284521)
failed immediately with `PREEXISTING_KVM_ACCESS_REQUIRED`, before fixture/tool
acquisition or creation of an evidence directory. There were **no artifacts**.
Its preserved original log SHA-256 is
`c29da4f2e6334059955b7f516120b986ded64e6f6f53f2de33f2b6697415b533`.
The log proves only that the runner's `os.access` check returned false. It does
not establish whether KVM was missing, its type/mode/group, ACL state, or whether
an unprivileged open/API query could succeed. Neither cancellation controls nor
Android/native tests ran. This failure is not replaced by a successful simulation.

The current correction keeps all pins and Android/JNI sources unchanged. After
hosted/exact-source admission, it creates owned state and writes a bounded private
metadata receipt before KVM failure or expensive acquisition. It reads the fixed
`/dev/kvm` path: a non-symlink character device with Linux misc identity `10:232`,
the existing named, nonzero `kvm` group matching that device's GID, device
identity/mode/owner/link/timestamps, and the presence/hash of its POSIX access ACL.
ACL bytes and group membership lists from the account database are not persisted.
Unsupported/unreadable ACL queries, absent/mismatched groups, changed devices,
denied opens and unsupported APIs fail closed with fixed codes and numeric errno.
The outer access boolean is diagnostic only; it no longer rejects valid delegated
access. A primary GID equal to the KVM GID is rejected because this correction
preserves the original primary GID while excluding KVM from build phases.

The root namespace launcher derives the sole supplementary group from that
validated device, sets all real/effective/saved GID and UID identities to the
original nonroot owner, and verifies exact credentials plus zero effective,
permitted and ambient capabilities. The parent process keeps its original
credentials/groups, which enter before/after baselines. No account membership,
chmod, ACL, udev, sudoers or service configuration changes are made. Existing
sudo/namespace support is required, not assumed proven by the earlier failure.
Supplementary groups are process credentials preserved across exec; see the
[Linux group interface](https://man7.org/linux/man-pages/man2/setgroups.2.html).

Four fixed phases run through registered namespace supervisors:

| Phase | Network | Supplementary groups | Outer command limit |
| --- | --- | --- | --- |
| `kvm-probe` | New network/PID/mount namespaces; no external link | Sole validated KVM GID | 10 seconds |
| `public-build` | Original public network; new PID/mount namespaces | Empty | 2,700 seconds |
| `offline-build` | New network/PID/mount namespaces; loopback only | Empty | 1,220 seconds |
| `emulator` | Separate new network/PID/mount namespaces; loopback only | Sole validated KVM GID | 420 seconds |

The public-build phase moves the **existing** archive/source/dependency
acquisition and Rust/Gradle build commands into an empty-group nonroot process.
It introduces no new SDK/plugin/dependency or product execution. This phase is
needed because clearing groups in the parent would change its authority, and
retaining arbitrary original groups could give a build KVM access on another
runner image. After permanent UID/GID drop, the existing bounded environment is
restored inside that process because sudo resets it; nested admission checks and
runner rustup discovery retain the same inputs. No caller environment is applied
while the bootstrap is root. Offline rebuilding and emulator work are separated so Gradle/native
builds never receive the delegated KVM group. The root `timeout` is two seconds
shorter than each outer command limit, with one-second TERM-to-KILL escalation;
existing resource monitoring, signal registration and cleanup rules still apply.

Before acquisition, the dropped-privilege probe actually opens the unchanged
device using read/write, no-follow and close-on-exec flags, verifies the opened
identity, queries only `KVM_GET_API_VERSION`, requires `12`, and closes its FD.
It creates no VM. The emulator phase repeats this probe before starting tools.
The KVM FD is neither passed to another process nor retained for emulator use;
the emulator opens its own device after the probe closes. See the
[Linux KVM API](https://docs.kernel.org/virt/kvm/api.html). A successful version
query alone does not prove VM creation, acceleration, Android execution or JNI.
No software-emulator fallback is allowed.

Privileged inputs are limited to three fixed caller-owned JSON files, opened
relative to a validated non-symlink private directory FD. Each must be a single
regular file with matching UID/GID, mode `0600`, and size `1..16384`; no-follow,
close-on-exec and stable-read checks reject symlinks, hardlinks and changes.
Fixed input and receipt opens also use `O_NONBLOCK`, so a replaced FIFO cannot
block the bootstrap before regular-file validation.
Only the fixed phase name is accepted. The caller preallocates each phase receipt
as an empty, single-link, matching-owner regular file at mode `0600`. The child
opens it relative to the held directory FD and writes at most 16 KiB, preserving
caller ownership even if UID dropping fails. Both metadata FDs are close-on-exec
and closed in finalization; neither is a KVM FD. Parent acceptance also requires
zero supervisor exit, drained group, exact source/phase/credentials/capabilities,
unchanged device and the correct probe contract, rather than a receipt alone.
Failed baseline reads retain public failure receipts and cannot accept cleanup.

The 15 added inert shims mock KVM metadata, credentials and every KVM open/ioctl.
They cover group/ACL/device rejection, permanent-drop ordering and exact group
selection, residual capability/group rejection, probe failure/FD closing and a
deferred cancellation after open, fixed namespace profiles, probe-before-build
ordering, bounded non-symlink bootstrap inputs, and nonblocking FIFO type rejection
before input reads or receipt writes. The FIFO cases mock opens and stat results;
they create no FIFO or privileged process. Local temporary-file tests
exercise only ordinary owned files. No local device, privilege, namespace, SDK,
VM, OS-signal or hardware trial occurred on the development host. The subsequently
published KVM query evidence and failed cancellation attempt are recorded below;
all eight Android tests remain **unrun**.

### Actual KVM query and cancellation baseline failure

Published source `c479fbd2fd6be9e039008965c2618a481a815bf1` passed source checks
with 28 inert tests. Actual hosted run
[39669351913](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39669351913)
failed before acquisition, builds or Android execution with
`CANCELLATION_CONTROL_FAILED`. Original artifact `12377934443.zip` is 5,544 bytes,
SHA-256 `620b9a12f0435e894a59d754279f36ad011d717060b0ebe1fd8241e7d1bdb3a4`;
the original log SHA-256 is
`8498bb8576eafacdc5dea7f1f8a95b0649857b331d495e34ad4cb20ecd6557f6`.

The actual query receipt reports API `12`, UID/GID `1001`, sole supplementary
group `993`, zero effective/permitted/ambient capabilities, a closed query FD,
unchanged device, and a drained supervisor. This proves the bounded KVM query;
it creates no VM and establishes no emulator, Android or JNI acceptance.
Both cancellation receipts report two signals, drained registered groups and
removed owned state. Term-first reports unchanged baseline. INT-to-TERM and the
outer finalizer report `HOST_BASELINE_UNCONFIRMED`; complete cancellation-control
acceptance was not achieved. The INT-to-TERM independent PID-extinction result
was not retained. Only the outer before-snapshot survived, so neither the changed
component nor a benign-versus-configuration-change explanation can be recovered.

The raw inputs can include live state: nft counters count packets/bytes, and the
Linux IPv6 proc renderer includes a live route reference count. These are possible
comparison-volatility mechanisms, **not established causes of this run**. See the
[official nft manual](https://netfilter.org/projects/nftables/manpage.html) and
[Linux IPv6 renderer](https://github.com/torvalds/linux/blob/v6.8/net/ipv6/ip6_fib.c#L2338-L2365).

The diagnostic correction retains the already collected before/after
snapshots in each existing finalizer receipt, plus a fixed seven-component changed
list (`netns`, `routes`, `ipv6_routes`, `resolver`, `firewall`, `credentials`, `kvm`).
Snapshots accept only fixed metadata and hash/size fields; raw route, DNS and nft
contents and arbitrary error text are excluded. A closed capture code distinguishes
not reached, absent before-snapshot, and capture failure. Invalid metadata is omitted
with a diagnostic shape code. The whole result receipt is bounded to 16 KiB; if
diagnostics exceed that budget, their snapshots are omitted with a bound code.
If the existing fields alone cannot fit, no oversized receipt is written and the
existing failure-precedence rules apply. Nested control result receipts follow
the existing retention path before private-state removal.

That correction kept exact baseline equality and every existing acceptance condition.
It added no host command, second snapshot read, retry, normalization,
baseline replacement, signal handler or cleanup-time extension. Its seven inert tests
cover matching snapshots, each component changing, capture failure, an unavailable
before-snapshot, rejection of raw/unknown metadata, the whole-receipt byte bound,
and nested retention established receipt orchestration only. The actual diagnostic
attempt below identified the changed component without establishing its raw cause.

### Firewall configuration comparison and preserved diagnostic attempt

Published diagnostic source `33c801783e6cf2356d299189774b78135b6140a4` failed actual
hosted run [39670953044](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39670953044)
before acquisition with `CANCELLATION_CONTROL_FAILED`. Artifact `12380230702.zip`
is 7,199 bytes, SHA-256
`869c2229658af191729f378eebb3c90b986fb7665de79b9cc594c60445c01284`;
the 69,949-byte original log SHA-256 is
`13d2bb3c75a48247d12e54f7a5dd201d2c2c291b5ac6d67102c3f73936386501`.
INT-to-TERM and outer receipts identify exactly `firewall` as changed, with no
capture/diagnostic error. The before/after firewall lengths are both 2,804 bytes
with different hashes. Term-first matches completely. All other baseline
components match, but retained hashes cannot establish what changed inside the
firewall output. Neither this run nor its predecessor is proved benign counters.

The current candidate changes the one existing snapshot command to
`sudo -n nft -j list ruleset`, retaining its ten-second timeout and subprocess
ownership mechanics. No extra command/read, retry, baseline replacement, host
write or lifecycle/deadline change is introduced. The command's captured output
remains an **unbounded subprocess buffer**; the 1 MiB check bounds parsing only.
Before JSON parsing, a quote/escape-aware scan limits nesting to 32. Parsing
rejects duplicate keys, noninteger/nonfinite numbers and unsupported schema;
an iterative traversal limits values/containers to 65,536 before projection.
Integers are restricted to signed-64/unsigned-64 range. Malformed output fails
closed before any comparison or expensive acquisition.

The schema-1 counter forms are checked against the upstream
[libnftables JSON manual distributed by Debian](https://manpages.debian.org/trixie/libnftables1/libnftables-json.5.en.html).
Only direct rule statements containing exactly `packets` and `bytes`, and named
counter objects with family/table/name, those statistics and an optional handle,
have their two uint64 statistics removed from the configuration projection.
Named string references are retained. Unknown fields or nested counter contexts
are rejected. All remaining fields and array order are preserved, including
selectors, verdicts, rule order, counter presence/placement, sets, expiry and quota
threshold/used values. This does not use blanket stateless output.

Each firewall snapshot retains schema, raw/configuration/counter-statistic
hash-and-size fingerprints, and counter count. No raw rules, names or addresses
are published. The baseline diagnostic schema is now `2`, with the same seven
raw-observation changed components plus a closed firewall classification:

- `UNCHANGED`: all fingerprints match.
- `COUNTERS_ONLY`: canonical counter statistics differ while every other parsed
  configuration field and order match; raw representation may differ as well.
- `REPRESENTATION_ONLY`: raw bytes differ, canonical configuration and statistics match.
- `CONFIG_CHANGED`: projected configuration or counter count differs; comparison fails.

The existing `host_routes_dns_firewall_unchanged` acceptance field now means
firewall **configuration** matched, with exact equality for the other six baseline
components. Raw observation differences remain in `changed_components`, even
when valid counter-only or representation-only changes permit that check to pass.
Nested controls automatically use this same baseline/finalizer path. KVM phase
checks still consume unchanged netns/credentials/KVM fields; typed diagnostic
validation accepts the new firewall record, and the 16 KiB result bound remains.

The original 45 inert tests include counter-only acceptance, raw difference retention,
named counters without handles, preserved identities/order/policy/quota/expiry,
counter addition/removal/placement, unknown shapes, parser bounds and one-command
acquisition semantics. Those synthetic checks did not explain historical failures
or establish emulator, Android/JNI or physical-positioning acceptance. The scoped
hosted observations below supply separate evidence for this comparison.

### Public build passed; preserved offline rebuild failure

Exact published Map source `4e165e6e6f382e45463f6977cb979f1284d4173e`, tree
`199d50c4b489ad943fae9ef2053544510fa3a335`, failed actual hosted run
[39672932771](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39672932771).
Artifact `12379174273` is 81,501 bytes, SHA-256
`6752878bfa9468c49ba42695c767787b775b8b75fb06ec2e65975b28d60253b9`;
the 72,097-byte original log SHA-256 is
`e1ebd303043cfa6079b66dde0ec22b21c1e6a0d62b625cf4e1afd9f86e4c9be5`.

The KVM API probe opened the device, queried API `12`, and closed its FD. The
earlier metadata-only admission receipt's `actual_open=false` describes that
admission step, not the subsequent API probe. Neither step created a VM.
Both actual cancellation controls passed their independent process-birth,
namespace/state removal, signal and baseline checks. The public acquisition/build
phase and its Android assembly command exited `0`, with zero capabilities and
empty supplementary groups; source/tool/library/dependency-metadata receipts
were retained. The offline Gradle command and enclosing namespace exited `1`.
No offline APK binding was produced and the emulator phase was never reached.
Outer cleanup drained groups, removed owned state, reported no cleanup errors,
and confirmed other baselines plus firewall configuration unchanged. Its firewall
classification is `COUNTERS_ONLY`: canonical counter statistics changed while
all other parsed configuration fields/order matched. This observation belongs
to this run and does not identify the cause of earlier raw-only mismatches.

The public build saves the updated environment and reuses the same prepared
project, Gradle/cache directories, generated verification metadata and Rust
static library in the offline phase. The offline command adds `--offline`, strict
dependency verification and module `clean`; it also runs in a private network
namespace. At that source, the namespace left loopback down; only the emulator
phase activated its own loopback. Those differences did not identify the failure. Gradle
can use a single-use daemon even with `--no-daemon`, and its client communicates
with that daemon over a local socket; see the
[official Gradle daemon manual](https://docs.gradle.org/current/userguide/gradle_daemon.html).
No loopback, cache or command-flag correction is justified by the retained evidence.

The inner private Gradle log and enclosing private namespace traceback were
excluded from retention and removed after drain. The retained exit/RSS summaries
prove the Gradle command failed, but contain no error text. The phase's generic
`ValueError` code supplies no further cause. The current three-file candidate
therefore adds diagnostics only, leaving commands, network phases, credentials,
KVM/firewall/cancellation mechanics, timeouts and first-error precedence unchanged.

After the fixed offline command fails, its diagnostic first requires a matching
fixed step receipt reporting nonzero exit, drained owned group and no cleanup
error, with no remaining registered children. It uses a held private directory FD
owned by the current nonroot UID/GID at mode `0700`. Both fixed input leaves are
opened no-follow, nonblocking and close-on-exec, then checked as matching-owner,
single-link, mode-`0600` regular files. The step summary is bounded to 4 KiB; logs
over the existing 64 MiB step limit are not read. Descriptor metadata/size must
remain stable across reads. Closing every acquired FD is attempted, including
rejected inputs; a close failure receives a closed capture code.

At most 64 KiB of log data is read: a 32 KiB prefix and, when needed, a tail with
no overlap. The source-bound `offline-build-diagnostic.json` records actual file
size, sampled byte count, window offsets/hash/size, and `COMPLETE` versus
`PREFIX_TAIL` coverage. An entire-log fingerprint exists only for complete
coverage. Disjoint windows are never concatenated for marker matching.
Eleven finite marker flags can observe Gradle failure/daemon messages, local
address/bind or unreachable-network text, offline-cache/verification/SDK/native
build messages, or Java launch messages. They are text observations, never an
authoritative diagnosis; absent markers do not rule out an unobserved cause.
Besides the exact source revision, no log text, error text, URL, argument,
environment field or filesystem path is retained.

Capture runs under the existing cleanup budget without extending it. Closed
capture codes report missing/unsupported/changed inputs or incomplete capture.
The JSON receipt is bounded to 16 KiB and follows the existing JSON retention
path before state removal. Capture or save failure cannot replace the original
command exception; a missing diagnostic cannot establish successful capture.
Eight inert tests exercise ordinary text inputs and mocked build failures,
complete/window boundaries, privacy/retention, rejected FIFO/symlink/type/owner/
link/size inputs, descriptor closing, unstable reads, original-error preservation,
and unchanged successful command arguments. No local Gradle/native/SDK/VM trial
occurred. The subsequent actual diagnostic observations are retained below.
Installed Android/JNI execution, all eight instrumentation tests and
physical-positioning acceptance remain open.

### Captured offline local-address marker and loopback correction

Published source `1d4b777c3cd06822d89d6f54a36a2646876a995d`, tree
`f2eaff7614f26f789830d058eb98def9a0d64a46`, passed source run `39675913577`
with 53 inert tests and Components run `39675913654` with 31 tests, against
test-merge `9f7be559` with that same tree. Actual hosted run
[39676098216](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39676098216)
failed after public-build exit `0` and offline-build exit `1`, before Android
execution. Original artifact `12381767253.zip` is 82,072 bytes, SHA-256
`dbaa5fb18aba812ad18c84478bc79d5a75de1a73db53909afca4f3c1a519210c`;
the 74,598-byte original log SHA-256 is
`3a5c1e14902ac0ed28972699e4d6704a68fa1e2757bd967f4b1535354d2e8187`.

The diagnostic captured the complete 537-byte offline log, with no capture error,
SHA-256 `227ae24043ca27c9dada9dd80c2d60d994f620b1923105d43d05158073c18e29`.
Its only observed flags were `GRADLE_FAILURE_HEADER` and `LOCAL_ADDRESS_SELECTION`.
The source verified a private network namespace containing only `lo`, then left
that interface down for offline-build. The current correction includes
offline-build in the existing loopback activation predicate, alongside emulator,
after both namespace/link guards and before credential drop. The existing fixed
`ip link set lo up` command and five-second timeout are unchanged. Public-build
and KVM probe do not activate loopback. This touches only the disposable fixture's
private loopback; it adds no external interface, host configuration change,
permission, cache change, fallback, retry or build-command change. It targets the
observed local-address-selection failure. At that checkpoint the correction was
unrun; the subsequent successful offline rebuild is recorded below, separately
from the unresolved emulator failure.

The same failed run passed both cancellation controls, with six tracked process
identities extinct in each. Outer registered groups drained and owned state was
removed, but host-baseline acceptance **failed**: cleanup errors contain
`HOST_BASELINE_UNCONFIRMED`, and the unchanged-host field is false. Changed
components are `ipv6_routes` and `firewall`. Firewall configuration matched with
`COUNTERS_ONLY` statistics changes. The raw IPv6 snapshot remained 900 bytes but
changed from SHA-256
`f2ffd75c47ca531b2748edf8b83d154d04a2eb70ca9d60d34aab332e425d2d55`
to `25e2aa2d38a16c10b89a95eb8919948e7c5dc4f2601a1b47ff1b499e5e6fcbae`.
No original IPv6 contents were retained, so that cause is unknown. No transient
counter explanation or successful unchanged-host acceptance is established.
This correction leaves IPv6 and every other baseline comparison unchanged.

Three added mocked phase tests verify offline-build/emulator activate only
loopback exactly once before drop, public-build/KVM do not, and wrong namespaces
or unexpected/missing private interfaces reject before activation or drop. No
local network, SDK, Gradle, native, Rust or VM execution occurred. The correction
did not itself establish VM, installed Android/JNI, instrumentation or
physical-positioning acceptance. Its subsequent actual observations are below.

### Offline rebuild passed; emulator failure observations candidate unrun

Actual run [39679300414](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39679300414)
used exact source `02cf65b9cea0fd9a09e51f434d8b55deccb40fb7`. Original artifact
`12383765694.zip` is 82,992 bytes, SHA-256
`e19bb6842b31a2c9f630236296a0ebf86e6bc835a7128c57cad38ac8bf40c23d`;
the 75,430-byte original log SHA-256 is
`ed5f185d6652d6e7efdae3c3e51ac56763f121e1f6b74533caac8ce46aad2de7`.
Both public assembly and strict offline rebuild exited `0`. The recorded offline
test APK is 2,742,891 bytes, SHA-256
`a4ae3849f6eae70e81cc1a35b80f3439bc842f4a5a33fc9c24d0bf01ac97301d`.
No installed APK or instrumentation result was reached.

The emulator phase failed `EMULATOR_BOOT`, after `boot-1` exited `1` and its owned
group drained. A failing ADB boot-property command is explicitly caught and
followed by another loop iteration. That exit is not the later fatal condition:
the top-of-loop guard rejects an exited emulator, an exited ADB server, or the
180-second boot deadline. The old receipts do not identify which condition fired
or why a process exited. The private emulator/ADB logs were not retained.

Namespace cleanup reported `ValueError`, remaining direct children and unremoved
private AVD/Android-home directories. Its drain was not confirmed before removal;
the receipt lacks a closed error code or failed group identity. `PROCESS_GROUP_DRAIN`
is a source-consistent possibility, but this artifact cannot prove that code or a
zombie-process explanation. Outer namespace/group termination and owned-state
removal were confirmed, while outer host-baseline acceptance failed. Changed
components were raw IPv6, firewall statistics and KVM node timestamps. Firewall
configuration matched. Raw IPv6 stayed 900 bytes but changed from
`ecb024bb1046fa7febe556967807b3fe657d62aafa7ff50a76bc1da15b5e78ae`
to `153e0b6fb746f1957032642861b728f8ec93e10d1d76cff268311a3cb73485a0`.
Its original contents were not retained, so the cause remains unknown.

The emulator-phase KVM API probe opened the device, queried `12`, closed its FD,
and passed its own before/after full-metadata checks. The phase receipt's
`device_unchanged=false` is its initial default: workload failure prevented the
final success-only device comparison from running. It is not an observed
`KVM_DEVICE_CHANGED` failure. Independently, the outer snapshot observed only
`mtime_ns`/`ctime_ns` changing, from `1792618139165185792` to
`1792618502442878002`; node identity/mode/owner/group/rdev/link count and ACL matched.
This proves neither which actor changed timestamps nor successful VM creation.
The probe's `vm_created=false` describes its query-only calls, not all actions
the launched emulator might have attempted. Both synthetic cancellation controls
passed with six tracked process identities extinct in each.

Upstream research supplies a **hypothesis**, not runner-version or event proof:
Linux v6.8, commit `e8f897f4afef0031fe618a8e94127a0934896aba`,
[`virt/kvm/kvm_main.c`](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/virt/kvm/kvm_main.c)
(SPDX `GPL-2.0-only`), returns the API version without a VM-create call, while VM
creation/destruction emits `KOBJ_CHANGE` events. Systemd v255, commit
`db11bab38ccf1ed257f310d29070843d4c58ea01`,
[`src/udev/udev-node.c`](https://github.com/systemd/systemd/blob/db11bab38ccf1ed257f310d29070843d4c58ea01/src/udev/udev-node.c)
(SPDX `GPL-2.0-or-later`), updates a reused node's timestamps with `futimens_opath`.
This is a possible timestamp-update mechanism; no recorded runner versions or
uevents bind it to this run. No upstream code is imported, and this research
grants no baseline-normalization authority. All KVM/IPv6 comparisons remain exact.

The observation slice added bounded observations only. It preserved short-circuit
emulator-poll, ADB-poll and deadline order, the 180-second limit, command arguments,
poll retry/sleep behavior and workload success conditions. Before cleanup it
records the exact terminal predicate reason, which statuses/deadline were actually
observed, bounded attempts/elapsed time and a separate last nonfatal poll code.
An explicit flag distinguishes a primary `EMULATOR_BOOT` failure from a later
guest check; a caught not-ready poll is never promoted into the terminal cause.

Around the existing unchanged `drain_all`, fixed cleanup stages and at most eight
registered object identities are observed in memory. Output contains fixed roles
and cached return codes, no process IDs or command names. A zero-based failed
group index in the initial reverse drain order is emitted only for the exact
`PROCESS_GROUP_DRAIN` code with a proven removed-prefix/remaining-order invariant;
otherwise it is unknown. The shared `Lifetime`, signal, wait, drain and directory
removal mechanics are unchanged. No new `/proc` scanning or process-state inference
is introduced. The existing namespace cleanup receipt remains bounded to 16 KiB.

Only after the enclosing namespace's failed command has joined and its registered
group drained may the parent sample fixed `emulator.private.log` and `adb.private.log`.
Its fixed step receipt must report nonzero exit, confirmed drain and no cleanup
error, with no remaining registered parent children. The previous private owned
directory/relative-FD pattern checks mode/owner/group, single-link regular files,
nonblocking/no-follow/close-on-exec opens and stable descriptor metadata. Inputs
over the existing 64 MiB log bound reject before reading. Each log contributes
at most 32 KiB (16 KiB prefix plus tail); aggregate sampled log bytes are at most
64 KiB, plus at most 4 KiB for the fixed step receipt. Actual sizes, sampled sizes,
offset/hash windows and complete-versus-window coverage remain distinct. Only
complete coverage gets an entire-log fingerprint. Disjoint windows are never
concatenated for marker matching.

The source-bound `emulator-failure-diagnostic.json` is bounded to 16 KiB and emits
only finite literal-token observations (`ERROR`, `FATAL`, `KVM`, `AVD`, `Vulkan`,
`Qt platform`), fingerprints and closed capture codes. Token observations are not
causes. No raw text, path, PID, URL, nonce or user data is published. All FD closing
is attempted; close failure is reported. Capture/save failures preserve the
original namespace failure, and existing cleanup budgets are never extended.
Hard kill or blocked I/O can still prevent receipts; missing data cannot prove
successful capture. Ten new inert tests cover predicate independence, not-ready
continuation, primary/cleanup separation, index proof, terminal-source gating,
read races, FD/type/size bounds, window coverage/privacy and error precedence.
At authorship this observation slice was **unrun on a hosted runner**. Its later
failed hosted attempt is preserved below; it establishes no emulator boot,
installed Android/JNI, sensor or physical-positioning acceptance.

### ADB listener correction and preserved abort

Actual run [39682806204](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39682806204)
on exact source `e09f99257ecb99a8ca1d2f6fabc13a801bceb713` passed public acquisition/build
and offline rebuilding, then failed `EMULATOR_BOOT`. The boot receipt records
`ADB_SERVER_EXITED`, ADB return code `-6`, the emulator still running at its poll,
one attempted not-ready poll and 1,202 ms elapsed; the deadline was not observed.
The complete 152-byte ADB log fingerprint is
`bf89cd60885cd8c3a6ef0c3c304a9c0616795b836bd034b3521d2954eddb43a4`.
It matched none of the original finite markers; its raw text was not retained.
Neither that size nor SIGABRT identifies its historical cause. Original artifact
`12385241545` is 83,919 bytes, SHA256
`1b681a111186a855b4ee4291386b86b9212d156fd8bef8ef5b5997f7dfc44e6f`;
the 78,533-byte original run log has SHA256
`d4f0c3526a90001926885e64eb62cd711a294701e576f4c786641a2eafea2cbf`.

The archived tool pin is platform-tools **37.0.1**, not 36.0.2; the source receipt
binds the 9,054,187-byte archive and SHA1 `477254aa5f903c15cf51001717bdf347fb6b53e0`.
There is no separate runtime `adb version` receipt or verified archive-to-AOSP
source-commit identity. Immutable AOSP commit
`9ccdca1b66d21a1bbfec0d3dd522391866c90cee` supplies a source-grounded rationale:
[`socket_spec.cpp`](https://android.googlesource.com/platform/packages/modules/adb/+/9ccdca1b66d21a1bbfec0d3dd522391866c90cee/socket_spec.cpp#369)
rejects explicit IPv4 listener host `127.0.0.1`, while `localhost` selects its
dedicated loopback implementation. That implementation constructs loopback socket
addresses directly, without DNS lookup
([`sysdeps/posix/network.cpp`](https://android.googlesource.com/platform/packages/modules/adb/+/9ccdca1b66d21a1bbfec0d3dd522391866c90cee/sysdeps/posix/network.cpp#122)).
[`client/main.cpp`](https://android.googlesource.com/platform/packages/modules/adb/+/9ccdca1b66d21a1bbfec0d3dd522391866c90cee/client/main.cpp#161)
reports exhausted listener startup as fatal. These are Apache-2.0 upstream source
references; no upstream code is imported, and they do not prove this archive's
implementation or the previous abort's cause.

The new correction changes only the server's `-L` argument from
`tcp:127.0.0.1:5037` to `tcp:localhost:5037`. Client arguments and every other launch
argument remain unchanged. The existing private namespace/only-loopback guard,
loopback activation and permanent credential/capability drop remain in force;
no DNS configuration, external interface or host configuration is enabled.
The existing terminal classifier adds only exact case-sensitive literals for
`ADB_LISTEN_HOST_UNSUPPORTED` and `ADB_SMARTSOCKET_LISTENER_FATAL`. Matches are
observations, never authoritative causes; no match leaves the cause unknown.
Three added inert tests check the mocked launch/client arguments, raw LF/CRLF
fixtures, changed case, truncation and unrelated near-matches. Capture mechanics,
receipt fields/bounds, deadlines, signals, KVM/IPv6 comparisons and cleanup are
unchanged. This correction has **not run on a hosted runner**.

The failed actual attempt separately reported `PROCESS_GROUP_DRAIN` at reverse
ownership index 0 (the emulator group), with cached emulator status `-9` and ADB
status `-6`. Its inner cleanup remained unconfirmed; those observations do not
prove what persisted in the group. Outer groups drained and private state was
removed, but host-baseline acceptance failed for raw IPv6 and KVM timestamps.
Firewall configuration matched with counter changes. Those unresolved failures
remain failures. The emulator phase's `device_unchanged=false` is still its
unreached success-only comparison's default, not an observed mismatch. Both
synthetic cancellation controls passed; no Android/JNI or positioning acceptance
follows from these results.

### Emulator boot passed; instrumentation result remains unaccepted

Actual run [39685493968](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/39685493968)
on exact source `281fdef38ad737dcd55e98e99bf4e9132fad74e5` passed public/offline
building, emulator boot, the API36/x86_64 predicates and APK installation command.
The instrumentation command exited zero and its registered group drained, but
the unchanged `ACTUAL_INSTRUMENTATION_RESULT` predicate failed. No native-result
receipt was written. The artifact retains neither the instrumentation output nor
individual test statuses, so the executed test count and failed test/exception
remain unknown. Command exit zero is not proof that the eight tests executed.
Original artifact `12385693095` is 95,618 bytes, SHA256
`f31f0cffc4c944d05867c26ffaf990bc4268090a4652c0524db7a8afe0a6202f`;
the original 79,473-byte log has SHA256
`391840e2963da180628714b6f5ba383af905d52fecf0ae922d454ef43ca728d0`.

Source inspection found no deterministic runner/summary-format incompatibility.
The fixture declares eight JUnit methods, configures AndroidJUnitRunner, copies
its public goldens into both test and debug assets, and installs the library's
test APK. AndroidX release source at immutable commit
`de417c55bd5226087b97a5121fae1d2ea1d58718` (tag `axt_2025_07_31_stable`)
[`InstrumentationResultPrinter.java`](https://github.com/android/android-test/blob/de417c55bd5226087b97a5121fae1d2ea1d58718/runner/android_junit_runner/java/androidx/test/internal/runner/listener/InstrumentationResultPrinter.java)
defines the raw status protocol and uses JUnit's text listener for the summary.
This Apache-2.0 source reference supports parsing; no upstream code is imported,
and it does not supply the missing installed-test result or merged manifest.

The new diagnostic uses only the bytes already returned by the joined/drained
instrumentation `run()` call, immediately before the byte-identical acceptance
predicate. It adds no capture, file read, process, network request, retry, command
argument, deadline or output digest. The parser accepts at most 256 KiB, 4,096
LF-delimited lines and 8,192 bytes per line before bounded protocol decoding.
This is a parser bound: the existing command capture/read retains its existing
64 MiB limit. LF/CRLF are supported. An incomplete final line, pending record,
unknown protocol key/class/method/status, unexpected ordinal, or duplicate record
leaves the inventory incomplete. Untagged stream/stack continuations are not
published or interpreted as protocol records.
Review caught a missing ordering check in the first diagnostic candidate. The
corrected decoder requires STATUS records, then a valid RESULT record with no
pending bundle, then CODE; later STATUS records, missing/reordered RESULT or
premature CODE cannot produce a complete inventory. Observed partial rows and
acceptance-token facts remain visible. The original acceptance guard is unchanged.

The only accepted test class is `org.volparossa.map.dps.QualityInstrumentedTest`,
with the eight method names fixed in the decoder and source fixture. Raw status
codes map only to start, pass, error, failure, ignored and assumption-failure.
Rows expose closed statuses, bounded counts and exception families (assertion,
JNI link, class load, initializer, security, argument, state, I/O or unknown).
Unknown exception names are never copied; missing observations remain unknown.
The final result code and the existing three acceptance-token observations remain
distinct from a complete inventory. Diagnostic fields never decide acceptance.

The fixed `instrumentation-result-diagnostic.json` is source-bound and checked
against the existing 16 KiB receipt limit. It contains no raw stack, output,
paths, URLs, exception messages or arbitrary class/method names. Existing
`finalize()` already copies fixed-state `*.json` except `environment.json`, so no
retention allowlist change is required. Decode/write failures are best-effort;
they cannot replace the existing instrumentation acceptance error. Catchable
interruption and cleanup deadlines retain authority, including cancellation
deferred during a decoder error. Existing lifetimes, signals, cleanup, KVM and
host-baseline comparisons are unchanged. Eight inert tests cover complete and
partial protocols, status/family privacy, duplicate/unknown inputs, bounds,
original-error precedence, cancellation and finalizer retention. This diagnostic
has **not run on a hosted runner**; the full suite remains required there.

The actual run's inner cleanup again reported `PROCESS_GROUP_DRAIN` at emulator
reverse-ownership index 0. Outer groups drained and private state was removed,
while raw IPv6 and KVM timestamps remained unconfirmed against the host baseline.
Both cancellation controls passed. These failures remain unresolved; the phase's
`device_unchanged=false` is still an unreached comparison's default. Boot and
installation do not establish passing JNI tests, main Map navigation, sensor or
physical-positioning acceptance.

### Private PID-1 group reaping candidate and hosted regression

Source review identified a cleanup gap in the published fixture: its private
PID-namespace controller is PID 1, but `Lifetime.drain()` waited only for the
registered `Popen` leader. It did not wait for adopted descendants. Linux
[`pid_namespaces(7)`](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)
describes orphan adoption by namespace init, and
[`waitpid(2)`](https://man7.org/linux/man-pages/man2/waitpid.2.html) describes
reaping terminated children and selection by a negative process-group ID.
These documentation references explain the source correction; they do not prove
that any historical emulator survivor was a zombie. No upstream code is imported.

The new per-`Lifetime` mode defaults false. The outer reviewed fixture records
its PID-namespace identity as `parent_pidns` in existing `source.json`, independently
of the unchanged seven-component host baseline. Before enabling reaping, bootstrap
requires a present, strictly shaped binding, a different current PID namespace,
`getpid()==1`, and private `/proc/self` resolving to `1`. These are fixed metadata
reads, not process discovery. Missing/malformed bindings fail closed. Each normal
phase enables the mode after its existing namespace guard and before workload
execution. The parent supervisor and the two cancellation controls retain their
default behavior. Credential drop, zero-capability checks and KVM scope remain
unchanged.

In that mode only, drain accepts a registered integer group ID greater than 1
and at most `2**31-1`. After the existing leader wait sets its cached return code,
the existing 0.75-second group-disappearance loop calls only
`waitpid(-owned_pgid, WNOHANG)`, with at most 256 calls across that drain and a
deadline check before each. It never calls a wildcard wait or changes the cached
leader status. A zero return or `ECHILD` ends a reap pass, not the group check:
the existing `killpg(pgid, 0)` disappearance proof still decides cleanup. Live or
later-adopted children remain subject to the existing TERM/KILL/sleep loop; an
exhausted deadline/call cap fails closed and retains ownership. No new signal
target, process scan, zombie exemption or deadline extension is introduced.
Existing original-error/cancellation precedence remains in force.

A separate fixed `--inside-reaping-control` mode runs in the existing reviewed
hosted workflow after the unchanged two cancellation cases and before acquisition.
It uses the existing private PID/net/mounted-proc launcher, with a 15-second
namespace bound and 17-second parent `run()` bound. It verifies the same PID-1
binding, only-loopback private network, caller-owned state and original UID/GID,
then permanently drops privileges with empty supplementary groups and zero
effective/permitted/ambient capabilities. It neither activates loopback nor
receives KVM access. No workflow YAML, SDK, native build or emulator is needed
for this control.

The fixed Python fixture creates an exited and a live adopted descendant in one
registered group. Atomic private files and positive-PID nonblocking `waitid`
observations with `WNOWAIT` establish adoption, exit status and liveness without
consuming statuses. Each handshake is bounded to two seconds; child lifetimes
also have finite bounds and remain contained by the namespace deadline. The
disabled default must first produce the exact group-drain failure. The controller
then enables the verified production reaper to clean that negative fixture and
drains a fresh live/exited family with reaping enabled. This deliberate negative
control is not a production retry or fallback. Two other registered groups must
retain an unconsumed distinctive exit status and a live leader. Acceptance checks
target-group disappearance, original leader statuses, the independent sentinel
facts, all registered groups drained and private fixture-directory removal.

The same verified reaper is enabled in `finally` after a setup/handshake failure,
so failure does not intentionally abandon a registered fixture group. Cleanup
uses the existing budget and error precedence. Unconfirmed groups or directory
removal remain failures; PID-namespace teardown is a final containment boundary,
not a substitute for the control's inner acceptance checks. The ordinary existing
`run()` resource monitor is reused without new discovery/scanning logic.

The parent requires the exact fixed receipt shape and true independent facts,
then takes exactly one approved post-control host-baseline snapshot and uses the
unchanged comparison. IPv6, KVM timestamps and firewall configuration rules are
not relaxed. `group-reaping-result.json` and `group-reaping-control.json` remain
source-bound, at most 16 KiB, and contain only fixed facts/statuses and existing
baseline metadata/hashes. Private descendant PID files are nested and removed,
never artifact-copied. The protected input helper adds only the fixed result name
and eight fixed handshake names, retaining its owner/mode/regular-file/link,
nonblocking/no-follow and 16 KiB checks. Existing finalizer JSON retention suffices.

Twelve new local inert tests model identity guards, group-only waits after leader
wait, unchanged parent behavior, delayed adoption, live/`ECHILD` cases, deadlines,
the aggregate call cap, failure precedence, failed-handshake cleanup, independent
control facts and exact post-control baseline rejection. No local fork, signal,
namespace, credential change or workload runs. The actual hosted regression is
**unrun**; the real adoption/reaping/status-isolation proof is still required.
Historical instrumentation, cleanup and baseline failures remain unresolved.
No Android/JNI, sensor, main Map navigation or positioning acceptance follows
from the inert tests or this source correction.

### Catchable cancellation requirements and scoped hosted proof

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
reviewed hosted runner. Failed attempts remain recorded above; run `39672932771`
passed both controls for its synthetic process/namespace/state fixture. That
scoped proof is separate from source checks and local shims, and does not prove
cancellation of a running SDK acquisition, complete build tree or emulator.

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


### Targeted test dependency permission correction

Actual `39690910102` on published Map
`b64fba86d56ef1ac9e8d1afe89274c009bb7fffe` passed the hosted group-reaping
control, inner emulator/ADB cleanup and outer cleanup. Its instrumentation
receipt contains seven passed methods and one `ASSERTION` failure in
`actualDebugActivityDisplayRevokeBackgroundAndPermissionBoundary`; the precise
assertion was not retained. The method first requires that the installed target
package declare no requested permissions. Its later Activity/UI/background
assertions were not independently accepted. Separately, observed KVM mtime/ctime
changes remain unresolved; IPv6 matched and firewall comparison was counters-only.
No timestamp normalization or historical zombie attribution follows.

Read-only ZIP/XML inspection of official Google Maven artifacts, with SHA-256
verified against the run's dependency-verification XML before inspection, found
that [ext-junit 1.3.0's POM](https://dl.google.com/dl/android/maven2/androidx/test/ext/junit/1.3.0/junit-1.3.0.pom)
(`92af7f253e5eabe2caec5c56f308312653d123042b22754d6ff664e1752dcf3f`)
depends on [androidx.test:core 1.7.0](https://dl.google.com/dl/android/maven2/androidx/test/core/1.7.0/core-1.7.0.aar).
That AAR (`f4dacd8edceeec48e0c76ecf28339b28f4b4f6b74f8e34e9e59b472c27d9eb81`)
declares `android.permission.REORDER_TASKS`. This is a source incompatibility with
the fixture's strict zero-requested-permission contract, not proof of the failed
historical assertion. The original retained artifact
`12388379237.zip` is 97,748 bytes with SHA-256
`179a955fc498b474a7f0f5c552caabe6630f34dd808cc226d2bc3f8ae4530c57`.
No raw instrumentation stack or merged installed manifest is retained there.

A new `module/src/androidTest/AndroidManifest.xml` contains only a
`tools:node="remove"` directive for that named permission. It follows the official
[manifest merger's node-removal semantics](https://developer.android.com/build/manage-manifests#node_markers):
the higher-priority manifest removes a matching lower-priority element. The
marker is scoped to the test source set and does not grant a permission. Source
gating accepts only this exact permission/name/removal marker with no extra
attributes or elements; other source sets still forbid permission declarations.
The runtime null-or-zero `requestedPermissions` assertion and all eight Android
test methods remain unchanged. No AndroidX helper activity is removed. Candidate
`androidx.core:core` 1.13/1.17 AAR manifests also contain an application-specific
signature permission; their presence in verification XML does not prove the
resolved APK graph. That permission is not removed. Actual merged permissions
and the complete eight-test runtime result remain necessary acceptance evidence.

The existing bounded instrumentation-output decoder additionally classifies
`PERMISSION_BOUNDARY` only when the fixed Activity test has terminal `FAILED`
status and the first stack line exactly equals
`java.lang.AssertionError: fixture declares no permissions`. Other methods,
messages and assertion statuses retain generic `ASSERTION`. No raw message,
stack, permission list or new capture is retained; the existing `failure_family`
field carries the closed classification. The original eight-test acceptance
predicate, parser bounds, receipt fields/retention, deadlines, cleanup, signals,
KVM checks, baselines, workflow and JNI/Activity/controller sources are unchanged.
The correction removes the known inherited permission at source. It does not
establish the historical cause or prove final merged-APK or runtime success.


### Completed-emulator KVM configuration checkpoint candidate

This full-fixture candidate starts from Map
`f3296c21f9787cb760e561954652158c7b21601d`, tree
`0002fa35860355a41541aa4563870909b03e7a5e`; it is separate from the control-only
branch. The principal and independent reviewer audited minimal hosted control
`39696546767`: the unchanged API12 query negative stayed unchanged, exactly one
empty VM creation/close produced only KVM ctime/mtime differences, all owned FDs
closed, groups drained and private state was removed. Its original strict overall
verdict remained failure. The retained control artifact has SHA-256
`d7c6dee986c0673f8531bd353d33e08a4437913356c9211c12ccb8f8a331581a`.
No Android workload ran in that control. Separately, full actual `39694570310` on
`f3296c21` passed all eight Android tests and inner/outer process/state cleanup,
but failed the strict KVM timestamp comparison; IPv6 matched and firewall changes
were counters-only. These historical verdicts remain failures under their tested
contracts and are not relabeled by this source change.

The principal's current contract distinguishes active host configuration from
operational timestamps at **completed-emulator checkpoints only**. A new pure
`kvm_comparison` validates both complete snapshots before equality or projection:
exact device/ACL/group key sets, integer types excluding bool/float, bounded
numeric values, full 16-bit mode describing a character device, major/minor
10/232, UID/GID within 32 bits, positive link count, and a nonzero named KVM group
matching device GID. ACL presence must be a bool; a present ACL has only its
bounded byte count and lowercase SHA-256 digest. Missing, unreadable, mistyped,
unknown or semantically wrong-device input fails closed. The matcher returns
`UNCHANGED`, `TIMESTAMPS_ONLY` or `CONFIGURATION_DIFFERENCE`, plus separate raw
and configuration equality booleans. Configuration comparison retains exact
`dev`, `ino`, `rdev`, full mode, UID, GID, link count, ACL presence/digest/size and
named group GID. Only mtime_ns and ctime_ns are excluded from that comparison;
neither raw snapshot is rewritten, dropped or adopted as a new baseline.

Phase receipts are now schema **2**. `device_unchanged` retains its original raw
metadata-equality meaning. New `device_configuration_unchanged`,
`device_comparison` and `device_checkpoint` fields describe the independent
configuration result and preserve the raw before/after snapshots. Null checkpoint
and comparison fields mean the final comparison was not reached. Initial lstat,
no-follow open and opened-FD identity checks remain exact. The API12 probe remains
query-only, closes its FD and uses exact metadata comparisons. The kvm-probe,
public-build and offline-build final checks remain strict, including timestamps.
Only after `emulator_trial` returns normally through its unchanged eight-test and
inner-cleanup acceptance path can the emulator's final check accept
`TIMESTAMPS_ONLY`; `device_unchanged` then remains false while
`device_configuration_unchanged` is true. The parent recomputes the comparison
against the original source-bound baseline, checks the raw origin snapshot and
both typed booleans/classification, and independently enforces the same phase
policy. Child-reported success or configuration equality alone is insufficient.

Outer finalizer receipts are schema **3**. The existing
`host_routes_dns_firewall_unchanged` retains its strict KVM-metadata criterion and
existing firewall comparison policy; it can remain false on a successful new
receipt. New `baseline_acceptance_mode`,
`host_configuration_checkpoint_accepted` and `kvm_comparison` make the selected
criterion explicit. The default baseline predicate remains strict. Its sole
configuration-mode call is in the finalizer, only when `complete is True`, no
original failure/control mode exists and no cancellation has been recorded.
`main` sets that complete flag only after all phases, including the emulator,
finish successfully. Deferred cancellation during capture or comparison revokes
the criterion and restores the strict verdict. Cancellation and reaping controls
continue to call the strict predicate; incomplete instrumentation or workload
failures cannot select configuration mode. Configuration-mode baseline comparison
requires the complete validated existing baseline schema, preserves exact checks
for every non-KVM component and retains the existing firewall configuration
policy. All raw before/after metadata and changed-component evidence remain in
the existing bounded diagnostic. Overall success still requires the full workload,
accepted host checkpoint, drained groups, removed state and no cleanup errors or
cancellation. Receipt bounds, error precedence and cleanup budgets are unchanged.

The measured limitation is explicit: matching configuration at these checkpoints
cannot exclude a transient permission/ACL change followed by restoration. The
ctime/mtime alarm for that possibility is removed only from the completed-emulator
criterion. Strict quiet controls retain it. Empty-VM reproduction does not identify
the actor, bind the actual kernel/udev builds, establish the kernel-to-udev syscall
chain or explain an older run. The immutable Linux/systemd source references and
licenses above remain a mechanism hypothesis; no upstream code is imported.
No event listener, tracing, device/configuration write, SDK permission or runtime
authority is added by this comparison change.

The inert inventory is **93 existing + 12 new = 105**. The new tests cover raw
preservation and strict versus configuration replay, every authority/ACL/group
field with and without timestamp differences, malformed identical inputs and
bool/int traps, other baseline components/firewall policy, schema2 parent
recomputation and quiet-phase rejection, schema3 mode selection, capture/cleanup
and incomplete-instrumentation failures, cancellation during capture/comparison,
and the explicit configuration call-site allowlist. Of 93 existing test methods,
92 retain identical ASTs. The existing matching-malformed-KVM test now requires
closed failure rather than accepting two identically invalid snapshots; its
privacy/diagnostic assertions remain. Main, device_identity, query-only negative,
initial device/FD checks, lifecycle, cancellation/reaping controls, firewall
policy, eight Java methods/acceptance guard, pins and workflow are unchanged.
These are inert source/mocked observations only. Independent review and a new
exact-source full hosted trial remain necessary; this candidate has no actual
Android/KVM acceptance yet and supplies no sensor, main navigation, physical
positioning or private protected-execution claim.
