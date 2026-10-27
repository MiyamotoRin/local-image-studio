"""Download and verify the pinned Base quantization; optionally run a GPU smoke test."""
import argparse
from app_config import load_config
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
CONFIG = load_config(ROOT)
REPO = 'unsloth/Z-Image-GGUF'
REVISION = '8acd92073b207fc779b9c77e87934cbe92d4aa10'
FILENAME = 'z-image-Q4_0.gguf'
SHA256 = '4c5cfc02e6007ae1f0b0d690f68bda452f51f7b8ab6d9f500f8c5b829fea4377'
URL = f'https://huggingface.co/{REPO}/resolve/{REVISION}/{FILENAME}?download=true'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def install(source=None):
    destination = CONFIG['models_dir'] / FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if digest(destination) == SHA256:
            print('Base model already installed; SHA256 verified.', flush=True)
            return
        raise ValueError(f'Existing file has a different SHA256. Preserve/rename it before retrying: {destination}')
    partial = destination.with_suffix('.gguf.part')
    if source:
        source = Path(source).resolve()
        if source == partial.resolve():
            raise ValueError('Select the downloaded .gguf, not the partial destination.')
        if digest(source) != SHA256:
            raise ValueError('Source SHA256 does not match the pinned Base Q4_0 model.')
        shutil.copyfile(source, partial)
    else:
        if shutil.disk_usage(ROOT).free < 6_000_000_000:
            raise ValueError('At least 6 GB free space is required for download and generation.')
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(URL, headers={'Range': f'bytes={offset}-'} if offset else {})
        print('Downloading Base Q4_0 (~4.59 GB). An interrupted download can be resumed.', flush=True)
        with urllib.request.urlopen(request, timeout=60) as response:
            append = offset > 0 and response.status == 206
            if append and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                raise ValueError('Download server returned an unexpected byte range.')
            received = offset if append else 0
            last_update = time.monotonic()
            with partial.open('ab' if append else 'wb') as stream:
                while chunk := response.read(8 * 1024 * 1024):
                    stream.write(chunk)
                    received += len(chunk)
                    if time.monotonic() - last_update >= 2:
                        print(f'{received / 1e9:.2f} GB received', flush=True)
                        last_update = time.monotonic()
    print('Verifying SHA256...', flush=True)
    if digest(partial) != SHA256:
        rejected = partial.with_name(partial.name + f'.invalid-{time.time_ns()}')
        partial.rename(rejected)
        raise ValueError(f'SHA256 mismatch; file preserved for inspection: {rejected}')
    if destination.exists():
        raise ValueError('Destination appeared during download; refusing to replace it.')
    partial.rename(destination)
    print(f'Installed: {destination}', flush=True)


def smoke_test():
    from image_studio import command, MODELS
    from image_storage import finish_image
    settings = dict(model_id='z-image-base', prompt='A red ceramic teapot on a wooden table, soft window light, still life photograph',
                    negative_prompt='text, watermark, blurry', cfg=4.0, loras=[], width=512, height=512,
                    steps=28, seed=42, count=1, format='PNG', jpeg_quality=95)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    output = CONFIG['output_dir'] / f'base-check-{stamp}.png'
    logfile = CONFIG['data_dir'] / 'logs' / f'base-check-{stamp}.log'
    output.parent.mkdir(parents=True, exist_ok=True)
    logfile.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    print('Running 512x512, 28 steps, CFG 4.0 + negative prompt. This may take several minutes.', flush=True)
    with logfile.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command(settings, output), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            for raw in iter(process.stdout.readline, b''):
                line = raw.decode('utf-8', errors='replace')
                print(line, end='', flush=True)
                log.write(line); log.flush()
            code = process.wait()
        except BaseException:
            process.terminate(); process.wait()
            raise
    if code != 0 or not output.is_file():
        raise RuntimeError(f'Generation failed (exit {code}). Log: {logfile}')
    validate_smoke_image(output)
    elapsed = round(time.monotonic() - started, 2)
    metadata = dict(settings, model=MODELS['z-image-base']['label'], seconds=elapsed,
                    engine_args=MODELS['z-image-base'].get('extra_args', []))
    finish_image(output, metadata)
    result = dict(image=str(output), log=str(logfile), settings=metadata,
                  validation='Base generation and embedded metadata round trip; no LoRA tested')
    (CONFIG['data_dir'] / 'logs' / f'base-check-{stamp}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Generated in {elapsed}s: {output}\nLoRA compatibility requires a separate test with your chosen Base LoRA.', flush=True)


def validate_smoke_image(path):
    """The teapot smoke test must contain image content, not a blank success PNG."""
    from PIL import Image
    with Image.open(path) as image:
        if image.size != (512, 512):
            raise RuntimeError(f'Smoke test returned an unexpected image size: {image.size}')
        if all(low == high for low, high in image.convert('RGB').getextrema()):
            raise RuntimeError(f'The engine exited successfully but generated a solid-color image. '
                               f'The smoke test FAILED. Preserved output: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, help='Install a manually downloaded model after verifying SHA256')
    parser.add_argument('--test', action='store_true', help='Run a 512x512 Base generation after installation')
    args = parser.parse_args()
    try:
        install(args.file)
        if args.test:
            smoke_test()
    except (OSError, ValueError, RuntimeError) as error:
        print(f'\nSetup incomplete: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
