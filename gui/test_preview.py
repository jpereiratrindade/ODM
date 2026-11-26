"""Preview bounds and pixel handling; GDAL tests skip without a native install."""
import base64
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

from gui.preview import mesh_preview, raster_preview, planning_metadata


class PreviewTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow not installed')
    def test_planning_exif_sensor_estimate_and_relative_height(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flight.jpg'
            exif = Image.Exif(); exif[37386] = 8.8; exif[41989] = 24; exif[272] = 'Test camera'
            Image.new('RGB', (800, 450)).save(path, exif=exif)
            with path.open('ab') as file:
                file.write(b'<drone-dji:Data drone-dji:RelativeAltitude="+110.30"/>')
            before = path.read_bytes()
            data = planning_metadata(path)
            self.assertAlmostEqual(data['sensor_mm'], 13.2)
            self.assertEqual(data['focal_mm'], 8.8)
            self.assertEqual(data['relative_altitude'], 110.3)
            self.assertIn('Estimativa', data['sensor_source'])
            self.assertNotIn('altitude', data['gps'])
            self.assertNotIn('height_agl', data)
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow not installed')
    def test_planning_does_not_invent_missing_parameters(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'no-exif.jpg'
            Image.new('RGB', (640, 480)).save(path)
            data = planning_metadata(path)
            self.assertEqual(data['width'], 640)
            for key in ['focal_mm', 'sensor_mm', 'focal_35_mm', 'relative_altitude']:
                self.assertIsNone(data[key])
            self.assertEqual(data['gps'], {})

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow not installed')
    def test_photo_orientation_exif_and_original_preserved(self):
        from PIL import Image
        from gui.preview import photo_preview
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'photo.jpg'
            exif = Image.Exif(); exif[274] = 6; exif[272] = 'Test camera\x00'
            Image.new('RGB', (800, 400), 'green').save(path, exif=exif)
            original = path.read_bytes()
            result = photo_preview(path, True)
            self.assertEqual((result['width'], result['height']), (400, 800))
            self.assertEqual(result['metadata']['Câmera'], 'Test camera')
            import io
            with Image.open(io.BytesIO(base64.b64decode(result['image']))) as image:
                self.assertLessEqual(max(image.size), 360)
                self.assertLess(image.width, image.height)
            self.assertEqual(path.read_bytes(), original)

    def test_obj_sample_is_bounded_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.obj'
            path.write_text(''.join(f'v {i} {i / 2} 0\n' for i in range(25001)))
            original = path.read_bytes()
            result = mesh_preview(path)
            self.assertEqual(result['total'], 25001)
            self.assertLessEqual(len(result['points']), 12000)
            self.assertEqual(result['points'][0], [0, 0, 0])
            self.assertEqual(path.read_bytes(), original)

    @unittest.skipUnless(importlib.util.find_spec('osgeo'), 'GDAL not installed in native Python')
    def test_float_raster_preview_bounds_and_nodata(self):
        from osgeo import gdal
        gdal.UseExceptions()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dem.tif'
            ds = gdal.GetDriverByName('GTiff').Create(str(path), 3000, 20, 1, gdal.GDT_Float32)
            band = ds.GetRasterBand(1)
            band.SetNoDataValue(-9999)
            values = [-9999] + [float(i % 100) for i in range(59999)]
            band.WriteRaster(0, 0, 3000, 20, struct.pack('<60000f', *values), buf_type=gdal.GDT_Float32)
            ds = None
            original = path.read_bytes()
            result = raster_preview(path)
            png = base64.b64decode(result['image'])
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', png[16:24])
            self.assertEqual(width, 1600)
            self.assertLessEqual(height, 1600)
            self.assertGreaterEqual(result['range'][0], 0)
            self.assertEqual(result['width'], 3000)
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
