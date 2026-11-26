"""Contract tests; no photogrammetry runtime or real imagery required."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from gui.server import Handler, Workspace, failure_message, select_engine


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'flight' / 'images').mkdir(parents=True)
        (self.root / 'flight' / 'images' / 'one.JPG').touch()
        self.workspace = Workspace(self.root)

    def test_command_and_inventory(self):
        result = self.workspace.projects()
        self.assertEqual(result, [{'name': 'flight', 'images': 1, 'outputs': []}])
        command = self.workspace.prepare({'project': 'flight', 'dtm': True})
        self.assertIn('--dtm', command)
        self.assertEqual(command[command.index('--project-path') + 1], str(self.root))
        self.assertNotIn('--rerun-all', command)

    def test_podman_mounts_only_selected_project_and_preserves_options(self):
        workspace = Workspace(self.root, engine='podman')
        command = workspace.prepare({'project': 'flight', 'quality': 'high', 'resolution': 3,
                                     'skip-3dmodel': True, 'dtm': True})
        self.assertEqual(command[:2], ['podman', 'run'])
        self.assertIn('--pull=never', command)
        self.assertEqual(command[command.index('--volume') + 1], f'{self.root}/flight:/datasets/flight')
        self.assertEqual(command[command.index('--project-path') + 1], '/datasets')
        self.assertIn('--skip-3dmodel', command)
        self.assertIn('--dtm', command)
        self.assertNotIn(str(self.root), command)

    def test_auto_engine_uses_existing_image_and_error_is_explained(self):
        with patch('gui.server.shutil.which', return_value='/usr/bin/podman'), \
                patch('gui.server.Path.exists', return_value=False), \
                patch('gui.server.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertEqual(select_engine('auto', 'local-odm'), 'podman')
            run.assert_called_once_with(['podman', 'image', 'exists', 'local-odm'], capture_output=True, timeout=15)
        message = failure_message([{'text': "ImportError: cannot import name '_find_unsafe' from 'shlex'"}])
        self.assertIn('Python', message)
        self.assertIn('Podman', message)

    def test_podman_stop_stops_container(self):
        workspace = Workspace(self.root, engine='podman')
        with patch('gui.server.subprocess.run') as run:
            run.return_value.returncode = 0
            workspace.stop_container()
        run.assert_called_once_with(['podman', 'stop', '--time', '10', workspace.container_name],
                                    capture_output=True, timeout=30)

    def test_empty_dense_cloud_failure_and_persisted_history(self):
        log = {'success': False, 'options': {'pc_quality': 'low'}, 'endTime': '2026-11-26',
               'stages': [{'name': 'openmvs'}], 'error': {'code': 139},
               'processes': [{'output': ['Densifying point-cloud completed: 0 points',
                                         '\x1b[91m[ERROR] no valid point-cloud for the ROI estimation\x1b[0m']}]}
        (self.root / 'flight' / 'log.json').write_text(json.dumps(log))
        self.workspace.configure(str(self.root / 'flight'))
        status = self.workspace.snapshot()
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['stage'], 'openmvs')
        self.assertIn('zero pontos', status['error'])
        self.assertIn('Equilíbrio', status['error'])
        self.assertNotIn('\x1b', status['error'])
        self.assertEqual(self.workspace.history('flight')['quality'], 'low')
        command = self.workspace.prepare({'project': 'flight', 'quality': 'medium'})
        self.assertEqual(command[command.index('--rerun-from') + 1], 'opensfm')
        self.assertNotIn('--rerun-from', self.workspace.prepare({'project': 'flight', 'quality': 'low'}))

    def test_result_paths_reject_unknown_products_and_symlinks(self):
        output = self.root / 'flight' / 'odm_report' / 'report.pdf'
        output.parent.mkdir()
        output.write_bytes(b'%PDF-test')
        self.assertEqual(self.workspace.output_path('flight', 'report'), output)
        for project, product in [('flight', '../log.json'), ('../flight', 'report'), ('flight', 'ortho')]:
            with self.subTest(project=project, product=product), self.assertRaises(ValueError):
                self.workspace.output_path(project, product)
        if os.name != 'nt':
            outside = self.root / 'outside.pdf'; outside.write_bytes(b'private')
            output.unlink(); output.symlink_to(outside)
            with self.assertRaises(ValueError):
                self.workspace.output_path('flight', 'report')

    def test_preview_cache_uses_file_revision(self):
        output = self.root / 'flight' / 'odm_orthophoto' / 'odm_orthophoto.tif'
        output.parent.mkdir(); output.write_bytes(b'first')
        with patch('gui.server.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({'kind': 'raster', 'image': 'preview'})
            self.workspace.view('flight', 'ortho')
            self.workspace.view('flight', 'ortho')
            self.assertEqual(run.call_count, 1)
            output.write_bytes(b'new raster')
            self.workspace.view('flight', 'ortho')
            self.assertEqual(run.call_count, 2)

    def test_media_inventory_and_restricted_paths(self):
        images = self.root / 'flight' / 'images'
        (images / 'clip.mp4').write_bytes(b'video')
        (images / 'notes.txt').write_text('not media')
        self.assertEqual(len(self.workspace.media_list('flight')), 2)
        self.assertEqual(self.workspace.media_list('flight')[0]['kind'], 'video')
        for name in ['../notes.txt', 'notes.txt', '/etc/passwd', 'sub/file.jpg', 'a\\b.JPG']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.workspace.media_path('flight', name)
        if os.name != 'nt':
            outside = self.root / 'private.JPG'; outside.write_bytes(b'private')
            (images / 'escape.JPG').symlink_to(outside)
            with self.assertRaises(ValueError):
                self.workspace.media_path('flight', 'escape.JPG')

    def test_start_empty_then_select_dataset(self):
        workspace = Workspace()
        self.assertEqual(workspace.projects(), [])
        with self.assertRaises(ValueError):
            workspace.prepare({'project': 'flight'})
        self.assertEqual(workspace.configure(str(self.root / 'flight')), 'flight')
        self.assertEqual(workspace.datasets, self.root)
        self.assertEqual(workspace.projects()[0]['name'], 'flight')
        workspace.state = 'running'
        with self.assertRaises(ValueError):
            workspace.configure(str(self.root))
        self.assertEqual(workspace.datasets, self.root)
        workspace.state = 'idle'
        for path in ['', None, 'relative/path', str(self.root / 'missing')]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                workspace.configure(path)
        self.assertIsNone(workspace.configure(str(self.root)))
        self.assertEqual(workspace.configure(str(self.root / 'flight' / 'images')), 'flight')

    def test_browse_folders_without_changing_selection(self):
        workspace = Workspace()
        result = workspace.browse(str(self.root))
        self.assertEqual(result['folders'][0]['name'], 'flight')
        self.assertTrue(result['folders'][0]['dataset'])
        self.assertTrue(workspace.browse(str(self.root / 'flight'))['dataset'])
        self.assertIsNone(workspace.datasets)
        with self.assertRaises(ValueError):
            workspace.browse(str(self.root / 'missing'))

    def test_reject_escape_and_bad_parameters(self):
        for name in ['../flight', '/tmp', '..', '', None, 'a\\b']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.workspace.prepare({'project': name})
        for value in [float('nan'), float('inf'), -1, 0, 101, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.workspace.prepare({'project': 'flight', 'resolution': value})
        with self.assertRaises(ValueError):
            self.workspace.prepare({'project': 'flight', 'quality': '--help'})
        if os.name != 'nt':
            (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.workspace.project_path('escape')

    def wait_finished(self):
        deadline = time.monotonic() + 5
        while self.workspace.snapshot()['state'] in ('running', 'stopping'):
            if time.monotonic() > deadline:
                self.fail('Process did not finish')
            time.sleep(0.01)

    def test_real_process_logs_exit_and_cursor(self):
        command = [sys.executable, '-u', '-c', "print('test log'); print('Running odm_dem stage'); raise SystemExit(7)"]
        with patch.object(self.workspace, 'prepare', return_value=command):
            self.workspace.start({'project': 'flight'})
        self.wait_finished()
        status = self.workspace.snapshot()
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['exit_code'], 7)
        self.assertEqual(status['logs'][0]['text'], 'test log')
        self.assertEqual(status['stage'], 'odm_dem')
        self.assertEqual(self.workspace.snapshot(status['logs'][-1]['id'])['logs'], [])

    def test_cancel_and_reject_concurrent_run(self):
        command = [sys.executable, '-u', '-c', 'import time; time.sleep(30)']
        with patch.object(self.workspace, 'prepare', return_value=command):
            self.workspace.start({'project': 'flight'})
            try:
                with self.assertRaises(ValueError):
                    self.workspace.start({'project': 'flight'})
            finally:
                self.workspace.stop()
        self.wait_finished()
        self.assertEqual(self.workspace.snapshot()['state'], 'cancelled')

    def test_http_origin_token_and_static_routes(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.workspace = self.workspace
        server.token = 'test-secret'
        server.authority = f'127.0.0.1:{server.server_port}'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, data=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            connection.request(method, path, json.dumps(data) if data is not None else None, headers or {})
            response = connection.getresponse()
            body = response.read()
            connection.close()
            return response.status, body

        self.assertEqual(request('GET', '/')[0], 200)
        self.assertIn(b'GNU AFFERO GENERAL PUBLIC LICENSE', request('GET', '/LICENSE')[1])
        self.assertEqual(request('GET', '/wasm/gsd.wasm')[1][:4], b'\0asm')
        self.assertEqual(request('GET', '/../run.py')[0], 404)
        self.assertEqual(request('GET', '/api/projects', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(request('POST', '/api/run', {'project': 'flight'})[0], 403)
        headers = {'Origin': f'http://{server.authority}', 'X-ODM-Token': server.token}
        output = self.root / 'flight' / 'odm_report' / 'report.pdf'
        output.parent.mkdir(); output.write_bytes(b'%PDF-test')
        self.assertEqual(request('GET', '/api/output?project=flight&product=report')[0], 403)
        download_headers = {'Cookie': 'odm_session=' + server.token}
        self.assertEqual(request('GET', '/api/output?project=flight&product=report', headers=download_headers)[1], b'%PDF-test')
        self.assertEqual(request('GET', '/api/output?project=flight&product=../LICENSE', headers=download_headers)[0], 400)
        video = self.root / 'flight' / 'images' / 'clip.mp4'; video.write_bytes(b'0123456789')
        route = '/api/media?project=flight&file=clip.mp4&mode=original'
        self.assertEqual(request('GET', route)[0], 403)
        self.assertEqual(request('GET', route, headers=download_headers)[1], b'0123456789')
        self.assertEqual(request('GET', route, headers=dict(download_headers, Range='bytes=2-5')), (206, b'2345'))
        self.assertEqual(request('GET', route, headers=dict(download_headers, Range='bytes=-3')), (206, b'789'))
        self.assertEqual(request('GET', route, headers=dict(download_headers, Range='bytes=7-')), (206, b'789'))
        for invalid in ['bytes=40-', 'bytes=-0', 'bytes=6-2', 'bytes=0-1,3-4', 'nonsense']:
            self.assertEqual(request('GET', route, headers=dict(download_headers, Range=invalid))[0], 416)
        self.assertEqual(request('POST', '/api/browse', {'path': str(self.root)}, headers)[0], 200)
        self.assertEqual(request('POST', '/api/browse', {'path': str(self.root)})[0], 403)
        server.workspace = Workspace()
        self.assertEqual(json.loads(request('GET', '/api/projects')[1])['projects'], [])
        status, body = request('POST', '/api/dataset', {'path': str(self.root / 'flight')}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['selected'], 'flight')
        self.assertEqual(request('POST', '/api/dataset', {'path': str(self.root / 'missing')}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/preview', {'project': 'flight'}, headers)[0], 200)
        self.assertEqual(request('POST', '/api/planning', {'project': '../outside'}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/project', {'project': 'flight'}, headers)[0], 200)
        self.assertEqual(server.workspace.selected_project, 'flight')
        self.assertEqual(request('POST', '/api/project', {'project': '../outside'}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/preview', [], headers)[0], 400)
        headers['Origin'] = 'http://evil.example'
        self.assertEqual(request('POST', '/api/run', {'project': 'flight'}, headers)[0], 403)


if __name__ == '__main__':
    unittest.main()
