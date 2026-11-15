"""Memory-mapped native elevation for whole-Moon matching.

Keep the original 512 px/degree SLDEM delivery tiles and two stereographic LOLA
caps; a global equirectangular expansion would waste space around the poles.
Only completed, atomically published arrays are visible to readers.
"""
from __future__ import annotations

import json
import os
import tempfile
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import numpy as np
import rasterio
from rasterio.windows import Window

from .moon_detail import source_at, download
from .terrain import ROOT

# fcntl is Unix-only
if platform.system() != 'Windows':
    import fcntl

DIRECTORY = ROOT / 'data/cache/moon-native-v1'
SOURCES = tuple(source_at(lat, lon) for lat in (-45, -15, 15, 45) for lon in np.arange(22.5, 360, 45)) + (source_at(90, 0), source_at(-90, 0))


def path_for(source):
    return DIRECTORY / (source.name + '.npy')


def prepared(source):
    path = path_for(source)
    if not path.exists():
        return False
    try:
        a = np.load(path, mmap_mode='r')
        expected = (31040, 31040) if source.pole else (15360, 23040)
        return a.shape == expected and a.dtype == np.dtype('<i2')
    except (OSError, ValueError):
        return False


def prepare(source, progress=print):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    # Two terminals can request a rebuild. Serialize each source so they reuse
    # completed work instead of doubling large temporary arrays and downloads.
    lock_path = DIRECTORY / (source.name + '.lock')
    with lock_path.open('a') as lock:
        if platform.system() != 'Windows':
            fcntl.flock(lock, fcntl.LOCK_EX)
        # On Windows, we don't have true file locking, but writing to the lock file
        # is atomic enough for our purposes (single machine, not distributed)
        return _prepare(source, progress)


def _prepare(source, progress):
    if prepared(source):
        return
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    source_path = download(source, lambda message: progress(f'{source.name}: {message}'))
    handle, name = tempfile.mkstemp(prefix=source.name + '.', suffix='.partial', dir=DIRECTORY)
    os.close(handle)
    pending = type(source_path)(name)
    try:
        with rasterio.Env(GDAL_NUM_THREADS='2', GDAL_CACHEMAX=128 * 1024 * 1024), rasterio.open(source_path) as ds:
            array = np.lib.format.open_memmap(pending, mode='w+', dtype='<i2', shape=(ds.height, ds.width))
            for row in range(0, ds.height, 1024):
                count = min(1024, ds.height - row)
                array[row:row + count] = ds.read(1, window=Window(0, row, ds.width, count))
                progress(f'{source.name}: converting elevations, {row + count}/{ds.height} rows')
            array.flush()
            del array
        pending.replace(path_for(source))
        progress(f'{source.name}: native elevation prepared')
    finally:
        pending.unlink(missing_ok=True)


def prepare_all(progress=print, workers=2):
    # Convert cached regions first, so interrupted preparation is useful and
    # resumable. Each source download uses at most six HTTP connections.
    from .moon_detail import SOURCE_DIR
    remaining = sorted((s for s in SOURCES if not prepared(s)), key=lambda s: not (SOURCE_DIR / s.name).exists())
    completed = len(SOURCES) - len(remaining)
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(prepare, source, progress): source for source in remaining}
    try:
        for future in as_completed(futures):
            future.result()
            completed += 1
            progress(f'Native elevation: {completed}/{len(SOURCES)} regions ready')
    finally:
        # An interrupted/failed build need only finish its active files; queued
        # regions must not keep downloading after the caller has cancelled.
        pool.shutdown(wait=True, cancel_futures=True)
    manifest = dict(version=1, sources=[s.name for s in SOURCES], scale=.5, radius=1737400)
    with tempfile.NamedTemporaryFile(mode='w', prefix='meta-', suffix='.json.partial', dir=DIRECTORY, delete=False) as handle:
        json.dump(manifest, handle, indent=2)
        pending = type(DIRECTORY)(handle.name)
    pending.replace(DIRECTORY / 'meta.json')


def ready():
    return (DIRECTORY / 'meta.json').exists() and all(prepared(s) for s in SOURCES)


def maps(require_complete=True):
    if require_complete and not ready():
        raise RuntimeError('Prepare the shared 59–60 m terrain with scripts/build_moon.py.')
    arrays = []
    for source in SOURCES:
        if prepared(source):
            a = np.load(path_for(source), mmap_mode='r')
        else:
            a = np.zeros((1, 1), dtype=np.int16)
            a.flags.writeable = False
        arrays.append(a)
    # A typed.List takes a shared reference-count lock on every pixel lookup.
    # An immutable tuple lets Numba inline reads without that parallel bottleneck.
    return tuple(arrays)


@lru_cache(maxsize=1)
def all_maps():
    return maps()
