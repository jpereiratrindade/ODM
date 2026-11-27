"""Photogrammetric camera profiles and flight-planning calculations (stdlib only)."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
import xml.etree.ElementTree as ET

VERTICAL_REFERENCES = ('relative_to_takeoff', 'agl', 'msl')


def _require_positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} deve ser um número positivo.')
    return float(value)


def _require_overlap(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value < 1:
        raise ValueError(f'{name} deve estar entre 0 e 1 (exclusivo).')
    return float(value)


@dataclass(frozen=True)
class CameraProfile:
    """Physical and nominal acquisition parameters for a mapping camera."""

    profile_id: str
    name: str
    width_px: int
    height_px: int
    sensor_width_mm: float
    sensor_height_mm: float
    focal_mm: float
    min_interval_s: float
    fov_diag_deg: float | None = None
    sensor_source: str = 'Especificação nominal do fabricante'
    uncertainty_note: str = 'Parâmetros nominais; não substituem calibração óptica geométrica.'

    def __post_init__(self):
        if isinstance(self.width_px, bool) or not isinstance(self.width_px, int) or self.width_px <= 0:
            raise ValueError('width_px deve ser inteiro positivo.')
        if isinstance(self.height_px, bool) or not isinstance(self.height_px, int) or self.height_px <= 0:
            raise ValueError('height_px deve ser inteiro positivo.')
        _require_positive(self.sensor_width_mm, 'sensor_width_mm')
        _require_positive(self.sensor_height_mm, 'sensor_height_mm')
        _require_positive(self.focal_mm, 'focal_mm')
        _require_positive(self.min_interval_s, 'min_interval_s')
        if self.fov_diag_deg is not None:
            _require_positive(self.fov_diag_deg, 'fov_diag_deg')

    def to_dict(self):
        return asdict(self)


CAMERA_PROFILES = {
    'dji-mini-4-pro-12mp': CameraProfile(
        profile_id='dji-mini-4-pro-12mp',
        name='DJI Mini 4 Pro · 12 MP',
        width_px=4000,
        height_px=3000,
        sensor_width_mm=9.7,
        sensor_height_mm=7.275,
        focal_mm=6.72,
        min_interval_s=2.0,
        fov_diag_deg=82.1,
        sensor_source='Sensor 1/1.3" CMOS · modo 12 MP (4:3)',
        uncertainty_note='Dimensões nominais e cadência mínima nominal (2 s); confirmar por EXIF em campo.',
    ),
    'dji-mini-4-pro-48mp': CameraProfile(
        profile_id='dji-mini-4-pro-48mp',
        name='DJI Mini 4 Pro · 48 MP',
        width_px=8000,
        height_px=6000,
        sensor_width_mm=9.7,
        sensor_height_mm=7.275,
        focal_mm=6.72,
        min_interval_s=5.0,
        fov_diag_deg=82.1,
        sensor_source='Sensor 1/1.3" CMOS · modo 48 MP (4:3)',
        uncertainty_note='Cadência mínima nominal de 5 s exige menor velocidade para manter sobreposição.',
    ),
}


def get_camera_profile(profile_id):
    if profile_id not in CAMERA_PROFILES:
        raise ValueError(f'Perfil de câmera desconhecido: {profile_id}')
    return CAMERA_PROFILES[profile_id]


def camera_from_metadata(metadata, min_interval_s=2.0, profile_id='exif-metadata'):
    """Build a CameraProfile from preview.planning_metadata() without inventing missing fields."""
    if not isinstance(metadata, dict):
        raise ValueError('Metadados inválidos.')
    width = metadata.get('width')
    height = metadata.get('height')
    sensor_w = metadata.get('sensor_mm')
    focal = metadata.get('focal_mm')
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
        raise ValueError('Dimensões originais da imagem ausentes nos metadados.')
    if sensor_w is None or focal is None:
        raise ValueError('Largura do sensor ou distância focal ausente nos metadados.')
    sensor_w = _require_positive(sensor_w, 'sensor_mm')
    focal = _require_positive(focal, 'focal_mm')
    sensor_h = sensor_w * (height / width)
    label = ' '.join(part for part in (metadata.get('make'), metadata.get('model')) if part) or 'Câmera por EXIF'
    source = metadata.get('sensor_source') or 'Metadados EXIF da fotografia'
    return CameraProfile(
        profile_id=profile_id,
        name=label,
        width_px=width,
        height_px=height,
        sensor_width_mm=sensor_w,
        sensor_height_mm=sensor_h,
        focal_mm=focal,
        min_interval_s=_require_positive(min_interval_s, 'min_interval_s'),
        sensor_source=f'{source}; altura do sensor inferida pela proporção da imagem',
        uncertainty_note='Parametrização por metadados EXIF; não equivale a calibração óptica geométrica.',
    )


def compute_gsd(altitude_m, sensor_mm, focal_mm, pixels):
    """Return nominal GSD in cm/px using the exact same f64 expression as gsd.wasm."""
    altitude = _require_positive(altitude_m, 'altitude_m')
    sensor = _require_positive(sensor_mm, 'sensor_mm')
    focal = _require_positive(focal_mm, 'focal_mm')
    px = _require_positive(pixels, 'pixels')
    return altitude * sensor / focal / px * 100.0


def _evaluate_flight(camera, altitude_m, front_overlap, side_overlap, speed_ms,
                     vertical_reference='relative_to_takeoff', shutter_s=None, max_blur_px=1.0):
    if vertical_reference not in VERTICAL_REFERENCES:
        raise ValueError(f'Referência vertical inválida: {vertical_reference}')
    altitude = _require_positive(altitude_m, 'altitude_m')
    front = _require_overlap(front_overlap, 'front_overlap')
    side = _require_overlap(side_overlap, 'side_overlap')
    speed = _require_positive(speed_ms, 'speed_ms')
    blur_limit = _require_positive(max_blur_px, 'max_blur_px')
    shutter = _require_positive(shutter_s, 'shutter_s') if shutter_s is not None else None

    gsd_h = compute_gsd(altitude, camera.sensor_width_mm, camera.focal_mm, camera.width_px)
    gsd_v = compute_gsd(altitude, camera.sensor_height_mm, camera.focal_mm, camera.height_px)
    gsd_nominal = max(gsd_h, gsd_v)

    footprint_w = altitude * camera.sensor_width_mm / camera.focal_mm
    footprint_h = altitude * camera.sensor_height_mm / camera.focal_mm
    line_spacing = footprint_w * (1.0 - side)
    photo_spacing = footprint_h * (1.0 - front)

    required_interval = photo_spacing / speed
    max_speed_cadence = photo_spacing / camera.min_interval_s
    max_speed_blur = (blur_limit * (gsd_nominal / 100.0) / shutter) if shutter is not None else None
    effective_max_speed = min(max_speed_cadence, max_speed_blur) if max_speed_blur is not None else max_speed_cadence

    estimated_step = max(required_interval, camera.min_interval_s) * speed
    estimated_front_overlap = max(0.0, 1.0 - estimated_step / footprint_h)

    warnings = []
    if speed > max_speed_cadence + 1e-9:
        warnings.append(
            f'Velocidade ({speed:.2f} m/s) excede o limite de cadência ({max_speed_cadence:.2f} m/s) '
            f'para intervalo mínimo de {camera.min_interval_s:.1f} s; sobreposição frontal cai para {estimated_front_overlap * 100:.1f}%.'
        )
    if max_speed_blur is not None and speed > max_speed_blur + 1e-9:
        warnings.append(
            f'Velocidade ({speed:.2f} m/s) excede o limite de borramento ({max_speed_blur:.2f} m/s) '
            f'para exposição de {shutter:.4f} s e limite de {blur_limit:.2f} px.'
        )
    if altitude > 120.0:
        warnings.append('Altura planejada acima de 120 m; verifique requisitos regulatórios e autorização no SARPAS.')
    if vertical_reference == 'relative_to_takeoff':
        warnings.append(
            'Altitude relativa ao ponto de decolagem; não corresponde a AGL real em terreno inclinado ou ondulado.'
        )

    return {
        'camera': camera.to_dict(),
        'altitude_m': altitude,
        'vertical_reference': vertical_reference,
        'front_overlap': front,
        'side_overlap': side,
        'speed_ms': speed,
        'gsd_horizontal_cm': gsd_h,
        'gsd_vertical_cm': gsd_v,
        'gsd_nominal_cm': gsd_nominal,
        'footprint_width_m': footprint_w,
        'footprint_height_m': footprint_h,
        'line_spacing_m': line_spacing,
        'photo_spacing_m': photo_spacing,
        'required_interval_s': required_interval,
        'max_speed_cadence_ms': max_speed_cadence,
        'max_speed_blur_ms': max_speed_blur,
        'effective_max_speed_ms': effective_max_speed,
        'estimated_front_overlap': estimated_front_overlap,
        'speed_compatible': speed <= effective_max_speed + 1e-9,
        'warnings': warnings,
        'assumptions': [
            'Câmera nadiral sobre terreno plano.',
            'Pixels quadrados na proporção informada.',
            'Não inclui vento, distorção óptica ou variação de relevo.',
        ],
    }


def plan_direct(camera, altitude_m, front_overlap=0.8, side_overlap=0.75, speed_ms=4.0,
                vertical_reference='relative_to_takeoff', shutter_s=None, max_blur_px=1.0):
    """Compute GSD, footprint, spacing and cadence limits from flight altitude."""
    result = _evaluate_flight(
        camera,
        altitude_m=altitude_m,
        front_overlap=front_overlap,
        side_overlap=side_overlap,
        speed_ms=speed_ms,
        vertical_reference=vertical_reference,
        shutter_s=shutter_s,
        max_blur_px=max_blur_px,
    )
    result['mode'] = 'direct'
    return result


def plan_inverse(camera, target_gsd_cm, front_overlap=0.8, side_overlap=0.75, speed_ms=None,
                 vertical_reference='relative_to_takeoff', shutter_s=None, max_blur_px=1.0):
    """Compute required altitude and compatible speed from a target nominal GSD."""
    target = _require_positive(target_gsd_cm, 'target_gsd_cm')
    alt_h = target * camera.focal_mm * camera.width_px / (100.0 * camera.sensor_width_mm)
    alt_v = target * camera.focal_mm * camera.height_px / (100.0 * camera.sensor_height_mm)
    required_altitude = min(alt_h, alt_v)

    probe_speed = 1.0 if speed_ms is None else speed_ms
    probe = _evaluate_flight(
        camera,
        altitude_m=required_altitude,
        front_overlap=front_overlap,
        side_overlap=side_overlap,
        speed_ms=probe_speed,
        vertical_reference=vertical_reference,
        shutter_s=shutter_s,
        max_blur_px=max_blur_px,
    )
    if speed_ms is None:
        probe = _evaluate_flight(
            camera,
            altitude_m=required_altitude,
            front_overlap=front_overlap,
            side_overlap=side_overlap,
            speed_ms=probe['effective_max_speed_ms'],
            vertical_reference=vertical_reference,
            shutter_s=shutter_s,
            max_blur_px=max_blur_px,
        )
    probe['mode'] = 'inverse'
    probe['target_gsd_cm'] = target
    return probe


EARTH_RADIUS_M = 6378137.0
DEFAULT_BATTERY_SAFE_SECONDS = 22 * 60
MAX_DJI_WAYPOINTS = 200


class LocalProjection:
    """Local tangent-plane projection (meters East/North) around a WGS84 origin."""

    def __init__(self, origin_lon, origin_lat):
        if isinstance(origin_lon, bool) or not isinstance(origin_lon, (int, float)) or not math.isfinite(origin_lon) or not -180.0 <= origin_lon <= 180.0:
            raise ValueError('Longitude de origem inválida.')
        if isinstance(origin_lat, bool) or not isinstance(origin_lat, (int, float)) or not math.isfinite(origin_lat) or not -85.0 <= origin_lat <= 85.0:
            raise ValueError('Latitude de origem inválida.')
        self.origin_lon = float(origin_lon)
        self.origin_lat = float(origin_lat)
        self._cos_lat = math.cos(math.radians(self.origin_lat))

    def to_local(self, lon, lat):
        x = math.radians(lon - self.origin_lon) * EARTH_RADIUS_M * self._cos_lat
        y = math.radians(lat - self.origin_lat) * EARTH_RADIUS_M
        return (x, y)

    def to_wgs84(self, x_m, y_m):
        lon = self.origin_lon + math.degrees(x_m / (EARTH_RADIUS_M * self._cos_lat))
        lat = self.origin_lat + math.degrees(y_m / EARTH_RADIUS_M)
        return (lon, lat)


def _validate_wgs84_polygon(polygon):
    if not isinstance(polygon, (list, tuple)) or len(polygon) < 3:
        raise ValueError('O polígono precisa ter ao menos 3 vértices.')
    cleaned = []
    for pt in polygon:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError('Cada vértice deve ser um par (longitude, latitude).')
        lon, lat = pt
        if isinstance(lon, bool) or not isinstance(lon, (int, float)) or not math.isfinite(lon) or not -180.0 <= lon <= 180.0:
            raise ValueError('Longitude inválida no polígono.')
        if isinstance(lat, bool) or not isinstance(lat, (int, float)) or not math.isfinite(lat) or not -85.0 <= lat <= 85.0:
            raise ValueError('Latitude inválida no polígono.')
        pair = (float(lon), float(lat))
        if not cleaned or cleaned[-1] != pair:
            cleaned.append(pair)
    if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned.pop()
    if len(cleaned) < 3:
        raise ValueError('O polígono precisa ter ao menos 3 vértices distintos.')
    return cleaned


def _polygon_area_and_convexity(pts_m):
    n = len(pts_m)
    area2 = 0.0
    sign = 0
    for i in range(n):
        x1, y1 = pts_m[i]
        x2, y2 = pts_m[(i + 1) % n]
        x3, y3 = pts_m[(i + 2) % n]
        area2 += x1 * y2 - x2 * y1
        cross = (x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2)
        if abs(cross) > 1e-7:
            curr = 1 if cross > 0 else -1
            if sign == 0:
                sign = curr
            elif sign != curr:
                raise ValueError('Polígono côncavo ou auto-intersectante; suporte completo entra no PATCH-002B.')
    area = abs(area2) * 0.5
    if area <= 1e-3:
        raise ValueError('O polígono possui área nula.')
    return area


def rectangle_polygon(center_lon, center_lat, width_m, height_m, angle_deg=0.0):
    """Create a 4-vertex WGS84 rectangle centered at (center_lon, center_lat)."""
    w = _require_positive(width_m, 'width_m')
    h = _require_positive(height_m, 'height_m')
    if isinstance(angle_deg, bool) or not isinstance(angle_deg, (int, float)) or not math.isfinite(angle_deg):
        raise ValueError('angle_deg inválido.')
    proj = LocalProjection(center_lon, center_lat)
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    hw, hh = w * 0.5, h * 0.5
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return [proj.to_wgs84(dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a) for dx, dy in corners]


def _rotate_to_track(x, y, heading_deg):
    # heading_deg is clockwise from North (+Y): u = along track, v = cross track (right)
    rad = math.radians(heading_deg)
    sin_h, cos_h = math.sin(rad), math.cos(rad)
    u = x * sin_h + y * cos_h
    v = x * cos_h - y * sin_h
    return (u, v)


def _rotate_from_track(u, v, heading_deg):
    rad = math.radians(heading_deg)
    sin_h, cos_h = math.sin(rad), math.cos(rad)
    x = u * sin_h + v * cos_h
    y = u * cos_h - v * sin_h
    return (x, y)


def _optimal_heading_deg(pts_m):
    best_heading = 0.0
    best_span = float('inf')
    n = len(pts_m)
    for i in range(n):
        x1, y1 = pts_m[i]
        x2, y2 = pts_m[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        if math.hypot(dx, dy) <= 1e-6:
            continue
        heading = math.degrees(math.atan2(dx, dy)) % 180.0
        vs = [_rotate_to_track(x, y, heading)[1] for x, y in pts_m]
        span = max(vs) - min(vs)
        if span < best_span - 1e-9:
            best_span = span
            best_heading = heading
    return best_heading


def _clip_line_v(track_pts, v_val):
    us = []
    n = len(track_pts)
    for i in range(n):
        u1, v1 = track_pts[i]
        u2, v2 = track_pts[(i + 1) % n]
        if abs(v2 - v1) <= 1e-9:
            if abs(v_val - v1) <= 1e-9:
                us.extend((u1, u2))
            continue
        if (v1 <= v_val <= v2) or (v2 <= v_val <= v1):
            t = (v_val - v1) / (v2 - v1)
            us.append(u1 + t * (u2 - u1))
    if len(us) < 2:
        return None
    u_min, u_max = min(us), max(us)
    if u_max - u_min <= 1e-6:
        return None
    return (u_min, u_max)


def plan_grid(camera, polygon, altitude_m=None, target_gsd_cm=None, front_overlap=0.8,
              side_overlap=0.75, speed_ms=None, heading_deg=None, overshoot_m=0.0,
              vertical_reference='relative_to_takeoff', shutter_s=None, max_blur_px=1.0,
              battery_safe_seconds=DEFAULT_BATTERY_SAFE_SECONDS):
    """Generate a simple nadir serpentine flight grid over a convex WGS84 polygon."""
    if (altitude_m is None) == (target_gsd_cm is None):
        raise ValueError('Informe exatamente um entre altitude_m (direto) ou target_gsd_cm (inverso).')
    if isinstance(overshoot_m, bool) or not isinstance(overshoot_m, (int, float)) or not math.isfinite(overshoot_m) or overshoot_m < 0:
        raise ValueError('overshoot_m deve ser >= 0.')
    battery_s = _require_positive(battery_safe_seconds, 'battery_safe_seconds')

    if target_gsd_cm is not None:
        base = plan_inverse(
            camera,
            target_gsd_cm=target_gsd_cm,
            front_overlap=front_overlap,
            side_overlap=side_overlap,
            speed_ms=speed_ms,
            vertical_reference=vertical_reference,
            shutter_s=shutter_s,
            max_blur_px=max_blur_px,
        )
    else:
        base = plan_direct(
            camera,
            altitude_m=altitude_m,
            front_overlap=front_overlap,
            side_overlap=side_overlap,
            speed_ms=4.0 if speed_ms is None else speed_ms,
            vertical_reference=vertical_reference,
            shutter_s=shutter_s,
            max_blur_px=max_blur_px,
        )

    ring = _validate_wgs84_polygon(polygon)
    origin_lon = sum(p[0] for p in ring) / len(ring)
    origin_lat = sum(p[1] for p in ring) / len(ring)
    proj = LocalProjection(origin_lon, origin_lat)
    pts_m = [proj.to_local(lon, lat) for lon, lat in ring]
    area_m2 = _polygon_area_and_convexity(pts_m)

    if heading_deg is None:
        resolved_heading = _optimal_heading_deg(pts_m)
    else:
        if isinstance(heading_deg, bool) or not isinstance(heading_deg, (int, float)) or not math.isfinite(heading_deg):
            raise ValueError('heading_deg inválido.')
        resolved_heading = float(heading_deg) % 360.0

    track_pts = [_rotate_to_track(x, y, resolved_heading) for x, y in pts_m]
    v_min = min(p[1] for p in track_pts)
    v_max = max(p[1] for p in track_pts)
    span_v = v_max - v_min

    spacing = base['line_spacing_m']
    strip_count = max(1, math.ceil(span_v / spacing))
    if strip_count == 1:
        v_positions = [(v_min + v_max) * 0.5]
    else:
        total_strip_span = (strip_count - 1) * spacing
        margin = (span_v - total_strip_span) * 0.5
        v_positions = [v_min + margin + i * spacing for i in range(strip_count)]

    waypoints_local = []
    photo_points_local = []
    flight_lines = []
    photo_spacing = base['photo_spacing_m']

    for idx, v_val in enumerate(v_positions):
        clipped = _clip_line_v(track_pts, v_val)
        if clipped is None:
            continue
        u_start, u_end = clipped
        u_a = u_start - overshoot_m
        u_b = u_end + overshoot_m
        if idx % 2 == 1:
            u_a, u_b = u_b, u_a

        p_start = _rotate_from_track(u_a, v_val, resolved_heading)
        p_end = _rotate_from_track(u_b, v_val, resolved_heading)
        waypoints_local.extend((p_start, p_end))
        flight_lines.append([proj.to_wgs84(*p_start), proj.to_wgs84(*p_end)])

        seg_len = abs(u_b - u_a)
        n_photos = max(1, math.floor(seg_len / photo_spacing) + 1)
        direction = 1.0 if u_b >= u_a else -1.0
        for j in range(n_photos):
            u_p = u_a + direction * min(seg_len, j * photo_spacing)
            photo_points_local.append(_rotate_from_track(u_p, v_val, resolved_heading))

    total_distance_m = sum(
        math.hypot(waypoints_local[i + 1][0] - waypoints_local[i][0],
                   waypoints_local[i + 1][1] - waypoints_local[i][1])
        for i in range(len(waypoints_local) - 1)
    )
    estimated_flight_time_s = total_distance_m / base['speed_ms']
    estimated_batteries = max(1, math.ceil(estimated_flight_time_s / battery_s))
    waypoints_wgs84 = [proj.to_wgs84(x, y) for x, y in waypoints_local]
    photo_points_wgs84 = [proj.to_wgs84(x, y) for x, y in photo_points_local]

    warnings = list(base['warnings'])
    if len(waypoints_wgs84) > MAX_DJI_WAYPOINTS:
        warnings.append(
            f'A missão gera {len(waypoints_wgs84)} waypoints (limite do DJI Fly: {MAX_DJI_WAYPOINTS}); '
            'exigirá divisão em blocos no PATCH-004.'
        )
    if estimated_batteries > 1:
        warnings.append(
            f'Tempo estimado ({estimated_flight_time_s / 60:.1f} min) excede a janela conservadora de '
            f'{battery_s / 60:.0f} min/bateria ({estimated_batteries} baterias estimadas).'
        )

    base.update({
        'crs': 'EPSG:4326',
        'projection_origin': {'lon': origin_lon, 'lat': origin_lat},
        'study_area': ring,
        'area_m2': area_m2,
        'area_ha': area_m2 / 10000.0,
        'heading_deg': resolved_heading,
        'overshoot_m': float(overshoot_m),
        'strip_count': len(flight_lines),
        'waypoint_count': len(waypoints_wgs84),
        'estimated_photos': len(photo_points_wgs84),
        'total_distance_m': total_distance_m,
        'estimated_flight_time_s': estimated_flight_time_s,
        'estimated_batteries': estimated_batteries,
        'battery_safe_seconds': battery_s,
        'waypoints': waypoints_wgs84,
        'flight_lines': flight_lines,
        'photo_points': photo_points_wgs84,
        'warnings': warnings,
    })
    return base


def mission_to_dict(plan, name='missao-001', mission_id=None):
    """Build the versioned mission.json payload from a plan_grid() result."""
    return {
        'schema_version': '0.1.0',
        'mission_id': mission_id or name,
        'name': name,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'crs': plan['crs'],
        'vertical_reference': plan['vertical_reference'],
        'projection_origin': plan['projection_origin'],
        'camera': plan['camera'],
        'parameters': {
            'mode': plan['mode'],
            'altitude_m': plan['altitude_m'],
            'target_gsd_cm': plan.get('target_gsd_cm'),
            'front_overlap': plan['front_overlap'],
            'side_overlap': plan['side_overlap'],
            'speed_ms': plan['speed_ms'],
            'heading_deg': plan['heading_deg'],
            'overshoot_m': plan['overshoot_m'],
            'battery_safe_seconds': plan['battery_safe_seconds'],
        },
        'estimates': {
            'area_m2': plan['area_m2'],
            'area_ha': plan['area_ha'],
            'gsd_horizontal_cm': plan['gsd_horizontal_cm'],
            'gsd_vertical_cm': plan['gsd_vertical_cm'],
            'gsd_nominal_cm': plan['gsd_nominal_cm'],
            'footprint_width_m': plan['footprint_width_m'],
            'footprint_height_m': plan['footprint_height_m'],
            'line_spacing_m': plan['line_spacing_m'],
            'photo_spacing_m': plan['photo_spacing_m'],
            'strip_count': plan['strip_count'],
            'waypoint_count': plan['waypoint_count'],
            'estimated_photos': plan['estimated_photos'],
            'total_distance_m': plan['total_distance_m'],
            'estimated_flight_time_s': plan['estimated_flight_time_s'],
            'estimated_batteries': plan['estimated_batteries'],
            'speed_compatible': plan['speed_compatible'],
        },
        'study_area': plan['study_area'],
        'waypoints': plan['waypoints'],
        'flight_lines': plan['flight_lines'],
        'warnings': plan['warnings'],
        'assumptions': plan['assumptions'],
    }


def mission_to_geojson(plan, name='missao-001'):
    """Serialize study area, planned route and waypoints as a GeoJSON FeatureCollection."""
    ring = list(plan['study_area'])
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    features = [
        {
            'type': 'Feature',
            'properties': {'kind': 'study_area', 'name': name, 'area_ha': plan['area_ha']},
            'geometry': {'type': 'Polygon', 'coordinates': [ring]},
        },
        {
            'type': 'Feature',
            'properties': {
                'kind': 'planned_route',
                'name': name,
                'altitude_m': plan['altitude_m'],
                'vertical_reference': plan['vertical_reference'],
                'gsd_nominal_cm': plan['gsd_nominal_cm'],
                'speed_ms': plan['speed_ms'],
            },
            'geometry': {'type': 'LineString', 'coordinates': plan['waypoints']},
        },
    ]
    for idx, wp in enumerate(plan['waypoints'], start=1):
        features.append({
            'type': 'Feature',
            'properties': {'kind': 'waypoint', 'index': idx, 'altitude_m': plan['altitude_m']},
            'geometry': {'type': 'Point', 'coordinates': wp},
        })
    return {'type': 'FeatureCollection', 'features': features}


def mission_to_kml(plan, name='missao-001'):
    """Serialize study area, route and waypoints as a KML 2.2 document (stdlib XML)."""
    kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
    doc = ET.SubElement(kml, 'Document')
    ET.SubElement(doc, 'name').text = name
    ET.SubElement(doc, 'description').text = (
        f'GSD nominal: {plan["gsd_nominal_cm"]:.2f} cm/px | '
        f'Altitude ({plan["vertical_reference"]}): {plan["altitude_m"]:.1f} m | '
        f'Velocidade: {plan["speed_ms"]:.1f} m/s'
    )

    ring = list(plan['study_area'])
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    area_pm = ET.SubElement(doc, 'Placemark')
    ET.SubElement(area_pm, 'name').text = f'{name} - Área de estudo'
    poly = ET.SubElement(area_pm, 'Polygon')
    outer = ET.SubElement(poly, 'outerBoundaryIs')
    lr = ET.SubElement(outer, 'LinearRing')
    ET.SubElement(lr, 'coordinates').text = ' '.join(f'{lon:.7f},{lat:.7f},0' for lon, lat in ring)

    alt = plan['altitude_m']
    route_pm = ET.SubElement(doc, 'Placemark')
    ET.SubElement(route_pm, 'name').text = f'{name} - Trajetória planejada'
    ls = ET.SubElement(route_pm, 'LineString')
    ET.SubElement(ls, 'altitudeMode').text = 'relativeToGround'
    ET.SubElement(ls, 'coordinates').text = ' '.join(f'{lon:.7f},{lat:.7f},{alt:.2f}' for lon, lat in plan['waypoints'])

    for idx, (lon, lat) in enumerate(plan['waypoints'], start=1):
        wp_pm = ET.SubElement(doc, 'Placemark')
        ET.SubElement(wp_pm, 'name').text = f'WP-{idx:03d}'
        pt = ET.SubElement(wp_pm, 'Point')
        ET.SubElement(pt, 'altitudeMode').text = 'relativeToGround'
        ET.SubElement(pt, 'coordinates').text = f'{lon:.7f},{lat:.7f},{alt:.2f}'

    ET.indent(kml, space='  ')
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(kml, encoding='unicode')
