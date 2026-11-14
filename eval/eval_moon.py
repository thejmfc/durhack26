"""Blind whole-Moon retrieval from ideal mesh horizons (not a photo accuracy test).

This isolates global retrieval from image extraction. Locations and headings are
used to generate queries and score errors; they are never passed into locate().
Run with backend/.venv/bin/python eval/eval_moon.py after build_moon.py completes.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append')
    parser.add_argument('--photos', type=Path, help='JSON fixtures with camera settings, photo files and evaluation coordinates')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not moon.database_ready():
        raise SystemExit('The synchronized whole-Moon index is not ready.')
    records = []
    fixtures = json.loads(args.photos.read_text()) if args.photos else None
    cases = [(f['name'], f['lat'], f['lon'], f['heading']) for f in fixtures] if fixtures else CASES
    for case_index, (name, lat, lon, heading) in enumerate(cases):
        if args.case and name not in args.case:
            continue
        if fixtures:
            f = fixtures[case_index]
            query = build_query([load_gray((ROOT / path).read_bytes()) for path in f['files']],
                                f['hfov'], f.get('pitch', 0), f.get('yaws'), subpixel=True)
        else:
            h = moon.horizons([lat], [lon])[0]
            query = Query(np.roll(h, -heading), np.ones(360, bool), 360)
        start = time.monotonic()
        result = moon.locate([query])  # No coordinates, heading hint or local prior.
        candidates = result['candidates']
        distances = moon.distance_m(lat, lon, np.array([c['lat'] for c in candidates]), np.array([c['lon'] for c in candidates]))
        record = dict(case=name, truth=dict(lat=lat, lon=lon, heading=heading),
                      seconds=round(time.monotonic() - start, 2),
                      error_m=float(distances[0]), best_of_top5_m=float(distances.min()),
                      heading_error_deg=abs((candidates[0]['headings'][0] - heading + 180) % 360 - 180),
                      quality=result['quality'],
                      candidates=[dict(lat=c['lat'], lon=c['lon'], error_m=float(d), loglik=c['loglik']) for c, d in zip(candidates, distances)])
        records.append(record)
        print(json.dumps(record), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(dict(test='rendered photos, global and blind' if fixtures else 'ideal horizons, global and blind', grid=moon.database()[0], records=records), indent=2))


if __name__ == '__main__':
    main()
