import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline.locate import Query
from skyline.match import Spectra, score_profiles
from skyline.moon_match import score, exact_score


class MoonMatchingTests(unittest.TestCase):
    def test_sparse_rays_keep_every_heading_phase_for_independent_photos(self):
        a = np.arange(360) * np.pi / 180
        h = (.6 * np.sin(5*a) + .4 * np.cos(11*a) + .2 * np.sin(17*a)).astype(np.float32)
        profiles = np.stack([h[::4], np.ones(90, np.float32)])
        queries = [Query(np.roll(h, -heading), np.ones(360, bool), 360) for heading in [17, 102, 279]]
        scores, headings = score(profiles, queries, 'ssd', [None] * 3)
        self.assertEqual(int(np.argmax(scores)), 0)
        self.assertGreater(scores[0], -1e-4)
        np.testing.assert_allclose(headings[0], [17, 102, 279], atol=.01)

    def test_masked_ssd_preserves_the_original_full_resolution_score(self):
        rng = np.random.default_rng(42)
        profiles = rng.normal(size=(12, 360)).astype(np.float32)
        mask = rng.random(360) > .3
        q = Query(rng.normal(size=360).astype(np.float32), mask, 360)
        expected = score_profiles(Spectra(profiles), q.q, q.m, 'ssd', heading_window=(18, 12))
        actual = score(profiles, [q], 'ssd', [(18, 12)])
        np.testing.assert_allclose(actual[0], expected[0], rtol=2e-6, atol=2e-6)
        np.testing.assert_allclose(actual[1][:, 0], expected[1], atol=.001)

    def test_phase_with_no_visible_samples_is_skipped(self):
        q = np.sin(np.arange(360) * np.pi / 45).astype(np.float32)
        mask = np.zeros(360, bool); mask[1::4] = True
        values, headings = score(q[1::4][None], [Query(q, mask, 90)], 'ssd', [None])
        self.assertTrue(np.isfinite(values).all() and np.isfinite(headings).all())
        self.assertGreater(values[0], -1e-4)

    def test_exact_comparison_fits_fractional_heading(self):
        a = np.arange(1440) / 4
        h = (.7*np.sin(a*np.pi/18) + .3*np.cos(a*np.pi/7)).astype(np.float32)
        q = np.interp(np.arange(360) + 37.375, a, h, period=360).astype(np.float32)
        _, headings = exact_score(h[None], [Query(q, np.ones(360, bool), 360)], 'ssd', [(37, 2)])
        self.assertAlmostEqual(float(headings[0, 0]), 37.375, delta=.02)


if __name__ == '__main__':
    unittest.main()
