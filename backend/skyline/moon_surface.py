"""Shared surface specification and horizons of the actual rendered triangles.

The global proposal index uses the same native elevation sources. The final
candidate and reference curves intersect the delivered clipmap, including the
near ground, triangle diagonals, curvature and transitions to distant terrain.
"""
from __future__ import annotations

import math
import numpy as np
from numba import njit, prange

RADIUS = 1_737_400.0
OBSERVER_HEIGHT = 1.7
N = 257
STEPS = (60, 120, 240, 480, 960, 1920, 3840)
FINE_LEVELS = 4
MODEL_ID = 'sldem2015-lola60-clipmap-v2'
INDEX_METHOD = 'native-elevation-proposals-v1'
PROPOSAL_RANGES = np.unique(np.r_[np.geomspace(2, 600, 32), np.arange(660, 7681, 120),
                                np.arange(7920, 30721, 480), np.geomspace(31200, 400000, 80)])


def specification():
    return dict(id=MODEL_ID, n=N, steps=list(STEPS), fine_levels=FINE_LEVELS,
                radius=RADIUS, observer_height=OBSERVER_HEIGHT, native_resolution_m=60)


@njit(cache=True, inline='always')
def native_height(maps, lat, lon):
    if abs(lat) >= 60:
        pole = 1 if lat >= 0 else -1
        source = 32 if pole == 1 else 33
        rho = 2 * RADIUS * math.tan(math.radians(90 - pole * lat) / 2)
        r = (931200 + pole * rho * math.cos(math.radians(lon))) / 60 - .5
        c = (rho * math.sin(math.radians(lon)) + 931200) / 60 - .5
    else:
        band = int(math.floor(lat / 30))
        west = int(math.floor((lon % 360) / 45))
        source = (band + 2) * 8 + west
        r = ((band + 1) * 30 - lat) * 512 - .5
        c = ((lon % 360) - west * 45) * 512 - .5
    raw = maps[source]
    if raw.shape[0] < 2:
        return np.nan
    r, c = min(max(r, 0.), raw.shape[0] - 1.), min(max(c, 0.), raw.shape[1] - 1.)
    r0, c0 = int(r), int(c)
    r1, c1 = min(r0 + 1, raw.shape[0] - 1), min(c0 + 1, raw.shape[1] - 1)
    fr, fc = r - r0, c - c0
    a, b, d, e = raw[r0, c0], raw[r0, c1], raw[r1, c0], raw[r1, c1]
    if a == -32768 or b == -32768 or d == -32768 or e == -32768:
        return np.nan
    return .5 * ((1 - fr) * ((1 - fc) * a + fc * b) + fr * ((1 - fc) * d + fc * e))


@njit(cache=True, nogil=True)
def coarse_samples(dem, lat, lon):
    out = np.empty(lat.size, np.float32)
    for i in range(lat.size):
        out[i] = coarse_height(dem, lat[i], lon[i])
    return out


# Imported after constants so moon.py can lazily call this module without a cycle.
from .moon import height_at as coarse_height


@njit(cache=True)
def _up(heights, level, r, c, step):
    mid = (heights.shape[1] - 1) // 2
    x, y = (c - mid) * step, (mid - r) * step
    return (RADIUS + heights[level, r, c]) / math.sqrt(1 + (x * x + y * y) / RADIUS ** 2)


@njit(cache=True)
def vertex_up(heights, level, r, c, step):
    n = heights.shape[1]
    if level < heights.shape[0] - 1:
        if (r == 0 or r == n - 1) and c % 2 == 1:
            return (_up(heights, level, r, c - 1, step) + _up(heights, level, r, c + 1, step)) / 2
        if (c == 0 or c == n - 1) and r % 2 == 1:
            return (_up(heights, level, r - 1, c, step) + _up(heights, level, r + 1, c, step)) / 2
    return _up(heights, level, r, c, step)


