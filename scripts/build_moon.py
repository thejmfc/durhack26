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
from skyline import moon_native, moon_surface


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--spacing', type=float, default=0.125, help='global search grid spacing in degrees')
    ap.add_argument('--azimuths', type=int, choices=[90, 180, 360], default=90)
    ap.add_argument('--assets-only', action='store_true')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()
    if not 0.0625 <= args.spacing <= 10 or abs(round(180 / args.spacing) * args.spacing - 180) > 1e-8:
        ap.error('spacing must divide 180 degrees and be between 0.0625 and 10')
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
        if old['spacing_deg'] == args.spacing and old['n_az'] == args.azimuths:
            print('Global search database already exists; use --force to rebuild.')
            return
    moon.DB_DIR.mkdir(parents=True, exist_ok=True)
    def status(phase, completed=0, total=0):
        # Download workers report concurrently; each publication needs its own
        # temporary name so one worker cannot replace another worker's file.
        with tempfile.NamedTemporaryFile(mode='w', prefix='build-', suffix='.json.partial', dir=moon.DB_DIR, delete=False) as handle:
            json.dump(dict(phase=phase, completed=completed, total=total, updated=time.time()), handle)
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
    # Build a separate generation. Existing readers keep valid memory maps, even during a rebuild.
    generation = Path(tempfile.mkdtemp(prefix='grid-', dir=moon.DB_DIR))
    lat = np.arange(90 - args.spacing / 2, -90, -args.spacing)
    lon = np.arange(-180 + args.spacing / 2, 180, args.spacing)
    lons, lats = np.meshgrid(lon, lat)
    pos = np.stack([lats.ravel(), lons.ravel()], axis=1)
    np.save(generation / 'positions.npy', pos)
    data = np.lib.format.open_memmap(generation / 'horizons.npy', mode='w+', dtype=np.float32, shape=(len(pos), args.azimuths))
    start = time.time()
    done = 0
    status('index', 0, len(pos))
    # Neighbouring positions reuse native pages. Latitude-wide batches thrash
    # the 26 GB elevation cache and make a build needlessly memory intensive.
    for row in range(0, len(lat), 16):
        for col in range(0, len(lon), 64):
            rr, cc = np.meshgrid(np.arange(row, min(row + 16, len(lat))),
                                 np.arange(col, min(col + 64, len(lon))), indexing='ij')
            indices = (rr * len(lon) + cc).ravel()
            p = pos[indices]
            values = moon.proposal_horizons(p[:, 0], p[:, 1], args.azimuths)
            if not np.isfinite(values).all() or np.any(values <= -89):
                raise ValueError('Missing elevations in the global index; no database was published')
            data[indices] = values
            done += len(p)
            if done % 16384 == 0 or done == len(pos):
                data.flush()
                status('index', done, len(pos))
                print(f'{done}/{len(pos)} positions, {time.time() - start:.1f}s', flush=True)
    data.flush()
    meta = dict(version=moon.DB_VERSION, spacing_deg=args.spacing, shape=[len(lat), len(lon)],
                positions=len(pos), source=info, terrain_model=moon_surface.specification(),
                index_method=moon_surface.INDEX_METHOD, native_sources=[s.name for s in moon_native.SOURCES],
                horizons=f'{generation.name}/horizons.npy', positions_file=f'{generation.name}/positions.npy',
                max_range_m=float(moon_surface.PROPOSAL_RANGES[-1]), n_az=args.azimuths, dem=moon.dem_path().name)
    pending = generation / 'meta.json'
    pending.write_text(json.dumps(meta, indent=2))
    pending.replace(moon.DB_DIR / 'meta.json')
    status('ready', len(pos), len(pos))
    print('Whole-Moon search database ready.', flush=True)


if __name__ == '__main__':
    moon.DB_DIR.mkdir(parents=True, exist_ok=True)
    with (moon.DB_DIR / 'build.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A whole-Moon build is already running. Let that build finish.')
        # Also recognise a build started before the advisory lock was introduced.
        try:
            active = json.loads((moon.DB_DIR / 'build.json').read_text())
        except (OSError, ValueError):
            active = {}
        if active.get('phase') in ('elevation', 'index') and time.time() - active.get('updated', 0) < 180:
            raise SystemExit('Another build has reported progress recently. Wait for it to finish (or three minutes after stopping it).')
        main()
