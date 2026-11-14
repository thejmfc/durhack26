"""Horizon profiles by ray-marching the full-resolution DEM (Numba, parallel)."""
from __future__ import annotations

import math

import numpy as np
from numba import njit, prange

STEP0 = 4.0      # m, near-field step (DEM is 5 m)
GROWTH = 0.004   # step grows to 0.4 % of range (0.23 deg) - finer than the 1 deg azimuth bins


@njit(inline="always")
def _bilin(g, r, c):
    H, W = g.shape[0], g.shape[1]
    r0 = int(r)
    c0 = int(c)
    if r0 > H - 2:
        r0 = H - 2
    if c0 > W - 2:
        c0 = W - 2
    fr = r - r0
    fc = c - c0
    w00 = (1.0 - fr) * (1.0 - fc)
    w01 = (1.0 - fr) * fc
    w10 = fr * (1.0 - fc)
    w11 = fr * fc
    x = w00 * g[r0, c0, 0] + w01 * g[r0, c0 + 1, 0] + w10 * g[r0 + 1, c0, 0] + w11 * g[r0 + 1, c0 + 1, 0]
    y = w00 * g[r0, c0, 1] + w01 * g[r0, c0 + 1, 1] + w10 * g[r0 + 1, c0, 1] + w11 * g[r0 + 1, c0 + 1, 1]
    z = w00 * g[r0, c0, 2] + w01 * g[r0, c0 + 1, 2] + w10 * g[r0 + 1, c0, 2] + w11 * g[r0 + 1, c0 + 1, 2]
    return x, y, z


@njit(parallel=True, fastmath=True, cache=True)
def compute_horizons(g, rows, cols, obs_h, mc, axis, n_az, step0, growth):
    """Horizon elevation angle (deg) for each observer (rows[i], cols[i]) and azimuth.

    g: [H, W, 3] site-local Cartesian DEM points. Azimuth k*360/n_az is clockwise from
    local north at the observer. Rays are marched along straight lines in the raster; the
    raster direction for each azimuth comes from the local Jacobian of the grid (so no
    projection formula is assumed) and each sample's elevation angle is computed from true
    3D vectors against the observer's local vertical.
    """
    n = rows.shape[0]
    H, W = g.shape[0], g.shape[1]
    out = np.empty((n, n_az), np.float32)
    for i in prange(n):
        r0 = rows[i]
        c0 = cols[i]
        gx, gy, gz = _bilin(g, r0, c0)
        ux = gx - mc[0]
        uy = gy - mc[1]
        uz = gz - mc[2]
        inv = 1.0 / math.sqrt(ux * ux + uy * uy + uz * uz)
        ux *= inv
        uy *= inv
        uz *= inv
        px = gx + obs_h * ux
        py = gy + obs_h * uy
        pz = gz + obs_h * uz
        # east = axis x up, north = up x east
        ex = axis[1] * uz - axis[2] * uy
        ey = axis[2] * ux - axis[0] * uz
        ez = axis[0] * uy - axis[1] * ux
        inv = 1.0 / math.sqrt(ex * ex + ey * ey + ez * ez)
        ex *= inv
        ey *= inv
        ez *= inv
        nx = uy * ez - uz * ey
        ny = uz * ex - ux * ez
        nz = ux * ey - uy * ex
        # Jacobian of the grid (metres per pixel) at the observer
        ri = min(max(int(r0), 1), H - 2)
        ci = min(max(int(c0), 1), W - 2)
        tcx = 0.5 * (g[ri, ci + 1, 0] - g[ri, ci - 1, 0])
        tcy = 0.5 * (g[ri, ci + 1, 1] - g[ri, ci - 1, 1])
        tcz = 0.5 * (g[ri, ci + 1, 2] - g[ri, ci - 1, 2])
        trx = 0.5 * (g[ri + 1, ci, 0] - g[ri - 1, ci, 0])
        try_ = 0.5 * (g[ri + 1, ci, 1] - g[ri - 1, ci, 1])
        trz = 0.5 * (g[ri + 1, ci, 2] - g[ri - 1, ci, 2])
        a11 = tcx * ex + tcy * ey + tcz * ez
        a12 = trx * ex + try_ * ey + trz * ez
        a21 = tcx * nx + tcy * ny + tcz * nz
        a22 = trx * nx + try_ * ny + trz * nz
        det = a11 * a22 - a12 * a21
        for k in range(n_az):
            az = 2.0 * math.pi * k / n_az
            se = math.sin(az)
            sn = math.cos(az)
            dc = (se * a22 - a12 * sn) / det   # pixels per metre
            dr = (a11 * sn - a21 * se) / det
            best = -2.0
            t = 0.5 * step0
            while True:
                r = r0 + dr * t
                c = c0 + dc * t
                if r < 0.0 or c < 0.0 or r > H - 1 or c > W - 1:
                    break
                qx, qy, qz = _bilin(g, r, c)
                dx = qx - px
                dy = qy - py
                dz = qz - pz
                du = dx * ux + dy * uy + dz * uz
                s = du * abs(du) / (dx * dx + dy * dy + dz * dz)   # signed sin^2(elev), monotonic
                if s > best:
                    best = s
                st = growth * t
                t += st if st > step0 else step0
            if best < -1.0:
                out[i, k] = -30.0
            else:
                out[i, k] = math.degrees(math.asin(math.copysign(math.sqrt(abs(best)), best)))
    return out


def horizons(terrain, rows, cols, n_az=360, obs_h=None):
    rows = np.ascontiguousarray(rows, np.float64).ravel()
    cols = np.ascontiguousarray(cols, np.float64).ravel()
    if obs_h is None:
        obs_h = terrain.meta["observer_height"]
    return compute_horizons(terrain.xyz, rows, cols, float(obs_h), terrain.moon_centre, terrain.axis,
                            n_az, STEP0, GROWTH)
