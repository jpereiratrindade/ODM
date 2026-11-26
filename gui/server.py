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
import base64
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit, parse_qs
if __package__:
    from .cleanup import CleanupManager
else:
    from cleanup import CleanupManager

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / 'web'
QUALITIES = {'lowest', 'low', 'medium', 'high', 'ultra'}
OUTPUTS = ('odm_orthophoto/odm_orthophoto.tif', 'odm_dem/dsm.tif',
           'odm_dem/dtm.tif', 'odm_georeferencing/odm_georeferenced_model.laz',
           'odm_texturing/odm_textured_model.obj', 'odm_report/report.pdf')
ODM_IMAGE = 'docker.io/opendronemap/odm:latest'
PRODUCTS = dict(zip(('ortho', 'dsm', 'dtm', 'cloud', 'model', 'report'), OUTPUTS))
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
PHOTO_TYPES = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.dng', '.nef', '.raw', '.bmp', '.jxl'}
VIDEO_TYPES = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
               '.m4v': 'video/mp4', '.avi': 'video/x-msvideo', '.mkv': 'video/x-matroska',
               '.ts': 'video/mp2t', '.lrv': 'video/mp4', '.mts': 'video/mp2t'}


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
    text = ANSI.sub('', '\n'.join(line['text'] for line in logs))
    if 'no valid point-cloud' in text or re.search(r'Densifying point-cloud completed:\s*0 points', text):
        return 'A reconstrução densa gerou zero pontos e o OpenMVS parou ao filtrar a nuvem vazia. Tente o perfil Equilíbrio; o perfil rápido reduz detalhes e pode não reconstruir este levantamento.'
    if "cannot import name '_find_unsafe'" in text:
        return 'O Python do sistema é incompatível com esta versão do ODM. Use o motor Podman ou um ambiente nativo ODM configurado.'
    if 'ModuleNotFoundError' in text or 'ImportError' in text:
        return 'Faltam dependências no ambiente de processamento. Use o motor Podman ou o ambiente completo do ODM.'
    if 'image not known' in text or 'image not found' in text:
        return 'A imagem de processamento ODM não está instalada no Podman. Confira a instalação do motor.'
    if 'Permission denied' in text or 'permission denied' in text:
        return 'O processamento não conseguiu acessar arquivos ou gravar no projeto. Confira as permissões da pasta.'
    errors = [line for line in text.splitlines() if re.search(r'\[ERROR\]|Error:|Exception:', line)]
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
        self.preview_cache = {}
        self.preview_lock = threading.Lock()
        self.media_cache = {}
        self.media_lock = threading.Lock()
        self.cleanup_manager = CleanupManager()

    def cleanup(self, action, data):
        with self.lock:
            if self.state in ('running', 'stopping'):
                raise ValueError('Limpeza e restauração bloqueadas durante processamento ativo. Aguarde concluir ou interrompa pela interface.')
            root = self.project_path(data.get('project'))
            if action == 'preview':
                return self.cleanup_manager.preview(root)
            if action == 'execute':
                result = self.cleanup_manager.execute(root, data.get('plan'), data.get('confirmation'))
            elif action == 'restore':
                result = self.cleanup_manager.restore(root, data.get('backup'))
            else:
                raise ValueError('Ação de limpeza inválida.')
            self.preview_cache.clear(); self.media_cache.clear()
            self.state = 'idle'; self.project = None; self.command = []
            self.stage = None; self.error = None; self.exit_code = None; self.logs.clear()
            self.selected_project = root.name
            return result

    def planning(self, project, name=None):
        photos = [file['name'] for file in self.media_list(project) if file['kind'] == 'photo']
        if not photos:
            raise ValueError('Projeto sem fotos. Extraia quadros do vídeo antes de carregar parâmetros de câmera.')
        name = name or next((n for n in photos if Path(n).suffix.lower() in ('.jpg', '.jpeg')), photos[0])
        path = self.media_path(project, name)
        if name not in photos:
            raise ValueError('Selecione uma foto de referência.')
        if importlib.util.find_spec('PIL'):
            if __package__:
                from .preview import planning_metadata
            else:
                from preview import planning_metadata
            try:
                data = planning_metadata(path)
            except Exception as error:
                raise ValueError('Não foi possível ler os metadados desta foto. Escolha outra foto ou informe os parâmetros manualmente.') from error
        elif self.engine == 'podman':
            root = self.project_path(project)
            if ':' in str(root):
                raise ValueError('Caminho não suportado pelo motor Podman.')
            command = ['podman', 'run', '--rm', '--pull=never', '--network=none',
                       '--security-opt', 'label=disable', '--userns=keep-id',
                       '--user', f'{os.getuid()}:{os.getgid()}', '--volume', f'{root}:/project:ro',
                       '--entrypoint', 'python3', '-i', self.image, '-', 'planning', '/project/images/' + name]
            run = subprocess.run(command, input=Path(__file__).with_name('preview.py').read_text(),
                                 text=True, capture_output=True, timeout=30)
            if run.returncode:
                raise ValueError('Não foi possível ler metadados da foto no motor selecionado.')
            data = json.loads(run.stdout)
        else:
            raise ValueError('Leitura EXIF requer Pillow no servidor ou motor Podman.')
        return dict(data, project=project, photo=name, photos=photos, source='EXIF/XMP da foto original')

    def media_path(self, project, name):
        root = self.project_path(project)
        images = (root / 'images').resolve()
        if not isinstance(name, str) or Path(name).name != name or '\\' in name or name.startswith('.'):
            raise ValueError('Nome de arquivo inválido.')
        path = (images / name).resolve()
        if (not images.is_relative_to(root) or path.parent != images or not path.is_file()
                or path.suffix.lower() not in PHOTO_TYPES | VIDEO_TYPES.keys()):
            raise ValueError('Mídia não disponível neste projeto.')
        return path

    def media_list(self, project):
        root = self.project_path(project) / 'images'
        result = []
        for file in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
            try:
                file = self.media_path(project, file.name)
                stat = file.stat()
                result.append({'name': file.name, 'kind': 'video' if file.suffix.lower() in VIDEO_TYPES else 'photo',
                               'bytes': stat.st_size, 'format': file.suffix[1:].upper()})
            except (ValueError, OSError):
                continue
        return result

    def inspect_photo(self, project, name, thumbnail=False):
        path = self.media_path(project, name)
        if path.suffix.lower() not in PHOTO_TYPES:
            raise ValueError('Selecione uma foto.')
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, thumbnail)
        with self.media_lock:
            if key in self.media_cache:
                return self.media_cache[key]
            if importlib.util.find_spec('PIL'):
                if __package__:
                    from .preview import photo_preview
                else:
                    from preview import photo_preview
                try:
                    data = photo_preview(path, thumbnail)
                except Exception as error:
                    raise ValueError('Formato de foto não decodificado. O original continua disponível.') from error
            elif self.engine == 'podman':
                root = self.project_path(project)
                if ':' in str(root):
                    raise ValueError('Caminho não suportado pelo motor Podman.')
                command = ['podman', 'run', '--rm', '--pull=never', '--network=none',
                           '--security-opt', 'label=disable', '--userns=keep-id',
                           '--user', f'{os.getuid()}:{os.getgid()}', '--volume', f'{root}:/project:ro',
                           '--entrypoint', 'python3', '-i', self.image, '-',
                           'thumbnail' if thumbnail else 'photo', '/project/images/' + name]
                run = subprocess.run(command, input=Path(__file__).with_name('preview.py').read_text(),
                                     text=True, capture_output=True, timeout=30)
                if run.returncode:
                    raise ValueError('Formato de foto não decodificado pelo motor. O original continua disponível.')
                data = json.loads(run.stdout)
            else:
                raise ValueError('Prévia de fotos requer Pillow no servidor ou motor Podman.')
            if len(self.media_cache) >= 48:
                self.media_cache.pop(next(iter(self.media_cache)))
            self.media_cache[key] = data
            return data

    def output_path(self, project, product):
        root = self.project_path(project)
        if product not in PRODUCTS:
            raise ValueError('Produto inválido.')
        path = (root / PRODUCTS[product]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Produto não disponível neste levantamento.')
        return path

    def history(self, project):
        path = self.project_path(project) / 'log.json'
        try:
            if not path.is_file() or path.stat().st_size > 10_000_000:
                return None
            data = json.loads(path.read_text())
            lines = [{'text': line} for process in data.get('processes', [])
                     for line in process.get('output', [])]
            return {'success': data.get('success'), 'quality': data.get('options', {}).get('pc_quality'),
                    'stage': next((s.get('name') for s in reversed(data.get('stages', []))), None),
                    'error': failure_message(lines) if data.get('success') is False else None,
                    'exit_code': data.get('error', {}).get('code'), 'version': data.get('odmVersion'),
                    'seconds': data.get('totalTime'), 'finished': bool(data.get('endTime'))}
        except (ValueError, OSError, TypeError, AttributeError):
            return None

    def view(self, project, product):
        path = self.output_path(project, product)
        if product == 'report':
            return {'kind': 'pdf'}
        key = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        with self.preview_lock:
            if key in self.preview_cache:
                return self.preview_cache[key]
            kind = 'points' if product == 'cloud' else 'mesh' if product == 'model' else 'raster'
            script = Path(__file__).with_name('preview.py')
            if self.engine == 'podman':
                root = self.project_path(project)
                if ':' in str(root):
                    raise ValueError('O caminho do projeto não pode conter dois-pontos no motor Podman.')
                command = ['podman', 'run', '--rm', '--pull=never', '--network=none',
                           '--userns=keep-id', '--user', f'{os.getuid()}:{os.getgid()}',
                           '--security-opt', 'label=disable', '--volume', f'{root}:/project:ro',
                           '--entrypoint', 'python3', '-i', self.image, '-', kind,
                           '/project/' + str(path.relative_to(root))]
                result = subprocess.run(command, input=script.read_text(), capture_output=True,
                                        text=True, timeout=60)
            else:
                result = subprocess.run([sys.executable, str(script), kind, str(path)],
                                        capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise ValueError('Não foi possível criar prévia. Verifique GDAL/PDAL no motor selecionado. O arquivo original continua disponível para download.')
            data = json.loads(result.stdout)
            data.update(file=path.name, bytes=path.stat().st_size)
            if len(self.preview_cache) >= 6:
                self.preview_cache.pop(next(iter(self.preview_cache)))
            self.preview_cache[key] = data
            return data

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
        previous = self.history(path.name)
        if previous and previous['success'] is False and previous['quality'] != quality:
            # Failed runs do not save options.json; avoid reusing low-quality intermediates.
            options.extend(['--rerun-from', 'opensfm'])
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
            result = {'state': self.state, 'project': self.project, 'command': self.command,
                    'stage': self.stage,
                    'engine': self.engine, 'error': self.error,
                    'exit_code': self.exit_code,
                    'logs': [line for line in self.logs if line['id'] > since]}
            if self.state not in ('running', 'stopping') and self.selected_project and (
                    self.state == 'idle' or self.selected_project != self.project):
                result.update(state='idle', project=self.selected_project, stage=None,
                              error=None, exit_code=None, command=[], logs=[])
                history = self.history(self.selected_project)
                if history and history['finished'] and history['success'] is not None:
                    result.update(state='completed' if history['success'] else 'failed',
                                  project=self.selected_project, stage=history['stage'],
                                  error=history['error'], exit_code=history['exit_code'])
            return result


class Handler(BaseHTTPRequestHandler):
    def authenticated(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except CookieError:
            return False
        session = cookie.get('odm_session')
        return (session.value if session else self.headers.get('X-ODM-Token')) == self.server.token

    def stream_media(self, path, download=False):
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if match and (match[1] or match[2]) and size:
                if match[1]:
                    start = int(match[1]); end = min(int(match[2]), end) if match[2] else end
                else:
                    start = max(0, size - int(match[2]))
            if not match or not (match[1] or match[2]) or start > end or start >= size or not size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0'); self.end_headers(); return
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', VIDEO_TYPES.get(path.suffix.lower(), 'application/octet-stream'))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(max(0, end - start + 1)))
        if requested:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download:
            from urllib.parse import quote
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(path.name))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            with path.open('rb') as file:
                file.seek(start); remaining = end - start + 1
                while remaining > 0:
                    chunk = file.read(min(256 * 1024, remaining))
                    if not chunk: break
                    self.wfile.write(chunk); remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Seeking/closing a browser video aborts the previous range request.

    def send(self, code, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob:; media-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'")
        if self.path == '/api/projects' and code == 200:
            self.send_header('Set-Cookie', f'odm_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        return self.headers.get('Host') == self.server.authority

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': 'Host inválido.'})
        try:
            if urlsplit(self.path).path == '/api/media':
                if not self.authenticated():
                    return self.send(403, {'error': 'Sessão inválida.'})
                query = parse_qs(urlsplit(self.path).query)
                project, name = query.get('project', [''])[0], query.get('file', [''])[0]
                mode = query.get('mode', ['original'])[0]
                path = self.server.workspace.media_path(project, name)
                if mode == 'thumb':
                    data = self.server.workspace.inspect_photo(project, name, True)
                    return self.send(200, base64.b64decode(data['image']), 'image/jpeg')
                if mode not in ('original', 'download'):
                    raise ValueError('Modo de mídia inválido.')
                return self.stream_media(path, mode == 'download')
            if urlsplit(self.path).path == '/api/output':
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get('Cookie', ''))
                except CookieError:
                    return self.send(403, {'error': 'Sessão inválida.'})
                session = cookie.get('odm_session')
                if (session.value if session else self.headers.get('X-ODM-Token')) != self.server.token:
                    return self.send(403, {'error': 'Sessão inválida.'})
                query = parse_qs(urlsplit(self.path).query)
                path = self.server.workspace.output_path(query.get('project', [''])[0], query.get('product', [''])[0])
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf' if path.suffix == '.pdf' else 'application/octet-stream')
                self.send_header('Content-Length', str(path.stat().st_size))
                disposition = 'inline' if path.suffix == '.pdf' and query.get('inline') == ['1'] else 'attachment'
                self.send_header('Content-Disposition', f'{disposition}; filename="{path.name}"')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "frame-ancestors 'self'")
                self.end_headers()
                with path.open('rb') as file:
                    shutil.copyfileobj(file, self.wfile, length=256 * 1024)
                return
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
                     '/viewer.js': ('viewer.js', 'text/javascript'),
                     '/media.js': ('media.js', 'text/javascript'),
                     '/planning.js': ('planning.js', 'text/javascript'),
                     '/cleanup.js': ('cleanup.js', 'text/javascript'),
                     '/worker.js': ('worker.js', 'text/javascript'),
                     '/wasm/gsd.wasm': ('wasm/gsd.wasm', 'application/wasm')}
            if self.path not in files:
                return self.send(404, {'error': 'Não encontrado.'})
            name, kind = files[self.path]
            self.send(200, (WEB / name).read_bytes(), kind)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
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
            if self.path in ('/api/cleanup-preview', '/api/cleanup', '/api/cleanup-restore'):
                action = {'/api/cleanup-preview': 'preview', '/api/cleanup': 'execute',
                          '/api/cleanup-restore': 'restore'}[self.path]
                return self.send(200, workspace.cleanup(action, data))
            if self.path == '/api/planning':
                return self.send(200, workspace.planning(data.get('project'), data.get('file')))
            if self.path == '/api/media-list':
                return self.send(200, {'files': workspace.media_list(data.get('project'))})
            if self.path == '/api/photo':
                return self.send(200, workspace.inspect_photo(data.get('project'), data.get('file')))
            if self.path == '/api/project':
                with workspace.lock:
                    if workspace.state in ('running', 'stopping'):
                        raise ValueError('Aguarde encerrar execução antes de trocar levantamento.')
                    path = workspace.project_path(data.get('project'))
                    workspace.selected_project = path.name
                    return self.send(200, workspace.snapshot())
            if self.path == '/api/view':
                return self.send(200, workspace.view(data.get('project'), data.get('product')))
            if self.path == '/api/results':
                project = data.get('project')
                return self.send(200, {'history': workspace.history(project),
                                      'project': project})
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
        except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
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
