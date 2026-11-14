"""Fast accuracy read: single 66 deg photo, first N stored spots, three scoring rules. usage: quick.py [N]"""
import json, pickle, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from skyline.locate import locate
from skyline.match import HorizonDB
from skyline.terrain import load_terrain
N = int(sys.argv[1]) if len(sys.argv) > 1 else 80
t = load_terrain(); db = HorizonDB(50)
spots = pickle.loads((ROOT / "eval/runs/main/queries.pkl").read_bytes())[:N]
for method in ("ssd", "zssd", "ncc"):
    errs = []; top5 = []
    for sp in spots:
        Q = sp["f66"][0]
        if Q.m.sum() < 10:
            errs.append(np.inf); top5.append(np.inf); continue
        tr, tc = (float(v) for v in t.xy_to_rc(sp["meta"]["x"], sp["meta"]["y"]))
        c = locate(t, db, [Q], method)["candidates"]
        e = [np.hypot(k["row"] - tr, k["col"] - tc) * 5 for k in c]
        errs.append(e[0]); top5.append(min(e))
    e = np.array(errs); u = np.isfinite(e)
    print(f"{method:5s} n={N} usable={u.mean()*100:.0f}%  within100m={np.mean(e<=100)*100:.0f}%  within500m={np.mean(e<=500)*100:.0f}%  within1km={np.mean(e<=1000)*100:.0f}%  top5hit={np.mean(np.array(top5)<=100)*100:.0f}%  median(usable)={np.median(e[u]):.0f} m  p90(usable)={np.percentile(e[u],90):.0f} m", flush=True)
