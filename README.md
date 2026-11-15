# Skyline: locating photos on the Moon from the horizon

Hackathon MVP (DurHack 2026). A web app with an explorable 3D Moon that estimates where a
lunar-surface photo was taken by matching its skyline against horizons computed from NASA
LOLA terrain. Results are shown as a likelihood heatmap plus top candidates, never a single pin.

**Status: work in progress. Accuracy is poor and only partly measured (see below). No real photos have been tested.**

## Whole-Moon exploration and matching

The default URL now opens the **whole-Moon explorer**. Orbit, zoom, click any surface
point, or enter latitude / east longitude to visit the near side, far side, or poles.
Regional and ground views load **real 59–60 m elevation**: LOLA/Kaguya SLDEM2015
between 60°S and 60°N, and polar LOLA above those latitudes. These values change the
actual mesh, slopes and skyline. There is no vertical exaggeration or procedural
terrain. The 60 m surface grid extends 7.68 km in each direction; 120, 240 and 480 m
rings extend the measured patch to 61.44 km. Three more rings carry the overview
elevations out to 491.52 km, beyond the visible surface horizon. Tangent
grids avoid polar crowding, and stitched edges close the rings. The ground camera
and terrain picking use the detailed mesh.

The first visit downloads the required regional elevation file(s), with progress
shown in the app. Each is roughly 160–385 MB and is cached in `data/dem/moon-detail/`.
Later visits read small windows from the cache. Locations near delivery boundaries
can need more than one file. The 64 px/degree LOLA globe (~474 m at the equator)
remains the distant overview. The Sun is the only light by default, with local
terrain shadows. An optional fill light can reveal dark terrain for exploration.
The shadow map covers the nearby 32 km; this is not a full lunar radiative-transfer
or long-range illumination simulation.

**Take photo & locate globally** renders either one photograph or a six-frame panorama,
extracts its skyline, and searches the entire Moon. The test coordinates are used only
to draw the diagnostic reference and calculate error after the search; they do not
restrict or seed it. Photo analysis returns the extracted/reference overlays before
the global search finishes, and errors remain visible beside the photos. Uploads use the
same global search. The index has 4,147,200 positions at 0.125° spacing with compact
4° horizon profiles. Each of 96 global regions is refined independently with a
small local beam, down to roughly 4 m position steps and full 1° profiles before
final triangle scoring. A region cannot be thrown away just because a kilometre
sample fits poorly. That final parameter spacing is not the DEM resolution or an
accuracy claim. **The index now uses the
same native 59–60 m elevation sources as the surface.** Its compact proposal rays
are approximate; the final candidates are re-scored against the actual rendered
triangles. The three best distinct regions also receive a bounded exact-mesh
position refinement: a 200 m grid covering ±400 m, followed by continuous local
optimization. This repairs proposal-model offsets but cannot recover regions
missed by global retrieval. The green reference curve uses those same triangles, observer height,
curvature, near ground and distant rings. Surface model versions are checked so an
old coarse index cannot silently be used with a new render. Many horizons remain
ambiguous, and whole-planet retrieval is still experimental.
Flat, incomplete, poorly fitted or nearly tied skylines are explicitly marked as
unreliable. These are rejection heuristics, not calibrated confidence. The global
position grid is still about 3.8 km at the equator; it can miss a correct region
whose skyline changes sharply between samples. Higher mesh resolution alone does
not solve that retrieval limitation.

The results show the five candidates, whole-Moon relative-fit map, extracted / predicted
skyline overlays, and actual geodesic error for synthetic tests. These likelihoods are
not calibrated probabilities. The existing five detailed south-pole sites remain at
`/?site=Site01&view=site` and the site selector; their original measured accuracy below
**does not describe the new global matcher**.

The whole-Moon explorer also includes a route planner. Set endpoints by entering
coordinates or picking terrain, then calculate a slope-aware route that strongly
penalizes shadowed grid cells but can cross shadows when needed. It reports an
estimated shadowed distance and checks terrain shadows out to 32 km, using the
current Sun azimuth and elevation at the start as one fixed direction for the route.
Long routes are split at fixed great-circle waypoints, so they are not globally
optimized. Grid spacing follows the clipmap rings (60 m nearby, coarser for longer
legs); beyond the measured patch the rings use overview elevations. Shadows between
grid samples, distant obstacles and changing sunlight can be missed. These routes
are exploratory, not mission-certified navigation.

### Global setup

#### Linux / macOS

If you use the backend's `uv` project, start the API from its own environment:

```bash
cd backend
uv sync
WATCHFILES_FORCE_POLLING=true uv run fastapi dev app.py --host 127.0.0.1 --port 8077
```

