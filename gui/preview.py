"""Bounded previews of ODM products; executed with GDAL/PDAL available."""
import argparse
import base64
import json
from pathlib import Path
import struct
import tempfile
import io


def photo_preview(path, thumbnail=False):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        width, height = source.size
        exif = source.getexif()
        details = dict(exif)
        try:
            details.update(exif.get_ifd(34665))
        except (KeyError, TypeError, AttributeError):
            pass
        metadata = {name: str(details[tag]).replace('\x00', '').strip()[:160] for tag, name in
                    [(271, 'Fabricante'), (272, 'Câmera'), (36867, 'Captura'),
                     (33434, 'Exposição (s)'), (33437, 'Abertura'), (37386, 'Focal (mm)'),
                     (34855, 'ISO')] if tag in details}
        if details.get(274) in (5, 6, 7, 8):
            width, height = height, width
        bound = 360 if thumbnail else 2400
        source.draft('RGB', (bound, bound))
        image = ImageOps.exif_transpose(source)
        image.thumbnail((bound, bound))
        buffer = io.BytesIO()
        image.convert('RGB').save(buffer, format='JPEG', quality=85)
    return {'kind': 'photo', 'width': width, 'height': height, 'metadata': metadata,
            'image': base64.b64encode(buffer.getvalue()).decode('ascii')}


def raster_preview(path):
    from osgeo import gdal
    gdal.UseExceptions()
    source = gdal.Open(str(path))
    width, height = source.RasterXSize, source.RasterYSize
    ratio = min(1, 1600 / max(width, height))
    bands = list(range(1, min(source.RasterCount, 4) + 1)) if source.RasterCount >= 3 else [1]
    info = {'kind': 'raster', 'width': width, 'height': height,
            'crs': source.GetProjection(), 'pixel_size': list(source.GetGeoTransform()[1:6:4]),
            'bands': source.RasterCount}
    spatial = source.GetSpatialRef()
    info['epsg'] = spatial.GetAuthorityCode(None) if spatial else None
    info['unit'] = spatial.GetLinearUnitsName() if spatial and spatial.IsProjected() else 'graus'
    options = {'format': 'PNG', 'width': max(1, round(width * ratio)),
               'height': max(1, round(height * ratio)), 'bandList': bands,
               'outputType': gdal.GDT_Byte, 'resampleAlg': 'bilinear'}
    if len(bands) == 1 or source.GetRasterBand(1).DataType != gdal.GDT_Byte:
        # Compute range on the reduced raster, keeping preview memory bounded.
        reduced = gdal.Translate('', source, format='MEM', width=options['width'],
                                 height=options['height'], bandList=[1])
        low, high = reduced.GetRasterBand(1).ComputeRasterMinMax()
        options['scaleParams'] = [[low, high if high > low else low + 1, 0, 255]]
        info['range'] = [low, high]
    with tempfile.TemporaryDirectory(prefix='odm-raster-') as directory:
        output = Path(directory) / 'preview.png'
        result = gdal.Translate(str(output), source, **options)
        result = None
        info['image'] = base64.b64encode(output.read_bytes()).decode('ascii')
    return info


def point_preview(path):
    import pdal
    with open(path, 'rb') as file:
        header = file.read(375)
    if header[:4] != b'LASF':
        raise ValueError('Arquivo LAS/LAZ inválido.')
    count = struct.unpack_from('<I', header, 107)[0]
    if not count and len(header) >= 255 and header[25] >= 4:
        count = struct.unpack_from('<Q', header, 247)[0]
    step = max(1, (count + 11999) // 12000)
    pipeline = pdal.Pipeline(json.dumps([
        {'type': 'readers.las', 'filename': str(path)},
        {'type': 'filters.decimation', 'step': step},
        {'type': 'filters.head', 'count': 12000},
    ]))
    points = []
    for chunk in pipeline.iterator(chunk_size=12000, allowed_dims=['X', 'Y', 'Z']):
        points.extend([[float(p['X']), float(p['Y']), float(p['Z'])] for p in chunk])
        if len(points) >= 12000:
            break
    return {'kind': 'points', 'points': points[:12000], 'total': count}


def mesh_preview(path):
    # Sample actual OBJ vertices without loading textures or the full mesh.
    with open(path, encoding='utf-8', errors='replace') as file:
        count = sum(line.startswith('v ') for line in file)
    step = max(1, (count + 11999) // 12000)
    points, index = [], 0
    with open(path, encoding='utf-8', errors='replace') as file:
        for line in file:
            if line.startswith('v '):
                if index % step == 0:
                    points.append([float(value) for value in line.split()[1:4]])
                index += 1
    return {'kind': 'points', 'points': points[:12000], 'total': count,
            'note': 'Geometria amostrada dos vértices do OBJ; textura e faces não exibidas.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=('raster', 'points', 'mesh', 'photo', 'thumbnail'))
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    print(json.dumps({'raster': raster_preview, 'points': point_preview,
                      'mesh': mesh_preview, 'photo': photo_preview,
                      'thumbnail': lambda path: photo_preview(path, True)}[args.kind](args.path), allow_nan=False))
