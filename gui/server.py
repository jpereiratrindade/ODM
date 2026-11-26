#!/usr/bin/env python3
"""Experimental loopback-only ODM interface; no extra Python dependencies."""
import argparse
from collections import deque
import json
import math
import os
import re
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / 'web'
QUALITIES = {'lowest', 'low', 'medium', 'high', 'ultra'}
OUTPUTS = ('odm_orthophoto/odm_orthophoto.tif', 'odm_dem/dsm.tif',
           'odm_dem/dtm.tif', 'odm_georeferencing/odm_georeferenced_model.laz',
           'odm_texturing/odm_textured_model.obj', 'odm_report/report.pdf')
ODM_IMAGE = 'docker.io/opendronemap/odm:latest'


def select_engine(requested, image):
    if requested != 'auto':
        return requested
    binaries = ROOT / 'SuperBuild' / 'install' / 'bin'
    if all((binaries / name).exists() for name in ('odm_orthophoto', 'DensifyPointCloud')):
        return 'native'
    if shutil.which('podman'):
        probe = subprocess.run(['podman', 'image', 'exists', image], capture_output=True, timeout=15)
        if probe.returncode == 0:
            return 'podman'
    return 'native'


def failure_message(logs):
    text = '\n'.join(line['text'] for line in logs)
    if "cannot import name '_find_unsafe'" in text:
        return 'O Python do sistema é incompatível com esta versão do ODM. Use o motor Podman ou um ambiente nativo ODM configurado.'
    if 'ModuleNotFoundError' in text or 'ImportError' in text:
        return 'Faltam dependências no ambiente de processamento. Use o motor Podman ou o ambiente completo do ODM.'
    if 'image not known' in text or 'image not found' in text:
        return 'A imagem de processamento ODM não está instalada no Podman. Confira a instalação do motor.'
    if 'Permission denied' in text or 'permission denied' in text:
        return 'O processamento não conseguiu acessar arquivos ou gravar no projeto. Confira as permissões da pasta.'
    errors = [line['text'] for line in logs if re.search(r'\[ERROR\]|Error:|Exception:', line['text'])]
    return errors[-1][-500:] if errors else 'O processamento parou antes de concluir. Veja o diagnóstico técnico para identificar a causa.'


