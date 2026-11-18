# VOLPAROSSA Map website

The Mountain Passes is the English project page for [VOLPAROSSA Map](https://volparossa.org/volparossa-map/). It uses the shared theme 1.1.0 exported from the central site at `991cf1638c906226574b9c21bb32c6618fc07fb7`. The ordinary offline build uses the Python standard library and committed illustrations.

## Local preview

From the repository root, run the following commands. Open [the nested preview](http://127.0.0.1:8782/volparossa-map/).

```sh
python3 website/scripts/build_site.py
python3 website/scripts/check_site.py
python3 website/scripts/test_mesh.py
python3 -m http.server 8782 --bind 127.0.0.1 --directory website/preview
```

## Production

```sh
python3 website/scripts/build_site.py --production --output website/dist/volparossa-map
python3 website/scripts/check_site.py --production --output website/dist/volparossa-map
```

The Map website workflow attaches the preview on pull requests. A manual run on main publishes the validated product directory through GitHub Pages. The preview carries `noindex,nofollow`. Production carries the canonical project address and a project sitemap.

## Design and verification

- [Asset sources and art direction](docs/design-and-sources.md)
- [Desktop, mobile and comparison captures](review/README.md)
- `src/network.json` is the supplied public-download graph. `src/scene-anchors.json` locates its nodes on the two images. The static road illustration uses `src/traffic-example.json`.
- `src/content.json` configures both product documentation buttons and the links to the accepted development evidence.
- `masters/` contains the reference, generated source images, generation descriptions and original icon paths. `src/variants.json` records export masters, crop rectangles and intrinsic sizes. `asset-manifest.json` inventories originals and public derivatives.
- Optional browser QA runs from `website/` with `npm ci`, Playwright browsers and `npm run qa` while the preview server is running. Set `CHROME_PATH` to use a system Chromium executable. Set `QA_ENGINES` to a comma-separated list of Chromium, Firefox or WebKit names in lowercase.
