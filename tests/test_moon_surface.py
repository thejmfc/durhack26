"""Regressions for the shared mesh, close slopes and stale global generations."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline import moon, moon_surface as surface, moon_native
from skyline.moon_detail import sample, source_at


class SharedSurfaceTests(unittest.TestCase):
    @unittest.skipUnless(moon_native.ready(), 'Native global elevation cache is not installed')
    def test_real_native_sources_match_mesh_sampling_everywhere(self):
        maps = moon_native.all_maps()
        rng = np.random.default_rng(20261114)
        for i, source in enumerate(moon_native.SOURCES):
            lat = (rng.uniform(60, 90, 8) * source.pole if source.pole else
                   rng.uniform(source.north - 29.999, source.north - .001, 8))
            lon = (rng.uniform(-180, 180, 8) if source.pole else
                   rng.uniform(source.west + .001, source.west + 44.999, 8))
            expected = sample(maps[i], source, lat, lon)
            actual = [surface.native_height(maps, float(a), float(b)) for a, b in zip(lat, lon)]
            np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=0, err_msg=source.name)

    def test_flat_mesh_has_the_spherical_horizon(self):
        grid = np.zeros((len(surface.STEPS), 257, 257), np.float32)
        hz = surface.patch_horizons(grid, np.asarray(surface.STEPS, float), 72)
        expected = -math.degrees(math.acos(surface.RADIUS / (surface.RADIUS + surface.OBSERVER_HEIGHT)))
        np.testing.assert_allclose(hz, expected, atol=.0001)

    def test_ridge_inside_old_100_m_blind_zone(self):
        grid = np.zeros((len(surface.STEPS), 257, 257), np.float32)
        grid[0, 127, 128] = 20
        hz = surface.patch_horizons(grid, np.asarray(surface.STEPS, float), 4)
        up = (surface.RADIUS + 20) / math.sqrt(1 + (60 / surface.RADIUS) ** 2)
        expected = math.degrees(math.atan2(up - surface.RADIUS - 1.7, up * 60 / surface.RADIUS))
        self.assertAlmostEqual(hz[0], expected, places=5)
        self.assertGreater(hz[0], 16)

    def test_diagonal_edge_can_be_the_horizon_peak(self):
        grid = np.zeros((len(surface.STEPS), 257, 257), np.float32)
        grid[0, 128, 129] = grid[0, 129, 128] = 20
        hz = surface.patch_horizons(grid, np.asarray(surface.STEPS, float), 8)
        # At 135 degrees the ray crosses the raised b--d triangle edge halfway
        # through the cell. Its next horizontal/vertical grid edge is at height 0.
        up = (surface.RADIUS + 20) / math.sqrt(1 + (60 / surface.RADIUS) ** 2)
        expected = math.degrees(math.atan2(up - surface.RADIUS - 1.7, up * math.sqrt(1800) / surface.RADIUS))
        self.assertAlmostEqual(hz[3], expected, places=5)

    def test_native_lookup_uses_same_sources_scale_and_projection_as_mesh(self):
        # Small arrays are enough: choose coordinates landing in their first
        # pixels, including both stereographic caps and a longitude seam.
        raw = np.arange(64, dtype=np.int16).reshape(8, 8) * 7 - 400
        raw.flags.writeable = False
        maps = tuple(raw for _ in range(34))
        for lat, lon in [(29.997, .004), (-.003, -44.996), (-59.997, 135.004), (60, 0), (-60, 0), (90, 0)]:
            expected = sample(raw, source_at(lat, lon), np.array([lat]), np.array([lon]))[0]
            self.assertAlmostEqual(surface.native_height(maps, lat, lon), expected, places=7)

    def test_old_surface_is_rejected_and_new_generation_reloads(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(moon, 'DB_DIR', Path(tmp)), patch('skyline.moon_native.ready', return_value=True):
            directory = Path(tmp)
            np.save(directory / 'p.npy', np.array([[0., 0.]]))
            np.save(directory / 'a.npy', np.zeros((1, 90), np.float32))
            np.save(directory / 'b.npy', np.ones((1, 90), np.float32))
            meta = dict(version=3, dem=moon.dem_path().name, horizons='a.npy', positions_file='p.npy')
            (directory / 'meta.json').write_text(json.dumps(meta))
            self.assertFalse(moon.database_ready())
            meta.update(version=moon.DB_VERSION, terrain_model=surface.specification(), index_method=surface.INDEX_METHOD)
            (directory / 'meta.json').write_text(json.dumps(meta))
            self.assertTrue(moon.database_ready())
            self.assertEqual(moon.database()[1][0, 0], 0)
            meta['horizons'] = 'b.npy'
            (directory / 'meta.json').write_text(json.dumps(meta))
            self.assertEqual(moon.database()[1][0, 0], 1)
        moon._load_database.cache_clear()


if __name__ == '__main__':
    unittest.main()
