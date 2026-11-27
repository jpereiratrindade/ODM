"""Create a separate photo-only dataset; never overwrite the source project."""
import json
import math
from pathlib import Path


def parameters(data):
    defaults = {'interval': 1.0, 'start': 0.0, 'end': None, 'blur': 40.0,
                'min_motion': .8, 'max_gap': 5.0, 'limit': 500, 'resolution': 2000}
    limits = {'interval': (.1, 60), 'start': (0, 86400), 'end': (0, 86400),
              'blur': (0, 10000), 'min_motion': (0, 20), 'max_gap': (.1, 120),
              'limit': (3, 10000), 'resolution': (320, 8000)}
    result = {}
    for key, default in defaults.items():
        value = data.get(key, default)
        if key == 'end' and value is None:
            result[key] = None
            continue
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or not limits[key][0] <= value <= limits[key][1]
                or key in ('limit', 'resolution') and not isinstance(value, int)):
            raise ValueError('Parâmetro de extração inválido: ' + key)
        result[key] = value
    if result['end'] is not None and result['end'] <= result['start']:
        raise ValueError('O fim do trecho deve ser posterior ao início.')
    if result['max_gap'] < result['interval']:
        raise ValueError('O intervalo de segurança deve ser maior ou igual ao intervalo entre candidatos.')
    return result


def extract(source, destination, options):
    import cv2
    try:
        from .preflight import image_metrics, pair_motion, motion_image
    except ImportError:
        from preflight import image_metrics, pair_motion, motion_image
    cv2.setNumThreads(1)
    options = parameters(options)
    destination = Path(destination)
    images = destination / 'images'
    if not images.is_dir() or any(images.iterdir()):
        raise ValueError('Extração requer uma pasta images/ nova e vazia.')
    capture = cv2.VideoCapture(str(source))
    rows, accepted, previous, last_time = [], [], None, None
    report = {'application': 'Paralaxe', 'source': Path(source).name, 'parameters': options, 'complete': False, 'frames': rows,
              'notice': 'Movimento em pixels não garante paralaxe. Quadros sem continuidade são preservados e sinalizados. Metadados GPS/SRT não são transferidos; mantenha os originais.'}
    (destination / 'extraction.json').write_text(json.dumps(report, indent=2))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise ValueError('Não foi possível determinar a duração e taxa de quadros do vídeo.')
        first = round(options['start'] * fps)
        end = min(count - 1, math.floor(options['end'] * fps)) if options['end'] is not None else count - 1
        if first >= end:
            raise ValueError('O trecho escolhido não contém quadros suficientes.')
        # Spread the candidate budget through the entire clip instead of truncating its end.
        step = max(1, math.ceil(options['interval'] * fps), math.ceil((end - first) / (options['limit'] - 1)))
        indices = list(range(first, end, step)) + [end]
        report['effective_interval_seconds'] = step / fps
        print('Running dataset stage · extração de quadros', flush=True)
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            row = {'frame': index, 'seconds': round(index / fps, 3), 'accepted': False}
            rows.append(row)
            if not ok:
                row['reason'] = 'decode_failed'
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            metrics, _ = image_metrics(gray)
            row.update(metrics)
            if metrics['sharpness'] < options['blur']:
                row['reason'] = 'blur'
                continue
            if max(metrics['dark_fraction'], metrics['bright_fraction']) > .8:
                row['reason'] = 'exposure'
                continue
            motion = pair_motion(previous, gray) if previous is not None else None
            row['motion'] = motion
            low_motion = motion and motion['motion_percent'] is not None and motion['motion_percent'] < options['min_motion']
            if low_motion and index / fps - last_time < options['max_gap']:
                row['reason'] = 'low_motion'
                continue
            row['reason'] = 'safety_gap' if low_motion else ('first' if motion is None else motion['status'])
            height, width = frame.shape[:2]
            scale = min(1, options['resolution'] / max(width, height))
            if scale < 1:
                frame = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
            name = f'frame_{index:09d}.jpg'
            if not cv2.imwrite(str(images / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise ValueError('Não foi possível gravar ' + name)
            row.update(accepted=True, file=name)
            accepted.append(name)
            previous, last_time = motion_image(gray), index / fps
            print(f'Quadro {index}/{end}: {len(accepted)} fotos · {row["reason"]}', flush=True)
        report['accepted'] = len(accepted)
        report['complete'] = len(accepted) >= 3
        if not report['complete']:
            raise ValueError('Menos de três fotos passaram pelos filtros. Revise os limiares e o trecho; os arquivos e o diagnóstico foram preservados.')
        print(f'Extração concluída: {len(accepted)} fotos. Revise o relatório antes de reconstruir.', flush=True)
    finally:
        capture.release()
        (destination / 'extraction.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    return report


if __name__ == '__main__':
    import sys
    extract(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]))
