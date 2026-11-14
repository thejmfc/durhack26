"""Offline scoring of a batch run (photos rendered by the three.js harness, stored by /api/eval/record).

usage: .venv/bin/python eval/run_eval.py [--run main] [--quick N]
Writes eval/results/{records.json, summary.json, tables.md, *.png, failures/}.
Every number comes from this script's own execution; nothing is hard-coded.
"""
import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from skyline.locate import (analyse_truth, build_query, classify_failure, load_gray, locate,  # noqa: E402
                            project_profile)
from skyline.match import HorizonDB, db_path  # noqa: E402
from skyline.terrain import load_terrain  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--run", default="main")
ap.add_argument("--quick", type=int, default=0, help="only the first N spots")
ap.add_argument("--default-method", default=None, help="skip selection and use this method for the config sweep")
args = ap.parse_args()
RUN, OUT = ROOT / "eval/runs" / args.run, ROOT / "eval/results"
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = json.loads((ROOT / "backend/config.json").read_text())
SCALES = [0.03, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0]  # multipliers on the log-likelihood (for calibration)
HIT = 100.0

terrain = load_terrain()
px = terrain.meta["pixel_size"]

# ---------------------------------------------------------------- stage 1: skylines (cached)
qfile = RUN / "queries.pkl"
if qfile.exists():
    spots = pickle.loads(qfile.read_bytes())
else:
    spots = []
    for d in sorted(p for p in RUN.iterdir() if p.is_dir()):
        m = json.loads((d / "meta.json").read_text())
        g = {s["name"]: load_gray((d / s["name"]).read_bytes()) for s in m["shots"]}
        by = lambda grp: [s for s in m["shots"] if s["group"] == grp]
        pano = by("pano")
        spots.append(dict(meta=m, dir=str(d),
                          f66=[build_query([g[s["name"]]], 66, m["pitch"]) for s in by("f66")],
                          f120=[build_query([g[s["name"]]], 120, m["pitch"]) for s in by("f120")],
                          pano=[build_query([g[s["name"]] for s in pano], 66, m["pitch"], [s["yaw"] for s in pano])],
                          h66=[s["heading"] for s in by("f66")], h0=by("f120")[0]["heading"]))
    qfile.write_bytes(pickle.dumps(spots))
if args.quick:
    spots = spots[:args.quick]
print(f"{len(spots)} spots loaded")

dbs = {s: HorizonDB(s) for s in CONFIG["db_spacings"] if db_path(s).exists()}


def trial(sp, queries, headings, method, spacing, refine=True, heading_sigma=None, rng=None):
    m = sp["meta"]
    tr, tc = (float(v) for v in terrain.xy_to_rc(m["x"], m["y"]))
    rec = dict(spot=m["spot"], sun_el=m["sun_el"], valid_frac=float(np.mean([Q.valid_frac for Q in queries])))
    keep = [i for i, Q in enumerate(queries) if Q.m.sum() >= 10]
    if not keep:
        return dict(rec, usable=False, err=None, cls="insufficient skyline (dark or sky-only image)")
    qs, hs = [queries[i] for i in keep], [headings[i] for i in keep]
    hw = None
    if heading_sigma is not None:  # simulated external heading estimate: truth + Gaussian noise, +-3 sigma search window
        hw = [((h + rng.normal(0, heading_sigma)) % 360, 3 * heading_sigma) for h in hs]
    db = dbs[spacing]
    res = locate(terrain, db, qs, method, CONFIG.get("calibration"), heading_windows=hw, refine=refine)
    c = res["candidates"]
    errs = [float(np.hypot(k["row"] - tr, k["col"] - tc) * px) for k in c]
    herr = float(np.mean([abs((a - b + 180) % 360 - 180) for a, b in zip(c[0]["headings"], hs)]))
    ta = analyse_truth(terrain, qs, tr, tc, hs, method, CONFIG.get("calibration"))
    # credible-region mass that contains the truth, for several likelihood temperatures
    ny, nx = db.shape
    step = db.spacing / px
    r = int(np.clip(round((tr - db.rows[0]) / step), 0, ny - 1))
    cc = int(np.clip(round((tc - db.cols[0]) / step), 0, nx - 1))
    ll = res["loglik"]
    # the truth can sit up to half a cell from its node: use the best of the 3x3 neighbourhood as "its" cell
    lt = ll[max(r - 1, 0):r + 2, max(cc - 1, 0):cc + 2].max()
    cred = {}
    for s in SCALES:
        p = np.exp((ll - ll.max()) * s)
        cred[str(s)] = float(p[ll >= lt].sum() / p.sum())
    return dict(rec, usable=True, err=errs[0], top5=min(errs), herr=herr, gap=res["confidence"]["loglik_gap"],
                cred=cred, ll_best=c[0]["loglik"], ll_truth=ta["loglik"],
                cls=None if errs[0] <= HIT else classify_failure(ta, c[0]["loglik"]),
                rmse_at_truth=float(np.mean([p["rmse"] for p in ta["per_query"]])),
                relief=float(np.mean([p["relief"] for p in ta["per_query"]])), _truth_horizon=ta["horizon"])


