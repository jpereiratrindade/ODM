import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flight_plan  # noqa: E402
import server  # noqa: E402


class FlightPlanTests(unittest.TestCase):
    def test_wasm_gsd_equivalence(self):
        # Exact IEEE-754 f64 expression from gui/build_wasm.py and gui/web/worker.js
        cases = [
            (60.0, 9.7, 6.72, 4000),
            (100.0, 13.2, 8.8, 5472),
            (45.5, 9.7, 6.72, 8000),
        ]
        for altitude, sensor, focal, pixels in cases:
            expected = altitude * sensor / focal / pixels * 100.0
            self.assertEqual(flight_plan.compute_gsd(altitude, sensor, focal, pixels), expected)

    def test_mini4_12mp_vs_48mp_cadence_and_blur(self):
        cam12 = flight_plan.get_camera_profile('dji-mini-4-pro-12mp')
        cam48 = flight_plan.get_camera_profile('dji-mini-4-pro-48mp')

        plan12 = flight_plan.plan_direct(cam12, altitude_m=60.0, front_overlap=0.8, side_overlap=0.75, speed_ms=4.0)
        plan48 = flight_plan.plan_direct(cam48, altitude_m=60.0, front_overlap=0.8, side_overlap=0.75, speed_ms=4.0)

        self.assertTrue(plan12['speed_compatible'])
        self.assertFalse(plan48['speed_compatible'])
        self.assertAlmostEqual(plan12['max_speed_cadence_ms'] / plan48['max_speed_cadence_ms'], 2.5, places=6)
        self.assertTrue(any('cadência' in w for w in plan48['warnings']))

        # With slow shutter (1/100 s) and 1 px blur limit at 60 m (~2.16 cm/px), max blur speed ~2.16 m/s
        plan_blur = flight_plan.plan_direct(
            cam12, altitude_m=60.0, front_overlap=0.8, side_overlap=0.75, speed_ms=4.0, shutter_s=0.01, max_blur_px=1.0
        )
        self.assertFalse(plan_blur['speed_compatible'])
        self.assertAlmostEqual(plan_blur['effective_max_speed_ms'], plan_blur['max_speed_blur_ms'], places=6)
        self.assertTrue(any('borramento' in w for w in plan_blur['warnings']))

    def test_inverse_then_direct_roundtrip(self):
        cam12 = flight_plan.get_camera_profile('dji-mini-4-pro-12mp')
        inverse = flight_plan.plan_inverse(
            cam12, target_gsd_cm=2.0, front_overlap=0.8, side_overlap=0.75, vertical_reference='agl'
        )
        direct = flight_plan.plan_direct(
            cam12,
            altitude_m=inverse['altitude_m'],
            front_overlap=0.8,
            side_overlap=0.75,
            speed_ms=inverse['effective_max_speed_ms'],
            vertical_reference='agl',
        )
        self.assertAlmostEqual(direct['gsd_nominal_cm'], 2.0, places=9)
        self.assertTrue(direct['speed_compatible'])
        self.assertEqual(direct['warnings'], [])

    def test_camera_from_metadata_preserves_provenance_and_rejects_incomplete(self):
        cam = flight_plan.camera_from_metadata({
            'make': 'DJI',
            'model': 'FC8482',
            'width': 4032,
            'height': 3024,
            'sensor_mm': 9.6,
            'focal_mm': 6.72,
            'sensor_source': 'Estimativa pela focal equivalente de 35 mm (EXIF)',
        })
        self.assertEqual(cam.name, 'DJI FC8482')
        self.assertAlmostEqual(cam.sensor_height_mm, 7.2, places=6)
        self.assertIn('não equivale a calibração óptica', cam.uncertainty_note)

        with self.assertRaises(ValueError):
            flight_plan.camera_from_metadata({'width': 4000, 'height': 3000, 'focal_mm': 6.72})

    def test_invalid_inputs_and_warnings(self):
        cam12 = flight_plan.get_camera_profile('dji-mini-4-pro-12mp')
        with self.assertRaises(ValueError):
            flight_plan.plan_direct(cam12, altitude_m=-10)
        with self.assertRaises(ValueError):
            flight_plan.plan_direct(cam12, altitude_m=60, front_overlap=1.0)
        with self.assertRaises(ValueError):
            flight_plan.plan_direct(cam12, altitude_m=60, vertical_reference='invalid')

        high = flight_plan.plan_direct(cam12, altitude_m=150.0, vertical_reference='relative_to_takeoff')
        self.assertTrue(any('120 m' in w for w in high['warnings']))
        self.assertTrue(any('decolagem' in w for w in high['warnings']))

    def test_local_projection_and_rectangle_area(self):
        proj = flight_plan.LocalProjection(-53.8, -29.7)
        x, y = proj.to_local(-53.799, -29.699)
        lon, lat = proj.to_wgs84(x, y)
        self.assertAlmostEqual(lon, -53.799, places=9)
        self.assertAlmostEqual(lat, -29.699, places=9)

        rect = flight_plan.rectangle_polygon(-53.8, -29.7, width_m=200.0, height_m=100.0, angle_deg=25.0)
        cam12 = flight_plan.get_camera_profile('dji-mini-4-pro-12mp')
        grid = flight_plan.plan_grid(cam12, rect, altitude_m=60.0)
        self.assertAlmostEqual(grid['area_m2'], 20000.0, delta=5.0)
        self.assertAlmostEqual(grid['area_ha'], 2.0, delta=0.001)
        self.assertEqual(grid['crs'], 'EPSG:4326')

    def test_optimal_heading_serpentine_and_overshoot(self):
        cam12 = flight_plan.get_camera_profile('dji-mini-4-pro-12mp')
        elongated = flight_plan.rectangle_polygon(-53.8, -29.7, width_m=300.0, height_m=80.0, angle_deg=0.0)
        opt = flight_plan.plan_grid(cam12, elongated, altitude_m=60.0, heading_deg=None, overshoot_m=0.0)
        cross = flight_plan.plan_grid(cam12, elongated, altitude_m=60.0, heading_deg=0.0, overshoot_m=0.0)
        self.assertLess(opt['strip_count'], cross['strip_count'])
        self.assertEqual(opt['waypoint_count'], opt['strip_count'] * 2)
        self.assertGreater(opt['estimated_photos'], opt['strip_count'])

        with_overshoot = flight_plan.plan_grid(cam12, elongated, altitude_m=60.0, heading_deg=90.0, overshoot_m=10.0)
        self.assertGreater(with_overshoot['total_distance_m'], opt['total_distance_m'])

    def test_concave_polygon_rejected_and_inverse_grid_limits(self):
        cam12 = flight_plan.get_camera_profile('dji-mini-4-pro-12mp')
        concave = [
            (-53.801, -29.701),
            (-53.799, -29.701),
            (-53.800, -29.700),
            (-53.799, -29.699),
            (-53.801, -29.699),
        ]
        with self.assertRaises(ValueError):
            flight_plan.plan_grid(cam12, concave, altitude_m=60.0)
        with self.assertRaises(ValueError):
            flight_plan.plan_grid(cam12, concave[:3])

        huge = flight_plan.rectangle_polygon(-53.8, -29.7, width_m=2500.0, height_m=2500.0)
        grid = flight_plan.plan_grid(cam12, huge, target_gsd_cm=1.5, vertical_reference='agl')
        self.assertGreater(grid['waypoint_count'], flight_plan.MAX_DJI_WAYPOINTS)
        self.assertGreater(grid['estimated_batteries'], 1)
        self.assertTrue(any('waypoints' in w for w in grid['warnings']))
        self.assertTrue(any('bateria' in w for w in grid['warnings']))

    def test_workspace_flight_plan_without_project_and_export_formats(self):
        ws = server.Workspace(datasets=None, engine='native')
        preview = ws.flight_plan_preview({
            'camera_profile': 'dji-mini-4-pro-12mp',
            'mode': 'inverse',
            'target_gsd_cm': 2.0,
            'front_overlap': 0.8,
            'side_overlap': 0.75,
            'center': {'lat': -29.7, 'lon': -53.8},
            'width_m': 200,
            'height_m': 120,
        })
        self.assertAlmostEqual(preview['gsd_nominal_cm'], 2.0, places=6)
        self.assertGreater(preview['strip_count'], 0)

        geojson_res = ws.flight_plan_export({
            'camera_profile': 'dji-mini-4-pro-12mp',
            'mode': 'direct',
            'altitude_m': 60,
            'center': {'lat': -29.7, 'lon': -53.8},
            'width_m': 150,
            'height_m': 100,
            'format': 'geojson',
            'name': 'campo-norte',
        })
        geo = json.loads(geojson_res['content'])
        self.assertEqual(geo['type'], 'FeatureCollection')
        self.assertEqual(geo['features'][0]['geometry']['type'], 'Polygon')

        kml_res = ws.flight_plan_export({
            'camera_profile': 'dji-mini-4-pro-12mp',
            'mode': 'direct',
            'altitude_m': 60,
            'center': {'lat': -29.7, 'lon': -53.8},
            'width_m': 150,
            'height_m': 100,
            'format': 'kml',
            'name': 'campo-norte',
        })
        root = ET.fromstring(kml_res['content'])
        self.assertTrue(root.tag.endswith('kml'))

    def test_save_mission_to_project_and_cleanup_preserves_flight_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            datasets = Path(tmp)
            proj = datasets / 'levantamento-a'
            (proj / 'images').mkdir(parents=True)
            (proj / 'odm_orthophoto').mkdir()
            (proj / 'odm_orthophoto' / 'odm_orthophoto.tif').write_bytes(b'tif')

            ws = server.Workspace(datasets=datasets, engine='native')
            res = ws.flight_plan_export({
                'camera_profile': 'dji-mini-4-pro-12mp',
                'mode': 'direct',
                'altitude_m': 60,
                'center': {'lat': -29.7, 'lon': -53.8},
                'width_m': 150,
                'height_m': 100,
                'format': 'json',
                'name': 'campanha-01',
                'save_to_project': True,
                'project': 'levantamento-a',
            })
            saved = Path(res['saved_dir'])
            self.assertTrue((saved / 'mission.json').is_file())
            self.assertTrue((saved / 'planned_route.geojson').is_file())
            self.assertTrue((saved / 'mission.kml').is_file())

            plan = ws.cleanup('preview', {'project': 'levantamento-a'})
            ws.cleanup('execute', {'project': 'levantamento-a', 'plan': plan['plan'], 'confirmation': 'levantamento-a'})
            self.assertTrue((saved / 'mission.json').is_file())
            self.assertFalse((proj / 'odm_orthophoto').exists())


if __name__ == '__main__':
    unittest.main()
