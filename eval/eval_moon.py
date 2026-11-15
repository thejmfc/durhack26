"""Broad, blind whole-Moon evaluation; no truth coordinates enter retrieval.

By default this samples unseen locations across equal-area latitude bands and
random headings. Ideal mesh horizons isolate retrieval from image extraction;
their timing excludes rendering/capture and is only a lower bound on total time.
Photo fixtures include decoding/extraction in the timer but exclude capture.
Rejected, missing and errored results all count as failures. The 500 m / 30 s
thresholds are acceptance criteria, never search hints or early-stop budgets.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from skyline import moon
from skyline.locate import Query, build_query, load_gray

CASES = [('tycho', -43.3, -11.2, 31), ('equator', 0., 0., 73),
         ('far_side', 20., 155., 147), ('north_pole', 89.9, 30., 213)]


def broad_cases(count, seed):
    rng = np.random.default_rng(seed)
    # Stratifying sin(latitude) gives equal surface areas, including both
    # hemispheres. Longitude and heading remain independent random variables.
    z = -1 + 2 * (np.arange(count) + rng.random(count)) / count
    latitudes = np.degrees(np.arcsin(z))
    longitudes = rng.uniform(-180, 180, count)
    headings = rng.uniform(0, 360, count)
    order = rng.permutation(count)
    return [(f'random_{i:03d}', float(latitudes[i]), float(longitudes[i]), float(headings[i])) for i in order]


def summarize(records, expected, max_error, max_seconds):
    errors = [r['error_m'] for r in records if r.get('error_m') is not None]
    durations = [r['seconds'] for r in records]
    passed = sum(r['passes_measured_targets'] for r in records)
    return dict(requested=expected, completed=len(records), complete=len(records) == expected,
                error_target_m=max_error, time_target_s=max_seconds,
                accepted=sum(r.get('accepted', False) for r in records),
                passes_measured_targets=passed, failures=len(records) - passed,
                success_fraction=passed / len(records) if records else 0.,
                error_m_percentiles=dict(zip(('p50', 'p90', 'p95', 'max'), np.percentile(errors, [50, 90, 95, 100]).tolist())) if errors else None,
                seconds_percentiles=dict(zip(('p50', 'p90', 'p95', 'max'), np.percentile(durations, [50, 90, 95, 100]).tolist())) if durations else None,
                end_to_end_target_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', choices=[c[0] for c in CASES], help='legacy named diagnostic; not the broad benchmark')
    parser.add_argument('--photos', type=Path, help='JSON fixtures with camera settings, photo files and evaluation coordinates')
    parser.add_argument('--samples', type=int, default=32)
    parser.add_argument('--seed', type=int, default=2026111503)
    parser.add_argument('--max-error-m', type=float, default=500.)
    parser.add_argument('--max-seconds', type=float, default=30.)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.samples < 1 or args.max_error_m <= 0 or args.max_seconds <= 0:
        parser.error('samples and acceptance thresholds must be positive')
    if args.photos and args.case:
        parser.error('--case and --photos describe different evaluation inputs')
    if not moon.database_ready():
        raise SystemExit('The synchronized whole-Moon index is not ready.')
    records = []
    fixtures = json.loads(args.photos.read_text()) if args.photos else None
    if fixtures is not None and not fixtures:
        parser.error('photo fixtures must contain at least one case')
    cases = ([(f['name'], f['lat'], f['lon'], f['heading']) for f in fixtures] if fixtures is not None
             else [c for c in CASES if c[0] in args.case] if args.case
             else broad_cases(args.samples, args.seed))
    report = dict(test='photo fixtures, global and blind' if fixtures is not None else 'ideal horizons, global and blind',
                  suite='fixtures' if fixtures is not None else 'legacy diagnostics' if args.case else 'equal-area random locations',
                  seed=None if fixtures is not None or args.case else args.seed,
                  timing_scope='decode, extract and search; excludes capture and HTTP' if fixtures is not None else 'search only; excludes rendering and capture',
                  grid=moon.database()[0], records=records)

    def save():
        report['summary'] = summarize(records, len(cases), args.max_error_m, args.max_seconds)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            pending = args.output.with_suffix(args.output.suffix + '.partial')
            pending.write_text(json.dumps(report, indent=2, allow_nan=False))
            pending.replace(args.output)

    save()
    for case_index, (name, lat, lon, heading) in enumerate(cases):
        record = dict(case=name, truth=dict(lat=lat, lon=lon, heading=heading), error_m=None,
                      accepted=False, passes_measured_targets=False)
        start = time.monotonic()
        try:
            if fixtures is None:
                # Synthetic construction is outside search timing. A fine
                # mesh also supports non-integer, unseen headings.
                h = moon.horizons([lat], [lon], 1440)[0]
                q = np.interp(np.arange(360) + heading, np.arange(1440) / 4, h, period=360)
                query = Query(q.astype(np.float32), np.ones(360, bool), 360)
                start = time.monotonic()
            else:
                f = fixtures[case_index]
                query = build_query([load_gray((ROOT / path).read_bytes()) for path in f['files']],
                                    f['hfov'], f.get('pitch', 0), f.get('yaws'), lunar=True)
            result = moon.locate([query])  # No coordinates, heading hint or local prior.
            elapsed = time.monotonic() - start
            candidates = result.get('candidates', [])
            record.update(timings=result.get('timings'), quality=result.get('quality'), candidates=[])
            if candidates:
                distances = moon.distance_m(lat, lon, np.array([c['lat'] for c in candidates]), np.array([c['lon'] for c in candidates]))
                accepted = not result.get('quality', {}).get('weak', True)
                record.update(error_m=float(distances[0]), best_of_top5_m=float(distances.min()),
                              accepted=accepted,
                              passes_measured_targets=bool(accepted and distances[0] < args.max_error_m and elapsed < args.max_seconds),
                              heading_error_deg=abs((candidates[0]['headings'][0] - heading + 180) % 360 - 180),
                              candidates=[dict(lat=c['lat'], lon=c['lon'], error_m=float(d), loglik=c['loglik']) for c, d in zip(candidates, distances)])
        except Exception as exc:
            elapsed = time.monotonic() - start
            record['failure'] = f'{type(exc).__name__}: {exc}'
        record['seconds'] = round(elapsed, 4)
        records.append(record)
        print(json.dumps(record, allow_nan=False), flush=True)
        save()
    print(json.dumps(report['summary'], allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
