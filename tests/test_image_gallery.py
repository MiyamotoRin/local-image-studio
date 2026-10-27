"""UI integration checks using isolated images; no GPU generation or user-file writes."""
import os
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image
from image_gallery import ImageGallery
from image_storage import finish_image, read_settings
from image_studio import App


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='studio-gallery-test-')
        self.directory = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = App(self.root)
        self.gallery = ImageGallery(self.app, self.directory)
        self.app.gallery = self.gallery

    def tearDown(self):
        self.gallery.close()
        for job in self.root.tk.call('after', 'info'):
            self.root.after_cancel(job)
        self.root.destroy()
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def image(self, name, settings=False, jpeg=False):
        path = self.directory / name
        Image.new('RGB', (256, 384), '#267e72').save(path)
        if settings:
            path = finish_image(path, dict(prompt='gallery integration test', width=256,
                                          height=384, steps=8, seed=123, cfg=1.0,
                                          model_id='z-image-turbo', model='Z-Image Turbo Q3_K',
                                          format='JPEG' if jpeg else 'PNG', jpeg_quality=95))
        return path

    def wait_for_thumbnails(self, count):
        deadline = time.monotonic() + 5
        while len(self.gallery.photos) < count and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertEqual(len(self.gallery.photos), count)

    def test_empty_and_newest_first_pagination(self):
        self.assertIn('0枚', self.gallery.page_label.get())
        for index in range(14):
            path = self.image(f'{index:02}.png')
            os.utime(path, (1000 + index, 1000 + index))
        self.image('incomplete.saving.png')
        (self.directory / 'folder.jpg').mkdir()
        (self.directory / 'notes.json').write_text('{}')
        self.gallery.refresh()
        self.assertEqual(len(self.gallery.paths), 14)
        self.assertEqual(self.gallery.paths[0].name, '13.png')
        self.wait_for_thumbnails(12)
        self.gallery.turn_page(1)
        self.wait_for_thumbnails(2)
        self.assertEqual(str(self.gallery.next['state']), 'disabled')
        # Refresh clamps the page when externally removed images shrink the list.
        for path in self.gallery.paths[:4]:
            path.unlink()
        self.gallery.refresh()
        self.assertEqual(self.gallery.page, 0)

    def test_settings_restore_png_and_jpeg(self):
        for jpeg in (False, True):
            path = self.image('restore.png', settings=True, jpeg=jpeg)
            self.gallery.refresh()
            self.gallery.open_viewer(path)
            self.root.update()
            self.assertIn('gallery integration test', self.gallery.details.get('1.0', 'end'))
            self.assertIsNotNone(self.gallery.viewer_photo)
            self.gallery.reuse_settings()
            self.assertIsNone(self.gallery.viewer)
            self.assertEqual(self.app.seed.get(), '123')
            self.assertEqual(self.app.height.get(), '384')
            self.assertEqual(self.app.count.get(), '1')
            self.assertEqual(self.app.output_format.get(), 'JPEG' if jpeg else 'PNG')
            self.assertEqual(self.app.last_image, path)

    def test_missing_settings_corrupt_and_deleted_images(self):
        normal = self.image('normal.png')
        corrupt = self.directory / 'broken.jpg'
        corrupt.write_bytes(b'not an image')
        self.gallery.refresh()
        self.gallery.open_viewer(normal)
        self.assertFalse(self.gallery.has_settings)
        self.assertEqual(str(self.gallery.reuse['state']), 'disabled')
        self.gallery.open_viewer(corrupt)
        self.assertIsNone(self.gallery.original)
        normal.unlink()
        self.gallery.open_viewer(normal)
        self.assertIsNone(self.gallery.original)

    def test_navigation_and_busy_guard(self):
        first = self.image('first.png', settings=True)
        second = self.image('second.png', settings=True)
        self.gallery.refresh()
        self.gallery.open_viewer(self.gallery.paths[0])
        self.assertEqual(str(self.gallery.prev_image['state']), 'disabled')
        self.gallery.navigate(1)
        self.assertEqual(self.gallery.selected, self.gallery.paths[1])
        self.assertEqual(str(self.gallery.next_image['state']), 'disabled')
        self.app.busy = True
        self.app.seed.set('999')
        self.gallery.open_viewer(first)
        self.assertEqual(str(self.gallery.reuse['state']), 'disabled')
        self.gallery.reuse_settings()
        self.assertEqual(self.app.seed.get(), '999')
        self.app.busy = False
        first.unlink()
        self.gallery.refresh()
        self.assertEqual(str(self.gallery.next_image['state']), 'disabled')
        self.assertIn('一覧にない', self.gallery.position.get())

    def test_existing_gallery_reopens_and_closes_cleanly(self):
        self.gallery.window.withdraw()
        original = self.gallery
        # The production opener reuses the existing gallery rather than spawning one.
        self.app.open_gallery()
        self.root.update()
        self.assertIs(self.app.gallery, original)
        self.assertEqual(self.gallery.window.state(), 'normal')

    def test_upscale_completion_and_restore_original_resolution(self):
        source = self.image('upscale-source.png', settings=True)
        settings = read_settings(source)
        settings.update(format='PNG', upscale=dict(model='RealESRGAN_x4plus.pth', scale=2,
                        source_file=source.name, output_width=512, output_height=768))
        output = self.directory / 'upscaled.png'
        Image.new('RGB', (512, 768), '#448877').save(output)
        finish_image(output, settings)
        self.gallery.refresh()
        self.gallery.open_viewer(source)
        self.assertEqual(str(self.gallery.upscale['state']), 'normal')
        with patch('image_studio.prepare_upscale', return_value=(read_settings(source), 'RealESRGAN_x4plus.pth', (256, 384))), patch('image_studio.upscale_image', return_value=(output, settings)):
            self.gallery.upscale_selected()
            self.assertTrue(self.app.busy)
            self.assertEqual(str(self.app.generate['state']), 'disabled')
            deadline = time.monotonic() + 5
            while self.app.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
        self.assertFalse(self.app.busy)
        self.assertEqual(self.gallery.selected, output)
        self.assertEqual(str(self.gallery.upscale['state']), 'disabled')
        self.assertIn('512 × 768', self.app.info.get())
        self.assertEqual(str(self.app.generate['state']), 'normal')
        self.gallery.reuse_settings()
        self.assertEqual(self.app.width.get(), '256')
        self.assertEqual(self.app.height.get(), '384')

    def test_completed_generation_refreshes_gallery_and_legacy_settings(self):
        path = self.image('legacy.png')
        metadata = dict(prompt='legacy sidecar test', width=256, height=384, steps=8,
                        seed=42, model_id='z-image-turbo', model='Z-Image Turbo Q3_K')
        path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf-8')
        self.app.events.put(('image_done', (path, metadata)))
        self.app.poll()
        self.assertEqual(self.gallery.paths, [path])
        self.gallery.open_viewer(path)
        self.assertTrue(self.gallery.has_settings)
        self.assertIn('legacy sidecar test', self.gallery.details.get('1.0', 'end'))
        self.gallery.reuse_settings()
        self.assertEqual(self.app.seed.get(), '42')


if __name__ == '__main__':
    unittest.main()
