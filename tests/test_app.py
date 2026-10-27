import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from app_config import load_config
from image_storage import finish_image, read_settings


class StorageTests(unittest.TestCase):
    def test_roundtrip_and_no_sidecar(self):
        for fmt in ('PNG', 'JPEG'):
            with self.subTest(format=fmt), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / 'sample.png'
                Image.new('RGB', (32, 32), 'teal').save(source)
                metadata = dict(prompt='桜の風景 🌸', seed=42, format=fmt, jpeg_quality=95)
                result = finish_image(source, metadata)
                self.assertEqual(read_settings(result), metadata)
                self.assertEqual(len(list(Path(folder).iterdir())), 1)
                with Image.open(result) as image:
                    self.assertEqual(image.size, (32, 32))

    def test_legacy_json(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'old.png'
            Image.new('RGB', (8, 8)).save(image)
            image.with_suffix('.json').write_text('{"seed": 10}')
            self.assertEqual(read_settings(image), {'seed': 10})

    def test_failed_conversion_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'sample.png'
            Image.new('RGB', (8, 8)).save(source)
            with self.assertRaises(ValueError):
                finish_image(source, dict(format='JPEG', prompt='x' * 65000))
            self.assertTrue(source.exists())
            self.assertEqual(len(list(Path(folder).iterdir())), 1)


class ConfigTests(unittest.TestCase):
    def test_paths_and_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config.json').write_text(json.dumps(dict(output_dir='pictures', max_vram=2.5)))
            config = load_config(root)
            self.assertEqual(config['output_dir'], root / 'pictures')
            self.assertEqual(config['max_vram'], 2.5)
            (root / 'config.json').write_text('{"max_vram": -1}')
            with self.assertRaises(ValueError):
                load_config(root)


class UITests(unittest.TestCase):
    def test_clean_start_and_restore(self):
        import image_studio as app
        with tempfile.TemporaryDirectory() as folder:
            root_dir = Path(folder)
            previous = app.DATA_DIR, app.OUTPUT_DIR
            app.DATA_DIR, app.OUTPUT_DIR = root_dir / 'data', root_dir / 'images'
            root = app.tk.Tk()
            root.withdraw()
            try:
                ui = app.App(root)
                root.update()
                self.assertTrue(app.OUTPUT_DIR.is_dir())
                ui.output_format.set('PNG')
                self.assertEqual(ui.jpeg_controls.winfo_manager(), '')
                ui.output_format.set('JPEG')
                self.assertEqual(ui.jpeg_controls.winfo_manager(), 'pack')
                source = app.OUTPUT_DIR / 'test.png'
                Image.new('RGB', (512, 512), 'teal').save(source)
                data = dict(prompt='A watercolor tree', width=512, height=512, steps=8,
                            seed=42, model_id='anima-turbo', format='JPEG', jpeg_quality=95)
                image = finish_image(source, data)
                self.assertTrue(ui.restore_settings(image))
                self.assertEqual(ui.seed.get(), '42')
                self.assertEqual(ui.count.get(), '1')
                self.assertEqual(ui.model_id(), 'anima-turbo')
            finally:
                for job in root.tk.call('after', 'info'):
                    root.after_cancel(job)
                root.destroy()
                app.DATA_DIR, app.OUTPUT_DIR = previous


if __name__ == '__main__':
    unittest.main()