@njit(cache=True)
def triangle_up(heights, level, x, y, step):
    n = heights.shape[1]
    r = min(max((n - 1) / 2 - y / step, 0), n - 1.)
    c = min(max((n - 1) / 2 + x / step, 0), n - 1.)
    r0, c0 = min(int(r), n - 2), min(int(c), n - 2)
    fr, fc = r - r0, c - c0
    # Perspective-correct interpolation of the plane through three spherical
    # vertices. A linear interpolation of their radial heights is a different
    # surface, particularly close to the camera.
    if fr + fc <= 1:
        reciprocal = ((1 - fr - fc) / vertex_up(heights, level, r0, c0, step)
                      + fr / vertex_up(heights, level, r0 + 1, c0, step)
                      + fc / vertex_up(heights, level, r0, c0 + 1, step))
    else:
        reciprocal = ((fr + fc - 1) / vertex_up(heights, level, r0 + 1, c0 + 1, step)
                      + (1 - fr) / vertex_up(heights, level, r0, c0 + 1, step)
                      + (1 - fc) / vertex_up(heights, level, r0 + 1, c0, step))
    return 1 / reciprocal


@njit(cache=True, nogil=True)
def patch_horizons(heights, steps, n_az=360):
    out = np.empty(n_az, np.float32)
    mid = (heights.shape[1] - 1) / 2
    eye = RADIUS + heights[0, int(mid), int(mid)] + OBSERVER_HEIGHT
    # Project each vertex once, rather than repeating square roots for every
    # ray/triangle intersection. Planar interpolation is linear in reciprocal up.
    inverse = np.empty(heights.shape, np.float64)
    for level in range(len(steps)):
        for r in range(heights.shape[1]):
            for c in range(heights.shape[2]):
                inverse[level, r, c] = 1 / vertex_up(heights, level, r, c, steps[level])
    for k in range(n_az):
        az = k * 2 * math.pi / n_az
        east, north = math.sin(az), math.cos(az)
        dominant = max(abs(east), abs(north))
        start, best = .001, -math.inf
        for level in range(len(steps)):
            step = steps[level]
            end = mid * step / dominant
            # A horizon maximum of a planar triangle lies on an edge of the
            # vertical ray plane. Visit x, y AND diagonal grid intersections.
            dx = step / abs(east) if abs(east) > 1e-12 else math.inf
            dy = step / abs(north) if abs(north) > 1e-12 else math.inf
            dd = step / abs(east - north) if abs(east - north) > 1e-12 else math.inf
            tx = (math.floor(start / dx + 1e-9) + 1) * dx if math.isfinite(dx) else math.inf
            ty = (math.floor(start / dy + 1e-9) + 1) * dy if math.isfinite(dy) else math.inf
            td = (math.floor(start / dd + 1e-9) + 1) * dd if math.isfinite(dd) else math.inf
            t = start
            while True:
                r = min(max(mid - north * t / step, 0), 2 * mid)
                c = min(max(mid + east * t / step, 0), 2 * mid)
                r0, c0 = min(int(r), int(2 * mid) - 1), min(int(c), int(2 * mid) - 1)
                fr, fc = r - r0, c - c0
                if fr + fc <= 1:
                    reciprocal = ((1 - fr - fc) * inverse[level, r0, c0]
                                  + fr * inverse[level, r0 + 1, c0]
                                  + fc * inverse[level, r0, c0 + 1])
                else:
                    reciprocal = ((fr + fc - 1) * inverse[level, r0 + 1, c0 + 1]
                                  + (1 - fr) * inverse[level, r0, c0 + 1]
                                  + (1 - fc) * inverse[level, r0 + 1, c0])
                best = max(best, RADIUS * (1 - eye * reciprocal) / t)
                if t >= end - 1e-7:
                    break
                t = min(tx, ty, td, end)
                if tx <= t + 1e-7:
                    tx += dx
                if ty <= t + 1e-7:
                    ty += dy
                if td <= t + 1e-7:
                    td += dd
            start = end
        out[k] = math.degrees(math.atan(best))
    return out


@njit(cache=True, inline='always')
def _native_grid_up(maps, sl, cl, so, co, x, y):
    vx = RADIUS * cl * co - x * so - y * sl * co
    vy = RADIUS * cl * so + x * co - y * sl * so
    vz = RADIUS * sl + y * cl
    lat, lon = math.degrees(math.atan2(vz, math.hypot(vx, vy))), math.degrees(math.atan2(vy, vx))
    h = np.float32(native_height(maps, lat, lon))
    return (RADIUS + h) / math.sqrt(1 + (x * x + y * y) / RADIUS ** 2)


