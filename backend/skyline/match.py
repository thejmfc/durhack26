"""Horizon database + masked, heading-free skyline matching (FFT cross-correlation)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import fft as sfft

from .terrain import CACHE, DEFAULT_SITE, Terrain

MARGIN_M = 300.0  # observers are kept this far inside the tile edge
N_AZ = 360
# Likelihood shape parameters (how many skyline samples count as one independent
# measurement / the effective skyline noise). Heuristic defaults; EVALUATION.md reports
# how well the resulting probabilities are calibrated.
DEFAULT_CALIB = {"rho": 8.0, "sigma": 1.0}
METHODS = ("ncc", "zssd", "ssd")


def db_path(spacing: float, site: str = DEFAULT_SITE) -> Path:
    return CACHE / f"horizon_db_{site}_{int(round(spacing))}m.npz"


def grid_positions(terrain: Terrain, spacing: float):
    px = terrain.meta["pixel_size"]
    H, W = terrain.shape
    m = MARGIN_M / px
    r = np.arange(m, H - 1 - m + 1e-6, spacing / px)
    c = np.arange(m, W - 1 - m + 1e-6, spacing / px)
    R, C = np.meshgrid(r, c, indexing="ij")
    return R.ravel(), C.ravel(), (len(r), len(c))


class HorizonDB:
    def __init__(self, spacing: float = 100.0, site: str = DEFAULT_SITE):
        d = np.load(db_path(spacing, site))
        self.site = site
        self.H = d["H"]
        self.rows, self.cols = d["rows"], d["cols"]
        self.shape = tuple(int(v) for v in d["shape"])
        self.spacing = float(d["spacing"])
        self.F = Spectra(self.H)


class Spectra:
    """FFTs of horizon profiles and their squares along azimuth (reused across queries)."""

    def __init__(self, H: np.ndarray):
        H = np.asarray(H, np.float32)
        self.n, self.A = H.shape
        self.FH = sfft.rfft(H, axis=1, workers=-1)
        self.FH2 = sfft.rfft(H * H, axis=1, workers=-1)

    def corr(self, a: np.ndarray, squared=False) -> np.ndarray:
        """out[n, s] = sum_k a[k] * H[n, (k + s) % A]"""
        Fa = np.conj(sfft.rfft(a.astype(np.float32)))[None, :]
        return sfft.irfft(Fa * (self.FH2 if squared else self.FH), n=self.A, axis=1, workers=-1)


def score_profiles(F: Spectra, q: np.ndarray, m: np.ndarray, method: str = "ncc", calib=None,
                   heading_window=None):
    """Score a masked query skyline against every profile at every heading.

    q[k]: skyline elevation (deg) at azimuth offset k deg from the camera heading (wrapped),
    m[k]: 1 where q is valid. Returns per-profile (loglik, heading_deg, raw_score, offset_deg)
    maximised over heading. raw_score is NCC for 'ncc' and -RMSE (deg) otherwise.
    """
    calib = {**DEFAULT_CALIB, **(calib or {})}
    m = m.astype(np.float32)
    M = float(m.sum())
    A = F.A
    qbar = float((q * m).sum() / M)
    q0 = (q - qbar) * m
    vq = float((q0 * q0).sum() / M)
    cov = F.corr(q0) / M
    mH = F.corr(m) / M
    vH = np.maximum(F.corr(m, squared=True) / M - mH * mH, 0.0)
    if method == "ncc":
        e = 0.01  # deg^2 noise floor so flat horizons do not blow up the normalisation
        raw = cov / np.sqrt((vq + e) * (vH + e))
        ll = -0.5 * M / calib["rho"] * np.log(1.0 - np.clip(raw, 0.0, 1.0) ** 2 + 1e-3)
    else:
        mse = np.maximum(vq + vH - 2.0 * cov, 0.0)
        if method == "ssd":
            mse = mse + (qbar - mH) ** 2
        raw = -np.sqrt(mse)
        ll = -0.5 * M / calib["rho"] * mse / calib["sigma"] ** 2
    if heading_window is not None:
        h0, half = heading_window
        d = (np.arange(A) * (360.0 / A) - h0 + 180.0) % 360.0 - 180.0
        ll = np.where(np.abs(d)[None, :] <= half, ll, -np.inf)
    s = np.argmax(ll, axis=1)
    idx = np.arange(F.n)
    best = ll[idx, s]
    lo, hi = ll[idx, (s - 1) % A], ll[idx, (s + 1) % A]
    den = lo - 2 * best + hi
    with np.errstate(invalid="ignore", divide="ignore"):
        sub = np.where(np.isfinite(den) & (den < -1e-9), 0.5 * (lo - hi) / den, 0.0)
    heading = ((s + np.clip(sub, -0.5, 0.5)) * (360.0 / A)) % 360.0
    return best, heading, raw[idx, s], (qbar - mH[idx, s])
