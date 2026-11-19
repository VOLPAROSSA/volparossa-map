# Advisory traffic in the Linux desktop

A local Core publication can supply traffic colors and vehicle travel costs to the pinned Organic Maps desktop. Both continue to use the installed offline map. This input reads a private handoff written by the existing Map adapter. Set the trusted publisher key and exact region explicitly. The desktop opens no traffic network connection for this input. [Core adapter](TRAFFIC_ADAPTER.md)

Apply the integration before configuring a clean checkout of the source commit in `upstream.lock.json`.

```sh
python3 -B integrations/native-traffic/apply.py /absolute/path/to/organicmaps
cmake -S /absolute/path/to/organicmaps -B /absolute/path/to/build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build /absolute/path/to/build --target desktop
/absolute/path/to/build/OrganicMaps --volparossa_traffic=/absolute/private-directory/traffic.json \
  --volparossa_traffic_region=Monaco --volparossa_traffic_publisher=TRUSTED_64_HEX_PUBLIC_KEY
```

The slot must be an absolute path in an owner-only directory (0700), with a regular, owned 0600 handoff. Use the adapter's newly created output or atomically rename a fresh private output into that slot. Delete the slot to withdraw it. The desktop verifies a full SHA-256 over the registered map reader in 1 MiB slices, checks the installed version and looks up each directed segment in the car graph. The private directory stores the latest accepted revision and time window for that publisher, region and map version. Keep it alongside the slot across launches. Unknown and omitted observations clear old colors. A replaced file must acquire a fresh map registration before it can receive traffic.

The disposable Debian 13 trial used real Core systemd services, a separate application account, a signed revision and the normal cache-only fetch. The Core cache had been seeded with the genuine signed envelope. The guest had no network card. An owner-only result channel carried the unchanged 65,309-byte handoff to isolated Linux Qt processes. The guest used QEMU software emulation and the renderer used Mesa llvmpipe. The three official 260928 maps stayed outside the repository. The 719 road observations were synthetic. The two measurements of independent provider delivery and the desktop cache cover different publications. [Provider trial](https://github.com/VOLPAROSSA/volparossa/actions/runs/44438057954)

| Monaco vehicle route | Directed route segments | Distance | ETA | Cached advisories |
| --- | ---: | ---: | ---: | ---: |
| Before handoff | 61 | 1,083.24 m | 108.19 s | 0 |
| Fresh handoff | 365 | 3,746.96 m | 1,996.62 s | 719 |
| After expiry or replacement | 61 | 1,083.24 m | 108.19 s | 0 |

A new inode containing the same map bytes cleared the old generation with 50 seconds of snapshot validity remaining. With the original map in place, expiry restored the same directed route and ETA. On two real Qt views, the fresh handoff added 820 and 141 red pixels. After expiry the red counts returned to baseline. Withdrawal also removed the overlay. [Route expiry](evidence/native-traffic-debian13-20261119/routing-expiry.json) · [Map replacement](evidence/native-traffic-debian13-20261119/routing-map-replacement.json) · [Drape expiry](evidence/native-traffic-debian13-20261119/drape-expiry.json) · [Drape withdrawal](evidence/native-traffic-debian13-20261119/drape-withdrawal.json)

The recorded build used Organic Maps `b20d1417eef1e3c63a447ac8658db4307a066024`, patch SHA-256 `f405faf673cbd0b3e42a2c4f294327abf7b4fa43b137dcdfdd05e60353fb4386`, Core `7fe73bf18d8f8f0d5b1096d4fd6485c3315a6e69` and desktop executable SHA-256 `cd33178089a76f83bcef4e6b9697851fed512984eddaec5f2641ec71353fb2ce`. All four receipts bind to handoff SHA-256 `310f8ad5a7034341906efc29d7971368dc8ff95a017235a34b5ed102f10322b5`. The hosted workflow provisions the same pinned sources and runs the joint trial with KVM. [Workflow](../.github/workflows/native-traffic.yml)

Android traffic application, iOS traffic application and multi-contributor observation collection remain open. The separately tested Android offline routes are recorded in the project README. [Android offline trial](../tests/offline-route/README.md)
