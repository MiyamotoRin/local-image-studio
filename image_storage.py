"""Image settings stored in PNG text or JPEG EXIF UserComment."""
import json, os
from pathlib import Path
from PIL import Image, PngImagePlugin
from draw_things_metadata import export_comment, normalize_comment, xmp_packet, parse_xmp

def read_settings(path):
    path=Path(path)
    if path.suffix.lower()=='.json':
        return normalize_comment(json.loads(path.read_text(encoding='utf-8-sig')))
    with Image.open(path) as image:
        text=image.info.get('local_image_studio')
        if text: return normalize_comment(json.loads(text))
        packet=image.info.get('xmp') or image.info.get('XML:com.adobe.xmp')
        if packet:
            data=parse_xmp(packet)
            if data is not None: return normalize_comment(data)
        if not text:
            exif=image.getexif()
            comment=exif.get_ifd(34665).get(37510,exif.get(37510,b''))
            if isinstance(comment,bytes) and comment.startswith(b'ASCII\0\0\0'):
                text=comment[8:].decode('ascii')
            elif isinstance(comment,str): text=comment
        if text: return normalize_comment(json.loads(text))
    sidecar=path.with_suffix('.json')
    if sidecar.is_file(): return normalize_comment(json.loads(sidecar.read_text(encoding='utf-8-sig')))
    raise ValueError('画像内に生成設定がなく、同名のJSONも見つかりません。')

def finish_image(source,metadata):
    source=Path(source)
    jpeg=metadata.get('format','PNG')=='JPEG'
    destination=source.with_suffix('.jpg') if jpeg else source
    temporary=destination.with_name(destination.stem+'.saving'+destination.suffix)
    data=export_comment(metadata)
    text=json.dumps(data,ensure_ascii=True)
    packet=xmp_packet(data)
    try:
        with Image.open(source) as image:
            if jpeg:
                comment=b'ASCII\0\0\0'+text.encode('ascii')
                if max(len(comment),len(packet))>60000: raise ValueError('生成設定がJPEGの保存上限を超えています。PNG形式を選択してください。')
                exif=Image.Exif();exif[34665]={37510:comment}
                image.convert('RGB').save(temporary,format='JPEG',quality=metadata.get('jpeg_quality',95),subsampling=0,optimize=True,exif=exif,xmp=packet)
            else:
                info=PngImagePlugin.PngInfo();info.add_itxt('XML:com.adobe.xmp',packet.decode('utf-8'))
                exif=Image.Exif();exif[34665]={37510:b'ASCII\0\0\0'+text.encode('ascii')}
                image.save(temporary,format='PNG',pnginfo=info,exif=exif)
        if read_settings(temporary)!=metadata: raise ValueError('画像内の設定確認に失敗しました。')
        os.replace(temporary,destination)
        if jpeg: source.unlink()
        return destination
    finally:
        if temporary.exists(): temporary.unlink()
