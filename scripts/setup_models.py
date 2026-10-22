"""Explicit, checksum-verified downloads. Does not download on app startup."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_config import load_config

GROUPS = {'z-image-turbo': (0, 1, 2), 'flux2-klein-4b': (3, 4, 5),
          'anima-turbo': (6, 7, 8)}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, target, expected):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if digest(target) != expected:
            raise ValueError(f'Existing file checksum mismatch: {target}; move it aside and retry')
        return
    part = target.with_name(target.name + '.part')
    subprocess.run(['curl.exe', '-L', '--fail', '--retry', '3', '-C', '-',
                    '-o', str(part), url], check=True)
    if digest(part) != expected:
        raise ValueError(f'Checksum mismatch: {part}; remove the partial download and retry')
    part.replace(target)


def install_engine(manifest):
    release_url = 'https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/tags/' + manifest['release']
    request = urllib.request.Request(release_url, headers={'User-Agent': 'local-image-studio'})
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    assets = [a for a in release['assets'] if a['name'].endswith('bin-win-cuda12-x64.zip')
              or a['name'] == 'cudart-sd-bin-win-cu12-x64.zip']
    if len(assets) != 2:
        raise ValueError('Expected CUDA 12 engine and runtime archives; use manual setup in README')
    for asset in assets:
        expected = asset.get('digest', '')
        if not expected.startswith('sha256:'):
            raise ValueError('Release asset has no SHA256 digest; automatic installation stopped')
        archive = ROOT / 'downloads' / asset['name']
        download(asset['browser_download_url'], archive, expected[7:])
        destination = ROOT / 'bin'
        destination.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                # Flat destination avoids archive path traversal.
                name = Path(member.filename).name
                if not name or name in ('.', '..'):
                    raise ValueError('Invalid archive member')
                with bundle.open(member) as src, (destination / name).open('wb') as dst:
                    shutil.copyfileobj(src, dst)
    if digest(ROOT / 'bin/sd-cli.exe') != manifest['sd_cli_sha256']:
        raise ValueError('Engine executable checksum mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', action='store_true', help='Install tested Windows CUDA 12 engine into bin/')
    parser.add_argument('--model', choices=GROUPS)
    parser.add_argument('--list', action='store_true', help='List files and sources without downloading')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'model-files.json').read_text())
    if args.list:
        for name, indexes in GROUPS.items():
            print(name)
            for index in indexes:
                repo, file, checksum = manifest['models'][index]
                print(f'  {repo}: {file} (SHA256 {checksum})')
        return
    if not args.engine and not args.model:
        parser.error('Choose --engine, --model, or --list')
    if args.engine:
        install_engine(manifest)
    if args.model:
        config = load_config(ROOT)
        print('Model terms and source links: THIRD_PARTY.md')
        for index in GROUPS[args.model]:
            repo, file, checksum = manifest['models'][index]
            download(f'https://huggingface.co/{repo}/resolve/main/{file}',
                     config['models_dir'] / Path(file).name, checksum)
    print('Setup complete')


if __name__ == '__main__':
    main()
