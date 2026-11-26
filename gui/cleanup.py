"""Reviewable, reversible cleanup of known ODM processing artifacts."""
import hashlib
import json
from pathlib import Path
import secrets
import time
from datetime import datetime, timezone

ARTIFACTS = frozenset((
    'opensfm', 'odm_filterpoints', 'odm_meshing', 'odm_texturing', 'odm_texturing_25d',
    'odm_georeferencing', 'odm_orthophoto', 'odm_dem', 'odm_report',
    'entwine_pointcloud', 'dsm_tiles', 'dtm_tiles', 'orthophoto_tiles', '3d_tiles',
    'submodels', 'images.json', 'img_list.txt', 'options.json', 'benchmark.txt', 'log.json',
))


def inspect_artifacts(root):
    entries, preserved, fingerprint = [], [], hashlib.sha256()
    def inspect(path):
        stat = path.lstat()
        fingerprint.update(json.dumps([str(path.relative_to(root)), stat.st_ino,
                                       stat.st_size, stat.st_mtime_ns, stat.st_mode]).encode())
        if path.is_symlink():
            return 0
        if path.is_dir():
            return sum(inspect(child) for child in sorted(path.iterdir()))
        return stat.st_size
    for path in sorted(root.iterdir()):
        if path.name in ARTIFACTS and not path.is_symlink():
            size = inspect(path)
            entries.append({'name': path.name, 'bytes': size, 'directory': path.is_dir()})
        else:
            preserved.append(path.name)
    return entries, preserved, fingerprint.hexdigest()


class CleanupManager:
    def __init__(self):
        self.plans = {}

    def archive_root(self, root):
        archive = root.parent / '.odm-cleanup'
        if archive.is_symlink() or (archive.exists() and not archive.is_dir()):
            raise ValueError('Pasta de backup inválida. Limpeza não executada.')
        return archive

    def backups(self, root):
        archive = self.archive_root(root)
        found = []
        if archive.is_dir():
            for directory in archive.iterdir():
                if directory.is_symlink() or not directory.is_dir():
                    continue
                try:
                    manifest = directory / 'manifest.json'
                    if manifest.is_symlink() or manifest.stat().st_size > 100000:
                        continue
                    data = json.loads(manifest.read_text())
                    if data.get('project_path') == str(root) and data.get('state') == 'archived':
                        found.append({'id': directory.name, 'created': data['created'],
                                      'entries': data['entries'], 'path': str(directory)})
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return sorted(found, key=lambda item: item['created'], reverse=True)

    def preview(self, root):
        entries, preserved, digest = inspect_artifacts(root)
        token = secrets.token_urlsafe(24)
        self.plans = {token: {'root': str(root), 'digest': digest, 'entries': entries,
                              'created': time.monotonic()}}
        return {'project': root.name, 'plan': token, 'entries': entries,
                'bytes': sum(entry['bytes'] for entry in entries), 'preserved': preserved,
                'backups': self.backups(root)}

    def execute(self, root, token, confirmation):
        plan = self.plans.get(token)
        if not plan or plan['root'] != str(root) or time.monotonic() - plan['created'] > 600:
            raise ValueError('Prévia expirada. Revise a limpeza novamente.')
        if confirmation != root.name:
            raise ValueError('Digite exatamente o nome do levantamento para confirmar.')
        entries, _, digest = inspect_artifacts(root)
        if digest != plan['digest']:
            raise ValueError('Arquivos mudaram desde a prévia. Revise a limpeza novamente.')
        if not entries:
            raise ValueError('Nenhum arquivo de processamento para limpar.')
        archive = self.archive_root(root)
        archive.mkdir(exist_ok=True)
        backup = archive / secrets.token_hex(16)
        backup.mkdir()
        manifest = {'project_path': str(root), 'project': root.name,
                    'created': datetime.now(timezone.utc).isoformat(),
                    'state': 'moving', 'entries': [entry['name'] for entry in entries]}
        record = backup / 'manifest.json'
        record.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        moved = []
        try:
            for entry in entries:
                name = entry['name']
                (root / name).rename(backup / name)
                moved.append(name)
            manifest['state'] = 'archived'
            record.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        except OSError as error:
            for name in reversed(moved):
                if not (root / name).exists():
                    try:
                        (backup / name).rename(root / name)
                    except OSError:
                        pass
            raise ValueError(f'Limpeza não concluída. Confira o projeto e o backup em {backup}. Nenhum conteúdo foi apagado.') from error
        self.plans.clear()
        return {'backup': backup.name, 'path': str(backup), 'entries': moved}

    def restore(self, root, backup_id):
        available = {item['id']: item for item in self.backups(root)}
        if not isinstance(backup_id, str) or backup_id not in available:
            raise ValueError('Backup não disponível para este projeto.')
        info = available[backup_id]
        names = info['entries']
        if not isinstance(names, list) or any(name not in ARTIFACTS for name in names):
            raise ValueError('Conteúdo de backup inválido.')
        backup = Path(info['path'])
        for name in names:
            if (root / name).exists() or (root / name).is_symlink():
                raise ValueError('O projeto já tem novos arquivos de processamento. Restauração não sobrescreve arquivos.')
            if not (backup / name).exists() or (backup / name).is_symlink():
                raise ValueError('Backup incompleto ou alterado. Restauração não executada.')
        moved = []
        try:
            for name in names:
                (backup / name).rename(root / name)
                moved.append(name)
            data = json.loads((backup / 'manifest.json').read_text())
            data['state'] = 'restored'
            (backup / 'manifest.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except OSError as error:
            for name in reversed(moved):
                try:
                    (root / name).rename(backup / name)
                except OSError:
                    pass
            raise ValueError(f'Restauração não concluída. Arquivos preservados em {root} e {backup}.') from error
        self.plans.clear()
        return {'restored': names}
