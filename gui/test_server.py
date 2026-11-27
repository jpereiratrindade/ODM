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

    def test_external_container_attachment_and_guards(self):
        workspace = Workspace(self.root, engine='podman')
        container = {'Id': 'external-id', 'State': {'Running': True},
                     'Config': {'Cmd': ['--project-path', '/datasets', 'flight']},
                     'Mounts': [{'Source': str(self.root / 'flight'), 'Destination': '/datasets/flight'}]}
        from subprocess import CompletedProcess
        with patch('gui.server.subprocess.run', side_effect=[
                CompletedProcess([], 0, 'external-id', ''),
                CompletedProcess([], 0, json.dumps([container]), '')]), \
                patch('gui.server.subprocess.Popen'), patch('gui.server.threading.Thread'):
            workspace.discover_external()
        self.assertTrue(workspace.external)
        self.assertEqual(workspace.state, 'running')
        self.assertEqual(workspace.project, 'flight')
        self.assertEqual(workspace.container_name, 'external-id')
        with self.assertRaises(ValueError):
            workspace.start({'project': 'flight'})
        with self.assertRaises(ValueError):
            workspace.cleanup('execute', {'project': 'flight'})

    def test_external_discovery_ignores_other_mounts(self):
        workspace = Workspace(self.root, engine='podman')
        from subprocess import CompletedProcess
        container = {'Id': 'other', 'State': {'Running': True},
                     'Config': {'Cmd': ['--project-path', '/datasets', 'flight']},
                     'Mounts': [{'Source': '/unrelated/flight', 'Destination': '/datasets/flight'}]}
        with patch('gui.server.subprocess.run', side_effect=[
                CompletedProcess([], 0, 'other', ''),
                CompletedProcess([], 0, json.dumps([container]), '')]):
            workspace.discover_external()
        self.assertEqual(workspace.state, 'idle')

    def test_preflight_errors_block_processing(self):
        with patch.object(self.workspace, 'preflight', return_value={'errors': ['Foto inválida']}), \
                patch('gui.server.subprocess.Popen') as popen:
            with self.assertRaisesRegex(ValueError, 'Foto inválida'):
                self.workspace.start({'project': 'flight'})
            popen.assert_not_called()

    def test_extraction_parameters_are_validated_and_forwarded(self):
        command = self.workspace.prepare({'project': 'flight', 'video-limit': 120,
                                          'video-resolution': 1920, 'max-concurrency': 4})
        for key, value in [('video-limit', '120'), ('video-resolution', '1920'), ('max-concurrency', '4')]:
            self.assertEqual(command[command.index('--' + key) + 1], value)
            for invalid in [True, 0, '5', float('nan')]:
                with self.assertRaises(ValueError):
                    self.workspace.prepare({'project': 'flight', key: invalid})

    def test_custom_extraction_targets_new_project_and_preserves_source(self):
        source = self.root / 'flight' / 'images' / 'clip.mp4'
        source.write_bytes(b'original')
        workspace = Workspace(self.root, engine='native')
        with patch('gui.server.importlib.util.find_spec', return_value=True), \
                patch('gui.server.subprocess.Popen') as popen, patch('gui.server.threading.Thread'):
            workspace.extract_video({'project': 'flight', 'file': 'clip.mp4',
                                     'destination': 'flight-frames', 'extraction': {'interval': 2}})
        self.assertEqual(source.read_bytes(), b'original')
        self.assertTrue((self.root / 'flight-frames' / 'images').is_dir())
        self.assertEqual(workspace.project, 'flight-frames')
        self.assertEqual(workspace.state, 'running')
        command = popen.call_args.args[0]
        self.assertEqual(json.loads(command[-1])['interval'], 2)
        workspace.state = 'idle'
        with self.assertRaisesRegex(ValueError, 'já existe'):
            workspace.extract_video({'project': 'flight', 'file': 'clip.mp4', 'destination': 'flight-frames'})
        with self.assertRaises(ValueError):
            workspace.extract_video({'project': 'flight', 'file': 'clip.mp4', 'destination': '../escape'})

    def test_tool_source_does_not_change_active_processing(self):
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / 'flight'
            (other / 'images').mkdir(parents=True)
            (other / 'images' / 'external.jpg').write_bytes(b'external')
            workspace = self.workspace
            workspace.state = 'running'; workspace.project = 'flight'; workspace.selected_project = 'flight'
            workspace.command = ['processing-command']
            opened = workspace.open_tool_source(str(other))
            context = workspace.tool_context({'source': opened['source'], 'project': 'flight'})
            self.assertEqual(context.media_path('flight', 'external.jpg'), other / 'images' / 'external.jpg')
            self.assertEqual(workspace.datasets, self.root)
            self.assertEqual(workspace.state, 'running')
            self.assertEqual(workspace.command, ['processing-command'])
            self.assertEqual(workspace.selected_project, 'flight')
            self.assertEqual(workspace.open_tool_source(str(other / 'images'))['source'], opened['source'])
            for data in [{'source': 'missing', 'project': 'flight'}, {'source': opened['source'], 'project': 'another'}]:
                with self.assertRaises(ValueError):
                    workspace.tool_context(data)
            with self.assertRaises(ValueError):
                workspace.open_tool_source(str(Path(folder)))

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
        with patch.object(self.workspace, 'prepare', return_value=command), \
                patch.object(self.workspace, 'preflight', return_value={'errors': []}):
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
        with patch.object(self.workspace, 'prepare', return_value=command), \
                patch.object(self.workspace, 'preflight', return_value={'errors': []}):
            self.workspace.start({'project': 'flight'})
            try:
                with self.assertRaises(ValueError):
                    self.workspace.start({'project': 'flight'})
            finally:
                self.workspace.stop()
        self.wait_finished()
        self.assertEqual(self.workspace.snapshot()['state'], 'cancelled')

    def test_browser_photo_upload_preserves_active_job_and_rejects_paths(self):
        import io
        workspace = self.workspace
        workspace.state = 'running'; workspace.project = 'flight'; workspace.command = ['active-job']
        opened = workspace.browser_upload_start('browser-photos')
        key = opened['upload']; photo = b'\xff\xd8\xff' + b'image-data'
        with self.assertRaises(ValueError):
            workspace.browser_upload_start('flight')
        with self.assertRaises(ValueError):
            workspace.browser_upload_start('../escape')
        with self.assertRaises(ValueError):
            workspace.browser_upload_file(key, '../bad.jpg', io.BytesIO(photo), len(photo))
        with self.assertRaises(ValueError):
            workspace.browser_upload_file(key, 'bad.jpg', io.BytesIO(b'not-image'), 9)
        with self.assertRaises(ValueError):
            workspace.browser_upload_file(key, 'short.jpg', io.BytesIO(b'short'), 100)
        self.assertFalse(list((self.root / 'browser-photos/images').glob('*.part')))
        for i in range(3):
            workspace.browser_upload_file(key, f'photo_{i}.jpg', io.BytesIO(photo), len(photo))
        with self.assertRaises(ValueError):
            workspace.browser_upload_file(key, 'photo_0.jpg', io.BytesIO(photo), len(photo))
        result = workspace.browser_upload_finish(key, {'engine': 'browser-test'})
        self.assertEqual(result['files'], 3)
        manifest = json.loads((self.root / 'browser-photos/browser-import.json').read_text())
        self.assertTrue(manifest['complete'])
        self.assertEqual(workspace.state, 'running'); self.assertEqual(workspace.project, 'flight')
        self.assertEqual(workspace.command, ['active-job'])
        self.assertCountEqual([f.name for f in (self.root / 'browser-photos/images').iterdir()], ['photo_0.jpg', 'photo_1.jpg', 'photo_2.jpg'])
        with self.assertRaises(ValueError):
            workspace.browser_upload_file(key, 'late.jpg', io.BytesIO(photo), len(photo))

    def test_lan_authentication_allowed_hosts_and_origin(self):
        import base64
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.workspace = self.workspace; server.token = 'csrf-test'
        server.authority = f'127.0.0.1:{server.server_port}'
        lan = f'10.0.0.10:{server.server_port}'
        server.allowed_authorities = {server.authority, lan}
        server.lan_password = 'a-long-test-password'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def request(method, path, extra=None, data=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            headers = {'Host': lan, **(extra or {})}
            connection.request(method, path, json.dumps(data) if data is not None else None, headers)
            response = connection.getresponse(); body = response.read(); status = response.status
            connection.close(); return status, body
        for path in ['/', '/api/projects', '/api/status?since=0', '/sources.js']:
            self.assertEqual(request('GET', path)[0], 401)
        self.assertEqual(request('GET', '/', {'Authorization': 'Basic invalid'})[0], 401)
        auth = {'Authorization': 'Basic ' + base64.b64encode(b'paralaxe:a-long-test-password').decode()}
        self.assertEqual(request('GET', '/', auth)[0], 200)
        legacy = {'Authorization': 'Basic ' + base64.b64encode(b'odm:a-long-test-password').decode()}
        self.assertEqual(request('GET', '/', legacy)[0], 200)
        self.assertEqual(request('GET', '/api/projects', auth)[0], 200)
        self.assertEqual(request('GET', '/', dict(auth, Host='evil.example'))[0], 403)
        headers = dict(auth, Origin='http://' + lan, **{'X-ODM-Token': server.token})
        self.assertEqual(request('POST', '/api/preview', headers, {'project': 'flight'})[0], 200)
        headers['Origin'] = 'http://evil.example'
        self.assertEqual(request('POST', '/api/preview', headers, {'project': 'flight'})[0], 403)

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
        self.assertEqual(request('GET', '/sources.js')[0], 200)
        self.assertIn(b'Paralaxe', request('GET', '/')[1])
        self.assertIn(b'<svg', request('GET', '/assets/favicon.svg')[1])
        self.assertIn(b'id="analysis"', request('GET', '/assets/icons.svg')[1])
        self.assertEqual(json.loads(request('GET', '/manifest.webmanifest')[1])['short_name'], 'Paralaxe')
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
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / 'flight'; (other / 'images').mkdir(parents=True)
            (other / 'images' / 'external.mp4').write_bytes(b'external-video')
            (other / 'odm_report').mkdir(); (other / 'odm_report' / 'report.pdf').write_bytes(b'%PDF-external')
            server.workspace.state = 'running'; server.workspace.project = 'flight'
            status, body = request('POST', '/api/tool-source', {'path': str(other)}, headers)
            self.assertEqual(status, 200)
            source = json.loads(body)['source']
            scoped = {'source': source, 'project': 'flight'}
            status, body = request('POST', '/api/media-list', scoped, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['files'][0]['name'], 'external.mp4')
            self.assertEqual(request('GET', '/api/media?project=flight&file=external.mp4&source=' + source, headers=download_headers)[1], b'external-video')
            self.assertEqual(request('GET', '/api/output?project=flight&product=report&source=' + source, headers=download_headers)[1], b'%PDF-external')
            self.assertEqual(request('POST', '/api/project', scoped, headers)[0], 400)
            self.assertEqual(server.workspace.state, 'running')
            self.assertEqual(server.workspace.datasets, self.root)
        server.workspace = Workspace()
        self.assertEqual(json.loads(request('GET', '/api/projects')[1])['projects'], [])
        status, body = request('POST', '/api/dataset', {'path': str(self.root / 'flight')}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['selected'], 'flight')
        self.assertEqual(request('POST', '/api/dataset', {'path': str(self.root / 'missing')}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/preview', {'project': 'flight'}, headers)[0], 200)
        self.assertEqual(request('POST', '/api/cleanup-preview', {'project': 'flight'})[0], 403)
        self.assertEqual(request('POST', '/api/cleanup-preview', {'project': 'flight'}, headers)[0], 200)
        self.assertEqual(request('POST', '/api/cleanup', {'project': 'flight', 'plan': 'invalid', 'confirmation': 'flight'}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/planning', {'project': '../outside'}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/project', {'project': 'flight'}, headers)[0], 200)
        self.assertEqual(server.workspace.selected_project, 'flight')
        self.assertEqual(request('POST', '/api/project', {'project': '../outside'}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/preview', [], headers)[0], 400)
        headers['Origin'] = 'http://evil.example'
        self.assertEqual(request('POST', '/api/run', {'project': 'flight'}, headers)[0], 403)


if __name__ == '__main__':
    unittest.main()
