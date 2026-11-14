"""End-to-end localisation: photos -> skylines -> likelihood map -> refined candidates."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from .extract import (DEFAULT_THRESHOLD, extract_skyline, load_gray, profile_from_points,
                      project_profile, skyline_angles)
from .horizon import horizons
from .match import MARGIN_M, N_AZ, HorizonDB, Spectra, score_profiles
from .terrain import Terrain

FINE_STEP_M = 10.0
NMS_M = 300.0       # candidates closer than this on the coarse grid count as the same peak
DISTINCT_M = 150.0  # refined candidates closer than this are merged


@dataclass
class Query:
    """One skyline measurement with a single unknown heading (a photo, or a stitched set)."""
    q: np.ndarray
    m: np.ndarray
    n_expected: int                      # azimuth bins the field of view should cover
    images: list = field(default_factory=list)  # per image: dict(w, h, hfov, pitch, yaw, rows, valid)

    @property
    def valid_frac(self) -> float:
        return float(self.m.sum()) / max(self.n_expected, 1)


def build_query(images: list[np.ndarray], hfov: float, pitch: float = 0.0, yaws=None,
                thr: float = DEFAULT_THRESHOLD) -> Query:
    """images: grayscale arrays taken from one spot with known relative yaw (deg); one image is the usual case."""
    yaws = yaws or [0.0] * len(images)
    az_all, el_all, infos = [], [], []
    for g, yaw in zip(images, yaws):
        h, w = g.shape
        rows, valid = extract_skyline(g, thr)
        az, el = skyline_angles(rows, valid, w, h, hfov, pitch, yaw)
        az_all.append(az)
        el_all.append(el)
        infos.append(dict(w=w, h=h, hfov=hfov, pitch=pitch, yaw=yaw, rows=rows, valid=valid))
    px_per_deg = images[0].shape[1] / hfov
    q, m = profile_from_points(np.concatenate(az_all), np.concatenate(el_all), px_per_deg)
    cover = np.zeros(N_AZ, bool)
    for yaw in yaws:
        cover[np.round(yaw + np.arange(-np.floor(hfov / 2), np.floor(hfov / 2) + 1)).astype(int) % N_AZ] = True
    return Query(q, m, int(cover.sum()), infos)


_fine_cache: OrderedDict = OrderedDict()


def _fine_patch(terrain: Terrain, db: HorizonDB, i: int, half_m: float):
    key = (db.site, db.spacing, i, half_m)
    if key in _fine_cache:
        _fine_cache.move_to_end(key)
        return _fine_cache[key]
    px = terrain.meta["pixel_size"]
    H, W = terrain.shape
    o = np.arange(-half_m, half_m + 1e-6, FINE_STEP_M) / px
    R, C = np.meshgrid(db.rows[i] + o, db.cols[i] + o, indexing="ij")
    lim = MARGIN_M / px / 2
    ok = (R >= lim) & (R <= H - 1 - lim) & (C >= lim) & (C <= W - 1 - lim)
    R, C = R[ok], C[ok]
    val = (R, C, Spectra(horizons(terrain, R, C)))
    _fine_cache[key] = val
    if len(_fine_cache) > 400:
        _fine_cache.popitem(last=False)
    return val


def locate(terrain: Terrain, db: HorizonDB, queries: list[Query], method: str = "ncc", calib=None,
           top_k: int = 5, n_refine: int = 12, heading_windows=None, refine: bool = True) -> dict:
    """Score every grid position, refine the best distinct peaks at 10 m, return candidates + heatmap.

    Several queries are fused as independent measurements from the same spot (log-likelihoods add,
    each with its own free heading).
    """
    hw = heading_windows or [None] * len(queries)
    per = [score_profiles(db.F, Q.q, Q.m, method, calib, w) for Q, w in zip(queries, hw)]
    total = np.sum([p[0] for p in per], axis=0)

    ny, nx = db.shape
    work = total.reshape(ny, nx).copy()
    rad = int(round(NMS_M / db.spacing))
    picks = []
    for _ in range(n_refine):
        i = int(np.argmax(work))
        if not np.isfinite(work.flat[i]):
            break
        picks.append(i)
        r, c = divmod(i, nx)
        work[max(r - rad, 0):r + rad + 1, max(c - rad, 0):c + rad + 1] = -np.inf

    cands = []
    for i in picks:
        if refine:
            R, C, F = _fine_patch(terrain, db, i, db.spacing * 0.8)
            fper = [score_profiles(F, Q.q, Q.m, method, calib, w) for Q, w in zip(queries, hw)]
            ft = np.sum([p[0] for p in fper], axis=0)
            j = int(np.argmax(ft))
            row, col, ll = float(R[j]), float(C[j]), float(ft[j])
            src = fper
        else:
            j, row, col, ll, src = i, float(db.rows[i]), float(db.cols[i]), float(total[i]), per
        cands.append(dict(row=row, col=col, loglik=ll, coarse_index=i,
                          headings=[float(p[1][j]) for p in src], raw=[float(p[2][j]) for p in src],
                          pitch_offsets=[float(p[3][j]) for p in src]))
    cands.sort(key=lambda c: -c["loglik"])
    px = terrain.meta["pixel_size"]
    distinct = []
    for c in cands:
        if all(np.hypot(c["row"] - d["row"], c["col"] - d["col"]) * px >= DISTINCT_M for d in distinct):
            distinct.append(c)
    top = distinct[:top_k]
    for c in top:
        x, y = terrain.rc_to_xy(c["row"], c["col"])
        c["x"], c["y"] = float(x), float(y)
        c["rel_likelihood"] = float(np.exp(c["loglik"] - top[0]["loglik"]))
    prob = np.exp(total - total.max())
    prob /= prob.sum()
    gap = float(top[0]["loglik"] - top[1]["loglik"]) if len(top) > 1 else float("inf")
    return dict(candidates=top, prob=prob.reshape(ny, nx), loglik=total.reshape(ny, nx),
                confidence=dict(loglik_gap=gap, raw_gap=float(np.mean(top[0]["raw"]) - np.mean(top[1]["raw"])) if len(top) > 1 else None,
                                peak_prob=float(prob.max())),
                method=method)


def credible_rank(result: dict, db: HorizonDB, row: float, col: float) -> float:
    """Probability mass of all grid cells at least as likely as the cell containing (row, col)."""
    ny, nx = db.shape
    step = db.rows[nx] - db.rows[0]
    r = int(np.clip(round((row - db.rows[0]) / step), 0, ny - 1))
    c = int(np.clip(round((col - db.cols[0]) / step), 0, nx - 1))
    p = result["prob"]
    return float(p[p >= p[r, c]].sum())


def analyse_truth(terrain: Terrain, queries: list[Query], row: float, col: float, headings: list[float],
                  method: str = "ncc", calib=None) -> dict:
    """What the matcher sees at the true pose: used for error reporting and failure classification."""
    Ht = horizons(terrain, [row], [col])
    F = Spectra(Ht)
    out = dict(horizon=Ht[0], loglik=0.0, per_query=[])
    for Q, hd in zip(queries, headings):
        ll, h_est, raw, off = score_profiles(F, Q.q, Q.m, method, calib)
        ll_t = score_profiles(F, Q.q, Q.m, method, calib, (hd, 1.0))[0]
        k = np.nonzero(Q.m)[0]
        d = Q.q[k] - Ht[0][(k + int(round(hd))) % N_AZ] if k.size else np.zeros(0)
        cover = (int(round(hd)) + np.arange(-Q.n_expected // 2, Q.n_expected // 2 + 1)) % N_AZ
        out["loglik"] += float(ll_t[0])
        out["per_query"].append(dict(
            loglik_true_heading=float(ll_t[0]), loglik_free_heading=float(ll[0]), heading_free=float(h_est[0]),
            raw=float(raw[0]), valid_frac=Q.valid_frac,
            frac_below=float((d < -0.5).mean()) if d.size else 0.0,
            frac_above=float((d > 0.5).mean()) if d.size else 0.0,
            rmse=float(np.sqrt((d ** 2).mean())) if d.size else None,
            relief=float(Ht[0][cover].std())))
    return out


def classify_failure(truth: dict, best_loglik: float) -> str:
    """Heuristic cause of a miss, from how the extracted skyline compares with the true horizon."""
    pq = truth["per_query"]
    mean = lambda k: float(np.mean([p[k] for p in pq]))
    if mean("valid_frac") < 0.25:
        return "insufficient skyline (dark or sky-only image)"
    if mean("frac_below") > 0.3:
        return "shadow mistaken for sky"
    if mean("frac_above") > 0.3:
        return "near-field rocks hiding far horizon"
    if mean("relief") < 0.5:
        return "flat terrain"
    if truth["loglik"] >= best_loglik - 1e-6:
        return "search miss (true spot scores best but was not refined)"
    return "repeated horizon shapes"


def skyline_overlay(info: dict, step: int = 2) -> list:
    """Extracted skyline as [[x, y], ...] pixel points (None gaps removed) for drawing."""
    cols = np.nonzero(info["valid"])[0][::step]
    return np.stack([cols + 0.5, info["rows"][cols]], 1).round(1).tolist()


__all__ = ["Query", "build_query", "locate", "analyse_truth", "classify_failure", "credible_rank",
           "skyline_overlay", "project_profile", "load_gray"]
