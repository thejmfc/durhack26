import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline import moon
from skyline.locate import Query


class ExactRefinementTests(unittest.TestCase):
    def test_exact_grid_escapes_a_false_proposal_minimum(self):
        def synthetic_horizons(lats, lons, n_az=360):
            x = np.radians(np.asarray(lons)) * moon.RADIUS
            y = np.radians(np.asarray(lats)) * moon.RADIUS
            value = np.minimum(.3 + (x*x + y*y) / 1e6, ((x + 260)**2 + (y - 160)**2) / 1e6)
            return np.repeat(value[:, None], n_az, axis=1).astype(np.float32)
        q = Query(np.zeros(360, np.float32), np.ones(360, bool), 360)
        with patch.object(moon, 'horizons', side_effect=synthetic_horizons):
            lat, lon = moon.refine_surface(0., 0., [q], 'ssd', [None])
        x, y = np.radians([lon, lat]) * moon.RADIUS
        self.assertLess(np.hypot(x + 260, y - 160), 3)


if __name__ == '__main__':
    unittest.main()
