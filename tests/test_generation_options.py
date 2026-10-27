"""Conditioning reaches the engine and survives the saved-image reuse workflow."""
import tempfile
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image
from generation_options import conditioning
from image_storage import finish_image, read_settings
from image_studio import App, MODELS, command
from setup_z_image_base import validate_smoke_image


class ConditioningTests(unittest.TestCase):
    def test_smoke_test_rejects_solid_output_despite_successful_native_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'smoke.png'
            for color in ('white','black'):
                Image.new('RGB',(512,512),color).save(path)
                with self.assertRaisesRegex(RuntimeError,'solid-color'):
                    validate_smoke_image(path)
            image=Image.new('RGB',(512,512),'white')
            image.paste('red',(100,100,400,400)); image.save(path)
            validate_smoke_image(path)

    def base(self, **changes):
        settings = dict(model_id='z-image-base', prompt='a red teapot', negative_prompt='text, watermark',
                        cfg=4.0, loras=[], steps=28, width=512, height=512, seed=42,
                        format='PNG', jpeg_quality=95)
        settings.update(changes)
        return settings

    def test_base_command_changes_cfg_and_negative_and_limits_cfg_memory(self):
        for cfg in (3.0, 4.5, 5.0):
            args = command(self.base(cfg=cfg), Path('output.png'))
            self.assertEqual(float(args[args.index('--cfg-scale')+1]), cfg)
            self.assertEqual(args[args.index('-n')+1], 'text, watermark')
            self.assertEqual(args[args.index('--batched-cfg')+1], 'off')
            self.assertIn('z-image-Q4_0.gguf', args[args.index('--diffusion-model')+1])
            self.assertEqual(args[args.index('--steps')+1], '28')
            self.assertEqual(args[args.index('--tensor-type-rules')+1],r'.*(feed_forward|attention|adaLN_modulation).*weight=q5_0')

    def test_real_file_selection_is_required_and_runtime_tags_use_strength(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            # File existence only: no claim of this fixture being a usable LoRA.
            (directory/'style.safetensors').write_bytes(b'test fixture')
            with patch('generation_options.LORA_DIR', directory), patch('image_studio.LORA_DIR', directory):
                settings = self.base(loras=[dict(file='style.safetensors', strength=0.65)])
                args = command(settings, Path('output.png'))
                self.assertIn('<lora:style.safetensors:0.65>', args[args.index('-p')+1])
                self.assertEqual(args[args.index('--lora-apply-mode')+1], 'at_runtime')
                self.assertEqual(args[args.index('--lora-model-dir')+1], str(directory))
                (directory/'style.safetensors').unlink()
                with self.assertRaisesRegex(ValueError, 'LoRAが見つかりません'):
                    command(settings, Path('output.png'))
                args = command(self.base(loras=[dict(file='style.safetensors', strength=0)]), Path('output.png'))
                self.assertNotIn('<lora:', args[args.index('-p')+1])

    def test_invalid_numbers_and_non_base_conditioning_are_rejected(self):
        for cfg in ('nan', float('inf'), -1, 21, True, None):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                conditioning(self.base(cfg=cfg))
        for entry in (dict(file='../other.safetensors',strength=1),dict(file='a.safetensors',strength='nan'),
                      dict(file='a.safetensors',strength=3),dict(file='a.ckpt',strength=1)):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                conditioning(self.base(loras=[entry]))
        with self.assertRaises(ValueError):
            conditioning(self.base(model_id='z-image-turbo'))

    def test_turbo_legacy_command_keeps_defaults(self):
        args = command(dict(model_id='z-image-turbo', prompt='a cat', steps=8, width=512, height=512, seed=42), Path('output.png'))
        self.assertEqual(args[args.index('--cfg-scale')+1], '1.0')
        self.assertEqual(args[args.index('-p')+1], 'a cat')
        self.assertNotIn('-n', args)
        self.assertNotIn('--tensor-type-rules', args)

    def test_turbo_lora_command_and_base_turbo_selections_remain_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            (directory/'turbo-style.safetensors').write_bytes(b'file-existence fixture only')
            with patch('generation_options.LORA_DIR',directory),patch('image_studio.LORA_DIR',directory):
                settings=self.base(model_id='z-image-turbo',negative_prompt='',cfg=1.0,steps=8,
                                   loras=[dict(file='turbo-style.safetensors',strength=0.6)])
                args=command(settings,Path('output.png'))
                self.assertIn('<lora:turbo-style.safetensors:0.6>',args[args.index('-p')+1])
                self.assertNotIn('-n',args)
                self.assertIn('z_image_turbo-Q3_K.gguf',args[args.index('--diffusion-model')+1])
                root=tk.Tk(); root.withdraw()
                try:
                    app=App(root)
                    path=directory/'turbo.png'
                    Image.new('RGB',(512,512),'#993344').save(path)
                    path=finish_image(path,settings)
                    self.assertTrue(app.restore_settings(path))
                    self.assertEqual(app.settings()['loras'],settings['loras'])
                    app.model_label.set(MODELS['z-image-base']['label']); app.change_model()
                    self.assertEqual(app.loras,[])
                    app.negative_prompt='watermark'
                    app.loras=[dict(file='base-style.safetensors',strength=0.8)]
                    app.model_label.set(MODELS['z-image-turbo']['label']); app.change_model()
                    self.assertEqual(app.loras,settings['loras'])
                    self.assertEqual(app.negative_prompt,'')
                    app.model_label.set(MODELS['z-image-base']['label']); app.change_model()
                    self.assertEqual(app.loras,[dict(file='base-style.safetensors',strength=0.8)])
                    self.assertEqual(app.negative_prompt,'watermark')
                finally:
                    for job in root.tk.call('after','info'): root.after_cancel(job)
                    root.destroy()

    def test_saved_png_jpeg_restore_cfg_negative_and_loras_and_model_defaults(self):
        root = tk.Tk(); root.withdraw()
        try:
            app = App(root)
            with tempfile.TemporaryDirectory() as temporary:
                for output_format in ('PNG', 'JPEG'):
                    data = self.base(cfg=4.5, format=output_format, loras=[dict(file='my-style.safetensors',strength=0.7)])
                    path = Path(temporary)/f'{output_format}.png'
                    Image.new('RGB', (512,512), '#993344').save(path)
                    path = finish_image(path, data)
                    self.assertEqual(read_settings(path), data)
                    self.assertTrue(app.restore_settings(path))
                    self.assertEqual(app.cfg.get(), '4.5')
                    self.assertEqual(app.negative_prompt, 'text, watermark')
                    self.assertEqual(app.loras, data['loras'])
                    with self.assertRaisesRegex(ValueError, 'LoRAが見つかりません'):
                        app.settings()
                app.model_label.set(MODELS['z-image-turbo']['label']); app.change_model()
                self.assertEqual(app.settings()['loras'], [])
                self.assertEqual(app.settings()['negative_prompt'], '')
                self.assertEqual(app.cfg.get(), '1.0')
                app.model_label.set(MODELS['z-image-base']['label']); app.change_model()
                self.assertEqual(app.cfg.get(), '4.0')
                self.assertEqual(app.steps.get(), '28')
                app.loras=[]; app.cfg.set('1')
                with self.assertRaisesRegex(ValueError, 'CFGを1より大きく'):
                    app.settings()
        finally:
            for job in root.tk.call('after','info'): root.after_cancel(job)
            root.destroy()


if __name__ == '__main__':
    unittest.main()
