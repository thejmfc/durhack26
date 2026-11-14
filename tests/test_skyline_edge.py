import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline.extract import extract_skyline


class SkylineEdgeTests(unittest.TestCase):
    def test_boundary_does_not_move_with_terrain_brightness(self):
        for height in [32., 32.2, 32.5, 32.8]:
            coverage = np.clip(np.arange(64) + 1 - height, 0, 1)
            for brightness in [.2, .4, .8]:
                image = np.tile((brightness * coverage)[:, None], (1, 16)).astype(np.float32)
                rows, valid = extract_skyline(image, subpixel=True)
                self.assertTrue(valid.all())
                np.testing.assert_allclose(rows, height, atol=.1)

    def test_dark_and_top_clipped_frames_do_not_invent_a_horizon(self):
        for image in [np.zeros((64, 16), np.float32), np.ones((64, 16), np.float32)]:
            _, valid = extract_skyline(image, subpixel=True)
            self.assertFalse(valid.any())


if __name__ == '__main__':
    unittest.main()
