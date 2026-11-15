"""Skyline backend: FastAPI wrapper around the localisation pipeline.

run: backend/.venv/bin/uvicorn app:app --app-dir backend --port 8077 --reload
"""
from __future__ import annotations

import base64
import json
import math
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from skyline import moon, moon_detail, moon_surface, moon_native
from skyline.extract import DEFAULT_THRESHOLD
from skyline.locate import (analyse_truth, build_query, classify_failure, credible_rank, load_gray,
                            locate, project_profile, skyline_overlay)
from skyline.horizon import horizons
from skyline.match import METHODS, HorizonDB, db_path
from skyline.terrain import DEFAULT_SITE, ROOT, SITES, available_sites, dem_path, load_terrain, unload_terrain

EVAL_DIR = ROOT / "eval"
CONFIG = json.loads((ROOT / "backend/config.json").read_text())

app = FastAPI(title="Skyline")
lock = threading.Lock()  # Numba's parallel runtime must not be entered from two threads at once
_dbs: dict[str, HorizonDB] = {}


def ready_sites() -> list[str]:
    return [s for s in available_sites() if any(db_path(sp, s).exists() for sp in CONFIG["db_spacings"])
            and (ROOT / "frontend/public/data/sites" / s / "site_meta.json").exists()]


def get_site(site: str):
    """(terrain, horizon DB) of a site; keeps at most two sites in memory."""
    if site not in ready_sites():
        raise HTTPException(404, f"site {site} is not prepared (DEM or horizon database missing)")
    with lock:
        if site not in _dbs:
            for old in list(_dbs)[:-1]:
                _dbs.pop(old)
                unload_terrain(old)
            _dbs[site] = HorizonDB(next(sp for sp in CONFIG["db_spacings"] if db_path(sp, site).exists()), site)
        return load_terrain(site), _dbs[site]




def _eval_summary():
    f = EVAL_DIR / "results/summary.json"
    return json.loads(f.read_text()) if f.exists() else None


@app.get("/api/meta")
def meta(site: str = DEFAULT_SITE):
    terrain, db = get_site(site)
    return dict(site=terrain.meta, sites=[dict(id=s, name=SITES[s]) for s in ready_sites()],
                grid=dict(ny=db.shape[0], nx=db.shape[1], row0=float(db.rows[0]), col0=float(db.cols[0]),
                          step_px=db.spacing / terrain.meta["pixel_size"], spacing_m=db.spacing),
                method=CONFIG["method"], methods=METHODS, threshold=DEFAULT_THRESHOLD,
                evaluation=_eval_summary() if site == DEFAULT_SITE else None)


