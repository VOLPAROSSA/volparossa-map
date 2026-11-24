# DPS in Organic Maps

<a id="dps-location-integration-boundary"></a>
<a id="one-shot-current-fix-check-in-the-android-place-page"></a>
<a id="next-app-owned-android-slice"></a>

## Check the current Android fix

Open **My Position** in the VOLPAROSSA WebDebug app and tap **Check this fix once on this device**. With precise Android location permission, the app reads the latest fix already accepted by Organic Maps. Latitude, longitude, WGS84 ellipsoidal height and both platform error estimates go to the local Core library. The display shows a coordinate-free quality result. The feature requests no new sensor permissions and stores no position or summary.

A single eligible fix can show a comparison scale in metres. For example, horizontal and vertical estimates of 3 m and 4 m give a 5 m scale. The joint confidence of those estimates and the physical integrity of the position remain unknown. The sample must be less than five seconds old by both elapsed time and wall time. The summary clears when the fix changes, the time limit expires, the user taps **Clear position check**, or the panel leaves the foreground. Selecting another place hides the check. Returning requires a fresh tap. The original provider and offline route planner continue to use the ordinary Android position.

When Android marks a recent fix as simulated, the panel shows that origin and withholds the numerical quality estimate. This uses the platform marker on the selected fix. An unmarked fix still has unknown integrity. The route planner continues to use the ordinary Organic Maps provider.

The source-build script takes the pinned Organic Maps checkout, pinned VOLPAROSSA Core checkout and Android ABI. It supports build preparation for `x86_64` and `arm64-v8a`. Its prerequisites include the Android SDK, NDK and Rust target. It packages the linked Cargo dependency notices, Rust standard-library and NDK notices, and Map license in the APK. The hosted workflow provisions the disposable x86-64 build and verifies the notice bytes inside the APK. [Source-build script](../integrations/my-position/assemble.sh) · [Core revision](../integrations/my-position/core-revision) · [Android workflow](../.github/workflows/native-android-source.yml)

## Installed application evidence

On 24 November 2026, the source-built WebDebug application opened from the ordinary Android launcher. On the disconnected x86-64 Android 15 emulator (API 35), the installed Organic Maps app calculated two pedestrian routes of 390 m and 490 m after separate cold starts. Its actual **My Position** place page then exercised the linked Core geodetic assessor. Each test used fictional Android input. [Original receipt](evidence/current-fix-45424320264/receipt.json) · [Hosted test](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/45424320264)

| Installed-app check | Observed behavior |
| --- | --- |
| Precise permission denied | The panel reported the missing permission after the tap and cleared on request. |
| Accepted and incomplete fixes | An explicit tap on a fix accepted through the ordinary `LocationHelper` callback showed a 5 m comparison scale. A replacement cleared it. Missing vertical accuracy and an old provider timestamp each produced an unavailable result. |
| Selected observation expires | The instrumented test held a copy of one fictional accepted fix in the panel. Its summary disappeared, and another tap after expiry could not assess that same observation. |
| Background and selection | The Android task left the foreground and reopened through its normal launcher. The prior assessment was gone, and selecting a different place hid the control. |

