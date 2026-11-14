"""Measured surface geometry: SLDEM2015 (59 m) and polar LOLA (60 m).

The JP2 files contain signed elevation samples, not colour imagery. Cache the
regional source once, then read only the window needed for a surface clipmap.
Source scales come from their PDS labels: int16 * 0.5 metres above 1737.4 km.
"""
from __future__ import annotations

import math
import ssl
import threading
import tempfile
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass

import numpy as np
import rasterio
import certifi
from rasterio.windows import Window

from .terrain import ROOT
from .moon_surface import N, STEPS, FINE_LEVELS, MODEL_ID, coarse_samples

RADIUS = 1_737_400.0
SOURCE_DIR = ROOT / 'data/dem/moon-detail'
SLDEM_URL = 'https://imbrium.mit.edu/DATA/SLDEM2015/TILES/JP2/'
LOLA_URL = 'https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/polar/jp2/'
TLS = ssl.create_default_context()
TLS.load_verify_locations(certifi.where())  # Keep archive TLS verification current on macOS Python.


@dataclass(frozen=True)
class Source:
    name: str
    north: float = 0
    west: float = 0
    pole: int = 0

    @property
    def url(self):
        return (LOLA_URL if self.pole else SLDEM_URL) + self.name

    def pixels(self, lat, lon):
        if not self.pole:
            return (self.north - lat) * 512 - .5, (lon % 360 - self.west) * 512 - .5
        rho = 2 * RADIUS * np.tan(np.radians(90 - self.pole * lat) / 2)
        x = rho * np.sin(np.radians(lon))
        y = -self.pole * rho * np.cos(np.radians(lon))
        return (931200 - y) / 60 - .5, (x + 931200) / 60 - .5


def source_at(lat, lon):
    if abs(lat) >= 60:
        pole = 1 if lat >= 0 else -1
        return Source(f'ldem_60{"n" if pole == 1 else "s"}_60m.jp2', pole=pole)
    south = math.floor(lat / 30) * 30
    west = int(math.floor((lon % 360) / 45)) * 45
    band = {0: '00N_30N', 30: '30N_60N', -30: '30S_00S', -60: '60S_30S'}[south]
    return Source(f'SLDEM2015_512_{band}_{west:03d}_{west + 45:03d}.JP2', north=south + 30, west=west)


