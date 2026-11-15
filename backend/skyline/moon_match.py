"""Bounded-memory skyline scoring, including every coarse heading phase."""
from __future__ import annotations

import numpy as np
from scipy import fft

from .match import DEFAULT_CALIB, Spectra, score_profiles


def score(horizons, queries, method, heading_windows, clip=None):
    """Compare full queries with sparse stored rays without dropping headings.

    A 90-ray index requires four phases of a 360-bin query. Each independent
    photograph chooses its own phase and heading. Equal sample weighting keeps
    a phase with fewer visible bins from winning simply by ignoring more data.
    """
    azimuths = horizons.shape[1]
    prepared = []
    for query, window in zip(queries, heading_windows):
        stride = len(query.q) // azimuths
        if stride < 1 or len(query.q) % azimuths:
            raise ValueError('Query resolution must be a multiple of horizon resolution')
        expected = float(query.m.sum()) / stride
        phases = []
        for phase in range(stride):
            mask = query.m[phase::stride].astype(np.float32)
            count = float(mask.sum())
            if not count:
                continue
            q = query.q[phase::stride]
            if clip is not None:
                q = np.clip(q, -clip, clip)
            offset = phase * 360 / len(query.q)
            shifted = (window[0] + offset, window[1]) if window is not None else None
            phases.append((q, mask, count, expected / count, offset, shifted,
                           np.conj(fft.rfft(mask)), np.conj(fft.rfft(q * mask)), float(np.sum(q * q * mask))))
        if not phases:
            raise ValueError('A query must contain visible skyline samples')
        prepared.append(phases)
    scores = np.empty(len(horizons), np.float32)
    headings = np.empty((len(horizons), len(queries)), np.float32)
    scale = .5 / DEFAULT_CALIB['rho'] / DEFAULT_CALIB['sigma'] ** 2
    for start in range(0, len(horizons), 4096):
        block = np.asarray(horizons[start:start + 4096], np.float32)
        if clip is not None:
            block = np.clip(block, -clip, clip)
        rows = np.arange(len(block))
        total = np.zeros(len(block), np.float32)
        # SSD's two correlations can be combined BEFORE the inverse transform.
        # One worker is faster for these short transforms and avoids competing
        # with terrain workers for every 4,096-position batch.
        if method == 'ssd':
            fh = fft.rfft(block, axis=1, workers=1)
            fh2 = fft.rfft(block * block, axis=1, workers=1)
        else:
            spectra = Spectra(block)
        for j, phases in enumerate(prepared):
            best = np.full(len(block), -np.inf, np.float32)
            heading = np.zeros(len(block), np.float32)
            for q, mask, count, weight, offset, window, fm, fq, q2 in phases:
                if method == 'ssd':
                    squared = np.maximum(fft.irfft(fh2 * fm - 2 * fh * fq, n=azimuths, axis=1, workers=1) + q2, 0)
                    ll = -squared * (scale * weight)
                    if window is not None:
                        delta = (np.arange(azimuths) * 360 / azimuths - window[0] + 180) % 360 - 180
                        ll[:, np.abs(delta) > window[1]] = -np.inf
                    index = np.argmax(ll, axis=1)
                    value = ll[rows, index]
                    lo, hi = ll[rows, (index - 1) % azimuths], ll[rows, (index + 1) % azimuths]
                    denominator = lo - 2 * value + hi
                    with np.errstate(invalid='ignore', divide='ignore'):
                        fraction = np.where(np.isfinite(denominator) & (denominator < -1e-9), .5 * (lo - hi) / denominator, 0.)
                    angles = (index + np.clip(fraction, -.5, .5)) * 360 / azimuths
                else:
                    value, angles, _, _ = score_profiles(spectra, q, mask, method, heading_window=window)
                    value *= weight
                better = value > best
                best[better] = value[better]
                heading[better] = (angles[better] - offset) % 360
            total += best
            headings[start:start + len(block), j] = heading
        scores[start:start + len(block)] = total
    return scores, headings


def exact_score(horizons, queries, method, heading_windows):
    """Evaluate a quarter-degree heading grid against each original query bin.

    Sparse masks retain the actual 1-degree measurements; interpolating the
    query into extra measurements would artificially increase its weight.
    """
    from .locate import Query
    size = max(1440, horizons.shape[1])
    if horizons.shape[1] != size:
        x = np.arange(horizons.shape[1]) * size / horizons.shape[1]
        horizons = np.asarray([np.interp(np.arange(size), x, h, period=size) for h in horizons], np.float32)
    sparse = []
    for query in queries:
        q = np.zeros(size, np.float32)
        mask = np.zeros(size, bool)
        step = size // len(query.q)
        q[::step], mask[::step] = query.q, query.m
        sparse.append(Query(q, mask, query.n_expected))
    return score(horizons, sparse, method, heading_windows)
