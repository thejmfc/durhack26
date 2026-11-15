"""Whole-Moon skyline search using the shared measured surface model.

Coordinates are planetocentric latitude / east longitude in degrees. Unlike the
site ray marcher, rays follow great circles, wrap longitude and cross either pole.
The query is searched against the entire database; truth is never a search prior.
"""
from __future__ import annotations

import json
import math
import time
from functools import lru_cache

import numpy as np
from numba import njit, prange, get_num_threads, set_num_threads
from PIL import Image
from scipy.optimize import minimize

from .locate import Query
from .moon_match import score, exact_score
from .terrain import ROOT

RADIUS = 1_737_400.0
OBSERVER_HEIGHT = 1.7
DB_VERSION = 4
DB_DIR = ROOT / 'data/cache/moon'
# Near samples matter even with a coarse DEM: its interpolated slopes start at the observer.
RANGES = np.geomspace(100.0, 250_000.0, 128)


def dem_path():
    high = ROOT / 'data/raw/ldem_64_uint.tif'
    return high if high.exists() else ROOT / 'data/raw/ldem_16_uint.tif'


@lru_cache(maxsize=1)
def elevation():
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(dem_path()) as im:
        a = np.asarray(im, dtype=np.float32) * 0.5 - 10_000.0
    return a


@njit(cache=True)
def height_at(dem, lat, lon):
    h, w = dem.shape
    r = min(max((90.0 - lat) / 180.0 * h - 0.5, 0.0), h - 1.0)
    c = ((lon + 180.0) / 360.0 * w - 0.5) % w
    r0, c0 = min(int(r), h - 2), int(c)
    fr, fc = r - r0, c - c0
    z00, z01 = dem[r0, c0], dem[r0, (c0 + 1) % w]
    z10, z11 = dem[r0 + 1, c0], dem[r0 + 1, (c0 + 1) % w]
    # Same diagonal as the rendered DEM grid, rather than a different bilinear surface.
    if fr + fc <= 1:
        z = z00 + (z10 - z00) * fr + (z01 - z00) * fc
    else:
        z = z11 + (z01 - z11) * (1 - fr) + (z10 - z11) * (1 - fc)
    # Collapse each polar cap to one height, so all longitudes meet at a single point.
    polar = max(0.0, (abs(lat) - (90.0 - 90.0 / h)) / (90.0 / h))
    if polar > 0:
        pole = np.mean(dem[0 if lat >= 0 else h - 1])
        z = z * (1 - polar) + pole * polar
    return z


@njit(parallel=True, cache=True)
def compute_moon_horizons(dem, lats, lons, ranges, n_az=360):
    out = np.empty((len(lats), n_az), np.float32)
    sin_d, cos_d = np.sin(ranges / RADIUS), np.cos(ranges / RADIUS)
    for i in prange(len(lats)):
        lat, lon = math.radians(lats[i]), math.radians(lons[i])
        sl, cl, so, co = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
        # Local north/east/up, valid at the poles as well as the equator.
        ux, uy, uz = cl * co, cl * so, sl
        nx, ny, nz = -sl * co, -sl * so, cl
        ex, ey = -so, co
        observer_r = RADIUS + height_at(dem, lats[i], lons[i]) + OBSERVER_HEIGHT
        for k in range(n_az):
            a = k * 2 * math.pi / n_az
            ca, sa = math.cos(a), math.sin(a)
            dx, dy, dz = nx * ca + ex * sa, ny * ca + ey * sa, nz * ca
            best = -math.pi / 2
            for j in range(len(ranges)):
                qx, qy, qz = ux * cos_d[j] + dx * sin_d[j], uy * cos_d[j] + dy * sin_d[j], uz * cos_d[j] + dz * sin_d[j]
                qlat = math.degrees(math.asin(min(1.0, max(-1.0, qz))))
                qlon = math.degrees(math.atan2(qy, qx))
                qr = RADIUS + height_at(dem, qlat, qlon)
                el = math.atan2(qr * cos_d[j] - observer_r, qr * sin_d[j])
                best = max(best, el)
            out[i, k] = math.degrees(best)
    return out


def horizons(lats, lons, n_az=360):
    from .moon_surface import exact_horizons
    return exact_horizons(lats, lons, n_az)


