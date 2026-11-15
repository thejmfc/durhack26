"""Navigation to prepared positions in the currently published global index.

These coordinates are only for selecting a capture location. The image matcher
does not consume checkpoint IDs, coordinates or their stored horizon profiles.
"""
import math
import numpy as np

from . import moon


def nearby(lat, lon, radius_m=50000, limit=625):
    meta, _, positions = moon.database()
    height, width = meta['shape']
    spacing = meta['spacing_deg']
    col = int(math.floor((lon + 180) / spacing)) % width
    # For a fixed grid meridian, the nearest spherical latitude is slightly
    # poleward of the requested latitude. This also handles poles and wrapping.
    longitude = float(positions[col, 1])
    delta = math.radians((longitude - lon + 180) % 360 - 180)
    phi = math.radians(lat)
    optimum = math.degrees(math.atan2(math.sin(phi), math.cos(phi) * math.cos(delta)))
    row = min(height - 1, max(0, int(math.floor((90 - optimum) / spacing))))
    nearest_id = row * width + col
    nearest = positions[nearest_id]
    step_m = math.radians(spacing) * moon.RADIUS
    half = min(12, max(1, math.ceil(radius_m / step_m)))
    ids = {nearest_id}
    for r in range(max(0, row - half), min(height, row + half + 1)):
        latitude = float(positions[r * width, 0])
        # Thin only the visual markers around the poles. Every displayed
        # position is still a real checkpoint; nearest-point snapping is exact.
        stride = max(1, round(1 / max(1e-6, math.cos(math.radians(latitude)))))
        for offset in range(-half, half + 1):
            ids.add(r * width + (col + offset * stride) % width)
    ids = np.asarray(sorted(ids), np.int64)
    points = positions[ids]
    distances = moon.distance_m(lat, lon, points[:, 0], points[:, 1])
    keep = np.argsort(distances)
    keep = keep[(distances[keep] <= max(radius_m, float(moon.distance_m(lat, lon, *nearest))))][:limit]
    return dict(generation=meta['horizons'], spacing_m=step_m,
                nearest=dict(id=nearest_id, lat=float(nearest[0]), lon=float(nearest[1]),
                             distance_m=float(moon.distance_m(lat, lon, *nearest))),
                points=[dict(id=int(ids[i]), lat=float(points[i, 0]), lon=float(points[i, 1])) for i in keep])
