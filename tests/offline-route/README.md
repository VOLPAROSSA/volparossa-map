# Offline pedestrian routes on Android

This opt-in hosted trial runs the published Organic Maps Android app, in its original branding, in an Android 35 x86-64 emulator. Only the emulator namespace's loopback interface is available to the app and ADB. The real Monaco region and its two matching world files come from the public release catalog. All three full file SHA-256 hashes are checked before installation. The trial opens two public pedestrian routes through the app's ordinary URL handler after separate cold starts and reads the duration and distance from the native route UI.

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) and [Organic Maps](https://organicmaps.app). The runner uses the original release under its [binary data license](https://github.com/organicmaps/organicmaps/blob/b20d1417eef1e3c63a447ac8658db4307a066024/DATA_LICENSE.txt). The artifact contains only measurements and the public input digests.

The traffic adapter and its signed peer trial run separately. This Android proof measures base-map routing without a traffic overlay.
