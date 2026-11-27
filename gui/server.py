#!/usr/bin/env python3
"""Paralaxe: image preparation and photogrammetry with the OpenDroneMap engine."""
import argparse
import hmac
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
import time
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
        self.external = False
        self.browser_uploads = {}
        self.tool_sources = {}
        self.preflight_cache = {}
        self.discovery_time = 0
        self.logs = deque(maxlen=2000)
        self.sequence = 0
        self.state = 'idle'
        self.project = None
        self.command = []
        self.exit_code = None
        self.stage = None
        self.engine = engine
        self.image = image
        self.container_name = 'paralaxe-' + secrets.token_hex(8)
        self.error = None
        self.selected_project = None
        self.preview_cache = {}
        self.preview_lock = threading.Lock()
        self.media_cache = {}
        self.media_lock = threading.Lock()
        self.cleanup_manager = CleanupManager()

    def discover_external(self, force=False):
        """Attach only to running ODM containers mounted into this workspace."""
        with self.lock:
            if self.engine != 'podman' or self.datasets is None or self.state in ('running', 'stopping'):
                return
            if not force and time.monotonic() - self.discovery_time < 5:
                return
            self.discovery_time = time.monotonic()
            try:
                listing = subprocess.run(['podman', 'ps', '-q'], capture_output=True, text=True, timeout=10)
                ids = listing.stdout.split()
                if listing.returncode or not ids:
                    return
                result = subprocess.run(['podman', 'inspect', *ids], capture_output=True, text=True, timeout=10)
                if result.returncode:
                    return
                for container in json.loads(result.stdout):
                    if not container.get('State', {}).get('Running'):
                        continue
                    config = container.get('Config', {})
                    command = config.get('Cmd') or []
                    extraction_job = bool(command and command[0] == '/helpers/extraction.py')
                    if '--project-path' not in command and not extraction_job:
                        continue
                    for mount in container.get('Mounts', []):
                        source = Path(mount.get('Source', '')).resolve()
                        try:
                            project = self.project_path(source.name)
                        except ValueError:
                            continue
                        if source != project or mount.get('Destination') != ('/output' if extraction_job else '/datasets/' + project.name):
                            continue
                        container_id = container['Id']
                        follower = subprocess.Popen(['podman', 'logs', '--follow', '--tail', '2000', container_id],
                                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                    text=True, encoding='utf-8', errors='replace')
                        self.external = True
                        self.container_name = container_id
                        self.process = follower
                        self.project = self.selected_project = project.name
                        self.command = command
                        self.logs.clear()
                        self.exit_code = self.stage = self.error = None
                        self.state = 'running'
                        threading.Thread(target=self.collect_external, args=(follower, container_id), daemon=True).start()
                        return
            except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
                return

    def collect_external(self, follower, container_id):
        self.read_logs(follower)
        try:
            result = subprocess.run(['podman', 'wait', container_id], capture_output=True, text=True)
            code = int(result.stdout.strip()) if result.returncode == 0 else None
        except (OSError, ValueError):
            code = None
        with self.lock:
            self.exit_code = code
            self.state = 'cancelled' if self.state == 'stopping' else ('completed' if code == 0 else 'failed')
            if self.state == 'failed':
                self.error = failure_message(self.logs) if code is not None else 'Não foi possível confirmar o resultado do container externo. Consulte o log do projeto.'

    def cleanup(self, action, data):
        self.discover_external(force=True)
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

    def browser_upload_start(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
            raise ValueError('Nome de projeto inválido.')
        with self.lock:
            if self.datasets is None:
                raise ValueError('Configure a pasta de projetos do servidor antes de enviar fotos.')
            if len(self.browser_uploads) >= 64:
                raise ValueError('Limite de envios nesta sessão atingido.')
            target = self.datasets / name
            if target.exists() or target.is_symlink():
                raise ValueError('O projeto de destino já existe. Escolha outro nome.')
            target.mkdir()
            (target / 'images').mkdir()
            key = secrets.token_urlsafe(24)
            self.browser_uploads[key] = {'path': target, 'files': [], 'bytes': 0, 'complete': False, 'lock': threading.RLock()}
            (target / 'browser-import.json').write_text(json.dumps({'complete': False, 'files': []}))
            return {'upload': key, 'project': name}

    def browser_upload_file(self, key, name, stream, length):
        with self.lock:
            session = self.browser_uploads.get(key)
        if session is None:
            raise ValueError('Envio não está mais disponível.')
        with session['lock']:
            if session['complete']:
                raise ValueError('Envio não está mais disponível.')
            if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+\.(jpg|jpeg|png|webp|bmp|tif|tiff)', name, re.I):
                raise ValueError('Nome ou formato de foto inválido.')
            if not 0 < length <= 50 * 1024 * 1024:
                raise ValueError('Cada foto deve ter até 50 MB.')
            if len(session['files']) >= 10000 or session['bytes'] + length > 10 * 1024**3:
                raise ValueError('Limite do projeto de importação atingido.')
            target = session['path'] / 'images' / name
            if target.exists() or target.is_symlink():
                raise ValueError('A foto já foi enviada.')
            partial = target.with_suffix(target.suffix + '.part')
            try:
                with partial.open('xb') as output:
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError('Envio interrompido antes de concluir a foto.')
                        output.write(chunk); remaining -= len(chunk)
                with partial.open('rb') as source:
                    header = source.read(16)
                suffix = target.suffix.lower()
                valid = (suffix in ('.jpg', '.jpeg') and header.startswith(b'\xff\xd8\xff') or
                         suffix == '.png' and header.startswith(b'\x89PNG\r\n\x1a\n') or
                         suffix == '.webp' and header.startswith(b'RIFF') and header[8:12] == b'WEBP' or
                         suffix == '.bmp' and header.startswith(b'BM') or
                         suffix in ('.tif', '.tiff') and header[:4] in (b'II*\x00', b'MM\x00*'))
                if not valid:
                    raise ValueError('O conteúdo enviado não corresponde ao formato da foto.')
                partial.rename(target)
            finally:
                partial.unlink(missing_ok=True)
            session['files'].append(name); session['bytes'] += length
            return {'files': len(session['files']), 'bytes': session['bytes']}

    def browser_upload_finish(self, key, report):
        with self.lock:
            session = self.browser_uploads.get(key)
        if session is None:
            raise ValueError('Envio não está mais disponível.')
        with session['lock']:
            if session['complete'] or len(session['files']) < 3:
                raise ValueError('Envie pelo menos três fotos para concluir um novo projeto.')
            manifest = {'complete': True, 'files': session['files'], 'bytes': session['bytes'],
                        'source': 'browser-local', 'report': report}
            (session['path'] / 'browser-import.json').write_text(json.dumps(manifest, ensure_ascii=False))
            session['complete'] = True
            return {'project': session['path'].name, 'files': len(session['files'])}

    def open_tool_source(self, location):
        if not isinstance(location, str) or not location.strip():
            raise ValueError('Escolha a pasta de um projeto.')
        path = Path(location).expanduser()
        if not path.is_absolute():
            raise ValueError('Informe o caminho completo do projeto.')
        path = path.resolve()
        if path.name == 'images':
            path = path.parent
        if not path.is_dir() or not (path / 'images').is_dir():
            raise ValueError('Escolha a pasta de um projeto que contenha images/.')
        with self.lock:
            for key, context in self.tool_sources.items():
                if context.project_path(context.selected_project) == path:
                    return {'source': key, 'path': str(path), 'projects': [entry for entry in context.projects() if entry['name'] == path.name]}
            if len(self.tool_sources) >= 64:
                raise ValueError('Limite de pastas abertas nesta sessão. Reinicie a interface para abrir outras.')
            context = Workspace(path.parent, engine=self.engine, image=self.image)
            context.project_path(path.name)
            context.selected_project = path.name
            key = secrets.token_urlsafe(24)
            self.tool_sources[key] = context
            return {'source': key, 'path': str(path), 'projects': [entry for entry in context.projects() if entry['name'] == path.name]}

    def tool_context(self, data):
        key = data.get('source')
        if key is None:
            return self
        if not isinstance(key, str) or key not in self.tool_sources:
            raise ValueError('Pasta da ferramenta não está mais disponível. Escolha a pasta novamente.')
        context = self.tool_sources[key]
        if data.get('project') != context.selected_project:
            raise ValueError('Projeto não pertence à pasta selecionada para a ferramenta.')
        return context

    def extraction_options(self, data):
        if __package__:
            from .extraction import parameters
        else:
            from extraction import parameters
        raw = data.get('extraction', {})
        if not isinstance(raw, dict):
            raise ValueError('Parâmetros de extração inválidos.')
        return parameters(dict(raw, limit=data.get('video-limit', 500),
                               resolution=data.get('video-resolution', 2000)))

    def extract_video(self, data):
        self.discover_external(force=True)
        with self.lock:
            if self.state in ('running', 'stopping'):
                raise ValueError('Aguarde o processamento ativo terminar antes de extrair novos quadros.')
            source = self.tool_context(data).media_path(data.get('project'), data.get('file'))
            if source.suffix.lower() not in VIDEO_TYPES:
                raise ValueError('Escolha um vídeo do projeto.')
            options = self.extraction_options(data)
            name = data.get('destination')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
                raise ValueError('Nome do novo projeto deve conter apenas letras sem acento, números, hífen e sublinhado.')
            if self.datasets is None:
                raise ValueError('Escolha a pasta de saída em Meu levantamento antes de extrair quadros.')
            target = self.datasets / name
            if target.exists() or target.is_symlink():
                raise ValueError('O projeto de destino já existe. Escolha um novo nome.')
            if self.engine != 'podman' and not importlib.util.find_spec('cv2'):
                raise ValueError('Extração requer OpenCV local ou motor Podman.')
            if self.engine == 'podman' and any(':' in str(path) for path in (source, target, Path(__file__).parent.resolve())):
                raise ValueError('Caminho não suportado pelo motor Podman.')
            target.mkdir()
            (target / 'images').mkdir()
            try:
                if self.engine == 'podman':
                    self.container_name = 'paralaxe-' + secrets.token_hex(8)
                    command = ['podman', 'run', '--rm', '--pull=never', '--network=none',
                               '--name', self.container_name, '--userns=keep-id',
                               '--user', f'{os.getuid()}:{os.getgid()}', '--security-opt', 'label=disable',
                               '--env', 'PYTHONUNBUFFERED=1', '--volume', f'{source.parent.parent}:/source:ro',
                               '--volume', f'{target}:/output', '--volume', f'{Path(__file__).parent.resolve()}:/helpers:ro',
                               '--entrypoint', 'python3', self.image, '/helpers/extraction.py',
                               '/source/images/' + source.name, '/output', json.dumps(options)]
                else:
                    command = [sys.executable, '-u', str(Path(__file__).with_name('extraction.py')),
                               str(source), str(target), json.dumps(options)]
                self.process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                                errors='replace', start_new_session=os.name != 'nt')
            except OSError:
                (target / 'images').rmdir()
                target.rmdir()
                raise
            self.external = False
            self.command = command
            self.project = self.selected_project = name
            self.logs.clear()
            self.exit_code = self.error = None
            self.stage = 'dataset'
            self.state = 'running'
            threading.Thread(target=self.collect, args=(self.process,), daemon=True).start()

    def preflight(self, data):
        root = self.project_path(data.get('project'))
        extraction = self.extraction_options(data)
        quality = data.get('quality', 'medium')
        concurrency = data.get('max-concurrency', 8)
        limit = data.get('video-limit', 500)
        resolution = data.get('video-resolution', 2000)
        for value, minimum, maximum in [(concurrency, 1, 64), (limit, 3, 10000), (resolution, 320, 8000)]:
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError('Parâmetros de extração ou paralelismo inválidos.')
        stamp = [(f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in sorted((root / 'images').iterdir()) if f.is_file()]
        reconstruction = root / 'opensfm' / 'reconstruction.json'
        key = (str(root), str(stamp), reconstruction.stat().st_mtime_ns if reconstruction.exists() else None,
               quality, concurrency, limit, resolution, json.dumps(extraction, sort_keys=True))
        cached = self.preflight_cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        if importlib.util.find_spec('cv2'):
            if __package__:
                from .preflight import analyze
            else:
                from preflight import analyze
            report = analyze(root, extraction)
        elif self.engine == 'podman':
            command = ['podman', 'run', '--rm', '--pull=never', '--network=none',
                       '--security-opt', 'label=disable', '--userns=keep-id',
                       '--user', f'{os.getuid()}:{os.getgid()}', '--volume', f'{root}:/project:ro',
                       '--entrypoint', 'python3', '-i', self.image, '-', '/project', json.dumps(extraction)]
            result = subprocess.run(command, input=Path(__file__).with_name('preflight.py').read_text(),
                                    text=True, capture_output=True, timeout=180)
            if result.returncode:
                raise ValueError('Não foi possível concluir a verificação das imagens. Confira os arquivos e o motor de processamento.')
            report = json.loads(result.stdout)
        else:
            raise ValueError('Verificação de imagens requer OpenCV ou motor Podman.')
        report['settings'] = {'quality': quality, 'max-concurrency': concurrency,
                              'video-limit': limit, 'video-resolution': resolution, 'extraction': extraction}
        for video in report['videos']:
            video['nominal_interval_seconds'] = video['seconds'] / max(1, limit - 1) if video.get('seconds') else None
        if report['videos']:
            report['warnings'].append('Os controles de intervalo, movimento e nitidez são aplicados pelo botão Extrair para novo projeto. A extração automática do ODM usa apenas limite e resolução.')
        if quality in ('high', 'ultra'):
            report['warnings'].append('Qualidade alta aumenta custo e não resolve correspondências erradas. Comece pelo perfil Equilíbrio para validar a captura.')
        if concurrency > 8:
            report['warnings'].append('Mais de 8 tarefas simultâneas: confira a memória disponível e reduza o paralelismo se houver pressão de RAM.')
        self.preflight_cache = {key: (time.monotonic(), report)}
        return report

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
        self.discover_external(force=True)
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
        for key, default, minimum, maximum in [('max-concurrency', 8, 1, 64), ('video-limit', 500, 3, 10000), ('video-resolution', 2000, 320, 8000)]:
            value = data.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError('Parâmetro inválido: ' + key)
        options = ['--project-path', '/datasets' if self.engine == 'podman' else str(self.datasets), path.name,
                   '--feature-quality', quality, '--pc-quality', quality,
                   '--orthophoto-resolution', str(resolution),
                   '--max-concurrency', str(data.get('max-concurrency', 8)),
                   '--video-limit', str(data.get('video-limit', 500)),
                   '--video-resolution', str(data.get('video-resolution', 2000))]
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
        self.discover_external(force=True)
        with self.lock:
            if self.state in ('running', 'stopping'):
                raise ValueError('Já existe execução ativa.')
            if self.external:
                self.container_name = 'paralaxe-' + secrets.token_hex(8)
            command = self.prepare(data)
            import_manifest = self.project_path(data.get('project')) / 'browser-import.json'
            if import_manifest.is_file() and not json.loads(import_manifest.read_text()).get('complete'):
                raise ValueError('O envio deste projeto está incompleto. Conclua o envio antes de processar.')
            if any(not item['complete'] and item['path'] == self.project_path(data.get('project')) for item in self.browser_uploads.values()):
                raise ValueError('Aguarde concluir o envio das fotos deste projeto.')
            report = self.preflight(data)
            if report['errors']:
                raise ValueError('Verificação das imagens: ' + '; '.join(report['errors']))
            self.process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True,
                                            encoding='utf-8', errors='replace',
                                            start_new_session=os.name != 'nt')
            self.external = False
            self.command = command
            self.project = data['project']
            self.selected_project = self.project
            self.logs.clear()
            self.exit_code = None
            self.stage = None
            self.error = None
            self.state = 'running'
            threading.Thread(target=self.collect, args=(self.process,), daemon=True).start()

    def read_logs(self, process):
        for line in process.stdout:
            with self.lock:
                self.sequence += 1
                self.logs.append({'id': self.sequence, 'text': line.rstrip()})
                match = re.search(r'Running (\w+) stage', line)
                if match:
                    self.stage = match.group(1)
                elif 'DEBUG: Matching ' in line or 'Extracting ' in line and 'features' in line:
                    self.stage = 'opensfm'
                elif 'Estimated depth-maps' in line or 'Estimating depth' in line:
                    self.stage = 'openmvs'
        process.wait()
        process.stdout.close()

    def collect(self, process):
        self.read_logs(process)
        code = process.returncode
        with self.lock:
            self.exit_code = code
            self.state = 'cancelled' if self.state == 'stopping' else ('completed' if code == 0 else 'failed')
            if self.state == 'failed':
                self.error = failure_message(self.logs)

    def stop(self):
        with self.lock:
            if self.state != 'running' or (not self.external and self.process.poll() is not None):
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
        self.discover_external()
        with self.lock:
            result = {'state': self.state, 'project': self.project, 'command': self.command,
                    'external': self.external,
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
    def network_access(self):
        password = getattr(self.server, 'lan_password', None)
        if password is None:
            return True
        header = self.headers.get('Authorization', '')
        try:
            scheme, encoded = header.split(' ', 1)
            decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
            user, supplied = decoded.split(':', 1)
            if scheme.lower() == 'basic' and any(hmac.compare_digest(user.encode(), expected) for expected in (b'paralaxe', b'odm')) and hmac.compare_digest(supplied.encode(), password.encode()):
                return True
        except (ValueError, UnicodeError):
            pass
        body = b'Acesso restrito. Informe o usuario paralaxe e a senha configurada.'
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Paralaxe", charset="UTF-8"')
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

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
        policy = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; frame-src 'self'; frame-ancestors 'self'; object-src 'none'"
        if self.path == '/local-worker.js':
            policy = "default-src 'self'; script-src 'self' 'unsafe-eval'; object-src 'none'"
        self.send_header('Content-Security-Policy', policy)
        if self.path == '/api/projects' and code == 200:
            self.send_header('Set-Cookie', f'odm_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        return self.headers.get('Host') in getattr(self.server, 'allowed_authorities', {self.server.authority})

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': 'Host inválido.'})
        if not self.network_access():
            return
        try:
            if urlsplit(self.path).path == '/api/media':
                if not self.authenticated():
                    return self.send(403, {'error': 'Sessão inválida.'})
                query = parse_qs(urlsplit(self.path).query)
                project, name = query.get('project', [''])[0], query.get('file', [''])[0]
                mode = query.get('mode', ['original'])[0]
                context = self.server.workspace.tool_context(dict(project=project, **({'source': query['source'][0]} if 'source' in query else {})))
                path = context.media_path(project, name)
                if mode == 'thumb':
                    data = context.inspect_photo(project, name, True)
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
                project = query.get('project', [''])[0]
                context = self.server.workspace.tool_context(dict(project=project, **({'source': query['source'][0]} if 'source' in query else {})))
                path = context.output_path(project, query.get('product', [''])[0])
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
                     '/sources.js': ('sources.js', 'text/javascript'),
                     '/local-browser.js': ('local-browser.js', 'text/javascript'),
                     '/local-worker.js': ('local-worker.js', 'text/javascript'),
                     '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
                     '/assets/paralaxe.svg': ('assets/paralaxe.svg', 'image/svg+xml'),
                     '/assets/paralaxe-wordmark.svg': ('assets/paralaxe-wordmark.svg', 'image/svg+xml'),
                     '/assets/favicon.svg': ('assets/favicon.svg', 'image/svg+xml'),
                     '/assets/icons.svg': ('assets/icons.svg', 'image/svg+xml'),
                     '/assets/icon-192.png': ('assets/icon-192.png', 'image/png'),
                     '/assets/icon-512.png': ('assets/icon-512.png', 'image/png'),
                     '/favicon.ico': ('assets/favicon.svg', 'image/svg+xml'),
                     '/vendor/opencv.js': ('vendor/opencv.js', 'text/javascript'),
                     '/vendor/fflate.js': ('vendor/fflate.js', 'text/javascript'),
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
        if not self.valid_host():
            return self.send(403, {'error': 'Host inválido.'})
        if not self.network_access():
            return
        if (not self.valid_host() or self.headers.get('X-ODM-Token') != self.server.token
                or self.headers.get('Origin') != 'http://' + self.headers.get('Host', '')):
            return self.send(403, {'error': 'Origem ou token inválido.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if urlsplit(self.path).path == '/api/local-upload-file':
                self.connection.settimeout(60)
                query = parse_qs(urlsplit(self.path).query)
                return self.send(200, self.server.workspace.browser_upload_file(query.get('upload', [''])[0], query.get('name', [''])[0], self.rfile, length))
            limit = 1024 * 1024 if self.path == '/api/local-upload-finish' else 8192
            if not 0 < length <= limit:
                raise ValueError('Pedido inválido.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Pedido deve ser objeto JSON.')
            workspace = self.server.workspace
            if self.path == '/api/local-upload-start':
                return self.send(200, workspace.browser_upload_start(data.get('name')))
            if self.path == '/api/local-upload-finish':
                return self.send(200, workspace.browser_upload_finish(data.get('upload'), data.get('report', {})))
            if 'source' in data and self.path in ('/api/run', '/api/dataset', '/api/project', '/api/cleanup', '/api/cleanup-restore'):
                raise ValueError('A pasta da ferramenta não pode alterar o processamento ativo.')
            if self.path in ('/api/cleanup-preview', '/api/cleanup', '/api/cleanup-restore'):
                action = {'/api/cleanup-preview': 'preview', '/api/cleanup': 'execute',
                          '/api/cleanup-restore': 'restore'}[self.path]
                return self.send(200, workspace.cleanup(action, data))
            if self.path == '/api/extract-video':
                workspace.extract_video(data)
                return self.send(200, workspace.snapshot())
            if self.path == '/api/tool-source':
                return self.send(200, workspace.open_tool_source(data.get('path')))
            if self.path == '/api/preflight':
                return self.send(200, workspace.tool_context(data).preflight(data))
            if self.path == '/api/planning':
                return self.send(200, workspace.tool_context(data).planning(data.get('project'), data.get('file')))
            if self.path == '/api/media-list':
                return self.send(200, {'files': workspace.tool_context(data).media_list(data.get('project'))})
            if self.path == '/api/photo':
                return self.send(200, workspace.tool_context(data).inspect_photo(data.get('project'), data.get('file')))
            if self.path == '/api/project':
                with workspace.lock:
                    if workspace.state in ('running', 'stopping'):
                        raise ValueError('Aguarde encerrar execução antes de trocar levantamento.')
                    path = workspace.project_path(data.get('project'))
                    workspace.selected_project = path.name
                    return self.send(200, workspace.snapshot())
            if self.path == '/api/view':
                return self.send(200, workspace.tool_context(data).view(data.get('project'), data.get('product')))
            if self.path == '/api/results':
                project = data.get('project')
                return self.send(200, {'history': workspace.tool_context(data).history(project),
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
        return {'application': 'Paralaxe', 'projects': workspace.projects(), 'token': self.server.token,
                'engine': workspace.engine,
                'selected': workspace.selected_project,
                'datasets': str(workspace.datasets) if workspace.datasets is not None else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', type=Path, help='Pasta inicial opcional; também pode ser indicada pela interface.')
    parser.add_argument('--port', type=int, default=8097)
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1', help='0.0.0.0 habilita acesso IPv4 pela rede local.')
    parser.add_argument('--allowed-host', action='append', default=[], help='IP ou nome permitido na rede local, sem porta. Pode ser repetido.')
    parser.add_argument('--lan-password-file', type=Path, help='Arquivo da senha de acesso para o usuário paralaxe; obrigatório no modo de rede.')
    parser.add_argument('--engine', choices=('auto', 'native', 'podman'), default='auto',
                        help='Motor de processamento. Auto usa instalação nativa ou imagem Podman já instalada.')
    parser.add_argument('--image', default=ODM_IMAGE, help='Imagem ODM local para o motor Podman (sem download automático).')
    args = parser.parse_args()
    if args.datasets is not None and not args.datasets.is_dir():
        parser.error('--datasets deve ser diretório existente.')
    password = None
    if args.lan_password_file is not None:
        try:
            password = args.lan_password_file.read_text().strip()
        except OSError:
            parser.error('Não foi possível ler o arquivo da senha de acesso.')
        if len(password) < 12:
            parser.error('A senha de rede deve ter pelo menos 12 caracteres.')
    if args.host != '127.0.0.1' and (not password or not args.allowed_host):
        parser.error('Acesso de rede requer --lan-password-file e pelo menos um --allowed-host.')
    if any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', host) for host in args.allowed_host):
        parser.error('Host permitido inválido; informe somente IP ou nome, sem protocolo e porta.')
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.workspace = Workspace(engine=select_engine(args.engine, args.image), image=args.image)
        if args.datasets is not None:
            server.workspace.configure(str(args.datasets.resolve()))
        server.token = secrets.token_urlsafe(32)
        server.authority = f'127.0.0.1:{server.server_port}'
        server.allowed_authorities = {f'{host}:{server.server_port}' for host in ['127.0.0.1', 'localhost', *args.allowed_host]}
        server.lan_password = password
        print(f'Paralaxe: http://{server.authority}', flush=True)
        print(f'Motor de reconstrução: OpenDroneMap ({server.workspace.engine})', flush=True)
        if args.host == '0.0.0.0':
            print('Acesso de rede com usuário paralaxe e senha do arquivo configurado.', flush=True)
            for host in args.allowed_host:
                print(f'Rede local: http://{host}:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.workspace.stop()


if __name__ == '__main__':
    main()