@njit(cache=True, inline='always')
def _near_up(maps, sl, cl, so, co, x, y):
    # The first few hundred metres must use the actual 60 m triangles; native
    # interpolation at one metre can otherwise invent a slope the mesh lacks.
    r, c = 128 - y / 60, 128 + x / 60
    r0, c0 = math.floor(r), math.floor(c)
    fr, fc = r - r0, c - c0
    x0, y0 = (c0 - 128) * 60, (128 - r0) * 60
    if fr + fc <= 1:
        return 1 / ((1 - fr - fc) / _native_grid_up(maps, sl, cl, so, co, x0, y0)
                    + fr / _native_grid_up(maps, sl, cl, so, co, x0, y0 - 60)
                    + fc / _native_grid_up(maps, sl, cl, so, co, x0 + 60, y0))
    return 1 / ((fr + fc - 1) / _native_grid_up(maps, sl, cl, so, co, x0 + 60, y0 - 60)
                + (1 - fr) / _native_grid_up(maps, sl, cl, so, co, x0 + 60, y0)
                + (1 - fc) / _native_grid_up(maps, sl, cl, so, co, x0, y0 - 60))


@njit(cache=True, inline='always')
def _cached_near_up(grid, x, y):
    r, c = 128 - y / 60, 128 + x / 60
    r0, c0 = math.floor(r), math.floor(c)
    fr, fc = r - r0, c - c0
    r0, c0 = r0 - 117, c0 - 117
    if fr + fc <= 1:
        return 1 / ((1 - fr - fc) / grid[r0, c0]
                    + fr / grid[r0 + 1, c0] + fc / grid[r0, c0 + 1])
    return 1 / ((fr + fc - 1) / grid[r0 + 1, c0 + 1]
                + (1 - fr) / grid[r0, c0 + 1] + (1 - fc) / grid[r0 + 1, c0])


@njit(parallel=True, cache=True, nogil=True)
def compute_proposals(maps, coarse, lats, lons, ranges, n_az=90):
    out = np.empty((len(lats), n_az), np.float32)
    # Ray geometry is shared by every observer. Keep the original operations
    # and precision, but do the square roots once per range, not per sample.
    radial = np.sqrt(1 + ranges * ranges / RADIUS ** 2)
    for i in prange(len(lats)):
        p, q = math.radians(lats[i]), math.radians(lons[i])
        sl, cl, so, co = math.sin(p), math.cos(p), math.sin(q), math.cos(q)
        eye = RADIUS + np.float32(native_height(maps, lats[i], lons[i])) + OBSERVER_HEIGHT
        # Thousands of near-ray samples share these 529 vertices. Project and
        # read each once per observer, preserving the original triangle planes.
        near = np.empty((23, 23), np.float64)
        for r in range(23):
            for c in range(23):
                near[r, c] = _native_grid_up(maps, sl, cl, so, co, (c - 11) * 60., (11 - r) * 60.)
        for k in range(n_az):
            az = k * 2 * math.pi / n_az
            east, north = math.sin(az), math.cos(az)
            best = -math.inf
            for j in range(len(ranges)):
                t = ranges[j]
                x, y = east * t, north * t
                if t <= 600:
                    up = _cached_near_up(near, x, y)
                else:
                    vx = RADIUS * cl * co - x * so - y * sl * co
                    vy = RADIUS * cl * so + x * co - y * sl * so
                    vz = RADIUS * sl + y * cl
                    lat, lon = math.degrees(math.atan2(vz, math.hypot(vx, vy))), math.degrees(math.atan2(vy, vx))
                    extent = max(abs(x), abs(y))
                    if extent < 61440:
                        h = native_height(maps, lat, lon)
                        if extent > 57600:
                            fade = (extent - 57600) / 3840
                            fade = fade * fade * (3 - 2 * fade)
                            h = h * (1 - fade) + coarse_height(coarse, lat, lon) * fade
                    else:
                        h = coarse_height(coarse, lat, lon)
                    up = (RADIUS + h) / radial[j]
                # All samples have positive horizontal distance. Maximising
                # slope and taking atan once gives the same angular maximum.
                best = max(best, (up - eye) / (up * t / RADIUS))
            out[i, k] = math.degrees(math.atan(best))
    return out