def proposal_horizons(lats, lons, n_az=360):
    from .moon_surface import proposal_horizons as compute
    return compute(lats, lons, n_az)


def distance_m(lat, lon, lats, lons):
    p, q = np.radians(lat), np.radians(lats)
    a = np.sin((q - p) / 2) ** 2 + np.cos(p) * np.cos(q) * np.sin(np.radians(np.asarray(lons) - lon) / 2) ** 2
    return 2 * RADIUS * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def neighbourhood(lat, lon, step_m, half_steps):
    """Tangent-plane offsets projected to the sphere; no seam or pole special cases."""
    p, q = np.radians([lat, lon])
    up = np.array([np.cos(p) * np.cos(q), np.cos(p) * np.sin(q), np.sin(p)])
    east = np.array([-np.sin(q), np.cos(q), 0.0])
    north = np.cross(up, east)
    x, y = np.meshgrid(np.arange(-half_steps, half_steps + 1) * step_m, np.arange(-half_steps, half_steps + 1) * step_m)
    v = RADIUS * up + x.ravel()[:, None] * east + y.ravel()[:, None] * north
    v /= np.linalg.norm(v, axis=1)[:, None]
    return np.degrees(np.arcsin(v[:, 2])), np.degrees(np.arctan2(v[:, 1], v[:, 0]))


def database_ready():
    from .moon_surface import MODEL_ID, INDEX_METHOD
    from .moon_native import ready
    try:
        meta = json.loads((DB_DIR / 'meta.json').read_text())
        return (meta['version'] == DB_VERSION and meta['dem'] == dem_path().name
                and meta['terrain_model']['id'] == MODEL_ID and meta['index_method'] == INDEX_METHOD
                and ready()
                and (DB_DIR / meta['horizons']).exists() and (DB_DIR / meta['positions_file']).exists())
    except (OSError, ValueError, KeyError):
        return False


def database():
    # The manifest is atomically published, so a completed rebuild is visible
    # without restarting FastAPI or retaining an obsolete cached generation.
    if not database_ready():
        raise ValueError('Rebuild the global horizon database for the current surface')
    return _load_database(str(DB_DIR), (DB_DIR / 'meta.json').read_text())


@lru_cache(maxsize=1)
def _load_database(directory, manifest):
    from pathlib import Path
    meta = json.loads(manifest)
    directory = Path(directory)
    return meta, np.load(directory / meta['horizons'], mmap_mode='r'), np.load(directory / meta['positions_file'], mmap_mode='r')


def heatmap(scores, shape):
    """Pool any whole-Moon grid into at most 180 x 360 display cells."""
    values = np.exp(np.maximum(scores - scores.max(), -80)).reshape(shape)
    height, width = shape
    rows = np.arange(min(180, height), dtype=np.int64) * height // min(180, height)
    cols = np.arange(min(360, width), dtype=np.int64) * width // min(360, width)
    return np.maximum.reduceat(np.maximum.reduceat(values, rows, axis=0), cols, axis=1)


def distinct_indices(scores, lats, lons, n, separation):
    selected = []
    for i in np.argsort(scores)[::-1]:
        if not selected or np.all(distance_m(lats[i], lons[i], lats[selected], lons[selected]) >= separation):
            selected.append(int(i))
            if len(selected) == n:
                break
    return selected


def refine_regions(seed_lats, seed_lons, queries, method, heading_windows, cell_m):
    """Keep each global region alive until its fine surface has been sampled.

    A coarse position can fit poorly even a few hundred metres from the correct
    observer. Pruning whole regions at the kilometre stage loses that solution.
    Use a bounded local beam inside EVERY retrieved region before ranking them.
    """
    regions = [(np.array([lat]), np.array([lon])) for lat, lon in zip(seed_lats, seed_lons)]
    for divisor, keep, n_az in [(4, 6, 90), (16, 4, 90), (64, 2, 180), (256, 1, 360), (1024, 1, 360)]:
        step = cell_m / divisor
        batches = []
        offsets = [0]
        for lats, lons in regions:
            patches = [neighbourhood(lat, lon, step, 4) for lat, lon in zip(lats, lons)]
            a, b = np.concatenate([p[0] for p in patches]), np.concatenate([p[1] for p in patches])
            batches.append((a, b))
            offsets.append(offsets[-1] + len(a))
        lats, lons = np.concatenate([p[0] for p in batches]), np.concatenate([p[1] for p in batches])
        hw = [(w[0], min(180, w[1] + 180 / n_az)) if w is not None and n_az < 360 else w for w in heading_windows]
        ll, headings = score(proposal_horizons(lats, lons, n_az), queries, method, hw)
        selected = []
        for start, end in zip(offsets[:-1], offsets[1:]):
            local = distinct_indices(ll[start:end], lats[start:end], lons[start:end], keep, step)
            selected.extend(start + i for i in local)
        regions = [(lats[selected[i:i + keep]], lons[selected[i:i + keep]]) for i in range(0, len(selected), keep)]
    return lats[selected], lons[selected], ll[selected], headings[selected]


