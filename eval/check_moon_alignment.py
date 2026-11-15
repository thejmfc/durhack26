"""Offline check of browser mesh triangles, verifier horizons and photo detection.

Run with backend/.venv/bin/python eval/check_moon_alignment.py. Uses installed
terrain and frontend dependencies; starts no server and downloads no assets.
This checks alignment, not blind global location accuracy.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from numba import set_num_threads

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from skyline import moon, moon_detail, moon_native, moon_surface
from skyline.extract import load_gray
from skyline.locate import build_query

CASES = [(31.7354, 30.06756), (-11.552, 26.487), (0., 0.), (60., 45.),
         (-60., 180.), (89.9, 30.), (-89.9, -150.), (20.13, -179.999)]


def main():
    if not moon_native.ready():
        raise SystemExit('The native elevation cache must already be installed.')
    set_num_threads(4)
    with tempfile.TemporaryDirectory(prefix='moon-alignment-') as directory:
        directory = Path(directory)
        bundle = directory / 'triangles.mjs'
        subprocess.run([str(ROOT / 'frontend/node_modules/.bin/esbuild'), 'tools/check-moon-horizon.ts',
                        '--bundle', '--platform=node', '--format=esm', f'--outfile={bundle}'],
                       cwd=ROOT / 'frontend', check=True)
        for i, (lat, lon) in enumerate(CASES):
            meta, binary = moon_detail.build_patch(lat, lon)
            heights = np.frombuffer(binary, '<f4').reshape(len(meta['steps']), meta['n'], meta['n'])
            expected = moon.horizons([lat], [lon], 360)[0]
            delivered = moon_surface.patch_horizons(heights, np.asarray(meta['steps'], float), 360)
            np.testing.assert_array_equal(delivered, expected)
            prefix = directory / str(i)
            prefix.with_suffix('.json').write_text(json.dumps(meta))
            prefix.with_suffix('.bin').write_bytes(binary)
            prefix.with_suffix('.horizon.json').write_text(json.dumps(expected.tolist()))
            check = subprocess.run(['node', str(bundle), str(prefix)], text=True, capture_output=True, check=True)
            print(check.stdout.strip().splitlines()[-1], flush=True)

    fixtures = ROOT / 'eval/runs/moon/horizon-photo-fixtures-20261114.json'
    if fixtures.exists():
        for case in json.loads(fixtures.read_text()):
            photos = [ROOT / p for p in case['files']]
            if not all(p.exists() for p in photos):
                continue
            query = build_query([load_gray(p.read_bytes()) for p in photos],
                                case['hfov'], case['pitch'], case['yaws'], lunar=True)
            reference = moon.horizons([case['lat']], [case['lon']], 1440)[0]
            rmse = moon.horizon_rmse(reference, [query], [case['heading']])
            assert rmse is not None and rmse < .03 and query.m.sum() >= 350, (case['name'], rmse, query.m.sum())
            print(f"{case['name']}: photo/mesh RMSE {rmse:.6f} degrees, {query.m.sum()}/360 bins", flush=True)


if __name__ == '__main__':
    main()
