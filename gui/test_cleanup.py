"""Cleanup safety contracts; operate only on disposable fixture projects."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gui.cleanup import CleanupManager
from gui.server import Workspace


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'flight'
        (self.root / 'images').mkdir(parents=True)
        (self.root / 'videos').mkdir()
        for name in ['images/photo.JPG', 'images/video.mp4', 'images/mask.png',
                     'videos/original.mov', 'gcp_list.txt', 'geo.txt', 'align.tif',
                     'cameras.json', 'calibration.json', 'notes.txt', 'original.tif']:
            (self.root / name).write_bytes(('original-' + name).encode())
        self.originals = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        (self.root / 'opensfm').mkdir()
        (self.root / 'opensfm' / 'cache.bin').write_bytes(b'cache')
        (self.root / 'images.json').write_bytes(b'generated index')
        self.manager = CleanupManager()

    def assert_originals(self):
        for path, data in self.originals.items():
            self.assertEqual(path.read_bytes(), data, str(path))

    def test_preview_clean_and_restore_preserve_all_originals(self):
        preview = self.manager.preview(self.root)
        self.assertEqual({e['name'] for e in preview['entries']}, {'opensfm', 'images.json'})
        self.assertIn('images', preview['preserved'])
        self.assertIn('cameras.json', preview['preserved'])
        result = self.manager.execute(self.root, preview['plan'], 'flight')
        self.assertFalse((self.root / 'opensfm').exists())
        self.assert_originals()
        self.assertEqual((Path(result['path']) / 'opensfm' / 'cache.bin').read_bytes(), b'cache')
        # A new manager can recover the persistent backup after server restart.
        fresh = CleanupManager()
        self.assertEqual(fresh.backups(self.root)[0]['id'], result['backup'])
        fresh.restore(self.root, result['backup'])
        self.assertEqual((self.root / 'opensfm' / 'cache.bin').read_bytes(), b'cache')
        self.assertEqual(fresh.backups(self.root), [])
        self.assert_originals()

    def test_confirmation_and_stale_preview_block_changes(self):
        preview = self.manager.preview(self.root)
        with self.assertRaises(ValueError):
            self.manager.execute(self.root, preview['plan'], 'wrong project')
        (self.root / 'opensfm' / 'new.bin').write_bytes(b'new data')
        with self.assertRaises(ValueError):
            self.manager.execute(self.root, preview['plan'], 'flight')
        self.assertTrue((self.root / 'images.json').is_file())
        self.assert_originals()

    def test_restore_never_overwrites_new_results(self):
        plan = self.manager.preview(self.root)
        backup = self.manager.execute(self.root, plan['plan'], 'flight')
        (self.root / 'images.json').write_bytes(b'new processing')
        with self.assertRaises(ValueError):
            self.manager.restore(self.root, backup['backup'])
        self.assertEqual((self.root / 'images.json').read_bytes(), b'new processing')
        self.assertTrue((Path(backup['path']) / 'opensfm').is_dir())
        self.assert_originals()

    def test_active_run_blocks_all_cleanup_actions(self):
        workspace = Workspace(self.root.parent)
        for state in ['running', 'stopping']:
            workspace.state = state
            for action in ['preview', 'execute', 'restore']:
                with self.subTest(state=state, action=action), self.assertRaises(ValueError):
                    workspace.cleanup(action, {'project': 'flight'})
        self.assert_originals()

    def test_symlink_artifact_and_archive_are_not_followed(self):
        (self.root / 'odm_report').symlink_to(self.root / 'images', target_is_directory=True)
        preview = self.manager.preview(self.root)
        self.assertIn('odm_report', preview['preserved'])
        self.assertNotIn('odm_report', [e['name'] for e in preview['entries']])
        archive = self.root.parent / '.odm-cleanup'
        archive.symlink_to(self.root / 'images', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.manager.execute(self.root, preview['plan'], 'flight')
        self.assert_originals()

    def test_move_failure_rolls_back_without_deleting_contents(self):
        preview = self.manager.preview(self.root)
        original_rename = Path.rename
        def fail_on_second_artifact(path, destination):
            if path == self.root / 'opensfm':
                raise OSError('injected move failure')
            return original_rename(path, destination)
        with patch.object(Path, 'rename', fail_on_second_artifact), self.assertRaises(ValueError):
            self.manager.execute(self.root, preview['plan'], 'flight')
        self.assertEqual((self.root / 'images.json').read_bytes(), b'generated index')
        self.assertEqual((self.root / 'opensfm' / 'cache.bin').read_bytes(), b'cache')
        self.assert_originals()


if __name__ == '__main__':
    unittest.main()
