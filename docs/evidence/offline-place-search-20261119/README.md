# Find a place on the offline Monaco map

Open the original Organic Maps Linux desktop with the pinned 260928 editions of Monaco, World and WorldCoasts installed. The recorded trial used a fresh profile and a network namespace containing only loopback. [Map input pins](../../../tests/offline-route/inputs.json) · [Desktop source and build](../../../.github/workflows/native-toolchains.yml)

Open **Offline Search** with **Ctrl+F**, use **Everywhere**, enter each query and click its first result.

| Query | First result | Place page |
| --- | --- | --- |
| Oceanographic | Oceanographic Museum of Monaco | Museum |
| Monte Carlo Casino | Monte-Carlo Casino and Opera House | Attraction and casino |

Both search rows showed Monaco, Monaco. The running Qt app displayed the selected place name and feature category in its ordinary place panel. Tesseract read the first result row and place panel in four captures of the X11 display. The application exited with code 0. The report records all four original screenshot hashes, the executable and all three maps. Map data © OpenStreetMap contributors and Organic Maps. [Recorded observations](result.json) · [Data provenance](../../../upstream.lock.json)
