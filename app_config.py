"""Machine-specific configuration; never include config.json in commits."""
import json
import math
from pathlib import Path


def load_config(root):
    root = Path(root)
    defaults = dict(engine_path='bin/sd-cli.exe', models_dir='models',
                    output_dir='images', data_dir='data', max_vram=3.2)
    file = root / 'config.json'
    if file.exists():
        data = json.loads(file.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('config.json must contain an object')
        defaults.update(data)
    budget = float(defaults['max_vram'])
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError('max_vram must be a positive finite number')
    defaults['max_vram'] = budget
    for key in ('engine_path', 'models_dir', 'output_dir', 'data_dir'):
        path = Path(defaults[key]).expanduser()
        defaults[key] = path if path.is_absolute() else root / path
    return defaults