@app.get("/api/patch")
def patch(x: float, y: float, half: float = 200.0, step: float = 1.0, site: str = DEFAULT_SITE):
    """Full-resolution terrain around a map point as float32 site-local XYZ, row-major n x n (north to south)."""
    terrain, _ = get_site(site)
    n = 2 * int(round(half / step)) + 1
    if n > 1201:
        raise HTTPException(400, "patch too large")
    o = (np.arange(n) - n // 2) * step
    r, c = terrain.xy_to_rc(x + o[None, :], y - o[:, None])
    r, c = np.broadcast_arrays(r, c)
    return Response(terrain.sample(r, c).astype("<f4").tobytes(), media_type="application/octet-stream",
                    headers={"X-N": str(n)})


@app.post("/api/locate")
def locate_endpoint(params: str = Form(...), files: list[UploadFile] = File(...)):
    p = json.loads(params)
    terrain, db = get_site(p.get("site") or DEFAULT_SITE)
    t0 = time.time()
    hfov = float(p.get("hfov", 66.0))
    pitch = float(p.get("pitch", 0.0))
    thr = float(p.get("threshold", DEFAULT_THRESHOLD))
    method = p.get("method") or CONFIG["method"]
    if method not in METHODS or not 5 <= hfov <= 170:
        raise HTTPException(400, "bad method or field of view")
    grays = [load_gray(f.file.read()) for f in files]
    if p.get("mode") == "panorama":
        yaws = [float(v) for v in p["yaws"]]
        queries = [build_query(grays, hfov, pitch, yaws, thr)]
    else:
        queries = [build_query([g], hfov, pitch, None, thr) for g in grays]
    usable = [Q for Q in queries if Q.m.sum() >= 10]  # Query is a dataclass with eq on arrays; compare by identity below
    ids = [id(Q) for Q in usable]
    qi = [ids.index(id(Q)) if id(Q) in ids else None for Q in queries for _ in Q.images]
    infos = [info for Q in queries for info in Q.images]
    out = dict(images=[dict(w=i["w"], h=i["h"], hfov=i["hfov"], pitch=i["pitch"], yaw=i["yaw"], query=k,
                            skyline=skyline_overlay(i)) for i, k in zip(infos, qi)],
               valid_frac=[Q.valid_frac for Q in queries], method=method)
    if p.get("truth"):
        out["truth_pos"] = terrain.sample(*terrain.xy_to_rc(p["truth"]["x"], p["truth"]["y"])).tolist()
    if not usable:
        out["error"] = ("No usable skyline found: the image is almost entirely dark, or lit terrain fills "
                        "the frame to the top edge. Nothing to match, so no position is reported.")
        return out
    hw = None
    if p.get("heading_hint"):
        hh = p["heading_hint"]
        hw = [(float(hh["heading"]), float(hh.get("half_width", 10.0)))] * len(usable)
    with lock:
        res = locate(terrain, db, usable, method, CONFIG.get("calibration"), heading_windows=hw)
        cands = res["candidates"]
        Hc = horizons(terrain, [c["row"] for c in cands], [c["col"] for c in cands])
        truth = p.get("truth")
        if truth:
            tr, tc = (float(v) for v in terrain.xy_to_rc(truth["x"], truth["y"]))
            th = [float(h) for h, Q in zip(truth["headings"], queries) if Q.m.sum() >= 10]
            ta = analyse_truth(terrain, usable, tr, tc, th, method, CONFIG.get("calibration"))
    px = terrain.meta["pixel_size"]
    for c, hz in zip(cands, Hc):
        c["pos"] = terrain.sample(c["row"], c["col"]).tolist()
        c["horizon"] = np.round(hz, 2).tolist()
    if truth:
        err = [float(np.hypot(c["row"] - tr, c["col"] - tc) * px) for c in cands]
        herr = [abs((a - b + 180) % 360 - 180) for a, b in zip(cands[0]["headings"], th)]
        out["truth"] = dict(
            error_m=err[0], heading_error_deg=float(np.mean(herr)), candidate_errors_m=err,
            best_of_top5_m=min(err), horizon=np.round(ta["horizon"], 2).tolist(),
            loglik_at_truth=ta["loglik"], per_query=ta["per_query"],
            credible_mass=credible_rank(res, db, tr, tc),
            failure_class=None if err[0] <= 100 else classify_failure(ta, cands[0]["loglik"]))
    hm = res["prob"] / res["prob"].max()
    out.update(candidates=cands, confidence=res["confidence"],
               heatmap=base64.b64encode(hm.astype("<f4").tobytes()).decode(),
               seconds=round(time.time() - t0, 2))
    return out


@app.get("/api/eval/spot")
def eval_spot(run: str = "main", i: int = 0):
    """Ground truth of a stored batch spot, plus how many spots the run has (for replay in the app)."""
    d = EVAL_DIR / "runs" / "".join(ch for ch in run if ch.isalnum() or ch in "-_")
    f = d / f"{i:04d}" / "meta.json"
    if not f.exists():
        raise HTTPException(404, "no such stored spot")
    return dict(json.loads(f.read_text()), count=sum(1 for q in d.iterdir() if q.is_dir()))


@app.get("/api/eval/photo")
def eval_photo(run: str = "main", i: int = 0, name: str = "f66_0.jpg"):
    f = EVAL_DIR / "runs" / "".join(ch for ch in run if ch.isalnum() or ch in "-_") / f"{i:04d}" / Path(name).name
    if not f.exists():
        raise HTTPException(404, "no such photo")
    return Response(f.read_bytes(), media_type="image/jpeg")


@app.post("/api/eval/record")
def eval_record(meta: str = Form(...), files: list[UploadFile] = File(...)):
    """Batch harness: store rendered photos + ground truth; scoring happens offline (eval/run_eval.py)."""
    m = json.loads(meta)
    run = "".join(ch for ch in str(m["run"]) if ch.isalnum() or ch in "-_")
    if m.get("site", DEFAULT_SITE) != DEFAULT_SITE:
        run = f"{m['site']}_{run}"
    d = EVAL_DIR / "runs" / run / f"{int(m['spot']):04d}"
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / Path(f.filename).name).write_bytes(f.file.read())
    (d / "meta.json").write_text(json.dumps(m))
    return dict(ok=True)


@app.get("/api/moon/meta")
def moon_meta():
    ready = moon.database_ready()
    try:
        build = json.loads((moon.DB_DIR / 'build.json').read_text())
        if build.get('phase') != 'failed' and time.time() - build['updated'] > 180:
            build = None
    except (OSError, ValueError, KeyError):
        build = None
    return dict(ready=ready, radius=moon.RADIUS,
                features=dict(photo_analysis=True),
                observer_height=moon.OBSERVER_HEIGHT,
                terrain_model=moon_surface.specification(), build=build,
                native_regions=sum(moon_native.prepared(s) for s in moon_native.SOURCES),
                native_regions_total=len(moon_native.SOURCES),
                grid=moon.database()[0] if ready else None)