def refine_surface(lat, lon, queries, method, heading_windows):
    """Refine the final position against the real mesh, not proposal ray samples.

    A small exact grid first escapes minima introduced by the approximate
    proposal model. Continuous refinement then uses the same triangles as the
    displayed candidate horizon. No reference/test coordinates enter here.
    """
    p, q = np.radians([lat, lon])
    up = np.array([np.cos(p) * np.cos(q), np.cos(p) * np.sin(q), np.sin(p)])
    east = np.array([-np.sin(q), np.cos(q), 0.])
    north = np.cross(up, east)
    def point(x):
        v = RADIUS * up + x[0] * east + x[1] * north
        v /= np.linalg.norm(v)
        return float(np.degrees(np.arcsin(v[2]))), float(np.degrees(np.arctan2(v[1], v[0])))
    offsets = np.array([(x, y) for y in [-400, -200, 0, 200, 400] for x in [-400, -200, 0, 200, 400]], np.float64)
    positions = np.array([point(x) for x in offsets])
    values, _ = exact_score(horizons(positions[:, 0], positions[:, 1], 360), queries, method, heading_windows)
    best = int(np.argmax(values))
    cache = {tuple(x): float(-v) for x, v in zip(offsets, values)}
    def loss(x):
        key = tuple(float(v) for v in x)
        if key not in cache:
            a, b = point(x)
            cache[key] = -float(exact_score(horizons([a], [b], 360), queries, method, heading_windows)[0][0])
        return cache[key]
    x = offsets[best]
    minimize(loss, x, method='Nelder-Mead', bounds=[(-500, 500)] * 2,
             options=dict(maxfev=65, xatol=2, fatol=1e-5, initial_simplex=[x, x + [40, 0], x + [0, 40]]))
    # Include the starting position and every visited point, even if the solver
    # reaches its evaluation limit. Exact refinement can never worsen its fit.
    winner = min(cache, key=cache.get)
    return point(winner)


def horizon_rmse(horizon, queries, headings):
    """Angular mismatch of the actual displayed curve, at its reported heading."""
    azimuths = np.arange(len(horizon)) * 360 / len(horizon)
    squared, count = 0., 0
    for query, heading in zip(queries, headings):
        prediction = np.interp(np.arange(len(query.q)) * 360 / len(query.q) + heading,
                               azimuths, horizon, period=360)
        squared += float(np.sum((prediction[query.m] - query.q[query.m]) ** 2))
        count += int(query.m.sum())
    return math.sqrt(squared / count) if count else None


def locate(queries: list[Query], method='ssd', heading_windows=None):
    # Leave CPU capacity for the browser and the user's other applications.
    previous = get_num_threads()
    try:
        set_num_threads(min(previous, 4))
        return _locate(queries, method, heading_windows)
    finally:
        set_num_threads(previous)


