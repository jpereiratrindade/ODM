import json
from pathlib import Path
import tempfile
import unittest
from gui.preflight import geometry_report, selected


class GeometryTests(unittest.TestCase):
    def test_detect_extreme_relative_to_scene_not_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'opensfm').mkdir()
            points = {str(i): {'coordinates': [1000000 + i / 100, 0, 0]} for i in range(200)}
            points['extreme'] = {'coordinates': [1e12, 0, 0]}
            (root / 'opensfm' / 'reconstruction.json').write_text(json.dumps([
                {'shots': {'one': {}}, 'points': points}]))
            self.assertEqual(geometry_report(root)['components'][0]['extreme_points'], 1)

    def test_sampling_includes_ends(self):
        self.assertEqual(selected(range(1000), 3), [0, 500, 999])


try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None


@unittest.skipUnless(cv2, 'OpenCV is available in the ODM container')
class InputTests(unittest.TestCase):
    def test_duplicate_flat_and_corrupt_inputs(self):
        from gui.preflight import analyze
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'images').mkdir()
            for i in range(3):
                cv2.imwrite(str(root / 'images' / f'frame_{i}.jpg'), np.full((80, 100), 128, np.uint8))
            report = analyze(root)
            self.assertFalse(report['errors'])
            self.assertTrue(any('semelhantes' in w for w in report['warnings']))
            self.assertTrue(any('nitidez' in w for w in report['warnings']))
            (root / 'images' / 'broken.jpg').write_text('invalid')
            self.assertTrue(analyze(root)['errors'])

    def test_video_probe_preserves_original(self):
        from gui.preflight import analyze
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'images').mkdir(); path = root / 'images' / 'clip.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (100, 80))
            for i in range(30):
                writer.write(np.full((80, 100, 3), i * 5, np.uint8))
            writer.release(); original = path.read_bytes()
            report = analyze(root)
            self.assertEqual(report['videos'][0]['frames'], 30)
            self.assertGreaterEqual(report['sample_count'], 2)
            self.assertTrue(report['motion_pairs'])
            self.assertEqual(path.read_bytes(), original)

@unittest.skipUnless(cv2, 'OpenCV is available in the ODM container')
class MotionTests(unittest.TestCase):
    def test_stationary_translation_and_unrelated(self):
        from gui.preflight import pair_motion
        rng = np.random.default_rng(3)
        first = rng.integers(0, 256, (480, 640), dtype=np.uint8)
        self.assertEqual(pair_motion(first, first)['status'], 'stationary')
        shifted = cv2.warpAffine(first, np.float32([[1, 0, 20], [0, 1, 5]]), (640, 480))
        result = pair_motion(first, shifted)
        self.assertGreater(result['matches'], 20)
        self.assertGreater(result['motion_percent'], 1)
        self.assertEqual(result['status'], 'homography_dominant')
        other = rng.integers(0, 256, first.shape, dtype=np.uint8)
        self.assertEqual(pair_motion(first, other)['status'], 'insufficient_features')
        blank = np.zeros_like(first)
        self.assertEqual(pair_motion(blank, blank)['status'], 'insufficient_features')

    def test_extraction_covers_clip_preserves_source_and_refuses_overwrite(self):
        from gui.extraction import extract
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / 'clip.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (320, 240))
            rng = np.random.default_rng(5)
            for _ in range(30):
                writer.write(rng.integers(0, 256, (240, 320, 3), dtype=np.uint8))
            writer.release(); original = path.read_bytes()
            destination = root / 'output'; (destination / 'images').mkdir(parents=True)
            report = extract(path, destination, {'limit': 3, 'resolution': 320, 'blur': 0})
            self.assertEqual(report['accepted'], 3)
            self.assertEqual(report['frames'][-1]['frame'], 29)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(ValueError):
                extract(path, destination, {})
            self.assertTrue((destination / 'extraction.json').is_file())


class ParameterTests(unittest.TestCase):
    def test_invalid_extraction_parameters(self):
        from gui.extraction import parameters
        for value in [True, -1, float('inf'), '1']:
            with self.assertRaises(ValueError):
                parameters({'interval': value})
        with self.assertRaises(ValueError):
            parameters({'start': 10, 'end': 5})
        with self.assertRaises(ValueError):
            parameters({'interval': 10, 'max_gap': 5})