Use `uv run`: a bare `fastapi` command can pick up a different system Python and
miss packages already installed by `uv add`. The backend project includes the
FastAPI CLI, Uvicorn, rasterio, pyproj and certificate bundle. `scripts/dev.sh`
also prefers `backend/.venv` when it exists.

```bash
./scripts/fetch_data.sh
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/build_moon.py   # first run downloads/converts all 34 elevation regions, then builds the index
(cd frontend && npm install)
./scripts/dev.sh
```

#### Windows

**Prerequisites:**
- Python 3.13+ ([python.org](https://www.python.org/downloads/) or Microsoft Store)
- Node.js 20+ ([nodejs.org](https://nodejs.org/))
- Git Bash or WSL is optional; all scripts work natively in PowerShell or `cmd.exe`
- ~35 GB free disk space (compressed sources, cache, browser tiles, search index)

**Setup steps:**

1. **Download NASA data:**
   ```powershell
   .\scripts\fetch_data.ps1
   ```
   (Alternatively, if curl is not available: use `python -m pip install requests` and modify the script to use Python's `requests` module, or manually download files from the links in the script comments.)

2. **Create Python virtual environment and install dependencies:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

3. **Build the Moon index (first run takes ~30 minutes):**
   ```powershell
   .\.venv\Scripts\python scripts\build_moon.py
   ```
   This downloads/converts all 34 elevation regions and builds the global search index. On restart, completed regions are reused. The build is atomic; if interrupted, it cannot expose a partial database.

4. **Install frontend dependencies:**
   ```powershell
   cd frontend
   npm install
   cd ..
   ```

5. **Start the dev server:**
   ```powershell
   .\scripts\dev.ps1
   ```
   This starts:
   - Backend (FastAPI) on `http://127.0.0.1:8077`
   - Frontend on `http://127.0.0.1:5277` (opens in browser)

   To use a different backend `uv` environment instead:
   ```powershell
   cd backend
   uv sync
   $env:WATCHFILES_FORCE_POLLING = 'true'
   uv run fastapi dev app.py --host 127.0.0.1 --port 8077
   ```
   Then in another PowerShell window:
   ```powershell
   cd frontend
   $env:SKYLINE_API_TARGET = 'http://127.0.0.1:8077'
   npm run dev -- --port 5277
   ```

**Environment variables** (all optional):
- `SKYLINE_PYTHON`: Path to Python executable (auto-detected if not set)
- `SKYLINE_BACKEND_PORT`: Backend port (default: 8077)
- `SKYLINE_FRONTEND_PORT`: Frontend port (default: 5277)

**Rebuilding after code changes:**
- Python changes: automatic with `--reload` (no action needed)
- Frontend changes: automatic with `npm run dev` (no action needed)
- Elevation cache: stored in `data/dem/moon-detail/` (reused on restart)

`build_moon.py --assets-only` prepares exploration without matching. The 16 px/degree
map remains a lightweight overview/fallback; the 64 px/degree map is used when present.
Allow about 35 GB for the compressed sources, native elevation cache, browser tiles
and search index (plus room for an old index during replacement). Native arrays
preserve the source projections, including stereographic polar caps; they are
memory-mapped rather than all loaded into RAM. Completed regions are reused on a
restart. Initial preparation can take tens of minutes depending on the network.
Rebuilding writes a new generation and atomically replaces the completion metadata,
so an interrupted build cannot expose a partial database. The backend reloads a
completed generation automatically. `scripts/dev.sh` and the `fastapi dev` command
above reload Python changes automatically. The page
polls rebuild progress and enables matching when the compatible index is ready.
`--spacing` and `--azimuths` control the index
tradeoff; coarse settings substantially reduce recall. Data files remain git-ignored.

`SKYLINE_PYTHON` can select an existing Python environment. `SKYLINE_BACKEND_PORT` and
`SKYLINE_FRONTEND_PORT` select alternate ports. The default ports remain 8077 and 5277.
The coarse globe can run without a backend. The 59–60 m surface needs the backend
(including rasterio's JP2OpenJPEG driver) and internet on an uncached first visit.
Photo matching also requires its completed index. The original detailed-site assets
are prepared separately below.

Moon photos use a subpixel, half-contrast edge crossing after the initial skyline
detection. The blurred threshold alone moved the skyline with surface brightness.
Across eight stored rendered panoramas, this correction reduced angular RMSE from
0.024–0.086° to 0.017–0.021° without using their coordinates. It cannot recover a
silhouette hidden in complete darkness.

### Current global retrieval diagnostics

The surface/index synchronization is verified, but reliable global localisation
is **not finished**. Four development checks on 2026-11-14 used ideal 360° mesh
horizons with unknown headings and no location prior. These isolate retrieval
from photo extraction; they are not a real-photo or independent accuracy benchmark.

| Test position | Best candidate error | Flagged unreliable |
|---|---:|---|
| Tycho, −43.3°, −11.2° | 5.5 m | No (still labelled unverified) |
| Equator, 0°, 0° | 2,302 km | Yes |
| Far side, 20°, 155° | 1,685 km | Yes |
| Near north pole, 89.9°, 30° | 4,637 km | Yes |

Keeping each retrieved region alive through fine sampling fixed Tycho's earlier
3,281 km miss. The other correct regions were absent from the selected global
seeds. Changing the skyline comparison metric did not resolve those misses.
The position grid needs a better global retrieval strategy; a fine-looking mesh
or a close fit to a false candidate does not establish location. Runs took about
59–67 seconds each on the development laptop.

Reproduce with `backend/.venv/bin/python eval/eval_moon.py --output eval/runs/moon/check.json`.

### Global code and checks

- `backend/skyline/moon.py`: wrapped overview sampling,
  chunked whole-planet scoring and refinement.
- `backend/skyline/moon_native.py`: global native elevation preparation and memory maps.
- `backend/skyline/moon_surface.py`: shared surface specification, global proposal
  rays and exact triangle-edge horizon calculation.
- `backend/skyline/moon_detail.py`: measured elevation downloads, caching, polar
  projection and bounded, pollable surface preparation.
- `scripts/build_moon.py`: elevation tiles and global search-index preparation.
- `frontend/src/moon-{detail,terrain,world,app}.ts`: measured clipmaps, terrain LOD,
  navigation, capture and results.
- `tests/test_moon*.py`: spherical geometry, source coverage, half-metre elevation
  scale, polar projection, seam and interpolation regressions.
- `frontend/tools/test-moon-terrain.ts`: coordinate frames, LOD, seam and pole picking.
- `frontend/tools/check-moon-horizon.ts`: independent intersection of Three.js mesh
  triangles with observer planes, for comparison with exported backend horizons.
  Checked at Tycho, the equator and north pole: maximum discrepancy below 0.00001°
  over 27 tested azimuths. This verifies geometry alignment, not global localisation accuracy.

**Linux / macOS:**
```bash
.venv/bin/python -m unittest discover -s tests
npm run build --prefix frontend
frontend/node_modules/.bin/esbuild frontend/tools/test-moon-terrain.ts --bundle --platform=node --format=esm --outfile=/tmp/skyline-terrain-test.mjs
node /tmp/skyline-terrain-test.mjs
```

**Windows:**
```powershell
.\.venv\Scripts\python -m unittest discover -s tests
npm run build --prefix frontend
$tempFile = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), 'skyline-terrain-test.mjs')
npx esbuild frontend/tools/test-moon-terrain.ts --bundle --platform=node --format=esm --outfile=$tempFile
node $tempFile
Remove-Item -Path $tempFile
```

Sources: [NASA SLDEM2015 description](https://pgda.gsfc.nasa.gov/products/54),
[LOLA/Kaguya elevation tiles](https://imbrium.mit.edu/DATA/SLDEM2015/TILES/JP2/),
[PDS polar LOLA elevations](https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/polar/jp2/),
and [NASA CGI Moon Kit](https://svs.gsfc.nasa.gov/4720/) for the overview.
These provide real topography; they do not provide 5 m ground detail everywhere.

## Detailed south-pole site mode

### What works


- **3D viewer** (Vite + TypeScript + three.js): global overview, measured 59–60 m surface and
  five 16 x 16 km high-res site blocks near the south pole (LOLA 5 m DEMs, shown at ~20 m).
  One block is active at a time (Site dropdown).
- **Test from a spot**: click the site terrain to drop in at eye height (Street View style: drag to
  look, scroll to zoom, click ground to walk). "Take photo & locate" renders a synthetic photo with
  three.js (boulders, small craters, sensor noise, hard shadows) and runs it through the pipeline;
  shows true vs estimated position, heading error, likely cause of a miss.
- **Locate a photo**: upload one or more photos (optionally as a panorama, optionally with a known heading).
- **Batch tab**: render random spots with live scoring, or replay stored test photos; click a row for a
  full inspector (photo with skylines, renders from estimated and true spot, maps, numbers).
- **Backend** (FastAPI + NumPy + Numba + rasterio): DEM -> Moon-centred Cartesian (curvature
  included), ray-marched horizon database (50 m grid, 1 deg azimuth, 1.7 m observer), skyline
  extraction, FFT heading-free matching, 10 m refinement around the best peaks.

## Measured so far (simulation only)

Quick check, `eval/quick.py`: first 80 of 400 stored synthetic spots on Site01, single 66 deg photo,
sun elevation 2-8 deg, 50 m grid. 30% of photos had no usable skyline (too dark) and count as misses.

| scoring rule | within 100 m | within 500 m | within 1 km | top-5 hit | median error (usable) | p90 (usable) |
|---|---|---|---|---|---|---|
| `ssd` absolute height (app default) | 19% | 29% | 31% | 20% | 2597 m | 9496 m |
| `zssd` shape only, pitch-free | 16% | 21% | 24% | 18% | 3183 m | 12301 m |
| `ncc` cross-correlation (original brief) | 10% | 11% | 14% | 10% | 7660 m | 12457 m |

Not yet measured: panorama / multi-photo / 120 deg / heading hint, failure-class breakdown,
calibration of the heatmap, the other four sites. `eval/run_eval.py` does all of this for Site01
(roughly 30-40 min CPU) and writes tables, plots and a failure log to `eval/results/`.
**EVALUATION.md has not been written.**

## Detailed-site setup

Needs Python 3.12+, Node 20+, ~2 GB disk. Google Chrome is only needed for the headless batch driver.

### Linux / macOS

```bash
./scripts/fetch_data.sh                                   # ~250 MB of NASA data into data/raw/
python3.12 -m venv .venv
.venv/bin/pip install numpy numba rasterio pyproj fastapi 'uvicorn[standard]' python-multipart pillow scipy matplotlib
for S in Site01 Site04 Site07 Site11 Site20; do           # ~2 min and ~260 MB per site
  .venv/bin/python scripts/build_horizon_db.py --site $S --spacing 50
done
.venv/bin/python scripts/prep_assets.py                   # browser assets -> frontend/public/data/
(cd frontend && npm install)
./scripts/dev.sh                                          # backend :8077 + frontend http://localhost:5277
```

### Windows

1. **Download NASA data:**
   ```powershell
   .\scripts\fetch_data.ps1
   ```

2. **Create Python environment and install dependencies:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install numpy numba rasterio pyproj fastapi "uvicorn[standard]" python-multipart pillow scipy matplotlib
   ```

3. **Build horizon databases for all sites** (~2 min per site, ~260 MB each):
   ```powershell
   foreach ($site in @('Site01', 'Site04', 'Site07', 'Site11', 'Site20')) {
       .\.venv\Scripts\python scripts\build_horizon_db.py --site $site --spacing 50
   }
   ```

4. **Prepare browser assets:**
   ```powershell
   .\.venv\Scripts\python scripts\prep_assets.py
   ```

5. **Install frontend dependencies:**
   ```powershell
   cd frontend
   npm install
   cd ..
   ```

6. **Start the dev server:**
   ```powershell
   .\scripts\dev.ps1
   ```
   Opens backend on `http://127.0.0.1:8077` and frontend on `http://127.0.0.1:5277`

**To recreate the 400 stored test photos** (needed for "Replay" and eval scripts), with the app running:

```powershell
cd frontend
node tools/drive.mjs "http://localhost:5277/?batch=400&run=main&seed=20261114" --wait-batch
cd ..
.\.venv\Scripts\python eval\run_eval.py --quick 1
.\.venv\Scripts\python eval\quick.py 80
```

## Layout

- `backend/skyline/terrain.py` DEM -> Cartesian, site list; `horizon.py` Numba ray-marcher;
  `extract.py` skyline from image; `match.py` FFT matcher; `locate.py` pipeline + failure classes
- `backend/app.py` API; `backend/config.json` default scoring rule and grid
- `frontend/src/world.ts` scene; `photo.ts` synthetic photos; `main.ts` UI; `batch.ts` harness
- `eval/` offline scoring (`run_eval.py`, `quick.py`, `gallery.py`); `scripts/` data + build + dev
- `INDICATORS.md` ranked list of further indicators (mostly unmeasured)

## Known limitations

- **Shadows are the main failure.** At polar sun angles much of the true horizon is black against a
  black sky, so the extracted skyline is the top of the lit ground. Next step: match the predicted
  *lit* skyline (needs sun direction), or at least treat the found skyline as a lower bound.
- Each block is an island: terrain outside the 16 km tile does not exist for the matcher or the
  synthetic photos. Real photos would show distant mountains the app knows nothing about.
- One block is searched at a time; uploads are not searched across blocks.
- Roll is assumed 0, pitch and field of view are user inputs, camera is a pinhole.
- Even in an idealised check (queries from the backend's own ray-marcher) about 20% of spots were
  missed because the horizon changes faster than the 50 m grid near the camera.
- The heatmap likelihood scale is a heuristic and is not calibrated.
- The globe is for looks only; the site blocks do not join it seamlessly.
- Synthetic renders use a Lambert surface, no secondary illumination, shadow-map resolution ~3 m.
- Stored test photos, the in-app accuracy note and the eval scripts cover Site01 only.
