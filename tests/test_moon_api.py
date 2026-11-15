import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException, UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app as api
from skyline.locate import Query
from skyline.moon_surface import MODEL_ID
from scripts import build_moon


class GlobalApiTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows build locking')
    def test_second_build_cannot_share_windows_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            holder = subprocess.Popen(
                [sys.executable, '-c', "import msvcrt, sys; f=open(sys.argv[1], 'w+'); f.write('0'); f.flush(); f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); print('ready', flush=True); sys.stdin.readline()", str(Path(directory) / 'build.lock')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'ready')
                runner = "import runpy, sys; from pathlib import Path; sys.path.insert(0, 'backend'); from skyline import moon; moon.DB_DIR=Path(sys.argv[1]); sys.argv=['build_moon.py', '--index-only']; runpy.run_path('scripts/build_moon.py', run_name='__main__')"
                result = subprocess.run([sys.executable, '-c', runner, directory], capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Could not lock the Moon build', result.stderr)
            finally:
                holder.communicate(input='\n', timeout=10)

    def test_build_failure_is_published_and_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(build_moon.moon, 'DB_DIR', Path(directory)), patch.object(build_moon, '_build_started', True), patch.object(build_moon, 'main', side_effect=RuntimeError('test failure')):
                with self.assertRaisesRegex(RuntimeError, 'test failure'):
                    build_moon.run_build()
                status = json.loads((Path(directory) / 'build.json').read_text())
                self.assertEqual(status['phase'], 'failed')
                self.assertEqual(status['message'], 'test failure')

    def test_failed_build_remains_visible_but_stale_progress_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / 'build.json'
            with patch.object(api.moon, 'DB_DIR', Path(directory)), patch.object(api.moon, 'database_ready', return_value=False), patch.object(api.moon_native, 'SOURCES', ()):
                status.write_text(json.dumps(dict(phase='failed', updated=0, message='disk full')))
                self.assertEqual(api.moon_meta()['build']['message'], 'disk full')
                status.write_text(json.dumps(dict(phase='index', updated=0)))
                self.assertIsNone(api.moon_meta()['build'])

    def test_analysis_returns_reference_without_a_ready_search_database(self):
        with patch.object(api.moon, 'database_ready', return_value=False), patch.object(api, 'load_gray', return_value=np.zeros((8, 8))), patch.object(api.moon, 'horizons', return_value=np.zeros((1, 1440))), patch.object(api.moon, 'locate') as search:
            result = api.moon_analyse(json.dumps(dict(terrain_model=MODEL_ID, truth=dict(lat=0, lon=0, heading=0))), [UploadFile(io.BytesIO(b'dark'))])
        search.assert_not_called()
        self.assertEqual(len(result['reference_horizon']), 1440)
        self.assertEqual(result['images'][0]['skyline'], [])
        self.assertIn('error', result)

    def test_stale_render_cannot_silently_match_a_new_surface(self):
        with patch.object(api.moon, 'database_ready', return_value=True):
            with self.assertRaises(HTTPException) as error:
                api.moon_locate(json.dumps(dict(truth=dict(lat=0, lon=0, heading=0))), [])
            self.assertEqual(error.exception.status_code, 409)

    def test_truth_only_affects_post_search_diagnostics(self):
        query = Query(np.sin(np.arange(360)).astype(np.float32), np.ones(360, bool), 360)
        inputs = []
        def locate(*args, **kwargs):
            inputs.append((args, kwargs))
            return dict(candidates=[dict(lat=10., lon=20., headings=[30.])], heatmap=np.ones((1, 1), np.float32))
        with patch.object(api.moon, 'database_ready', return_value=True), patch.object(api, 'load_gray', return_value=np.zeros((8, 8))), patch.object(api, 'build_query', return_value=query), patch.object(api.moon, 'locate', side_effect=locate), patch.object(api.moon, 'horizons', return_value=np.zeros((1, 360))):
            for lat, lon in [(0, 0), (-89, 179)]:
                result = api.moon_locate(json.dumps(dict(terrain_model=MODEL_ID, truth=dict(lat=lat, lon=lon, heading=0))), [UploadFile(io.BytesIO(b'fixture'))])
                self.assertEqual(result['terrain_model'], MODEL_ID)
                self.assertIn('error_m', result['truth'])
        self.assertEqual(len(inputs), 2)
        for args, kwargs in inputs:
            self.assertEqual(len(args), 3)
            self.assertIs(args[0][0], query)
            self.assertEqual(args[1:], ('ssd', [None]))
            self.assertEqual(kwargs, {})


if __name__ == '__main__':
    unittest.main()
