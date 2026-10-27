"""Post-processing transaction tests without GPU dependencies."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from image_storage import finish_image, read_settings
from image_upscale import upscale_image, upscaler_for, UPSCALERS


class UpscaleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'bin').mkdir()
        (self.root / 'bin/sd-cli.exe').touch()
        (self.root / 'models/upscalers').mkdir(parents=True)
        for name in set(UPSCALERS.values()):
            (self.root / 'models/upscalers' / name).write_bytes(b'test model')
        self.settings = dict(prompt='test', model_id='z-image-turbo', model='Z-Image Turbo Q3_K',
                             width=256, height=384, steps=8, cfg=1.0, seed=42, format='PNG')

    def tearDown(self):
        self.temporary.cleanup()

    def source(self, jpeg=False):
        path = self.root / 'source.png'
        Image.new('RGB', (256, 384), '#448877').save(path)
        return finish_image(path, dict(self.settings, format='JPEG' if jpeg else 'PNG'))

    def run_upscale(self, source, fake, cancel=None):
        with patch('image_upscale.subprocess.Popen', side_effect=fake):
            return upscale_image(source, self.root, self.root / 'run.log',
                                 cancel or threading.Event(), root=self.root)

    def successful_process(self, command, **kwargs):
        source = Path(command[command.index('-i') + 1])
        output = Path(command[command.index('-o') + 1])
        with Image.open(source) as image:
            image.resize((image.width * 4, image.height * 4)).save(output)
        class Process:
            def wait(self): return 0
            def poll(self): return 0
        return Process()

    def test_fixed_mapping_and_no_repeat(self):
        self.assertIn('anime_6B', upscaler_for({'model_id': 'anima-turbo'}))
        for model_id in ('z-image-turbo', 'flux2-klein-4b'):
            self.assertEqual(upscaler_for({'model_id': model_id}), 'RealESRGAN_x4plus.pth')
        self.assertEqual(upscaler_for({'model': 'Z-Image Turbo Q3_K'}), 'RealESRGAN_x4plus.pth')
        for settings in ({'model_id': 'unknown'}, dict(self.settings, upscale={'scale': 2})):
            with self.assertRaises(ValueError): upscaler_for(settings)

    def test_png_and_jpeg_source_integrity_and_generation_settings(self):
        for jpeg in (False, True):
            with self.subTest(jpeg=jpeg):
                source = self.source(jpeg)
                before = hashlib.sha256(source.read_bytes()).hexdigest()
                output, metadata = self.run_upscale(source, self.successful_process)
                self.assertNotEqual(source, output)
                self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
                with Image.open(output) as image:
                    self.assertEqual(image.size, (512, 768))
                self.assertEqual(read_settings(output), metadata)
                for key in ('width', 'height', 'prompt', 'seed', 'steps', 'model_id'):
                    self.assertEqual(metadata[key], self.settings[key])
                self.assertEqual(metadata['upscale']['output_width'], 512)
                self.assertEqual(metadata['upscale']['model_sha256'], hashlib.sha256(b'test model').hexdigest())
                self.assertEqual(metadata['format'], 'PNG')
                output.unlink()

    def test_failure_does_not_publish_partial_image(self):
        source = self.source()
        def fail(command, **kwargs):
            Path(command[command.index('-o') + 1]).write_bytes(b'partial')
            class Process:
                def wait(self): return 7
                def poll(self): return 7
            return Process()
        with self.assertRaisesRegex(ValueError, '7'):
            self.run_upscale(source, fail)
        self.assertEqual(list(self.root.glob('*.png')), [source])
        self.assertEqual(list(self.root.glob('studio-upscale-*')), [])

    def test_cancel_terminates_child_and_keeps_source(self):
        source = self.source()
        cancel = threading.Event()
        terminated = threading.Event()
        def running(command, **kwargs):
            cancel.set()
            class Process:
                def wait(self):
                    if not terminated.wait(3): raise AssertionError('child not terminated')
                    return -1
                def poll(self): return -1 if terminated.is_set() else None
                def terminate(self): terminated.set()
            return Process()
        self.assertIsNone(self.run_upscale(source, running, cancel))
        self.assertTrue(terminated.is_set())
        self.assertEqual(list(self.root.glob('*.png')), [source])


if __name__ == '__main__':
    unittest.main()
