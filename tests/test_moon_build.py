import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from skyline import moon, moon_build


class MoonBuildTests(unittest.TestCase):
    def test_resume_space_check_without_block_allocation_metadata(self):
        config = dict(spacing_m=1500, positions=2, shape=[1, 2], bytes=100)
        fake_stat = SimpleNamespace(st_size=100)
        with patch.object(moon_build, 'pending_build', return_value=(Path('generation'), {'completed': 1})), \
                patch.object(Path, 'stat', return_value=fake_stat), \
                patch.object(moon_build.shutil, 'disk_usage', return_value=SimpleNamespace(free=1000)):
            result = moon_build.describe(config)
        self.assertEqual(result['resume_completed'], 1)
        self.assertEqual(result['additional_gib'], 100 / 1024 ** 3)

    def test_continuous_build_publishes_completed_index(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(shape=[1, 2], positions=2, n_az=90, spacing_deg=180,
                          spacing_m=1000, signature='test', bytes=752)
            phases = []
            with patch.object(moon, 'DB_DIR', Path(directory)), \
                    patch.object(moon_build, 'describe', return_value={'free_gib': 100, 'additional_gib': 0, 'reserve_gib': 2}), \
                    patch.object(moon, 'proposal_horizons', return_value=np.zeros((2, 90), dtype=np.float32)):
                complete = moon_build.build(config, {}, lambda phase, *args, **kwargs: phases.append(phase), None, 1)
            self.assertTrue(complete)
            self.assertEqual(phases[-1], 'ready')
            self.assertTrue((Path(directory) / 'meta.json').is_file())


if __name__ == '__main__':
    unittest.main()
