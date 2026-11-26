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

from gui.server import Handler, Workspace


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
        self.assertEqual(request('GET', '/wasm/gsd.wasm')[1][:4], b'\0asm')
        self.assertEqual(request('GET', '/../run.py')[0], 404)
        self.assertEqual(request('GET', '/api/projects', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(request('POST', '/api/run', {'project': 'flight'})[0], 403)
        headers = {'Origin': f'http://{server.authority}', 'X-ODM-Token': server.token}
        self.assertEqual(request('POST', '/api/browse', {'path': str(self.root)}, headers)[0], 200)
        self.assertEqual(request('POST', '/api/browse', {'path': str(self.root)})[0], 403)
        server.workspace = Workspace()
        self.assertEqual(json.loads(request('GET', '/api/projects')[1])['projects'], [])
        status, body = request('POST', '/api/dataset', {'path': str(self.root / 'flight')}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['selected'], 'flight')
        self.assertEqual(request('POST', '/api/dataset', {'path': str(self.root / 'missing')}, headers)[0], 400)
        self.assertEqual(request('POST', '/api/preview', {'project': 'flight'}, headers)[0], 200)
        self.assertEqual(request('POST', '/api/preview', [], headers)[0], 400)
        headers['Origin'] = 'http://evil.example'
        self.assertEqual(request('POST', '/api/run', {'project': 'flight'}, headers)[0], 403)


if __name__ == '__main__':
    unittest.main()
