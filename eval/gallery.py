"""Picture gallery of test runs: photo + skylines on the left, map with true vs estimated spot on the right.

usage: .venv/bin/python eval/gallery.py [--run main] [--method ssd] [--n 12] [--start 0]
Takes the first usable spots in order (no cherry-picking). Writes eval/results/gallery_*.png
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from skyline.horizon import horizons  # noqa: E402
from skyline.locate import locate, project_profile  # noqa: E402
from skyline.match import HorizonDB, db_path  # noqa: E402
from skyline.terrain import load_terrain  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--run", default="main")
ap.add_argument("--method", default=None)
ap.add_argument("--n", type=int, default=12)
ap.add_argument("--start", type=int, default=0)
a = ap.parse_args()
CONFIG = json.loads((ROOT / "backend/config.json").read_text())
method = a.method or CONFIG["method"]
terrain = load_terrain()
db = HorizonDB(next(s for s in CONFIG["db_spacings"] if db_path(s).exists()))
spots = pickle.loads((ROOT / "eval/runs" / a.run / "queries.pkl").read_bytes())
px = terrain.meta["pixel_size"]
relief = terrain.xyz[::8, ::8, 2]
ny, nx = db.shape
ext = [db.cols[0], db.cols[-1], db.rows[-1], db.rows[0]]

rows = []
for sp in spots[a.start:]:
    Q = sp["f66"][0]
    if Q.m.sum() < 10:
        continue
    rows.append(sp)
    if len(rows) == a.n:
        break
fig, axes = plt.subplots(len(rows), 2, figsize=(9.5, 3.3 * len(rows)), gridspec_kw=dict(width_ratios=[1.33, 1]))
for (ax1, ax2), sp in zip(np.atleast_2d(axes), rows):
    m, Q = sp["meta"], sp["f66"][0]
    tr, tc = (float(v) for v in terrain.xy_to_rc(m["x"], m["y"]))
    res = locate(terrain, db, [Q], method, CONFIG.get("calibration"))
    c = res["candidates"]
    err = np.hypot(c[0]["row"] - tr, c[0]["col"] - tc) * px
    herr = abs((c[0]["headings"][0] - sp["h66"][0] + 180) % 360 - 180)
    info = Q.images[0]
    ax1.imshow(Image.open(Path(sp["dir"]) / "f66_0.jpg"), cmap="gray")
    cols = np.nonzero(info["valid"])[0]
    ax1.plot(cols, info["rows"][cols], ".", color="#35d0ff", ms=1.5, label="skyline found in photo")
    Hc = horizons(terrain, [c[0]["row"], tr], [c[0]["col"], tc])
    p = project_profile(Hc[0], c[0]["headings"][0], info["w"], info["h"], 66, 0)
    ax1.plot(p[:, 0], p[:, 1], color="#ff4fa3", lw=1.2, label="horizon predicted at estimate")
    p = project_profile(Hc[1], sp["h66"][0], info["w"], info["h"], 66, 0)
    ax1.plot(p[:, 0], p[:, 1], color="#3ddc97", lw=1.0, ls="--", label="true horizon (DEM at true pose)")
    ax1.set_xlim(0, info["w"]); ax1.set_ylim(info["h"], 0); ax1.axis("off")
    ok = err <= 100
    ax1.set_title(f"spot {m['spot']}: position error {err:,.0f} m, heading error {herr:.1f}°  ({'HIT' if ok else 'MISS'})",
                  fontsize=9, color="#0a7d4f" if ok else "#b3261e", loc="left")
    ax2.imshow(relief, cmap="gray", extent=[0, terrain.shape[1], terrain.shape[0], 0])
    ax2.imshow(np.sqrt(res["prob"] / res["prob"].max()), cmap="inferno", extent=ext, alpha=0.6)
    for k in c[1:]:
        ax2.plot(k["col"], k["row"], "o", mfc="none", mec="#ff7a45", ms=6, mew=1)
    ax2.plot(c[0]["col"], c[0]["row"], "o", mfc="none", mec="#ffd166", ms=10, mew=2, label="estimate")
    ax2.plot(tc, tr, "+", color="#3ddc97", ms=12, mew=2, label="true spot")
    ax2.axis("off")
    ax2.set_title(f"likelihood map, sun elev {m['sun_el']:.1f}°", fontsize=8)
    if sp is rows[0]:
        ax1.legend(fontsize=6.5, loc="lower left", framealpha=0.6); ax2.legend(fontsize=6.5, loc="lower left", framealpha=0.6)
fig.tight_layout()
out = ROOT / f"eval/results/gallery_{method}_{a.start}.png"
fig.savefig(out, dpi=85)
print(out)
