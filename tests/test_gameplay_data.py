import json
import os
import tempfile
import unittest

from bndlx import gameplay_data


class GameplayDataTests(unittest.TestCase):
    def _write_dataset(self, folder):
        records = {
            'races': {'Race A': {'checkpoints': [123], 'triggers': [456], 'kind': 'Sprint'}},
            'checkpoints': {'boxes': {'123': {'position': {'x': 1, 'y': 2, 'z': 3}}}},
            'triggers': {'456': {'outputs': []}},
            'trigger_locations': {'456': {'type': 'locator', 'data': {'position': {'x': 4, 'z': 5}}}},
            'ai_players': {'1': {'id': 1}},
            'cop_types': {'2': {'playerType': 1}},
            'rollouts': {'3': {'vehicle': 123}},
        }
        for key, filename in gameplay_data.FILES.items():
            with open(os.path.join(folder, filename), 'w', encoding='utf-8') as f:
                json.dump(records[key], f)

    def test_loads_visualizer_reference_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_dataset(folder)
            data = gameplay_data.load(folder)
        self.assertIn('Race A', data['races'])
        self.assertIn('123', data['checkpoints'])
        self.assertIn('1', data['ai_players'])

    def test_reports_missing_dataset(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(gameplay_data.GameplayDataError, 'all-races.json'):
                gameplay_data.load(folder)

    def test_rejects_invalid_race_records(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_dataset(folder)
            with open(os.path.join(folder, 'all-races.json'), 'w', encoding='utf-8') as f:
                json.dump({'Broken': {'checkpoints': 'not a list'}}, f)
            with self.assertRaisesRegex(gameplay_data.GameplayDataError, 'invalid checkpoint'):
                gameplay_data.load(folder)


if __name__ == '__main__':
    unittest.main()