def proposal_horizons(lats, lons, n_az=360):
    from .moon_native import all_maps
    from .moon import elevation
    return compute_proposals(all_maps(), elevation(), np.asarray(lats, np.float64), np.asarray(lons, np.float64), PROPOSAL_RANGES, n_az)


@njit(cache=True, nogil=True)
def mesh_heights(maps, coarse, lat, lon):
    """The delivered clipmap, sampled directly from the resident elevation maps.

    Keep the same float32 rounding, ring transition and source interpolation as
    moon_detail.build_patch; the renderer's delivery path is checked against this.
    """
    p, q = math.radians(lat), math.radians(lon)
    sl, cl, so, co = math.sin(p), math.cos(p), math.sin(q), math.cos(q)
    heights = np.empty((len(STEPS), N, N), np.float32)
    for level in range(len(STEPS)):
        step = STEPS[level]
        for r in range(N):
            y = (N // 2 - r) * step
            for c in range(N):
                x = (c - N // 2) * step
                vx = RADIUS * cl * co - x * so - y * sl * co
                vy = RADIUS * cl * so + x * co - y * sl * so
                vz = RADIUS * sl + y * cl
                a, b = math.degrees(math.atan2(vz, math.hypot(vx, vy))), math.degrees(math.atan2(vy, vx))
                if level < FINE_LEVELS:
                    height = np.float32(native_height(maps, a, b))
                    extent = max(abs(x), abs(y))
                    if level == FINE_LEVELS - 1 and extent > 57600:
                        fade = min(1., (extent - 57600) / 3840)
                        fade = fade * fade * (3 - 2 * fade)
                        distant = np.float32(coarse_height(coarse, a, b))
                        height = np.float32(height * (1 - fade) + distant * fade)
                else:
                    height = np.float32(coarse_height(coarse, a, b))
                heights[level, r, c] = height
    return heights


@njit(parallel=True, cache=True, nogil=True)
def compute_exact(maps, coarse, lats, lons, n_az):
    out = np.empty((len(lats), n_az), np.float32)
    steps = np.asarray(STEPS, np.float64)
    for i in prange(len(lats)):
        out[i] = patch_horizons(mesh_heights(maps, coarse, lats[i], lons[i]), steps, n_az)
    return out


def exact_horizons(lats, lons, n_az=360):
    from .moon_native import ready, all_maps
    from .moon import elevation
    from .moon_detail import build_patch
    lats, lons = np.asarray(lats, np.float64), np.asarray(lons, np.float64)
    if ready():
        # At an exact delivery meridian/band, a one-ULP difference between
        # NumPy and scalar atan2 can select opposite sides of a source seam.
        # Preserve the browser's existing sampling there rather than changing
        # the rendered surface as a side effect of a speed optimization.
        boundary = source_boundary(lats, lons)
        out = np.empty((len(lats), n_az), np.float32)
        if len(lats) == 1 and not boundary[0]:
            # Single reference/optimizer evaluations need no parallel runtime.
            # This also lets photo analysis proceed during a parallel search.
            heights = mesh_heights(all_maps(), elevation(), lats[0], lons[0])
            out[0] = patch_horizons(heights, np.asarray(STEPS, np.float64), n_az)
        elif np.any(~boundary):
            out[~boundary] = compute_exact(all_maps(), elevation(), lats[~boundary], lons[~boundary], n_az)
        for i in np.flatnonzero(boundary):
            meta, binary = build_patch(float(lats[i]), float(lons[i]))
            heights = np.frombuffer(binary, '<f4').reshape(len(meta['steps']), meta['n'], meta['n'])
            out[i] = patch_horizons(heights, np.asarray(meta['steps'], np.float64), n_az)
        return out
    results = []
    for lat, lon in zip(lats, lons):
        meta, binary = build_patch(float(lat), float(lon))
        heights = np.frombuffer(binary, '<f4').reshape(len(meta['steps']), meta['n'], meta['n'])
        results.append(patch_horizons(heights, np.asarray(meta['steps'], np.float64), n_az))
    return np.asarray(results, np.float32)


def source_boundary(lats, lons):
    """Use the same source-seam fallback for delivered and verified meshes."""
    lats, lons = np.asarray(lats), np.asarray(lons)
    return ((np.abs(lats / 30 - np.round(lats / 30)) < 1e-10)
            | (np.abs(lons / 45 - np.round(lons / 45)) < 1e-10))
