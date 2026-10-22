"""Image settings stored in PNG text or JPEG EXIF UserComment."""
import json, os
from pathlib import Path
from PIL import Image, PngImagePlugin

def read_settings(path):
    path=Path(path)
    if path.suffix.lower()=='.json':
        return json.loads(path.read_text(encoding='utf-8-sig'))
    with Image.open(path) as image:
        text=image.info.get('local_image_studio')
        if not text:
            comment=image.getexif().get(37510,b'')
            if isinstance(comment,bytes) and comment.startswith(b'ASCII\0\0\0'):
                text=comment[8:].decode('ascii')
        if text: return json.loads(text)
    sidecar=path.with_suffix('.json')
    if sidecar.is_file(): return json.loads(sidecar.read_text(encoding='utf-8-sig'))
    raise ValueError('画像内に生成設定がなく、同名のJSONも見つかりません。')

def finish_image(source,metadata):
    source=Path(source)
    jpeg=metadata.get('format','PNG')=='JPEG'
    destination=source.with_suffix('.jpg') if jpeg else source
    temporary=destination.with_name(destination.stem+'.saving'+destination.suffix)
    text=json.dumps(metadata,ensure_ascii=True)
    try:
        with Image.open(source) as image:
            if jpeg:
                comment=b'ASCII\0\0\0'+text.encode('ascii')
                if len(comment)>60000: raise ValueError('生成設定がJPEGの保存上限を超えています。PNG形式を選択してください。')
                exif=Image.Exif();exif[37510]=comment
                image.convert('RGB').save(temporary,format='JPEG',quality=metadata.get('jpeg_quality',95),subsampling=0,optimize=True,exif=exif)
            else:
                info=PngImagePlugin.PngInfo();info.add_itxt('local_image_studio',text)
                image.save(temporary,format='PNG',pnginfo=info)
        if read_settings(temporary)!=metadata: raise ValueError('画像内の設定確認に失敗しました。')
        os.replace(temporary,destination)
        if jpeg: source.unlink()
        return destination
    finally:
        if temporary.exists(): temporary.unlink()
