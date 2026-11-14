"""Skyline extraction from a photo and conversion to an azimuth/elevation profile."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter

from .match import N_AZ

DEFAULT_THRESHOLD = 0.10


def load_gray(data: bytes, max_width: int = 1600) -> np.ndarray:
    im = Image.open(io.BytesIO(data)).convert("L")
    if im.width > max_width:
        im = im.resize((max_width, round(im.height * max_width / im.width)), Image.BILINEAR)
    return np.asarray(im, np.float32) / 255.0


def extract_skyline(gray: np.ndarray, thr: float = DEFAULT_THRESHOLD):
    """Per column, the first row from the top that starts 3 bright pixels (sky is black).

    Returns (rows, valid). A column is invalid if nothing is bright (all sky or all shadow)
    or if the bright run touches the top edge (terrain continues above the frame).
    """
    b = uniform_filter(gray, 3) > thr
    b3 = b[:-2] & b[1:-1] & b[2:]
    rows = b3.argmax(axis=0)
    valid = b3.any(axis=0) & (rows > 0)
    return rows.astype(np.float32), valid


def skyline_angles(rows, valid, w, h, hfov_deg, pitch_deg=0.0, yaw_offset_deg=0.0):
    """Pixel skyline -> (azimuth offset, elevation) in degrees. Pinhole camera, roll = 0."""
    f = (w / 2) / np.tan(np.radians(hfov_deg) / 2)
    cols = np.nonzero(valid)[0]
    x = (cols + 0.5 - w / 2) / f
    y = (h / 2 - rows[cols]) / f
    p = np.radians(pitch_deg)
    up = y * np.cos(p) + np.sin(p)
    fwd = np.cos(p) - y * np.sin(p)
    az = np.degrees(np.arctan2(x, fwd)) + yaw_offset_deg
    el = np.degrees(np.arctan2(up, np.hypot(x, fwd)))
    return az, el


def profile_from_points(az, el, px_per_deg: float, n_az: int = N_AZ):
    """Bin (az, el) samples onto the 1-degree grid used by the horizon DB (mean per bin)."""
    k = np.round(np.asarray(az) * (n_az / 360.0)).astype(np.int64) % n_az
    cnt = np.bincount(k, minlength=n_az).astype(np.float64)
    s = np.bincount(k, weights=el, minlength=n_az)
    m = cnt >= max(1.0, 0.5 * px_per_deg * 360.0 / n_az)
    q = np.where(m, s / np.maximum(cnt, 1), 0.0)
    return q.astype(np.float32), m


def project_profile(profile, heading_deg, w, h, hfov_deg, pitch_deg=0.0):
    """Inverse of skyline_angles for drawing a DB horizon over an image: returns [[x_px, y_px], ...]."""
    A = len(profile)
    f = (w / 2) / np.tan(np.radians(hfov_deg) / 2)
    half = int(np.ceil(min(hfov_deg / 2 + 2, 89)))
    offs = np.arange(-half, half + 1)
    hz = np.asarray(profile)[np.round((heading_deg + offs) * A / 360.0).astype(int) % A]
    a, e, p = np.radians(offs - (heading_deg - np.round(heading_deg))), np.radians(hz), np.radians(pitch_deg)
    right, fwd0, up0 = np.cos(e) * np.sin(a), np.cos(e) * np.cos(a), np.sin(e)
    fwd = fwd0 * np.cos(p) + up0 * np.sin(p)
    up = up0 * np.cos(p) - fwd0 * np.sin(p)
    ok = fwd > 1e-3
    return np.stack([w / 2 + f * right[ok] / fwd[ok], h / 2 - f * up[ok] / fwd[ok]], 1)