def run_config(name, pick, method, spacing, **kw):
    t0 = time.time()
    rng = np.random.default_rng(7)
    recs = []
    for sp in spots:
        q, h = pick(sp)
        recs.append(dict(trial(sp, q, h, method, spacing, rng=rng, **kw), config=name, method=method, spacing=spacing))
    s = stats(recs)
    print(f"{name:34s} {method:5s} {spacing:3d} m  within100 {s['within_100m']:5.1f}%  median(usable) {s['median_m']:7.0f} m  [{time.time() - t0:.0f}s]", flush=True)
    return recs


def stats(recs):
    n = len(recs)
    u = [r for r in recs if r["usable"]]
    e = np.array([r["err"] for r in u]) if u else np.array([np.nan])
    pct = lambda a: round(100.0 * float(a) / n, 1)
    hits = [r for r in u if r["err"] <= HIT]
    return dict(n=n, n_usable=len(u), usable_pct=pct(len(u)),
                within_100m=pct((e <= 100).sum()), within_500m=pct((e <= 500).sum()), within_1km=pct((e <= 1000).sum()),
                top5_100m=pct(sum(r["top5"] <= HIT for r in u)),
                median_m=float(np.median(e)), p90_m=float(np.percentile(e, 90)),
                usable_within_100m=round(100.0 * float((e <= 100).mean()), 1),
                heading_median_deg=float(np.median([r["herr"] for r in u])) if u else None,
                heading_median_deg_hits=float(np.median([r["herr"] for r in hits])) if hits else None)


one = lambda sp: (sp["f66"][:1], sp["h66"][:1])
records = {}
# ---- ablation on the single 66 deg photo: scoring rule x grid spacing
for spacing in sorted(dbs, reverse=True):
    for method in ("ncc", "zssd", "ssd"):
        records[f"1x66|{method}|{spacing}"] = run_config("1 photo, 66 deg", one, method, spacing)
best = args.default_method or max(("ncc", "zssd", "ssd"), key=lambda mth: stats(records[f"1x66|{mth}|{min(dbs)}"])["within_100m"])
SP = min(dbs)
print("method used for the remaining configurations:", best, f"({SP} m grid)")
records[f"1x66|{best}|{SP}|norefine"] = run_config("1 photo, 66 deg, no 10 m refine", one, best, SP, refine=False)
sweep = {
    "1 photo, 66 deg": f"1x66|{best}|{SP}",
    "2 photos, 66 deg": lambda sp: (sp["f66"][:2], sp["h66"][:2]),
    "3 photos, 66 deg": lambda sp: (sp["f66"][:3], sp["h66"][:3]),
    "4 photos, 66 deg": lambda sp: (sp["f66"][:4], sp["h66"][:4]),
    "1 photo, 120 deg": lambda sp: (sp["f120"], [sp["h0"]]),
    "360 deg panorama (6 x 66 deg)": lambda sp: (sp["pano"], [sp["h0"]]),
}
for name, pick in sweep.items():
    if not isinstance(pick, str):
        records[name] = run_config(name, pick, best, SP)
    else:
        records[name] = records[pick]
for sig in (2.0, 10.0):
    records[f"1 photo, 66 deg + heading known to {sig:g} deg (1 sigma)"] = run_config(
        f"1x66 + heading sigma {sig:g}", one, best, SP, heading_sigma=sig)

# ---------------------------------------------------------------- calibration of the likelihood temperature
head = records["1 photo, 66 deg"]
u = [r for r in head if r["usable"]]
fit, test = u[0::2], u[1::2]
cover = lambda rs, s, level: float(np.mean([r["cred"][str(s)] <= level for r in rs]))
scale = min(SCALES, key=lambda s: abs(cover(fit, s, 0.9) - 0.9) + abs(cover(fit, s, 0.5) - 0.5))
calib = dict(scale=scale, rho=CONFIG["calibration"]["rho"] / scale,
             default_scale_coverage={f"{int(l * 100)}": round(100 * cover(u, 1.0, l), 1) for l in (0.5, 0.9)},
             fit_half={f"{int(l * 100)}": round(100 * cover(fit, scale, l), 1) for l in (0.5, 0.9)},
             test_half={f"{int(l * 100)}": round(100 * cover(test, scale, l), 1) for l in (0.5, 0.9)}, n_test=len(test))
