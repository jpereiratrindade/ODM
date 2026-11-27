"""Read-only, sampled input checks. Heuristics are warnings, not failure probabilities."""
import json
import math
from pathlib import Path
import re
import statistics

PHOTO_TYPES = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp'}
VIDEO_TYPES = {'.mp4', '.mov', '.webm', '.m4v', '.avi', '.mkv', '.mts', '.ts'}


def natural_key(path):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', path.name)]


def selected(items, limit):
    if len(items) <= limit:
        return items
    return [items[round(i * (len(items) - 1) / (limit - 1))] for i in range(limit)]


def image_metrics(gray):
    import cv2
    import numpy as np
    height, width = gray.shape
    gray = cv2.resize(gray, (max(1, round(width * min(1, 640 / width))),
                              max(1, round(height * min(1, 640 / width)))))
    return {'sharpness': round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
            'dark_fraction': round(float(np.mean(gray < 10)), 3),
            'bright_fraction': round(float(np.mean(gray > 245)), 3),
            'texture': round(float(gray.std()), 2)}, cv2.resize(gray, (64, 36))


def motion_image(gray):
    import cv2
    scale = min(1, 800 / max(gray.shape))
    return cv2.resize(gray, (max(1, round(gray.shape[1] * scale)), max(1, round(gray.shape[0] * scale))))


