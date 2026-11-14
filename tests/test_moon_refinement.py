import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline import moon
from skyline.locate import Query


class GlobalRefinementTests(unittest.TestCase):
    def test_capped_retrieval_recovers_shape_under_changed_foreground_height(self):
        angle = np.arange(360) * 2 * np.pi / 360
        photo = (.4 * np.sin(3 * angle) + .2 * np.cos(7 * angle)).astype(np.float32)
        photo[:40] += 20
        nearby = photo.copy(); nearby[:40] -= 8
        false_match = photo + .2
        profiles = np.stack([nearby, false_match])
        mask = np.ones(360, bool)
        raw, _ = moon.score(profiles, [Query(photo, mask, 360)], 'ssd', [None])
        capped, _ = moon.score(profiles, [Query(np.clip(photo, -1, 1), mask, 360)], 'ssd', [None], clip=1.)
        self.assertGreater(raw[1], raw[0])
        self.assertGreater(capped[0], capped[1])
        np.testing.assert_array_equal(profiles[0], nearby)

    def test_coarse_loser_keeps_its_chance_to_win_after_fine_sampling(self):
        target = (10.0082, 10.0041)
        query = Query(np.zeros(360, np.float32), np.ones(360, bool), 360)
        def terrain(lats, lons, n_az=360):
            # Region one is a consistently plausible false match. Region two
            # looks worse at kilometre spacing but has the true narrow optimum.
            residual = np.where(lats < 5, .1, np.minimum(1, moon.distance_m(*target, lats, lons) / 1000))
            return np.repeat(residual[:, None], n_az, axis=1).astype(np.float32)
        with patch.object(moon, 'proposal_horizons', side_effect=terrain):
            lats, lons, scores, _ = moon.refine_regions(np.array([0., 10.]), np.array([0., 10.]), [query], 'ssd', [None], 3790.)
        self.assertEqual(len(lats), 2, 'both global regions must survive until fine sampling')
        self.assertLess(moon.distance_m(*target, lats[1], lons[1]), 5.)
        self.assertGreater(scores[1], scores[0])


if __name__ == '__main__':
    unittest.main()
