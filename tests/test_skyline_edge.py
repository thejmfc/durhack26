import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline.extract import extract_skyline, extract_lunar_skyline, skyline_angles


class SkylineEdgeTests(unittest.TestCase):
    def test_lunar_edge_tracks_a_dim_ridge_above_bright_foreground(self):
        for height in [25., 25.2, 25.5, 25.8]:
            coverage = np.clip(np.arange(80) + 1 - height, 0, 1)
            image = np.tile((.025 * coverage)[:, None], (1, 64)).astype(np.float32)
            image[50:] = .8
            rows, valid = extract_lunar_skyline(image)
            self.assertTrue(valid.all())
            np.testing.assert_allclose(rows, height, atol=.1)

    def test_black_shadow_gap_is_unmeasured_instead_of_a_false_valley(self):
        image = np.zeros((80, 80), np.float32)
        image[25:] = .3
        image[25:52, 20:50] = 0
        rows, valid = extract_lunar_skyline(image)
        self.assertFalse(valid[20:50].any())
        self.assertTrue(valid[:20].all() and valid[50:].all())
        np.testing.assert_allclose(rows[valid], 25, atol=.1)

    def test_sensor_noise_and_isolated_bright_sky_pixels_are_not_skylines(self):
        rng = np.random.default_rng(19)
        image = np.clip(rng.normal(0, .005, (120, 120)), 0, 1).astype(np.float32)
        image[15, 30] = 1
        _, valid = extract_lunar_skyline(image)
        self.assertFalse(valid.any())
        image[60:] += .08
        rows, valid = extract_lunar_skyline(image)
        self.assertGreater(valid.mean(), .95)
        self.assertLess(float(np.sqrt(np.mean((rows[valid] - 60) ** 2))), .15)

    def test_lunar_dark_and_top_clipped_frames_are_unmeasured(self):
        for image in [np.zeros((64, 16), np.float32), np.ones((64, 16), np.float32)]:
            _, valid = extract_lunar_skyline(image)
            self.assertFalse(valid.any())

    def test_pitched_camera_projection_recovers_a_known_horizon(self):
        # Project independent 3-D directions into pixel centres, then invert.
        w, h, fov, pitch = 800, 600, 66., 17.
        f = w / 2 / np.tan(np.radians(fov) / 2)
        cols = np.arange(w)
        x = (cols + .5 - w / 2) / f
        p = np.radians(pitch)
        # A level world horizon has camera vertical coordinate -tan(pitch).
        rows = np.full(w, h / 2 + f * np.tan(p))
        az, el = skyline_angles(rows, np.ones(w, bool), w, h, fov, pitch)
        np.testing.assert_allclose(el, 0, atol=1e-10)
        np.testing.assert_allclose(az, np.degrees(np.arctan2(x, 1 / np.cos(p))), atol=1e-10)

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
