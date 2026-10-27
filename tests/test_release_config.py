"""The public package must honor custom paths in new v2 workflows."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from image_storage import finish_image
from image_upscale import prepare_upscale
import image_studio as app


class ReleaseConfigTests(unittest.TestCase):
    def test_generation_uses_configured_engine_models_and_budget(self):
        with patch.object(app, 'ENGINE', Path('custom/engine.exe')), patch.object(app, 'MODELS_DIR', Path('weights')), patch.dict(app.CONFIG, max_vram=2.5):
            args = app.command(dict(model_id='z-image-base', prompt='test', steps=28,
                                    width=512, height=512, seed=42, cfg=4), Path('test.png'))
        self.assertEqual(args[0], str(Path('custom/engine.exe')))
        self.assertEqual(args[args.index('--diffusion-model')+1], str(Path('weights/z-image-Q4_0.gguf')))
        self.assertEqual(args[args.index('--max-vram')+1], '2.5')

    def test_upscale_checks_configured_engine_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.json').write_text(json.dumps(dict(engine_path='custom/engine.exe', models_dir='weights')))
            (root/'custom').mkdir()
            (root/'custom/engine.exe').touch()
            (root/'weights/upscalers').mkdir(parents=True)
            (root/'weights/upscalers/RealESRGAN_x4plus.pth').touch()
            source = root/'source.png'
            Image.new('RGB', (256, 256), 'teal').save(source)
            finish_image(source, dict(model_id='z-image-base'))
            _, model, size = prepare_upscale(source, root)
            self.assertEqual(model, 'RealESRGAN_x4plus.pth')
            self.assertEqual(size, (256, 256))