class Workspace:
    def __init__(self, datasets=None, engine='native', image=ODM_IMAGE):
        self.datasets = Path(datasets).resolve() if datasets is not None else None
        self.lock = threading.RLock()
        self.process = None
        self.logs = deque(maxlen=2000)
        self.sequence = 0
        self.state = 'idle'
        self.project = None
        self.command = []
        self.exit_code = None
        self.stage = None
        self.engine = engine
        self.image = image
        self.container_name = 'odm-gui-' + secrets.token_hex(8)
        self.error = None
        self.selected_project = None

    def browse(self, location=None):
        if location is not None and not isinstance(location, str):
            raise ValueError('Pasta inválida.')
        path = Path(location).expanduser().resolve() if location else (self.datasets or Path.home())
        if not path.is_dir():
            raise ValueError('Pasta não encontrada.')
        folders = []
        for child in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
            try:
                if child.is_dir() and not child.name.startswith('.'):
                    folders.append({'name': child.name, 'path': str(child.resolve()),
                                    'dataset': (child / 'images').is_dir()})
            except OSError:
                continue
        return {'path': str(path), 'parent': str(path.parent),
                'dataset': (path / 'images').is_dir(), 'folders': folders,
                'shortcuts': [{'name': 'Minha pasta', 'path': str(Path.home())},
                              {'name': 'Discos e arquivos', 'path': str(Path(path.anchor))},
                              {'name': 'Pasta do ODM', 'path': str(ROOT)}]}

    def configure(self, location):
        if not isinstance(location, str) or not location.strip():
            raise ValueError('Informe caminho de uma pasta local.')
        path = Path(location).expanduser()
        if not path.is_absolute():
            raise ValueError('Informe caminho completo da pasta.')
        path = path.resolve()
        if path.name == 'images' and path.is_dir():
            path = path.parent
        if not path.is_dir():
            raise ValueError('Pasta não encontrada no computador do servidor.')
        selected = None
        if (path / 'images').is_dir():
            selected = path.name
            path = path.parent
        with self.lock:
            if self.state in ('running', 'stopping'):
                raise ValueError('Aguarde encerrar execução antes de trocar pasta.')
            # Check access before replacing the current workspace.
            list(path.iterdir())
            self.datasets = path
            self.selected_project = selected
        return selected

    def project_path(self, name):
        if self.datasets is None:
            raise ValueError('Indique pasta do dataset pela interface.')
        if not isinstance(name, str) or not name or name.startswith('.') or '/' in name or '\\' in name:
            raise ValueError('Nome de projeto inválido.')
        path = (self.datasets / name).resolve()
        if path.parent != self.datasets or not (path / 'images').is_dir():
            raise ValueError('Projeto deve estar na pasta de datasets e conter images/.')
        return path

    def projects(self):
        result = []
        if self.datasets is None:
            return result
        for path in sorted(self.datasets.iterdir()):
            try:
                path = self.project_path(path.name)
            except ValueError:
                continue
            images = sum(1 for p in (path / 'images').iterdir()
                         if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.tif', '.tiff', '.dng', '.png'})
            result.append({'name': path.name, 'images': images,
                           'outputs': [p for p in OUTPUTS if (path / p).is_file()]})
        return result

    def prepare(self, data):
        path = self.project_path(data.get('project'))
        quality = data.get('quality', 'medium')
        if quality not in QUALITIES:
            raise ValueError('Qualidade inválida.')
        resolution = data.get('resolution', 5)
        if isinstance(resolution, bool):
            raise ValueError('Resolução inválida.')
        resolution = float(resolution)
        if not math.isfinite(resolution) or not 0.1 <= resolution <= 100:
            raise ValueError('Resolução deve estar entre 0,1 e 100 cm/pixel.')
        options = ['--project-path', '/datasets' if self.engine == 'podman' else str(self.datasets), path.name,
                   '--feature-quality', quality, '--pc-quality', quality,
                   '--orthophoto-resolution', str(resolution)]
        for flag in ('dsm', 'dtm', 'skip-3dmodel'):
            value = data.get(flag, False)
            if not isinstance(value, bool):
                raise ValueError('Opção booleana inválida.')
            if value:
                options.append('--' + flag)
        if self.engine == 'podman':
            if ':' in str(path):
                raise ValueError('O caminho do projeto não pode conter dois-pontos no motor Podman.')
            return ['podman', 'run', '--rm', '--pull=never', '--network=none',
                    '--name', self.container_name, '--userns=keep-id',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--security-opt', 'label=disable',
                    '--env', 'PYTHONUNBUFFERED=1', '--volume', f'{path}:/datasets/{path.name}',
                    self.image] + options
        return [sys.executable, '-u', str(ROOT / 'run.py')] + options

    def start(self, data):
        with self.lock:
            command = self.prepare(data)
            if self.state in ('running', 'stopping'):
                raise ValueError('Já existe execução ativa.')
            self.process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True,
                                            encoding='utf-8', errors='replace',
                                            start_new_session=os.name != 'nt')
            self.command = command
            self.project = data['project']
            self.selected_project = self.project
            self.logs.clear()
            self.exit_code = None
            self.stage = None
            self.error = None
            self.state = 'running'
            threading.Thread(target=self.collect, args=(self.process,), daemon=True).start()

    def collect(self, process):
        for line in process.stdout:
            with self.lock:
                self.sequence += 1
                self.logs.append({'id': self.sequence, 'text': line.rstrip()})
                match = re.search(r'Running (\w+) stage', line)
                if match:
                    self.stage = match.group(1)
        code = process.wait()
        process.stdout.close()
        with self.lock:
            self.exit_code = code
            self.state = 'cancelled' if self.state == 'stopping' else ('completed' if code == 0 else 'failed')
            if self.state == 'failed':
                self.error = failure_message(self.logs)

    def stop(self):
        with self.lock:
            if self.state != 'running' or self.process.poll() is not None:
                return
            self.state = 'stopping'
            if self.engine == 'podman':
                threading.Thread(target=self.stop_container).start()
            elif os.name == 'nt':
                self.process.terminate()
            else:
                os.killpg(self.process.pid, signal.SIGTERM)

    def stop_container(self):
        try:
            for _ in range(20):
                result = subprocess.run(['podman', 'stop', '--time', '10', self.container_name],
                                        capture_output=True, timeout=30)
                if result.returncode == 0 or (self.process is not None and self.process.poll() is not None):
                    return
                # An immediate cancel can arrive before Podman registers the container.
                threading.Event().wait(0.1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            subprocess.run(['podman', 'kill', self.container_name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def snapshot(self, since=0):
        with self.lock:
            return {'state': self.state, 'project': self.project, 'command': self.command,
                    'stage': self.stage,
                    'engine': self.engine, 'error': self.error,
                    'exit_code': self.exit_code,
                    'logs': [line for line in self.logs if line['id'] > since]}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') == self.server.authority

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': 'Host inválido.'})
        try:
            if self.path == '/api/projects':
                with self.server.workspace.lock:
                    return self.send(200, self.project_inventory())
            if self.path.startswith('/api/status?since='):
                return self.send(200, self.server.workspace.snapshot(int(self.path.split('=', 1)[1])))
            if self.path == '/LICENSE':
                return self.send(200, (ROOT / 'LICENSE').read_bytes(), 'text/plain; charset=utf-8')
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css'),
                     '/app.js': ('app.js', 'text/javascript'),
                     '/worker.js': ('worker.js', 'text/javascript'),
                     '/wasm/gsd.wasm': ('wasm/gsd.wasm', 'application/wasm')}
            if self.path not in files:
                return self.send(404, {'error': 'Não encontrado.'})
            name, kind = files[self.path]
            self.send(200, (WEB / name).read_bytes(), kind)
        except (ValueError, OSError) as error:
            self.send(400, {'error': str(error)})

    def do_POST(self):
        if (not self.valid_host() or self.headers.get('X-ODM-Token') != self.server.token
                or self.headers.get('Origin') != 'http://' + self.server.authority):
            return self.send(403, {'error': 'Origem ou token inválido.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('Pedido inválido.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Pedido deve ser objeto JSON.')
            workspace = self.server.workspace
            if self.path == '/api/browse':
                return self.send(200, workspace.browse(data.get('path')))
            if self.path == '/api/dataset':
                with workspace.lock:
                    selected = workspace.configure(data.get('path'))
                    return self.send(200, dict(self.project_inventory(), selected=selected))
            if self.path == '/api/preview':
                return self.send(200, {'command': workspace.prepare(data)})
            if self.path == '/api/run':
                workspace.start(data)
            elif self.path == '/api/stop':
                workspace.stop()
            else:
                return self.send(404, {'error': 'Não encontrado.'})
            self.send(200, workspace.snapshot())
        except (ValueError, TypeError, OSError) as error:
            self.send(400, {'error': str(error)})

    def project_inventory(self):
        workspace = self.server.workspace
        return {'projects': workspace.projects(), 'token': self.server.token,
                'engine': workspace.engine,
                'selected': workspace.selected_project,
                'datasets': str(workspace.datasets) if workspace.datasets is not None else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', type=Path, help='Pasta inicial opcional; também pode ser indicada pela interface.')
    parser.add_argument('--port', type=int, default=8097)
    parser.add_argument('--engine', choices=('auto', 'native', 'podman'), default='auto',
                        help='Motor de processamento. Auto usa instalação nativa ou imagem Podman já instalada.')
    parser.add_argument('--image', default=ODM_IMAGE, help='Imagem ODM local para o motor Podman (sem download automático).')
    args = parser.parse_args()
    if args.datasets is not None and not args.datasets.is_dir():
        parser.error('--datasets deve ser diretório existente.')
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.workspace = Workspace(engine=select_engine(args.engine, args.image), image=args.image)
        if args.datasets is not None:
            server.workspace.configure(str(args.datasets.resolve()))
        server.token = secrets.token_urlsafe(32)
        server.authority = f'127.0.0.1:{server.server_port}'
        print(f'ODM experimental: http://{server.authority}', flush=True)
        print(f'Motor de processamento: {server.workspace.engine}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.workspace.stop()


if __name__ == '__main__':
    main()
