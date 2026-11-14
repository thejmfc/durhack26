"""Site DEM -> Moon-centred Cartesian geometry.

The DEM is read with rasterio and un-projected with pyproj using the CRS stored in the
file (no hand-written projection). Every pixel becomes a 3D point; all horizon geometry
is done on those points, so the Moon's curvature is included by construction.

To keep float32 precise to millimetres we store the points in a "site-local" frame: a
rigid rotation + translation of the Moon-centred frame whose origin sits on the reference
sphere at the tile centre, with axes (east, north, up) at that origin. It is still a
plain Cartesian frame - the Moon's centre is simply at (0, 0, -R).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
# PGDA product 78 sites that share the 16 x 16 km, 3200 x 3200 px layout
SITES = {
    "Site01": "Connecting ridge",
    "Site04": "Shackleton rim",
    "Site07": "Peak near Shackleton",
    "Site11": "de Gerlache rim",
    "Site20": "Leibnitz beta plateau",
}
DEFAULT_SITE = "Site01"


def dem_path(site: str) -> Path:
    return ROOT / f"data/raw/{site}_final_adj_5mpp_surf.tif"


CACHE = ROOT / "data/cache"
OBSERVER_HEIGHT = 1.7


@dataclass
class Terrain:
    xyz: np.ndarray  # [H, W, 3] float32 site-local Cartesian metres
    meta: dict

    @property
    def shape(self):
        return self.xyz.shape[:2]

    @property
    def moon_centre(self) -> np.ndarray:
        return np.array(self.meta["moon_centre_local"], dtype=np.float64)

    @property
    def axis(self) -> np.ndarray:
        return np.array(self.meta["spin_axis_local"], dtype=np.float64)

    # map (projected metres) <-> fractional pixel-centre indices
    def xy_to_rc(self, x, y):
        a, b, c, d, e, f = self.meta["affine"]  # x = a*col' + c ; y = e*row' + f (north-up, no shear)
        return (np.asarray(y, float) - f) / e - 0.5, (np.asarray(x, float) - c) / a - 0.5

    def rc_to_xy(self, r, c):
        a, b, c0, d, e, f = self.meta["affine"]
        return a * (np.asarray(c, float) + 0.5) + c0, e * (np.asarray(r, float) + 0.5) + f

    def sample(self, r, c) -> np.ndarray:
        """Bilinear XYZ at fractional pixel coords (vectorised, clamped to the tile)."""
        H, W = self.shape
        r = np.clip(np.asarray(r, float), 0, H - 1)
        c = np.clip(np.asarray(c, float), 0, W - 1)
        r0 = np.minimum(r.astype(np.int64), H - 2)
        c0 = np.minimum(c.astype(np.int64), W - 2)
        fr = (r - r0)[..., None]
        fc = (c - c0)[..., None]
        g = self.xyz
        return (g[r0, c0] * (1 - fr) * (1 - fc) + g[r0, c0 + 1] * (1 - fr) * fc
                + g[r0 + 1, c0] * fr * (1 - fc) + g[r0 + 1, c0 + 1] * fr * fc)

    def frame_at(self, p: np.ndarray):
        """(east, north, up) unit vectors of the local horizon at site-local point p."""
        up = p - self.moon_centre
        up = up / np.linalg.norm(up)
        east = np.cross(self.axis, up)
        east /= np.linalg.norm(east)
        return east, np.cross(up, east), up


def _build(site: str) -> Terrain:
    import rasterio
    from pyproj import CRS, Transformer

    with rasterio.open(dem_path(site)) as ds:
        h = ds.read(1).astype(np.float64)
        crs = CRS.from_wkt(ds.crs.to_wkt())
        T = ds.transform
        H, W = ds.height, ds.width
    assert T.b == 0 and T.d == 0, "expected a north-up raster"
    assert not np.isnan(h).any()
    R = crs.ellipsoid.semi_major_metre
    assert abs(R - crs.ellipsoid.semi_minor_metre) < 1e-6, "expected a spherical datum"
    to_geo = Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)

    def cart(x, y, hh):
        lon, lat = to_geo.transform(x, y)
        lon, lat = np.radians(lon), np.radians(lat)
        rr = R + hh
        return np.stack([rr * np.cos(lat) * np.cos(lon), rr * np.cos(lat) * np.sin(lon), rr * np.sin(lat)], -1)

    xc = T.c + T.a * (W / 2)
    yc = T.f + T.e * (H / 2)
    lon0, lat0 = to_geo.transform(xc, yc)
    O = cart(xc, yc, 0.0)
    up = O / np.linalg.norm(O)
    east = np.cross([0, 0, 1.0], up)
    east /= np.linalg.norm(east)
    north = np.cross(up, east)
    Rm = np.stack([east, north, up])  # moon-fixed -> local

    xs = T.c + T.a * (np.arange(W) + 0.5)
    xyz = np.empty((H, W, 3), np.float32)
    for r0 in range(0, H, 400):
        rows = np.arange(r0, min(r0 + 400, H))
        ys = T.f + T.e * (rows + 0.5)
        X, Y = np.meshgrid(xs, ys)
        xyz[rows] = ((cart(X, Y, h[rows]) - O) @ Rm.T).astype(np.float32)
    meta = dict(
        id=site, name=f"{site} ({SITES.get(site, site)})", height=H, width=W, pixel_size=float(T.a),
        affine=[T.a, T.b, T.c, T.d, T.e, T.f], radius=R, centre_lon=lon0, centre_lat=lat0,
        centre_xy=[xc, yc], rot_moon_to_local=Rm.tolist(), origin_moon=O.tolist(),
        moon_centre_local=(Rm @ -O).tolist(), spin_axis_local=Rm[:, 2].tolist(),
        observer_height=OBSERVER_HEIGHT, crs_wkt=crs.to_wkt(),
        height_range=[float(h.min()), float(h.max())],
    )
    return Terrain(xyz, meta)


_terrains: dict[str, Terrain] = {}


def available_sites() -> list[str]:
    return [s for s in SITES if dem_path(s).exists()]


def load_terrain(site: str = DEFAULT_SITE) -> Terrain:
    if site not in SITES:
        raise KeyError(f"unknown site {site}")
    if site not in _terrains:
        f_xyz, f_meta = CACHE / f"{site}_xyz.npy", CACHE / f"{site}_meta.json"
        if f_xyz.exists() and f_meta.exists():
            _terrains[site] = Terrain(np.load(f_xyz), json.loads(f_meta.read_text()))
        else:
            t = _build(site)
            CACHE.mkdir(parents=True, exist_ok=True)
            np.save(f_xyz, t.xyz)
            f_meta.write_text(json.dumps(t.meta))
            _terrains[site] = t
    return _terrains[site]


def unload_terrain(site: str):
    _terrains.pop(site, None)