Map head `bd9eb42d` and the tested merge `7010c26d` share tree `4800d5eb`. The trial linked Core `b6fa12bc` and Organic Maps `b20d1417`. The receipt records the APK and instrumentation APK identities, observed checkpoints and shutdown of the app, ADB and emulator. Physical positioning accuracy, independent integrity, sensor fusion and arm64 field operation remain open. [Original artifact ZIP](evidence/current-fix-45424320264/organic-maps-source-android.zip) · [Source and archive digests](evidence/current-fix-45424320264/verification.json) · [Core native proof](https://github.com/VOLPAROSSA/volparossa/actions/runs/45414210610/job/138516186682)

Earlier attempts preserve the direct-Activity cold-start failure, the expired-fix test that read a live provider, and the Android instrumentation abort. The final trial reported each independent case against the same installed application. [Cold start](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/45412651741) · [Live provider](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/45417232018) · [Instrumentation abort](https://github.com/VOLPAROSSA/volparossa-map/actions/runs/45419469459)

## Existing native inputs

The pinned upstream revision is Organic Maps `b20d1417`. [Upstream pin and licenses](UPSTREAM.md)

- On Android, `LocationHelper` filters provider updates and manages listeners. Organic Maps still sends ordinary fixes through `LocationState.nativeLocationUpdated` to routing and tracking. The optional quality check reads the current fix only from the selected My Position panel. [Android location helper](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/java/app/organicmaps/sdk/location/LocationHelper.java) · [Java callback](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/java/app/organicmaps/sdk/LocationState.java) · [C++ callback](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/android/sdk/src/main/cpp/app/organicmaps/sdk/LocationState.cpp)
- On iOS, `MWMLocationManager` owns platform authorization, location updates and observer delivery. A native iOS DPS consumer remains open. [iOS location manager](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/iphone/Maps/Core/Location/MWMLocationManager.mm)
- Routing and the current-position error circle use `GpsInfo` and its horizontal accuracy. The Android DPS summary has no coordinates and feeds neither consumer. [Map view integration](../integrations/my-position/organicmaps.patch)

## Data, trust and lifecycle requirements

Android API 26 or newer supplies the vertical-error field. The check requires a valid WGS84 ellipsoidal height, positive horizontal and vertical errors and consistent, recent platform timestamps. It uses Android’s raw ellipsoidal altitude. Mean-sea-level altitude has a different datum. The Core geodetic entry point handles the WGS84 transformation and returns a typed quality summary. The app has no evidence for a specific sensor provenance or the joint confidence of the two errors. [Android adapter](../integrations/my-position/java/MyPositionQuality.java) · [Native bridge](../integrations/my-position/quality_jni.c)

Further location inputs need an explicit frame, datum and time. A local Cartesian frame also needs a known origin, orientation and epoch before comparison. A future multi-input consumer must show conflicts and unresolved integrity, preserve consent and expiry, and report unavailable results when it lacks suitable evidence. Sensor acquisition, public contributions and recording movement each need a separate user choice. The mobile client uses the narrow local library interface. The Core agent’s administrative socket has no role here.

## Scope and acceptance stages

| Stage | Evidence and remaining work |
| --- | --- |
| Synthetic fixtures | The archived Android fixture covers typed Core results, expiry and revocation. The installed-app trial above adds fictional Android provider inputs in the ordinary Map place page. |
| Native apps | The source-built Android x86-64 app exercised the one-shot current-fix check and offline routing. A physical Android device, arm64 installation and an iOS consumer still require separate trials. |
| Positioning research | Supplementary indoor or weak-GNSS positioning, sensor fusion and integrity detection require independent reference measurements and controlled, authorized hardware trials before drawing performance conclusions. |

## Earlier Android fixture evidence

The original Android debug-Activity/JNI history is preserved byte for byte from Map `e17fd88f`, including the original failure reports, acceptance limits, toolchain pins and source links. Its SHA-256 is `279cbf3d6e233ede9273983ddd0e0202ba650309f3aeb062058d38c6308a0e68`. [Full original document](DPS_ANDROID_FIXTURE.md)

- <a id="app-owned-android-quality-candidate"></a>[App-owned Android quality candidate](DPS_ANDROID_FIXTURE.md#app-owned-android-quality-candidate)
- <a id="isolated-android-jni-fixture-source-status-and-reproduction"></a>[Isolated Android JNI fixture: source status and reproduction](DPS_ANDROID_FIXTURE.md#isolated-android-jni-fixture-source-status-and-reproduction)
- <a id="hosted-candidate-and-evidence-boundaries"></a>[Hosted candidate and evidence boundaries](DPS_ANDROID_FIXTURE.md#hosted-candidate-and-evidence-boundaries)
- <a id="preserved-first-failed-attempt-and-kvm-correction"></a>[Preserved first failed attempt and KVM correction](DPS_ANDROID_FIXTURE.md#preserved-first-failed-attempt-and-kvm-correction)
- <a id="actual-kvm-query-and-cancellation-baseline-failure"></a>[Actual KVM query and cancellation baseline failure](DPS_ANDROID_FIXTURE.md#actual-kvm-query-and-cancellation-baseline-failure)
- <a id="firewall-configuration-comparison-and-preserved-diagnostic-attempt"></a>[Firewall configuration comparison and preserved diagnostic attempt](DPS_ANDROID_FIXTURE.md#firewall-configuration-comparison-and-preserved-diagnostic-attempt)
- <a id="public-build-passed-preserved-offline-rebuild-failure"></a>[Public build passed; preserved offline rebuild failure](DPS_ANDROID_FIXTURE.md#public-build-passed-preserved-offline-rebuild-failure)
- <a id="captured-offline-local-address-marker-and-loopback-correction"></a>[Captured offline local-address marker and loopback correction](DPS_ANDROID_FIXTURE.md#captured-offline-local-address-marker-and-loopback-correction)
- <a id="offline-rebuild-passed-emulator-failure-observations-candidate-unrun"></a>[Offline rebuild passed; emulator failure observations candidate unrun](DPS_ANDROID_FIXTURE.md#offline-rebuild-passed-emulator-failure-observations-candidate-unrun)
- <a id="adb-listener-correction-and-preserved-abort"></a>[ADB listener correction and preserved abort](DPS_ANDROID_FIXTURE.md#adb-listener-correction-and-preserved-abort)
- <a id="emulator-boot-passed-instrumentation-result-remains-unaccepted"></a>[Emulator boot passed; instrumentation result remains unaccepted](DPS_ANDROID_FIXTURE.md#emulator-boot-passed-instrumentation-result-remains-unaccepted)
- <a id="private-pid-1-group-reaping-candidate-and-hosted-regression"></a>[Private PID-1 group reaping candidate and hosted regression](DPS_ANDROID_FIXTURE.md#private-pid-1-group-reaping-candidate-and-hosted-regression)
- <a id="catchable-cancellation-requirements-and-scoped-hosted-proof"></a>[Catchable cancellation requirements and scoped hosted proof](DPS_ANDROID_FIXTURE.md#catchable-cancellation-requirements-and-scoped-hosted-proof)
- <a id="targeted-test-dependency-permission-correction"></a>[Targeted test dependency permission correction](DPS_ANDROID_FIXTURE.md#targeted-test-dependency-permission-correction)
- <a id="completed-emulator-kvm-configuration-checkpoint-candidate"></a>[Completed-emulator KVM configuration checkpoint candidate](DPS_ANDROID_FIXTURE.md#completed-emulator-kvm-configuration-checkpoint-candidate)
- <a id="ipv6-route-diagnostics-candidate-raw-verdict-remains-strict"></a>[IPv6 route diagnostics candidate; raw verdict remains strict](DPS_ANDROID_FIXTURE.md#ipv6-route-diagnostics-candidate-raw-verdict-remains-strict)
- <a id="completed-emulator-ipv6-configuration-checkpoint-candidate"></a>[Completed-emulator IPv6 configuration checkpoint candidate](DPS_ANDROID_FIXTURE.md#completed-emulator-ipv6-configuration-checkpoint-candidate)
- <a id="separately-reviewed-linux-70-azure-renderer-profile"></a>[Separately reviewed Linux 7.0 Azure renderer profile](DPS_ANDROID_FIXTURE.md#separately-reviewed-linux-70-azure-renderer-profile)
- <a id="separately-reviewed-linux-617-azure-renderer-profile"></a>[Separately reviewed Linux 6.17 Azure renderer profile](DPS_ANDROID_FIXTURE.md#separately-reviewed-linux-617-azure-renderer-profile)
- <a id="accepted-bounded-synthetic-debug-activityjni-checkpoint"></a>[Accepted bounded synthetic debug-Activity/JNI checkpoint](DPS_ANDROID_FIXTURE.md#accepted-bounded-synthetic-debug-activityjni-checkpoint)
- <a id="expiry-after-android-sleep"></a>[Expiry after Android sleep](DPS_ANDROID_FIXTURE.md#expiry-after-android-sleep)
