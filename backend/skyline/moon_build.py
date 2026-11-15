"""Bounded-memory, resumable publication of a complete global horizon index."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from numba import get_num_threads, set_num_threads

from . import moon, moon_native, moon_surface

BLOCK_ROWS, BLOCK_COLS = 16, 64
RESERVE_BYTES = 2 * 1024 ** 3


def plan(spacing_km=1.5, spacing_deg=None, n_az=90):
    if spacing_deg is not None:
        if not math.isfinite(spacing_deg) or not .01 <= spacing_deg <= 10:
            raise ValueError('degree spacing must be between 0.01 and 10')
        rows = round(180 / spacing_deg)
        if abs(rows * spacing_deg - 180) > 1e-8:
            raise ValueError('degree spacing must divide 180')
    else:
        if not math.isfinite(spacing_km) or not .3 <= spacing_km <= 300:
            raise ValueError('kilometre spacing must be between 0.3 and 300')
        rows = math.ceil(math.pi * moon.RADIUS / (spacing_km * 1000))
    if n_az not in (90, 180, 360):
        raise ValueError('azimuths must be 90, 180 or 360')
    cols = rows * 2
    config = dict(version=moon.DB_VERSION, shape=[rows, cols], spacing_deg=180 / rows,
                  spacing_m=math.pi * moon.RADIUS / rows, positions=rows * cols, n_az=n_az,
                  terrain_model=moon_surface.specification(), index_method=moon_surface.INDEX_METHOD,
                  dem=moon.dem_path().name, block_shape=[BLOCK_ROWS, BLOCK_COLS])
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    return dict(config, signature=signature, bytes=rows * cols * (n_az * 4 + 2 * 8) + 256)


def write_json(path, data):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '-', suffix='.partial', delete=False) as handle:
        json.dump(data, handle, indent=2, allow_nan=False)
        pending = Path(handle.name)
    pending.replace(path)


def blocks(config):
    height, width = config['shape']
    for row in range(0, height, BLOCK_ROWS):
        for col in range(0, width, BLOCK_COLS):
            yield row, col, min(BLOCK_ROWS, height - row), min(BLOCK_COLS, width - col)


def pending_build(config):
    for path in sorted(moon.DB_DIR.glob('grid-*/progress.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            state = json.loads(path.read_text())
            if state['signature'] != config['signature'] or state.get('phase') == 'ready':
                continue
            generation = path.parent
            data = np.load(generation / 'horizons.npy', mmap_mode='r')
            pos = np.load(generation / 'positions.npy', mmap_mode='r')
            if data.shape != (config['positions'], config['n_az']) or data.dtype != np.float32:
                continue
            if pos.shape != (config['positions'], 2) or pos.dtype != np.float64:
                continue
            jobs = list(blocks(config))
            next_block = state['next_block']
            if not isinstance(next_block, int) or not 0 <= next_block <= len(jobs):
                continue
            if state['completed'] != sum(r * c for _, _, r, c in jobs[:next_block]):
                continue
            return generation, state
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def describe(config):
    pending = pending_build(config)
    allocated = 0
    if pending:
        allocated = sum((pending[0] / name).stat().st_blocks * 512 for name in ('horizons.npy', 'positions.npy'))
    available = shutil.disk_usage(moon.DB_DIR).free
    return dict(spacing_m=config['spacing_m'], positions=config['positions'], shape=config['shape'],
                raw_gib=config['bytes'] / 1024 ** 3, free_gib=available / 1024 ** 3,
                additional_gib=max(0, config['bytes'] - allocated) / 1024 ** 3,
                reserve_gib=RESERVE_BYTES / 1024 ** 3,
                resume_completed=pending[1]['completed'] if pending else 0,
                generation=pending[0].name if pending else None)


def build(config, source, status, max_minutes=20, workers=4):
    started = time.monotonic()
    deadline = started + max_minutes * 60
    moon.DB_DIR.mkdir(parents=True, exist_ok=True)
    pending = pending_build(config)
    allocation = describe(config)
    if allocation['free_gib'] < allocation['additional_gib'] + allocation['reserve_gib']:
        raise RuntimeError(f"Build needs {allocation['additional_gib']:.2f} GiB plus a 2 GiB reserve; only {allocation['free_gib']:.2f} GiB is free.")
    if pending:
        generation, state = pending
        data = np.load(generation / 'horizons.npy', mmap_mode='r+')
        pos = np.load(generation / 'positions.npy', mmap_mode='r+')
        next_block, completed = state['next_block'], state['completed']
        elapsed_before = state.get('elapsed_seconds', 0.)
        print(f'Resuming {generation.name}: {completed:,}/{config["positions"]:,} positions.', flush=True)
    else:
        generation = Path(tempfile.mkdtemp(prefix='grid-', dir=moon.DB_DIR))
        data = np.lib.format.open_memmap(generation / 'horizons.npy', mode='w+', dtype=np.float32,
                                       shape=(config['positions'], config['n_az']))
        pos = np.lib.format.open_memmap(generation / 'positions.npy', mode='w+', dtype=np.float64,
                                      shape=(config['positions'], 2))
        next_block, completed, elapsed_before = 0, 0, 0.
    previous_threads = get_num_threads()
    set_num_threads(min(previous_threads, workers, 4))

    def checkpoint(phase, reason=None):
        data.flush(); pos.flush()
        elapsed = elapsed_before + time.monotonic() - started
        write_json(generation / 'progress.json', dict(signature=config['signature'], config=config,
                   phase=phase, next_block=next_block, completed=completed, elapsed_seconds=elapsed))
        status(phase, completed, config['positions'], spacing_m=config['spacing_m'],
               generation=generation.name, reason=reason, elapsed_seconds=round(elapsed, 1))

    jobs = list(blocks(config)); height, width = config['shape']
    last_report = time.monotonic()
    checkpoint('index')
    try:
        while next_block < len(jobs):
            if time.monotonic() >= deadline - 2:
                checkpoint('paused', 'Time limit reached; rerun the same command to resume.')
                print(f'Time limit reached. Saved {completed:,}/{config["positions"]:,} positions. Rerun the same command to resume.', flush=True)
                return False
            row, col, nr, nc = jobs[next_block]
            rr, cc = np.meshgrid(np.arange(row, row + nr), np.arange(col, col + nc), indexing='ij')
            indices = (rr * width + cc).ravel()
            lats = 90 - (rr.ravel() + .5) * config['spacing_deg']
            lons = -180 + (cc.ravel() + .5) * config['spacing_deg']
            values = moon.proposal_horizons(lats, lons, config['n_az'])
            if not np.isfinite(values).all() or np.any(values <= -89):
                raise ValueError('Missing elevations; this partial index will not be published.')
            data[indices] = values
            pos[indices, 0], pos[indices, 1] = lats, lons
            completed += len(indices); next_block += 1
            if time.monotonic() - last_report >= 10:
                checkpoint('index')
                print(f'{completed:,}/{config["positions"]:,} positions ({100 * completed / config["positions"]:.1f}%), {time.monotonic() - started:.1f}s this pass', flush=True)
                last_report = time.monotonic()
                if shutil.disk_usage(moon.DB_DIR).free < RESERVE_BYTES:
                    checkpoint('paused', 'Disk reserve reached; free space before resuming.')
                    print('Paused to retain the 2 GiB disk reserve.', flush=True)
                    return False
        checkpoint('index')
        meta = {k: v for k, v in config.items() if k not in ('signature', 'bytes', 'block_shape')}
        meta.update(source=source, native_sources=[s.name for s in moon_native.SOURCES],
                    horizons=f'{generation.name}/horizons.npy', positions_file=f'{generation.name}/positions.npy',
                    max_range_m=float(moon_surface.PROPOSAL_RANGES[-1]))
        # Only a completed generation becomes visible to the running API.
        write_json(moon.DB_DIR / 'meta.json', meta)
        checkpoint('ready')
        print(f'Whole-Moon index ready: {config["positions"]:,} positions, {config["spacing_m"]:.1f} m maximum grid spacing.', flush=True)
        return True
    except KeyboardInterrupt:
        checkpoint('paused', 'Interrupted; rerun the same command to resume.')
        print('Saved progress. Rerun the same command to resume.', flush=True)
        return False
    except Exception:
        checkpoint('paused', 'Build stopped; the previous complete index remains active.')
        raise
    finally:
        set_num_threads(previous_threads)
