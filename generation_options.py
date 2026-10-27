"""Validated CFG and Z-Image conditioning shared by UI, commands, and restoration."""
import math
from app_config import load_config
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LORA_DIR = load_config(ROOT)['models_dir'] / 'loras'


def conditioning(settings, check_files=False):
    cfg = settings.get('cfg', 1.0)
    if isinstance(cfg, bool):
        raise ValueError('CFGには0〜20の数値を入力してください。')
    try:
        cfg = float(cfg)
    except (TypeError, ValueError):
        raise ValueError('CFGには0〜20の数値を入力してください。') from None
    if not math.isfinite(cfg) or not 0 <= cfg <= 20:
        raise ValueError('CFGには0〜20の数値を入力してください。')
    negative = settings.get('negative_prompt', '')
    if not isinstance(negative, str):
        raise ValueError('ネガティブプロンプトの形式が正しくありません。')
    entries = settings.get('loras', [])
    if not isinstance(entries, list):
        raise ValueError('LoRA設定の形式が正しくありません。')
    loras = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('LoRA設定の形式が正しくありません。')
        name = entry.get('file')
        if (not isinstance(name, str) or not name or name != Path(name).name
                or any(c in name for c in ':<>/\\') or Path(name).suffix.lower() != '.safetensors'):
            raise ValueError('LoRAはmodels/loras内の.safetensorsファイルを指定してください。')
        if name.casefold() in seen:
            raise ValueError('同じLoRAを重複して指定できません。')
        seen.add(name.casefold())
        value = entry.get('strength', 1.0)
        if isinstance(value, bool):
            raise ValueError('LoRA強度には-2〜2の数値を入力してください。')
        try:
            strength = float(value)
        except (TypeError, ValueError):
            raise ValueError('LoRA強度には-2〜2の数値を入力してください。') from None
        if not math.isfinite(strength) or not -2 <= strength <= 2:
            raise ValueError('LoRA強度には-2〜2の数値を入力してください。')
        if check_files and strength != 0 and not (LORA_DIR / name).is_file():
            raise ValueError('LoRAが見つかりません: ' + str(LORA_DIR / name))
        loras.append(dict(file=name, strength=strength))
    if settings.get('model_id') != 'z-image-base' and negative.strip():
        raise ValueError('このUIのネガティブプロンプト設定はZ-Image Base用です。')
    if settings.get('model_id') not in ('z-image-base', 'z-image-turbo') and loras:
        raise ValueError('このUIのLoRA設定はZ-Image Base / Turbo用です。')
    return dict(cfg=cfg, negative_prompt=negative, loras=loras)


def engine_prompt(settings):
    options = conditioning(settings, check_files=True)
    tags = [f"<lora:{item['file']}:{item['strength']:g}>"
            for item in options['loras'] if item['strength'] != 0]
    return ' '.join([settings['prompt']] + tags)
