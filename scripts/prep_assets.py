"""Convert the raw NASA files into small browser assets in frontend/public/data/.

- moon_color.jpg   : CGI Moon Kit colour map (visuals only)
- moon_disp.bin    : int16 metres relative to the 1737.4 km sphere, 721 x 1441 nodes (0.25 deg), north to south, lon -180..180
- sites/<id>/site_xyz.bin   : float32 site-local XYZ of each downloaded site DEM resampled to ~20 m (800 x 800 x 3)
- sites/<id>/site_meta.json : frame definition + grid sizes
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from skyline.terrain import available_sites, load_terrain, unload_terrain  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
RAW, OUT = ROOT / "data/raw", ROOT / "frontend/public/data"
OUT.mkdir(parents=True, exist_ok=True)

Image.open(RAW / "lroc_color_poles_4k.tif").convert("RGB").save(OUT / "moon_color.jpg", quality=88)

# uint16 half-metres relative to a radius of 1,727,400 m (per the SVS page) -> metres relative to 1,737,400 m
d = np.asarray(Image.open(RAW / "ldem_16_uint.tif"), np.float64) * 0.5 - 10000.0
ppd = d.shape[1] / 360.0
lat = np.linspace(90, -90, 721)
lon = np.linspace(-180, 180, 1441)
rr = np.clip((90 - lat) * ppd - 0.5, 0, d.shape[0] - 1)
cc = ((lon + 180) * ppd - 0.5) % d.shape[1]
R, C = np.meshgrid(rr, cc, indexing="ij")
node = map_coordinates(d, [R, C], order=1, mode="grid-wrap")
node[0], node[-1] = node[0].mean(), node[-1].mean()
node.round().astype("<i2").tofile(OUT / "moon_disp.bin")

for site in available_sites():
    t = load_terrain(site)
    H, W = t.shape
    N = 800
    R, C = np.meshgrid(np.linspace(0, H - 1, N), np.linspace(0, W - 1, N), indexing="ij")
    d = OUT / "sites" / site
    d.mkdir(parents=True, exist_ok=True)
    t.sample(R, C).astype("<f4").tofile(d / "site_xyz.bin")
    meta = dict(t.meta, mesh_n=N, disp_shape=[721, 1441])
    meta.pop("crs_wkt")
    (d / "site_meta.json").write_text(json.dumps(meta))
    print(site, "centre lon/lat:", round(t.meta["centre_lon"], 3), round(t.meta["centre_lat"], 3))
    unload_terrain(site)
