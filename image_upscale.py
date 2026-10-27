"""Fixed per-generator ESRGAN presets and cancellable, atomic 2x post-processing."""
import copy
from app_config import load_config
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

from PIL import Image, ImageOps
from image_storage import finish_image, read_settings

ROOT = Path(__file__).resolve().parent
UPSCALERS = {
    'z-image-base': 'RealESRGAN_x4plus.pth',
    'anima-turbo': 'RealESRGAN_x4plus_anime_6B.pth',
    'z-image-turbo': 'RealESRGAN_x4plus.pth',
    'flux2-klein-4b': 'RealESRGAN_x4plus.pth',
}
ALIASES = {
    'Z-Image Base · Q4_0': 'z-image-base',
    'Anima Turbo 1.1 · Q4_K_M': 'anima-turbo',
    'Z-Image Turbo · Q3_K': 'z-image-turbo',
    'Z-Image Turbo Q3_K': 'z-image-turbo',
    'FLUX.2 klein 4B · Q4_0': 'flux2-klein-4b',
}


def upscaler_for(settings):
    model_id = settings.get('model_id') or ALIASES.get(settings.get('model'))
    if model_id not in UPSCALERS:
        raise ValueError('この画像の生成モデルに対応するアップスケーラーがありません。')
    if settings.get('upscale'):
        raise ValueError('この画像は高精細化済みです。元画像を選んでください。')
    return UPSCALERS[model_id]


def prepare_upscale(source, root=ROOT):
    source = Path(source)
    settings = read_settings(source)
    model = upscaler_for(settings)
    config = load_config(root)
    for path in (config['engine_path'], config['models_dir'] / 'upscalers' / model):
        if not path.is_file():
            raise ValueError('必要なファイルがありません: ' + str(path))
    with Image.open(source) as image:
        width, height = ImageOps.exif_transpose(image).size
    # Native inference is 4x before downsampling to 2x; bound intermediate memory.
    if width * height > 4_194_304 or max(width, height) > 4096:
        raise ValueError('高精細化の入力は419万画素以内・各辺4096px以内にしてください。')
    return settings, model, (width, height)


def upscale_image(source, directory, logfile, cancel, on_line=lambda line: None, root=ROOT):
    """Keep the source and generation dimensions; publish only a complete PNG.

    A 4x ESRGAN pass followed by Lanczos downsampling produces the requested 2x.
    No diffusion model, prompt changes, or face restoration are involved.
    """
    source, directory, logfile = Path(source), Path(directory), Path(logfile)
    settings, model, size = prepare_upscale(source, root)
    started = time.monotonic()
    if cancel.is_set():
        return None
    with tempfile.TemporaryDirectory(prefix='studio-upscale-', dir=directory) as temporary:
        work = Path(temporary)
        # Normalize orientation and avoid any implicit JPEG/EXIF handling in sd.cpp.
        with Image.open(source) as image:
            ImageOps.exif_transpose(image).convert('RGB').save(work / 'input.png')
        config = load_config(root)
        command = [str(config['engine_path']), '-M', 'upscale',
                   '--upscale-model', str(config['models_dir'] / 'upscalers' / model),
                   '--upscale-tile-size', '128', '--upscale-repeats', '1',
                   '-i', str(work / 'input.png'), '-o', str(work / 'native.png')]
        process = None
        watcher = None
        finished = threading.Event()
        try:
            with logfile.open('w', encoding='utf-8') as log:
                log.write('Command: ' + subprocess.list2cmdline(command) + '\n')
                log.flush()
                process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

                def watch_cancel():
                    while not finished.wait(.1):
                        if cancel.is_set():
                            if process.poll() is None:
                                process.terminate()
                            return

                watcher = threading.Thread(target=watch_cancel, daemon=True)
                watcher.start()
                code = process.wait()
            if cancel.is_set():
                return None
            if code != 0 or not (work / 'native.png').is_file():
                raise ValueError(f'高精細化に失敗しました（終了コード {code}）。\nログ: {logfile}')
            on_line('2倍サイズに仕上げて保存中…')
            with Image.open(work / 'native.png') as image:
                if image.size != (size[0] * 4, size[1] * 4):
                    raise ValueError('アップスケーラーの出力サイズが想定と異なります。')
                image.resize((size[0] * 2, size[1] * 2), Image.Resampling.LANCZOS).save(work / 'final.png')
            metadata = copy.deepcopy(settings)
            metadata['format'] = 'PNG'
            with (root / 'models/upscalers' / model).open('rb') as model_file:
                model_hash = hashlib.file_digest(model_file, 'sha256').hexdigest()
            metadata['upscale'] = dict(
                model=model, scale=2, native_scale=4, source_file=source.name,
                input_width=size[0], input_height=size[1],
                output_width=size[0] * 2, output_height=size[1] * 2,
                seconds=round(time.monotonic() - started, 2),
                model_sha256=model_hash)
            final = finish_image(work / 'final.png', metadata)
            if cancel.is_set():
                return None
            destination = directory / f'{source.stem}-upscaled-2x-{uuid.uuid4().hex[:12]}.png'
            os.replace(final, destination)
            return destination, metadata
        finally:
            finished.set()
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait()
            if watcher is not None:
                watcher.join()