def _locate(queries: list[Query], method='ssd', heading_windows=None):
    clock = time.perf_counter()
    timings = {}
    def stage(name):
        nonlocal clock
        now = time.perf_counter()
        timings[name] = round(now - clock, 3)
        clock = now
    meta, hz, pos = database()
    hw = heading_windows or [None] * len(queries)
    # Compact 4-degree profiles allow a much denser position grid at bounded memory.
    stride = 360 // meta['n_az']
    coarse_hw = [(w[0], min(180, w[1] + stride / 2)) if w is not None else None for w in hw]
    ll, headings = score(hz, queries, method, coarse_hw)
    stage('global_search')
    lats, lons = pos[:, 0], pos[:, 1]
    coarse = ll.copy()
    # First cover the cells surrounding each independent global peak, then refine.
    cell_m = math.radians(meta['spacing_deg']) * RADIUS
    picks = distinct_indices(ll, lats, lons, 96, cell_m)
    # Nearby slopes can change their elevation by many degrees between grid
    # positions and dominate squared error, hiding an otherwise useful skyline
    # shape. Retrieve additional regions with that amplitude capped, while
    # retaining EVERY original region. Full, uncapped horizons decide all local
    # refinement, final scores and displayed curves.
    clipped_queries = [Query(np.clip(q.q, -1., 1.), q.m.copy(), q.n_expected) for q in queries]
    if any(q.m.any() and np.std(q.q[q.m]) >= .03 for q in clipped_queries):
        alternate, _ = score(hz, clipped_queries, 'ssd', coarse_hw, clip=1.)
        extra = distinct_indices(alternate, lats, lons, 64, cell_m)
        for i in extra:
            if np.all(distance_m(lats[i], lons[i], lats[picks], lons[picks]) > 500.):
                picks.append(i)
    stage('alternate_retrieval')
    lats, lons, ll, headings = refine_regions(lats[picks], lons[picks], queries, method, hw, cell_m)
    stage('regional_refinement')
    picks = distinct_indices(ll, lats, lons, 24, 500.)
    # Re-rank using the actual rendered triangles, not the approximate global
    # proposal rays. Candidate scores, headings and displayed curves agree.
    lats, lons = lats[picks], lons[picks]
    final_hz = horizons(lats, lons, 1440)
    ll, headings = exact_score(final_hz, queries, method, hw)
    for i in distinct_indices(ll, lats, lons, 3, 1000.):
        lat, lon = refine_surface(float(lats[i]), float(lons[i]), queries, method, hw)
        refined = horizons([lat], [lon], 1440)[0]
        improved, _ = exact_score(refined[None], queries, method, hw)
        if improved[0] >= ll[i]:
            lats[i], lons[i], final_hz[i] = lat, lon, refined
    ll, headings = exact_score(final_hz, queries, method, hw)
    stage('exact_refinement')
    residuals = np.asarray([horizon_rmse(h, queries, angles) for h, angles in zip(final_hz, headings)])
    if method == 'ssd':
        # Rank the same interpolated curves we display, avoiding float32 FFT
        # cancellation and the discrepancy between integer and reported headings.
        ll = -.5 / 8 * sum(q.m.sum() for q in queries) * residuals ** 2
    picks = distinct_indices(ll, lats, lons, 5, 1000.0)
    candidates = [dict(lat=float(lats[i]), lon=float(lons[i]), headings=headings[i].tolist(),
                       loglik=float(ll[i]), rel_likelihood=float(np.exp(ll[i] - ll[picks[0]])),
                       rmse_deg=float(residuals[i]),
                       horizon=final_hz[i].round(3).tolist()) for i in picks]
    # Relative fit per cell, deliberately not labelled as calibrated probability.
    heat = heatmap(coarse, meta['shape'])
    # Measure the actual angular residual at the reported (sub-degree) heading.
    # A likelihood/NCC score is not itself an angular error or a confidence.
    rmses = residuals[picks].tolist()
    rmse = rmses[0]
    fit_gap = max(0., min(rmses[1:]) - rmse) if len(rmses) > 1 else 0.
    relief = float(np.mean([np.std(q.q[q.m]) for q in queries]))
    # These are rejection heuristics, not a calibrated confidence probability.
    weak = relief < 0.25 or rmse > 0.2 or fit_gap < 0.1 or sum(q.m.sum() for q in queries) < 100
    return dict(candidates=candidates, heatmap=heat, grid=dict(meta, shape=list(heat.shape)), method=method, timings=timings,
                quality=dict(weak=bool(weak), rmse_deg=rmse, relief_deg=relief, fit_gap_deg=fit_gap,
                             message='No reliable global fix: the skyline is ambiguous, incomplete, or poorly matched.' if weak else 'Unverified global candidate; a close skyline fit does not establish the location.'))
