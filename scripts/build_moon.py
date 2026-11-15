"""Prepare global browser elevation data and the whole-Moon horizon search index.

Run with the scripts Python environment: scripts/.venv/bin/python scripts/build_moon.py
Use --assets-only to prepare exploration without the search index.
"""
import argparse
import json
import sys
import time
import tempfile
import fcntl
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from skyline import moon
from skyline import moon_native, moon_build


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    spacing = ap.add_mutually_exclusive_group()
    spacing.add_argument('--spacing', type=float, help='legacy grid spacing in degrees; must divide 180')
    spacing.add_argument('--spacing-km', type=float, default=1.5, help='maximum surface grid spacing in km (default: 1.5)')
    ap.add_argument('--azimuths', type=int, choices=[90, 180, 360], default=90)
    ap.add_argument('--max-minutes', type=float, default=20, help='time budget for this index pass; rerun to resume (default: 20)')
    ap.add_argument('--workers', type=int, choices=[1, 2, 3, 4], default=4)
    ap.add_argument('--plan', action='store_true', help='show grid size, storage and resumable progress without rebuilding')
    ap.add_argument('--assets-only', action='store_true')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()
    if not np.isfinite(args.max_minutes) or args.max_minutes <= 0:
        ap.error('max-minutes must be positive')
    try:
        config = moon_build.plan(args.spacing_km, args.spacing, args.azimuths)
    except ValueError as exc:
        ap.error(str(exc))
    if args.plan:
        print(json.dumps(moon_build.describe(config), indent=2))
        return
    out = ROOT / 'frontend/public/data'
    out.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / 'data/raw/ldem_16_uint.tif') as im:
        raw = np.asarray(im, dtype='<u2')
    raw.tofile(out / 'moon_elevation.bin')
    info = dict(width=raw.shape[1], height=raw.shape[0], radius=moon.RADIUS, scale=0.5, offset=-10000,
                observer_height=moon.OBSERVER_HEIGHT, source='NASA LOLA / CGI Moon Kit',
                source_url='https://svs.gsfc.nasa.gov/4720/', spacing_deg=360 / raw.shape[1])
    (out / 'moon.json').write_text(json.dumps(info, indent=2))
    high = ROOT / 'data/raw/ldem_64_uint.tif'
    if high.exists():
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(high) as im:
            detail = np.asarray(im, dtype='<u2')
        tiles = out / 'moon-tiles'
        tiles.mkdir(exist_ok=True)
        size = 360
        for r in range(detail.shape[0] // size):
            for c in range(detail.shape[1] // size):
                target = tiles / f'{r}_{c}.bin'
                if not target.exists():
                    detail[r * size:(r + 1) * size, c * size:(c + 1) * size].tofile(target)
        info['detail'] = dict(width=detail.shape[1], height=detail.shape[0], tile_size=size,
                              poles=[float(detail[0].mean()), float(detail[-1].mean())])
        (out / 'moon.json').write_text(json.dumps(info, indent=2))
        del detail
    if not (out / 'moon_color.jpg').exists():
        with Image.open(ROOT / 'data/raw/lroc_color_poles_4k.tif') as im:
            im.convert('RGB').save(out / 'moon_color.jpg', quality=88)
    print('Browser overview assets ready. The 59–60 m surface uses the separate regional elevation cache.', flush=True)
    if args.assets_only:
        return
    if moon.database_ready() and not args.force:
        old = json.loads((moon.DB_DIR / 'meta.json').read_text())
        if old['shape'] == config['shape'] and old['n_az'] == args.azimuths:
            print('Global search database already exists; use --force to rebuild.')
            return
    moon.DB_DIR.mkdir(parents=True, exist_ok=True)
    def status(phase, completed=0, total=0, **extra):
        # Download workers report concurrently; each publication needs its own
        # temporary name so one worker cannot replace another worker's file.
        with tempfile.NamedTemporaryFile(mode='w', prefix='build-', suffix='.json.partial', dir=moon.DB_DIR, delete=False) as handle:
            json.dump(dict(phase=phase, completed=completed, total=total, updated=time.time(), **extra), handle)
            pending = Path(handle.name)
        pending.replace(moon.DB_DIR / 'build.json')
    status('elevation')
    print('Preparing the shared 59–60 m elevation for the entire Moon…', flush=True)
    last_message = [0.]
    def progress(message):
        now = time.monotonic()
        if now - last_message[0] >= 10 or 'ready' in message or 'prepared' in message:
            print(message, flush=True)
            last_message[0] = now
            status('elevation')
    moon_native.prepare_all(progress)
    print('All 34 native elevation regions are ready. Building approximate horizon curves from these same elevations; no comparison images are generated.', flush=True)
    moon_build.build(config, info, status, args.max_minutes, args.workers)


if __name__ == '__main__':
    moon.DB_DIR.mkdir(parents=True, exist_ok=True)
    with (moon.DB_DIR / 'build.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A whole-Moon build is already running. Let that build finish.')
        # The advisory lock is authoritative. A checkpoint may still say
        # 'index' after a crash, and must not prevent an immediate resume.
        main()
