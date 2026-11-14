import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException, UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app as api
from skyline.locate import Query
from skyline.moon_surface import MODEL_ID


class GlobalApiTests(unittest.TestCase):
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