def coordinates(lat, lon, step, n=N):
    """Gnomonic square, north-to-south rows. Remains regular across both poles."""
    p, q = np.radians([lat, lon])
    up = np.array([np.cos(p) * np.cos(q), np.cos(p) * np.sin(q), np.sin(p)])
    east = np.array([-np.sin(q), np.cos(q), 0.])
    north = np.cross(up, east)
    o = (np.arange(n) - n // 2) * step
    xyz = RADIUS * up + o[None, :, None] * east - o[:, None, None] * north
    return np.degrees(np.arctan2(xyz[..., 2], np.hypot(xyz[..., 0], xyz[..., 1]))), np.degrees(np.arctan2(xyz[..., 1], xyz[..., 0]))


def download(source, progress):
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    path = SOURCE_DIR / source.name
    if path.exists():
        return path
    # Only one detail worker writes sources; failed or interrupted files are never
    # published as complete. Whole-Moon coverage is 32 midlatitude files + 2 caps.
    # A background global build and an interactive visit may request the same
    # source. Give each writer a private partial file before atomic publication.
    with tempfile.NamedTemporaryFile(prefix=source.name + '.', suffix='.part', dir=SOURCE_DIR, delete=False) as tmp:
        partial = type(path)(tmp.name)
    try:
        with urllib.request.urlopen(urllib.request.Request(source.url, method='HEAD'), timeout=30, context=TLS) as response:
            total = int(response.headers.get('Content-Length', 0))
            ranged = response.headers.get('Accept-Ranges') == 'bytes' and total > 0
        if ranged:
            # Both official archives support ranges. A few bounded connections
            # avoid a minutes-long first visit on a single slow archive stream.
            size = 8 * 1024 * 1024
            spans = [(start, min(start + size, total) - 1) for start in range(0, total, size)]
            counts = [0] * len(spans)
            count_lock = threading.Lock()
            with partial.open('wb') as target:
                target.truncate(total)
            def part(i):
                start, end = spans[i]
                for attempt in range(3):
                    try:
                        request = urllib.request.Request(source.url, headers={'Range': f'bytes={start}-{end}'})
                        with urllib.request.urlopen(request, timeout=45, context=TLS) as response, partial.open('r+b') as target:
                            if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
                                raise OSError('Invalid elevation byte range')
                            target.seek(start)
                            count = 0
                            while chunk := response.read(min(1024 * 1024, end - start + 1 - count)):
                                target.write(chunk)
                                count += len(chunk)
                                with count_lock:
                                    counts[i] = count
                                    progress(f'Downloading measured elevation: {sum(counts) // 1_000_000} / {total // 1_000_000} MB. Cached after this visit.')
                            if count != end - start + 1:
                                raise OSError('Incomplete elevation byte range')
                        return
                    except OSError:
                        if attempt == 2:
                            raise
            with ThreadPoolExecutor(max_workers=6) as workers:
                list(workers.map(part, range(len(spans))))
        else:
            with urllib.request.urlopen(source.url, timeout=45, context=TLS) as response, partial.open('wb') as target:
                count = 0
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
                    count += len(chunk)
                    progress(f'Downloading measured elevation: {count // 1_000_000} MB. Cached after this visit.')
                if total and count != total:
                    raise OSError('Incomplete terrain download')
        with rasterio.open(partial) as ds:
            if ds.count != 1 or ds.dtypes != ('int16',):
                raise ValueError('Unexpected elevation format')
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return path


def sample(ds, source, lat, lon):
    rows, cols = source.pixels(lat, lon)
    # Pixel centres sit half a sample inside each source tile. Clamping in this
    # half-pixel margin prevents holes where independently delivered tiles meet.
    height, width = ds.shape
    rows, cols = np.clip(rows, 0, height - 1), np.clip(cols, 0, width - 1)
    r0, c0 = np.floor(rows).astype(int), np.floor(cols).astype(int)
    r1, c1 = np.minimum(r0 + 1, height - 1), np.minimum(c0 + 1, width - 1)
    top, left = int(r0.min()), int(c0.min())
    raw = (ds[top:int(r1.max()) + 1, left:int(c1.max()) + 1] if isinstance(ds, np.ndarray)
           else ds.read(1, window=Window(left, top, int(c1.max()) - left + 1, int(r1.max()) - top + 1)))
    z00, z01 = raw[r0 - top, c0 - left], raw[r0 - top, c1 - left]
    z10, z11 = raw[r1 - top, c0 - left], raw[r1 - top, c1 - left]
    if np.any((z00 == -32768) | (z01 == -32768) | (z10 == -32768) | (z11 == -32768)):
        raise ValueError('The requested elevation window contains missing measurements')
    fr, fc = rows - r0, cols - c0
    return .5 * ((1 - fr) * ((1 - fc) * z00 + fc * z01) + fr * ((1 - fc) * z10 + fc * z11))


def build_patch(lat, lon, progress=lambda _: None):
    from .moon import elevation, dem_path
    from .moon_native import path_for, prepared
    all_coords = [coordinates(lat, lon, step) for step in STEPS]
    lats = np.concatenate([p[0].ravel() for p in all_coords[:FINE_LEVELS]])
    lons = np.concatenate([p[1].ravel() for p in all_coords[:FINE_LEVELS]])
    # Group by delivery tile using integer band / longitude IDs, including seams.
    bands = np.clip(np.floor(lats / 30).astype(int), -3, 2)
    cols = np.floor((lons % 360) / 45).astype(int)
    ids = np.where(lats >= 60, 100, np.where(lats <= -60, 101, (bands + 2) * 8 + cols))
    values = np.empty(len(lats), dtype='<f4')
    sources = []
    with rasterio.Env(GDAL_NUM_THREADS='2', GDAL_CACHEMAX=128 * 1024 * 1024), ExitStack() as stack:
        for key in np.unique(ids):
            mask = ids == key
            i = int(np.flatnonzero(mask)[0])
            source = source_at(float(lats[i]), float(lons[i]))
            progress('Building the surface mesh from measured elevations…')
            if prepared(source):
                ds = np.load(path_for(source), mmap_mode='r')
            else:
                ds = stack.enter_context(rasterio.open(download(source, progress)))
            values[mask] = sample(ds, source, lats[mask], lons[mask])
            sources.append(source.name)
    grids = values.reshape(FINE_LEVELS, N, N)
    coarse = elevation()
    # Deliver this transition once to both clients and the matcher, rather than
    # blending against whatever overview tiles happen to be in a browser cache.
    o = np.abs((np.arange(N) - N // 2) * STEPS[FINE_LEVELS - 1])
    extent = np.maximum(o[:, None], o[None, :])
    fade = np.clip((extent - 57600) / 3840, 0, 1)
    fade = fade * fade * (3 - 2 * fade)
    a, b = all_coords[FINE_LEVELS - 1]
    distant = coarse_samples(coarse, a.ravel(), b.ravel()).reshape(N, N)
    grids[-1] = grids[-1] * (1 - fade) + distant * fade
    far = [coarse_samples(coarse, a.ravel(), b.ravel()).reshape(N, N) for a, b in all_coords[FINE_LEVELS:]]
    values = np.concatenate([grids, np.asarray(far)]).astype('<f4')
    meta = dict(lat=lat, lon=lon, n=N, steps=STEPS, radius=RADIUS,
                terrain_model=MODEL_ID, resolution_m=60, sources=sources, coarse_source=dem_path().name,
                min_m=float(values.min()), max_m=float(values.max()))
    return meta, values.tobytes()


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='moon-detail')
_guard = threading.Lock()
_jobs = OrderedDict()


def request_patch(lat, lon):
    """Pollable preparation keeps first-visit downloads visible and bounds RAM/work."""
    key = (round(lat, 7), round(((lon + 180) % 360) - 180, 7))
    with _guard:
        if key in _jobs:
            job = _jobs[key]
            _jobs.move_to_end(key)
            if job['state'] != 'error' or time.monotonic() - job['finished'] < 5:
                return dict(job)
            del _jobs[key]  # A later visit can retry a network failure.
        if sum(j['state'] == 'loading' for j in _jobs.values()) >= 2:
            return dict(state='busy', message='Another terrain region is loading. Retrying…')
        from .moon_native import ready
        message = 'Preparing measured terrain from the local elevation cache…' if ready() else 'Preparing measured terrain. Missing regional elevation files will be downloaded…'
        job = dict(state='loading', message=message)
        _jobs[key] = job
        while len(_jobs) > 8:
            old = next((k for k, v in _jobs.items() if v['state'] != 'loading'), None)
            if old is None:
                break
            del _jobs[old]
        _executor.submit(_prepare, key, job)
        return dict(job)


def _prepare(key, job):
    def progress(message):
        with _guard:
            job['message'] = message
    try:
        meta, data = build_patch(*key, progress)
        with _guard:
            job.update(state='ready', meta=meta, data=data)
    except Exception as exc:
        with _guard:
            job.update(state='error', message=f'Detailed elevation could not load: {exc}', finished=time.monotonic())
