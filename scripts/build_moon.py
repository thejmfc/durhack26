"""Prepare global browser elevation data and the whole-Moon horizon search index.

Run with the scripts Python environment: scripts/.venv/bin/python scripts/build_moon.py
Use --assets-only to prepare exploration without the search index.
"""
import argparse
import json
import sys
import time
import tempfile
import platform
from pathlib import Path

import numpy as np
from PIL import Image

# Use OS-backed locks so multiple launchers cannot build the same cache.
if platform.system() == 'Windows':
    import msvcrt
else:
    import fcntl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from skyline import moon
from skyline import moon_native, moon_build


_build_started = False


def build_status(phase, completed=0, total=0, message=None, **extra):
    with tempfile.NamedTemporaryFile(mode='w', prefix='build-', suffix='.json.partial', dir=moon.DB_DIR, delete=False) as handle:
        json.dump(dict(phase=phase, completed=completed, total=total, updated=time.time(), message=message, **extra), handle)
        pending = Path(handle.name)
    pending.replace(moon.DB_DIR / 'build.json')


def run_build():
    try:
        main()
    except Exception as error:
        if _build_started or '--index-only' in sys.argv:
            build_status('failed', message=str(error))
        raise


def main():
    global _build_started
    ap = argparse.ArgumentParser(description=__doc__)
    spacing = ap.add_mutually_exclusive_group()
    spacing.add_argument('--spacing', type=float, help='legacy grid spacing in degrees; must divide 180')
    spacing.add_argument('--spacing-km', type=float, default=1.5, help='maximum surface grid spacing in km (default: 1.5)')
    ap.add_argument('--azimuths', type=int, choices=[90, 180, 360], default=90)
    duration = ap.add_mutually_exclusive_group()
    duration.add_argument('--max-minutes', type=float, default=20, help='time budget for this index pass; rerun to resume (default: 20)')
    duration.add_argument('--continuous', action='store_true', help='continue until complete or the disk reserve is reached')
    ap.add_argument('--workers', type=int, choices=[1, 2, 3, 4], default=4)
    ap.add_argument('--plan', action='store_true', help='show grid size, storage and resumable progress without rebuilding')
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument('--assets-only', action='store_true')
    modes.add_argument('--index-only', action='store_true', help='reuse prepared browser assets while building the search index')
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
    if args.index_only:
        info = json.loads((out / 'moon.json').read_text())
        if not (out / 'moon_elevation.bin').is_file() or not (out / 'moon_color.jpg').is_file():
            raise FileNotFoundError('Prepare browser terrain with --assets-only before using --index-only.')
    else:
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
    build_status('elevation')
    _build_started = True
    print('Preparing the shared 59–60 m elevation for the entire Moon…', flush=True)
    last_message = [0.]
    def progress(message):
        now = time.monotonic()
        if now - last_message[0] >= 10 or 'ready' in message or 'prepared' in message:
            print(message, flush=True)
            last_message[0] = now
            build_status('elevation')
    moon_native.prepare_all(progress)
    print('All 34 native elevation regions are ready. Building approximate horizon curves from these same elevations; no comparison images are generated.', flush=True)
    moon_build.build(config, info, build_status, None if args.continuous else args.max_minutes, args.workers)


if __name__ == '__main__':
    moon.DB_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = moon.DB_DIR / 'build.lock'
    with lock_file.open('a+') as lock:
        try:
            if platform.system() == 'Windows':
                lock.seek(0, 2)
                if lock.tell() == 0:
                    lock.write('0')
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise SystemExit(f'Could not lock the Moon build; another build may be running: {error}') from error
        run_build()
