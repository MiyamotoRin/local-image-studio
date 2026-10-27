"""Draw Things comment interchange. Preserve exact local settings separately."""
import json
import re
import xml.etree.ElementTree as ET

NS = {'x': 'adobe:ns:meta/', 'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
      'dc': 'http://purl.org/dc/elements/1.1/', 'xmp': 'http://ns.adobe.com/xap/1.0/',
      'exif': 'http://ns.adobe.com/exif/1.0/'}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

# These are import targets, not the quantizations used by our engine.
TARGET_MODELS = {'z-image-turbo': 'z_image_turbo_1.0_q8p.ckpt',
                 'flux2-klein-4b': 'flux_2_klein_4b_q8p.ckpt'}


def export_comment(metadata):
    result = {'c': metadata.get('prompt', ''), 'uc': metadata.get('negative_prompt', ''), 'v2': {}}
    v2 = result['v2']
    for source, target in [('seed', 'seed'), ('steps', 'steps'), ('cfg', 'scale')]:
        if source in metadata:
            result[target] = metadata[source]
            v2['guidanceScale' if target == 'scale' else target] = metadata[source]
    if 'width' in metadata and 'height' in metadata:
        result['size'] = f"{metadata['width']}x{metadata['height']}"
        v2.update(width=metadata['width'], height=metadata['height'])
    target = TARGET_MODELS.get(metadata.get('model_id'))
    if target:
        result['model'] = v2['model'] = target
    if 'seconds' in metadata:
        result['profile'] = {'duration': metadata['seconds']}
    result['local_image_studio'] = {
        'schema_version': 1, 'generation': metadata,
        'compatibility': {
            'model_is_import_target': bool(target),
            'sampler': 'No exact Draw Things equivalent for sd.cpp Euler; choose in Draw Things.',
            'model': 'Select the installed model manually if the import target is unavailable.',
        },
    }
    return result


def normalize_comment(data):
    if not isinstance(data, dict):
        raise ValueError('設定情報はJSONオブジェクトである必要があります。')
    own = data.get('local_image_studio')
    if isinstance(own, dict) and isinstance(own.get('generation'), dict):
        return own['generation']
    if 'c' not in data:
        return data  # Legacy Local Image Studio.
    v2 = data.get('v2', {})
    if not isinstance(v2, dict):
        raise ValueError('v2設定の形式が正しくありません。')
    model = data.get('model', v2.get('model', ''))
    if not isinstance(model, str):
        raise ValueError('モデル名の形式が正しくありません。')
    if re.fullmatch(r'z_image_turbo_1\.0_(q8p|q6p|i8x)\.ckpt', model):
        model_id = 'z-image-turbo'
    elif re.fullmatch(r'flux_2_klein_4b_(q8p|q6p|i8x)\.ckpt', model):
        model_id = 'flux2-klein-4b'
    else:
        raise ValueError('このDraw Thingsモデルは自動対応付けできません: ' + model)
    match = re.fullmatch(r'(\d+)x(\d+)', str(data.get('size', '')))
    width, height = (map(int, match.groups()) if match else (v2.get('width'), v2.get('height')))
    result = dict(prompt=data['c'], model_id=model_id, width=width, height=height,
                  steps=data.get('steps', v2.get('steps')), seed=data.get('seed', v2.get('seed')),
                  cfg=data.get('scale', v2.get('guidanceScale', 1.0)),
                  import_source='Draw Things')
    result['import_warnings'] = ('基本設定を読み込みました。モデルの量子化・サンプラー・seed方式は元と異なります。'
                                 'ネガティブプロンプト、LoRA、Control、shift等の追加設定は適用しません。')
    return result


def xmp_packet(comment):
    root = ET.Element(f"{{{NS['x']}}}xmpmeta")
    rdf = ET.SubElement(root, f"{{{NS['rdf']}}}RDF")
    description = ET.SubElement(rdf, f"{{{NS['rdf']}}}Description", {f"{{{NS['rdf']}}}about": ''})
    ET.SubElement(description, f"{{{NS['xmp']}}}CreatorTool").text = 'Local Image Studio'
    text = comment.get('c', '') + '\n' + ', '.join(
        f'{label}: {comment[key]}' for key, label in
        [('steps', 'Steps'), ('scale', 'Guidance Scale'), ('seed', 'Seed'), ('size', 'Size'), ('model', 'Model')]
        if key in comment)
    for tag, value in [('dc:description', text), ('exif:UserComment', json.dumps(comment, ensure_ascii=True))]:
        prefix, name = tag.split(':')
        element = ET.SubElement(description, f'{{{NS[prefix]}}}{name}')
        alt = ET.SubElement(element, f"{{{NS['rdf']}}}Alt")
        ET.SubElement(alt, f"{{{NS['rdf']}}}li", {'{http://www.w3.org/XML/1998/namespace}lang': 'x-default'}).text = value
    return ET.tostring(root, encoding='utf-8')


def parse_xmp(packet):
    if isinstance(packet, str):
        packet = packet.encode('utf-8')
    if len(packet) > 2_000_000 or b'<!DOCTYPE' in packet or b'<!ENTITY' in packet:
        raise ValueError('未対応のXMP形式です。')
    try:
        root = ET.fromstring(packet)
    except ET.ParseError as error:
        raise ValueError('XMPを読み取れません。') from error
    element = root.find('.//exif:UserComment', NS)
    if element is None:
        return None
    item = element.find('.//rdf:li', NS)
    return json.loads(item.text if item is not None else element.text)