@app.get("/api/moon/detail")
def moon_detail_endpoint(lat: float, lon: float):
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise HTTPException(422, 'Invalid lunar coordinates')
    job = moon_detail.request_patch(lat, lon)
    if job['state'] == 'ready':
        return Response(job['data'], media_type='application/octet-stream',
                        headers={'X-Terrain-Meta': json.dumps(job['meta'])})
    if job['state'] == 'error':
        raise HTTPException(503, job['message'])
    return JSONResponse({'state': job['state'], 'message': job['message']}, status_code=202)


def moon_photo_inputs(params, files):
    """Shared photo validation/extraction; no retrieval or reference geometry."""
    try:
        p = json.loads(params)
        if not isinstance(p, dict):
            raise ValueError('params must be an object')
        if p.get('truth') and p.get('terrain_model') != moon_surface.MODEL_ID:
            raise HTTPException(409, 'This photo was rendered with a different terrain version. Refresh the page and take it again.')
        hfov, pitch = float(p.get("hfov", 66)), float(p.get("pitch", 0))
        method = p.get("method", "ssd")
        if method not in METHODS or not 5 <= hfov <= 170 or not -60 <= pitch <= 60 or not 1 <= len(files) <= 8:
            raise ValueError("invalid camera settings or photo count")
        pano = p.get("mode") == "panorama"
        yaws = [float(y) for y in p.get("yaws", [])] if pano else None
        if pano and (len(yaws) != len(files) or not all(math.isfinite(y) for y in yaws)):
            raise ValueError("one finite yaw is required per panorama image")
        hw = None
        if p.get("heading_hint"):
            hh = p["heading_hint"]
            heading, width = float(hh["heading"]), float(hh.get("half_width", 5))
            if not math.isfinite(heading) or not 1 <= width <= 180:
                raise ValueError("invalid heading window")
            hw = (heading % 360, width)
        truth = p.get("truth")
        if truth:
            lat, lon = float(truth["lat"]), float(truth["lon"])
            heading = float(truth["heading"])
            if not -90 <= lat <= 90 or not -180 <= lon <= 180 or not math.isfinite(heading):
                raise ValueError("invalid truth coordinates")
        grays = [load_gray(f.file.read()) for f in files]
        if any(min(g.shape) < 3 for g in grays):
            raise ValueError("image is too small")
        queries = [build_query(grays, hfov, pitch, yaws, subpixel=True)] if pano else [build_query([g], hfov, pitch, subpixel=True) for g in grays]
    except (ValueError, TypeError, KeyError, OSError) as e:
        raise HTTPException(422, str(e)) from e
    images = [dict(w=i["w"], h=i["h"], yaw=i["yaw"], skyline=skyline_overlay(i)) for q in queries for i in q.images]
    usable = [q for q in queries if q.m.sum() >= 10]
    out = dict(images=images, valid_frac=[q.valid_frac for q in queries], terrain_model=moon_surface.MODEL_ID)
    if not usable:
        out['error'] = "No usable skyline. Look towards a visible horizon or try a panorama."
    return out, usable, method, hw, truth


@app.post("/api/moon/analyse")
def moon_analyse(params: str = Form(...), files: list[UploadFile] = File(...)):
    """Return overlays promptly, independently of the global search database.

    The reference is a display-only diagnostic. It is never used as a query.
    Even a fully dark photo still receives its reference curve.
    """
    out, _, _, _, truth = moon_photo_inputs(params, files)
    if truth:
        with lock:
            out['reference_horizon'] = moon.horizons([float(truth['lat'])], [float(truth['lon'])], 1440)[0].round(4).tolist()
    return out


@app.post("/api/moon/locate")
def moon_locate(params: str = Form(...), files: list[UploadFile] = File(...)):
    if not moon.database_ready():
        raise HTTPException(503, "Global search must be rebuilt for the 59–60 m surface. Run scripts/build_moon.py.")
    out, usable, method, hw, truth = moon_photo_inputs(params, files)
    if not usable:
        return out
    start = time.time()
    with lock:
        # Deliberately pass no truth/location to the search. It searches the whole planet.
        result = moon.locate(usable, method, [hw] * len(usable))
        if truth:
            lat, lon, heading = float(truth['lat']), float(truth['lon']), float(truth['heading'])
            cs = result["candidates"]
            errors = moon.distance_m(lat, lon, np.array([c["lat"] for c in cs]), np.array([c["lon"] for c in cs]))
            out["truth"] = dict(error_m=float(errors[0]), best_of_top5_m=float(errors.min()),
                                candidate_errors_m=errors.tolist(),
                                heading_error_deg=abs((cs[0]["headings"][0] - heading + 180) % 360 - 180),
                                horizon=moon.horizons([lat], [lon], 1440)[0].round(4).tolist())
    heat = result.pop("heatmap")
    out.update(result, heatmap=base64.b64encode(heat.astype("<f4").tobytes()).decode(), seconds=round(time.time() - start, 2))
    return out