print("calibration:", calib)

# confidence gap (in calibrated units) vs measured hit rate
edges = [0, 0.5, 1, 2, 4, 8, 1e9]
conf = []
for lo, hi in zip(edges[:-1], edges[1:]):
    rs = [r for r in u if lo <= r["gap"] * scale < hi]
    if rs:
        conf.append(dict(lo=lo, hi=hi, n=len(rs), within_100m=round(100 * float(np.mean([r["err"] <= HIT for r in rs])), 1)))

# ---------------------------------------------------------------- outputs
S = {k: stats(v) for k, v in records.items()}
fails = [r for r in head if not r["usable"] or r["err"] > HIT]
classes = {}
for r in fails:
    classes[r["cls"]] = classes.get(r["cls"], 0) + 1
sun = {}
for lo in (2, 4, 6):
    rs = [r for r in head if lo <= r["sun_el"] < lo + 2]
    sun[f"{lo}-{lo + 2}"] = stats(rs) if rs else None
h = S["1 photo, 66 deg"]
summary = dict(run=args.run, n_spots=len(spots), method=best, spacing_m=SP, calibration=calib,
               headline=dict(hfov=66, n=h["n"], within_100m=h["within_100m"], within_1km=h["within_1km"], median_m=h["median_m"],
                             usable_pct=h["usable_pct"]),
               confidence_table=conf, stats=S, failure_classes=classes, by_sun_elevation=sun,
               note="All numbers are from synthetic three.js renders of Site01; no real photos were tested.")
(OUT / "summary.json").write_text(json.dumps(summary, indent=1))
strip = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}
(OUT / "records.json").write_text(json.dumps({k: [strip(r) for r in v] for k, v in records.items()}))

row = lambda k, s: (f"| {k} | {s['n']} | {s['usable_pct']} | {s['within_100m']} | {s['within_500m']} | {s['within_1km']} | {s['top5_100m']} | "
                    f"{s['median_m']:.0f} | {s['p90_m']:.0f} | {s['heading_median_deg']:.1f} | {s['heading_median_deg_hits']:.2f} |")
hdr = ("| configuration | photos sets | usable skyline % | within 100 m % | within 500 m % | within 1 km % | top-5 hit (100 m) % | "
       "median err m (usable) | p90 err m (usable) | heading err median deg (usable) | heading err median deg (hits) |\n|---|---|---|---|---|---|---|---|---|---|---|")
md = ["## Scoring rule x grid spacing (single 66 deg photo)", hdr]
md += [row(f"{k.split('|')[1]}, {k.split('|')[2]} m grid" + (", no refine" if k.endswith("norefine") else ""), S[k]) for k in records if k.startswith("1x66|")]
md += ["", f"## Field of view and number of photos (method {best}, {SP} m grid)", hdr]
md += [row(k, S[k]) for k in records if not k.startswith("1x66|")]
md += ["", "## Failure causes (single 66 deg photo, misses > 100 m)", "| cause | count | % of all photos |", "|---|---|---|"]
md += [f"| {k} | {v} | {100 * v / len(head):.1f} |" for k, v in sorted(classes.items(), key=lambda kv: -kv[1])]
md += ["", "## By sun elevation (single 66 deg photo)", "| sun elevation deg | n | usable % | within 100 m % | within 1 km % |", "|---|---|---|---|---|"]
md += [f"| {k} | {s['n']} | {s['usable_pct']} | {s['within_100m']} | {s['within_1km']} |" for k, s in sun.items() if s]
md += ["", "## Confidence gap vs hit rate (single 66 deg photo, usable only)", "| gap (calibrated log-lik units) | n | within 100 m % |", "|---|---|---|"]
md += [f"| {c['lo']} - {'inf' if c['hi'] > 1e8 else c['hi']} | {c['n']} | {c['within_100m']} |" for c in conf]
md += ["", "## Calibration", "```", json.dumps(calib, indent=1), "```"]
(OUT / "tables.md").write_text("\n".join(md))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def cdf_plot(keys, fname, title):
    fig, ax = plt.subplots(figsize=(6.4, 4))
    for k in keys:
        rs = records[k]
        e = np.sort([r["err"] if r["usable"] else np.inf for r in rs])
        ax.step(np.maximum(e, 5), 100 * np.arange(1, len(e) + 1) / len(e), where="post", label=k.replace(" deg", "°"), lw=1.6)
    ax.set_xscale("log"); ax.set_xlim(5, 2e4); ax.set_ylim(0, 100)
    for x in (100, 500, 1000):
        ax.axvline(x, color="#999", lw=0.6, ls=":")
    ax.set_xlabel("position error (m)"); ax.set_ylabel("% of all photo sets with error below x"); ax.set_title(title, fontsize=10)
    ax.legend(fontsize=7.5, loc="upper left"); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(OUT / fname, dpi=130); plt.close(fig)


