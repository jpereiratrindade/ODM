"""Preview bounds and pixel handling; GDAL tests skip without a native install."""
import base64
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

from gui.preview import mesh_preview, raster_preview


class PreviewTests(unittest.TestCase):
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
