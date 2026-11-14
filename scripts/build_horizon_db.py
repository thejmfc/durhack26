"""Benchmark, then build and cache the horizon database for the site.

usage: python scripts/build_horizon_db.py [--site Site01] [--spacing 100] [--bench-only] [--force]
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from skyline.horizon import horizons  # noqa: E402
from skyline.match import db_path, grid_positions  # noqa: E402
from skyline.terrain import load_terrain  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--spacing", type=float, default=100.0)
ap.add_argument("--site", default="Site01")
ap.add_argument("--bench-only", action="store_true")
ap.add_argument("--force", action="store_true")
args = ap.parse_args()

t = time.time()
terrain = load_terrain(args.site)
print(f"terrain loaded in {time.time() - t:.1f}s, shape {terrain.shape}")
rows, cols, shape = grid_positions(terrain, args.spacing)
n = rows.size
print(f"grid: {shape[0]} x {shape[1]} = {n} observers at {args.spacing:g} m")

horizons(terrain, rows[:8], cols[:8])  # JIT warm-up
rng = np.random.default_rng(0)
idx = rng.choice(n, min(1000, n), replace=False)
t = time.time()
horizons(terrain, rows[idx], cols[idx])
dt = time.time() - t
proj = dt / idx.size * n
print(f"benchmark: {idx.size} observers in {dt:.2f}s -> projected full grid {proj:.0f}s ({proj / 60:.1f} min)")
if proj > 600:
    print("PROJECTED TIME EXCEEDS 10 MINUTES - stopping; consider a coarser grid or GPU.")
    sys.exit(2)
if args.bench_only:
    sys.exit(0)
out = db_path(args.spacing, args.site)
if out.exists() and not args.force:
    print(f"{out} exists (use --force to rebuild)")
    sys.exit(0)
t = time.time()
Hz = horizons(terrain, rows, cols)
print(f"full grid computed in {time.time() - t:.1f}s")
np.savez(out, H=Hz, rows=rows, cols=cols, shape=np.array(shape), spacing=args.spacing)
print(f"saved {out} ({out.stat().st_size / 1e6:.1f} MB); horizon elevation range {Hz.min():.1f}..{Hz.max():.1f} deg")
