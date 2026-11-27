"""Real Firefox integration: local decode/WASM, explicit upload, cancellation.
Run with: python -m gui.test_local_browser (Firefox and ffmpeg required).
"""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from gui.server import Handler, Workspace


def run():
    if not shutil.which('firefox') or not shutil.which('ffmpeg'):
        raise SystemExit('This integration check requires Firefox and ffmpeg.')
    with tempfile.TemporaryDirectory(prefix='odm-local-integration-') as directory:
        root = Path(directory); datasets = root / 'datasets'; (datasets / 'active/images').mkdir(parents=True)
        video = root / 'clip.webm'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=400x300:rate=10', '-t', '3', '-c:v', 'libvpx-vp9', '-an', str(video)], check=True)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.workspace = Workspace(datasets)
        server.workspace.selected_project = server.workspace.project = 'active'
        server.workspace.state = 'running'; server.workspace.stage = 'opensfm'
        server.workspace.command = ['synthetic-active-job']
        server.token = 'integration-test-token'; server.authority = f'127.0.0.1:{server.server_port}'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); marionette_port = probe.getsockname()[1]
        profile = root / 'profile'; profile.mkdir()
        (profile / 'user.js').write_text(f'user_pref("marionette.port", {marionette_port});\n')
        browser = subprocess.Popen(['firefox', '--headless', '--no-remote', '--marionette', '--profile', str(profile)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = None
        try:
            for _ in range(100):
                try:
                    client = socket.create_connection(('127.0.0.1', marionette_port), timeout=1); break
                except OSError:
                    time.sleep(.1)
            if client is None:
                raise RuntimeError('Firefox automation port did not open.')
            client.settimeout(180)
            def receive():
                prefix = b''
                while not prefix.endswith(b':'):
                    byte = client.recv(1)
                    if not byte: raise RuntimeError('Firefox disconnected.')
                    prefix += byte
                size = int(prefix[:-1]); body = b''
                while len(body) < size: body += client.recv(size - len(body))
                return json.loads(body)
            receive(); sequence = 0
            def call(command, params=None):
                nonlocal sequence
                sequence += 1; body = json.dumps([0, sequence, command, params or {}]).encode()
                client.sendall(str(len(body)).encode() + b':' + body)
                response = receive()
                if response[2]: raise RuntimeError(response[2]['message'])
                return response[3]
            def script(code):
                wrapped = 'const done=arguments[arguments.length-1];(async()=>{' + code + '})().then(done).catch(e=>done({error:e.message}));'
                result = call('WebDriver:ExecuteAsyncScript', {'script': wrapped, 'args': []})['value']
                if isinstance(result, dict) and result.get('error'): raise RuntimeError(result['error'])
                return result
            call('WebDriver:NewSession', {'capabilities': {}})
            call('WebDriver:SetTimeouts', {'script': 180000})
            call('WebDriver:Navigate', {'url': f'http://{server.authority}/#analise'})
            script("await loadProjects();document.getElementById('analysis-local-mode').click();return true;")
            element = call('WebDriver:FindElement', {'using': 'css selector', 'value': '#local-files'})['value']
            call('WebDriver:ElementSendKeys', {'id': next(iter(element.values())), 'text': str(video)})
            result = script("""
              let uploads=0;const fetchOriginal=window.fetch;window.fetch=(...args)=>{if(String(args[0]).includes('local-upload'))uploads++;return fetchOriginal(...args);};window.localUploadCount=()=>uploads;
              for(const [id,value] of [['local-limit',3],['local-motion',0],['local-blur',0]])document.getElementById(id).value=value;
              await localTask(localExtract);
              return {status:document.getElementById('local-status').textContent,report:document.getElementById('local-report').textContent,selection:document.getElementById('local-selection-summary').textContent,uploads};
            """)
            assert '3 fotos' in result['status'], result
            assert result['uploads'] == 0, result
            assert '3 de 3' in result['selection'], result
            assert not (datasets / 'browser-output').exists()
            result = script("""
              document.getElementById('local-upload-name').value='browser-output';await localTask(localUpload);
              const status=await api('status?since=0');return {message:document.getElementById('local-status').textContent,project:status.project,state:status.state};
            """)
            assert result['project'] == 'active' and result['state'] == 'running', result
            manifest = json.loads((datasets / 'browser-output/browser-import.json').read_text())
            assert manifest['complete'] and len(manifest['files']) == 3
            assert all(file.suffix == '.jpg' for file in (datasets / 'browser-output/images').iterdir())
            result = script("""
              window.confirm=()=>true;const task=localTask(localExtract);document.getElementById('local-cancel').click();await task;
              return {status:document.getElementById('local-status').textContent,cancelHidden:document.getElementById('local-cancel').hidden};
            """)
            assert 'cancelad' in result['status'].lower(), result
            assert result['cancelHidden'], result
            assert server.workspace.command == ['synthetic-active-job']
            print('Firefox integration passed: local video/WASM extraction, no automatic upload, explicit photo-only upload, active job preserved, cancellation.')
        finally:
            if client: client.close()
            browser.terminate(); browser.wait(timeout=10)
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    run()
