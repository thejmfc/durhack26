import sys
import unittest
from pathlib import Path

import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline.moon_detail import Source, coordinates, sample, source_at


class MeasuredTerrainTests(unittest.TestCase):
    def test_source_coverage_and_longitude_wrap(self):
        for lat in (-90, -60, -59.999, -30, 0, 30, 59.999, 60, 90):
            for lon in (-180, -45, 0, 45, 180):
                self.assertEqual(source_at(lat, lon), source_at(lat, lon + 360))
        self.assertEqual(source_at(-43.3, -11.2).name, 'SLDEM2015_512_60S_30S_315_360.JP2')
        self.assertEqual(source_at(0, 0).north, 30)
        self.assertEqual(source_at(-60, 0).pole, -1)
        self.assertEqual(source_at(60, 0).pole, 1)

    def test_polar_projection_and_orientation(self):
        for pole in (-1, 1):
            source = source_at(pole * 90, 0)
            r, c = source.pixels(pole * 90, 0)
            self.assertEqual((r, c), (15519.5, 15519.5))
            r, c = source.pixels(pole * 89, 0)
            self.assertEqual(c, 15519.5)
            self.assertGreater((r - 15519.5) * pole, 0)
            r, c = source.pixels(pole * 89, 90)
            self.assertAlmostEqual(r, 15519.5)
            self.assertGreater(c, 15519.5)

    def test_tangent_grid_poles_and_date_line(self):
        for lat, lon in ((0, 180), (90, 30), (-90, -70), (43, 120)):
            lats, lons = coordinates(lat, lon, 60, n=5)
            self.assertTrue(np.isfinite(lats).all() and np.isfinite(lons).all())
            self.assertAlmostEqual(lats[2, 2], lat)
            self.assertTrue(np.all(np.abs(lats) <= 90))
            p, q = np.radians(lats), np.radians(lons)
            xyz = np.stack([np.cos(p) * np.cos(q), np.cos(p) * np.sin(q), np.sin(p)], -1) * 1737400
            distances = np.linalg.norm(np.diff(xyz, axis=1), axis=-1)
            np.testing.assert_allclose(distances, 60, atol=.001)

    def test_signed_elevation_half_metre_scale_and_missing_data(self):
        source = Source('test', north=0, west=0)
        raw = np.array([[-400, -200], [200, 400]], dtype=np.int16)
        with MemoryFile() as mem:
            with mem.open(driver='GTiff', count=1, width=2, height=2, dtype='int16', transform=from_origin(0, 0, 1 / 512, 1 / 512)) as ds:
                ds.write(raw, 1)
                values = sample(ds, source, np.array([-.5, -1, -1.5]) / 512, np.array([.5, 1, 1.5]) / 512)
                np.testing.assert_allclose(values, [-200, 0, 200])
                raw[0, 0] = -32768
                ds.write(raw, 1)
                with self.assertRaises(ValueError):
                    sample(ds, source, np.array([-.5]) / 512, np.array([.5]) / 512)


if __name__ == '__main__':
    unittest.main()