cdf_plot(["1 photo, 66 deg", "1 photo, 120 deg", "360 deg panorama (6 x 66 deg)"], "cdf_fov.png", "Error vs field of view (simulation; unusable photos never reach 100%)")
cdf_plot(["1 photo, 66 deg", "2 photos, 66 deg", "3 photos, 66 deg", "4 photos, 66 deg"], "cdf_nphotos.png", "Error vs number of photos from the same spot (simulation)")

fig, ax = plt.subplots(figsize=(6.4, 3.6))
ks = [k for k in records if k.startswith("1x66|") and not k.endswith("norefine")]
ax.bar([f"{k.split('|')[1]}\n{k.split('|')[2]} m" for k in ks], [S[k]["within_100m"] for k in ks], color="#4878a8", label="best candidate")
ax.bar([f"{k.split('|')[1]}\n{k.split('|')[2]} m" for k in ks], [S[k]["top5_100m"] - S[k]["within_100m"] for k in ks], bottom=[S[k]["within_100m"] for k in ks], color="#a8c4e0", label="in top 5")
ax.set_ylabel("% of all photos within 100 m"); ax.set_title("Scoring rule and grid spacing, single 66° photo (simulation)", fontsize=10); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig(OUT / "methods.png", dpi=130); plt.close(fig)

fig, ax = plt.subplots(figsize=(6.4, 3.2))
it = sorted(classes.items(), key=lambda kv: kv[1])
ax.barh([k for k, _ in it], [v for _, v in it], color="#b05a4a")
ax.set_xlabel(f"misses (> 100 m) out of {len(head)} single 66° photos"); fig.tight_layout(); fig.savefig(OUT / "failures.png", dpi=130); plt.close(fig)

# failure thumbnails: photo + extracted skyline (cyan) + true horizon from the DEM (green)
from PIL import Image, ImageDraw  # noqa: E402
fd = OUT / "failures"
fd.mkdir(exist_ok=True)
for f in fd.glob("*.jpg"):
    f.unlink()
by_spot = {sp["meta"]["spot"]: sp for sp in spots}
lines = ["| spot | thumbnail | error m | cause | sun elev deg | usable skyline % |", "|---|---|---|---|---|---|"]
seen = {}
for r in sorted(fails, key=lambda r: r["spot"]):
    sp = by_spot[r["spot"]]
    cell = ""
    if seen.get(r["cls"], 0) < 8:  # thumbnails for up to 8 examples per cause
        seen[r["cls"]] = seen.get(r["cls"], 0) + 1
        im = Image.open(Path(sp["dir"]) / "f66_0.jpg").convert("RGB")
        dr = ImageDraw.Draw(im)
        info = sp["f66"][0].images[0]
        cols = np.nonzero(info["valid"])[0]
        dr.point([(int(c), int(info["rows"][c])) for c in cols], fill=(53, 208, 255))
        if r.get("_truth_horizon") is not None:
            pts = project_profile(r["_truth_horizon"], sp["h66"][0], info["w"], info["h"], 66, 0)
            dr.line([tuple(p) for p in pts], fill=(61, 220, 151), width=2)
        im.resize((320, 240)).save(fd / f"spot_{r['spot']:04d}.jpg", quality=80)
        cell = f"![](failures/spot_{r['spot']:04d}.jpg)"
    lines.append(f"| {r['spot']} | {cell} | {'-' if r['err'] is None else round(r['err'])} | {r['cls']} | {r['sun_el']:.1f} | {100 * min(r['valid_frac'], 1):.0f} |")
(OUT / "failures.md").write_text("# Failure log: single 66 deg photo, misses > 100 m\n\nCyan: skyline extracted from the photo. Green: true horizon from the DEM at the true pose.\n\n" + "\n".join(lines) + "\n")
print("wrote", OUT)
