"""Spherical geometry regressions. Run: scripts/.venv/bin/python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline import moon


class MoonGeometryTests(unittest.TestCase):
    def test_flat_sphere_horizon_at_equator_seam_and_poles(self):
        dem = np.zeros((36, 72), np.float32)
        ranges = np.geomspace(100, 100000, 50)
        lat = np.array([0, 45, -45, 89.99, -89.99, 90, -90], dtype=float)
        lon = np.array([0, 180, -180, 17, -173, 0, 150], dtype=float)
        result = moon.compute_moon_horizons(dem, lat, lon, ranges, 36)
        angle = ranges / moon.RADIUS
        expected = np.degrees(np.arctan2(moon.RADIUS * np.cos(angle) - moon.RADIUS - moon.OBSERVER_HEIGHT,
                                        moon.RADIUS * np.sin(angle))).max()
        self.assertTrue(np.isfinite(result).all())
        np.testing.assert_allclose(result, expected, atol=1e-5)

    def test_height_wraps_and_poles_have_one_height(self):
        dem = np.arange(36 * 72, dtype=np.float32).reshape(36, 72)
        for lat in [-90, -89.9, -30, 0, 40, 89.9, 90]:
            self.assertAlmostEqual(moon.height_at(dem, lat, 180), moon.height_at(dem, lat, -180))
        for lat in [-90, 90]:
            vals = [moon.height_at(dem, lat, lon) for lon in [-180, -90, 0, 90, 180]]
            np.testing.assert_allclose(vals, vals[0])

    def test_triangle_sampling_matches_rendered_diagonal(self):
        dem = np.zeros((4, 8), np.float32)
        dem[1:3, 3:5] = [[0, 10], [20, 40]]
        for fr, fc, expected in [(0.2, 0.3, 7), (0.8, 0.7, 28)]:
            lat = 90 - (1 + fr + 0.5) / 4 * 180
            lon = (3 + fc + 0.5) / 8 * 360 - 180
            self.assertAlmostEqual(moon.height_at(dem, lat, lon), expected)

    def test_distance_uses_short_path_across_date_line(self):
        self.assertAlmostEqual(moon.distance_m(0, 179.9, 0, -179.9), moon.RADIUS * np.radians(0.2), places=5)
        self.assertLess(moon.distance_m(90, 0, 90, 180), 1e-8)

    def test_refinement_crosses_poles_without_invalid_coordinates(self):
        for lat, lon in [(89.99, 179.99), (-89.99, -179.99), (0, 180)]:
            lats, lons = moon.neighbourhood(lat, lon, 1000, 4)
            self.assertTrue(np.isfinite(lats).all() and np.isfinite(lons).all())
            self.assertTrue(np.all(np.abs(lats) <= 90) and np.all(np.abs(lons) <= 180))
            self.assertLess(moon.distance_m(lat, lon, lats, lons).max(), 5660)
            self.assertLess(moon.distance_m(lat, lon, lats[40], lons[40]), 1e-4)


if __name__ == '__main__':
    unittest.main()
