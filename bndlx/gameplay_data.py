"""Loads the reference datasets used by the MW2012 event / traffic visualizer."""
import json
import os


FILES = {
    'races': 'all-races.json',
    'checkpoints': 'checkpoint-results.json',
    'triggers': 'gameplay-trigger-data-collection.json',
    'trigger_locations': 'trigger-byid-results.json',
    'ai_players': 'player-type-data.json',
    'cop_types': 'cop-ai-types.json',
    'rollouts': 'rollout-types.json',
}


class GameplayDataError(ValueError):
    pass


def load(folder):
    """Load the visualizer's JSON export folder, rejecting missing or malformed datasets."""
    data = {}
    for key, filename in FILES.items():
        path = os.path.join(folder, filename)
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                value = json.load(f)
        except OSError as exc:
            raise GameplayDataError(f'Cannot read {filename}: {exc}') from exc
        except json.JSONDecodeError as exc:
            raise GameplayDataError(f'Invalid JSON in {filename} at line {exc.lineno}: {exc.msg}') from exc
        if not isinstance(value, dict):
            raise GameplayDataError(f'{filename} must contain a JSON object')
        data[key] = value

    checkpoints = data['checkpoints'].get('boxes')
    if not isinstance(checkpoints, dict):
        raise GameplayDataError('checkpoint-results.json must contain a "boxes" object')
    for name, record in data['races'].items():
        if not isinstance(record, dict) or not isinstance(record.get('checkpoints', []), list):
            raise GameplayDataError(f'Race {name!r} has invalid checkpoint data')
        if not isinstance(record.get('triggers', []), list):
            raise GameplayDataError(f'Race {name!r} has invalid trigger data')

    data['checkpoints'] = checkpoints
    return data