def pair_motion(first, second):
    """Image-space evidence only: homography dominance cannot prove rotation."""
    import cv2
    import numpy as np
    first, second = motion_image(first), motion_image(second)
    result = {'status': 'insufficient_features', 'matches': 0, 'inliers': 0,
              'motion_percent': None, 'homography_ratio': None, 'residual_percent': None}
    if first.shape != second.shape:
        result['status'] = 'different_dimensions'
        return result
    cv2.setRNGSeed(7)
    detector = cv2.ORB_create(nfeatures=1800)
    ka, da = detector.detectAndCompute(first, None)
    kb, db = detector.detectAndCompute(second, None)
    if da is None or db is None or len(db) < 2:
        return result
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    forward = {m.queryIdx: m.trainIdx for neighbors in matcher.knnMatch(da, db, k=2)
               if len(neighbors) == 2 for m, n in [neighbors] if m.distance < .75 * n.distance}
    backward = {m.queryIdx: m.trainIdx for neighbors in matcher.knnMatch(db, da, k=2)
                if len(neighbors) == 2 for m, n in [neighbors] if m.distance < .75 * n.distance}
    matches = [(a, b) for a, b in forward.items() if backward.get(b) == a]
    result['matches'] = len(matches)
    if len(matches) < 20:
        return result
    a = np.float32([ka[i].pt for i, _ in matches])
    b = np.float32([kb[j].pt for _, j in matches])
    _, fundamental_mask = cv2.findFundamentalMat(a, b, cv2.FM_RANSAC, 1.5, .99)
    fundamental_count = int(fundamental_mask.sum()) if fundamental_mask is not None else 0
    result['fundamental_inliers'] = fundamental_count
    matrix, mask = cv2.findHomography(a, b, cv2.RANSAC, 3)
    if matrix is None or mask is None:
        result['inliers'] = fundamental_count
        result['status'] = 'tracked' if fundamental_count >= 15 and fundamental_count / len(matches) >= .25 else 'discontinuity'
        return result
    mask = mask.ravel().astype(bool)
    count = int(mask.sum())
    ratio = count / len(matches)
    diagonal = math.hypot(*first.shape)
    motion = float(np.median(np.linalg.norm(b[mask] - a[mask], axis=1))) / diagonal * 100 if count else 0
    projected = cv2.perspectiveTransform(a.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    residual = float(np.median(np.linalg.norm(projected - b, axis=1))) / diagonal * 100
    result.update(inliers=max(count, fundamental_count), motion_percent=round(motion, 3), homography_ratio=round(ratio, 3),
                  residual_percent=round(residual, 3))
    if max(count, fundamental_count) < 15 or max(ratio, fundamental_count / len(matches)) < .25:
        status = 'discontinuity'
    elif motion < .3 and residual < .15:
        status = 'stationary'
    elif ratio > .9 and residual < .15:
        status = 'homography_dominant'
    else:
        status = 'tracked'
    result['status'] = status
    return result


def geometry_report(root):
    path = root / 'opensfm' / 'reconstruction.json'
    if not path.is_file() or path.stat().st_size > 300_000_000:
        return None
    try:
        components = json.loads(path.read_text())
        summary = []
        for component in components:
            points = [p['coordinates'] for p in component.get('points', {}).values()]
            finite = [p for p in points if all(math.isfinite(v) for v in p)]
            if not finite:
                continue
            center = [statistics.median(p[i] for p in finite) for i in range(3)]
            radii = sorted(math.dist(p, center) for p in finite)
            radius = radii[int(.99 * (len(radii) - 1))]
            summary.append({'shots': len(component.get('shots', {})), 'points': len(points),
                            'nonfinite': len(points) - len(finite), 'radius_p99': radius,
                            'extreme_points': sum(r > max(radius * 100, 1e-9) for r in radii),
                            'max_radius': radii[-1]})
        return {'components': summary, 'source': 'Reconstrução existente; uma nova execução pode produzir outra geometria.'}
    except (OSError, ValueError, TypeError, KeyError):
        return {'unavailable': True}


def analyze(root, options=None):
    import cv2
    import numpy as np
    root = Path(root)
    options = options or {}
    cv2.setNumThreads(1)
    motion_pairs = []
    files = sorted((p for p in (root / 'images').iterdir() if p.is_file() and not p.is_symlink()), key=natural_key)
    photos = [p for p in files if p.suffix.lower() in PHOTO_TYPES]
    videos = [p for p in files if p.suffix.lower() in VIDEO_TYPES]
    warnings, errors, samples, video_info = [], [], [], []
    def record(gray, name, source, previous):
        metrics, thumb = image_metrics(gray)
        difference = float(np.abs(thumb.astype(float) - previous.astype(float)).mean()) if previous is not None else None
        samples.append(dict(metrics, file=name, source=source, difference=round(difference, 2) if difference is not None else None))
        return thumb
    # Adjacent pairs preserve temporal locality even when the dataset is sampled.
    ordered = []
    for anchor in selected(range(max(0, len(photos) - 1)), 32):
        ordered.extend([anchor, anchor + 1])
    if len(photos) == 1:
        ordered = [0]
    previous, previous_index, previous_gray, previous_name = None, None, None, None
    for index in sorted(set(ordered)):
        photo = photos[index]
        gray = cv2.imread(str(photo), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            errors.append('Não foi possível decodificar a foto: ' + photo.name)
            previous = None
            continue
        if previous_gray is not None and previous_index == index - 1:
            motion_pairs.append(dict(pair_motion(previous_gray, gray), first=previous_name, second=photo.name, source='photo'))
        previous = record(gray, photo.name, 'photo', previous if previous_index == index - 1 else None)
        previous_index, previous_gray, previous_name = index, motion_image(gray), photo.name
    for video in videos[:4]:
        capture = cv2.VideoCapture(str(video))
        try:
            fps, frames = capture.get(cv2.CAP_PROP_FPS), int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frames / fps if fps > 0 else None
            video_info.append({'file': video.name, 'fps': fps if math.isfinite(fps) else None,
                               'frames': frames, 'seconds': duration})
            if not capture.isOpened() or frames <= 0:
                errors.append('Não foi possível ler o vídeo: ' + video.name)
                continue
            step = max(1, round(options.get('interval', 1.0) * fps))
            begin = min(frames - 1, max(0, round(options.get('start', 0) * fps)))
            end = min(frames, round(options['end'] * fps)) if options.get('end') is not None else frames
            if begin >= end:
                errors.append('Trecho de extração vazio para ' + video.name)
                continue
            step = max(step, math.ceil((end - begin - 1) / (options.get('limit', 500) - 1)))
            video_info[-1]['probe_interval_seconds'] = step / fps
            for anchor in selected(range(begin, max(begin + 1, end - step)), 12):
                first, first_name = None, None
                for frame in sorted(set([anchor, min(anchor + step, end - 1)])):
                    capture.set(cv2.CAP_PROP_POS_FRAMES, frame)
                    ok, image = capture.read()
                    if not ok:
                        warnings.append('Falha ao ler uma amostra do vídeo ' + video.name)
                        first = None
                        continue
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    name = f'{video.name} · {frame / fps:.2f}s'
                    record(gray, name, 'video', None)
                    if first is not None:
                        motion_pairs.append(dict(pair_motion(first, gray), first=first_name, second=name, source='video'))
                    first, first_name = motion_image(gray), name
        finally:
            capture.release()
    if not photos and not videos:
        errors.append('Nenhuma foto ou vídeo compatível encontrado.')
    elif len(photos) < 3 and not videos:
        errors.append('São necessárias pelo menos três fotos para iniciar uma reconstrução.')
    blurry = [s['file'] for s in samples if s['sharpness'] < 40]
    exposure = [s['file'] for s in samples if max(s['dark_fraction'], s['bright_fraction']) > .6]
    flat = [s['file'] for s in samples if s['texture'] < 12]
    similar = [s['file'] for s in samples if s['difference'] is not None and s['difference'] < 2]
    for names, message in [(blurry, 'Amostras com pouca nitidez; confira foco e movimento'),
                           (exposure, 'Amostras com grande área escura ou saturada'),
                           (flat, 'Amostras com pouco contraste; podem faltar pontos para correspondência'),
                           (similar, 'Fotos vizinhas visualmente muito semelhantes; pode haver redundância ou pouco deslocamento')]:
        if names:
            warnings.append(f'{message}: {len(names)}. Exemplos: ' + ', '.join(names[:3]))
    if videos:
        warnings.append('A extração do ODM filtra quadros, mas mais fotos não garantem melhor geometria. Revise trechos parados, giros sem deslocamento, cortes, desfoque e mudanças de zoom. O limite é global; a distribuição final depende dos filtros.')
    if photos and videos:
        warnings.append('Há fotos e vídeos juntos: confirme se os JPGs já vieram destes vídeos para evitar extração redundante. Mova o vídeo para fora de images/ se quiser usar somente as fotos existentes.')
    if any(re.match(r'.+_\d+_\d+\.(jpg|jpeg)$', p.name, re.I) for p in photos):
        warnings.append('Nomes compatíveis com quadros extraídos de vídeo. Pouca translação pode produzir triangulação instável mesmo com imagens nítidas.')
    labels = {'stationary': 'Pouco movimento visual: possível redundância ou câmera parada',
              'homography_dominant': 'Movimento explicado por homografia: pode ser giro, cena plana ou pouca paralaxe',
              'discontinuity': 'Pouca consistência entre quadros: possível perda de continuidade',
              'insufficient_features': 'Poucas correspondências confiáveis para avaliar movimento',
              'different_dimensions': 'Dimensões diferentes impedem comparar o movimento'}
    for status, label in labels.items():
        affected = [pair for pair in motion_pairs if pair['status'] == status]
        if affected:
            warnings.append(f'{label}: {len(affected)} pares. Exemplo: {affected[0]["first"]} → {affected[0]["second"]}.')
    if len(videos) > 4:
        warnings.append('Somente os primeiros quatro vídeos foram amostrados.')
    extraction_report = None
    manifest = root / 'extraction.json'
    if manifest.is_file() and manifest.stat().st_size < 20_000_000:
        try:
            data = json.loads(manifest.read_text())
            from collections import Counter
            reasons = Counter(row.get('reason', 'unknown') for row in data.get('frames', []))
            extraction_report = {'accepted': data.get('accepted'), 'complete': data.get('complete'),
                                 'reasons': dict(reasons), 'parameters': data.get('parameters'),
                                 'notice': data.get('notice')}
            if not data.get('complete'):
                warnings.append('A extração anterior não foi concluída; confira extraction.json e a quantidade de fotos.')
            if any(reasons.get(key, 0) for key in ('discontinuity', 'insufficient_features', 'safety_gap')):
                warnings.append('A extração preservou quadros com baixa consistência, poucas correspondências ou pouco movimento por segurança. Revise os trechos indicados em extraction.json.')
        except (OSError, ValueError, TypeError, AttributeError):
            warnings.append('Não foi possível ler o diagnóstico de extração existente.')
    geometry = geometry_report(root)
    if geometry and geometry.get('components'):
        components = geometry['components']
        if len(components) > 1:
            warnings.append(f'A reconstrução existente tem {len(components)} componentes separadas; verifique cobertura e continuidade da captura.')
        if any(c['extreme_points'] or c['nonfinite'] for c in components):
            warnings.append('A reconstrução existente contém pontos extremos ou não finitos. Inspecione a geometria antes da densificação; aumentar RAM não corrige esses valores.')
    return {'application': 'Paralaxe', 'photos': len(photos), 'videos': video_info, 'sample_count': len(samples), 'samples': samples,
            'warnings': warnings, 'errors': errors, 'geometry': geometry, 'motion_pairs': motion_pairs, 'extraction_report': extraction_report,
            'limitations': 'Triagem por amostragem com limiares heurísticos em imagens reduzidas a até 640 px. Movimento ORB/RANSAC é medido a até 800 px. Homografia dominante não distingue giro de cena plana e não mede paralaxe 3D, sobreposição ou probabilidade de falha; padrões repetidos e objetos móveis podem enganar as correspondências. Fotos não amostradas podem conter problemas. Nenhum original foi alterado.'}


if __name__ == '__main__':
    import sys
    print(json.dumps(analyze(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else None), allow_nan=False))
