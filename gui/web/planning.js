'use strict';
const p = id => document.getElementById(id);
let planningGlobal = null, planningLoadedProject = null, planningData = null, planningSerial = 0;
let planningHeightMode = 'manual';

function updatePlanningProjects() {
  const globalProject = toolSources.planning ? toolSources.planning.projects[0].name : p('project').value, current = p('planning-project').value;
  const selected = globalProject !== planningGlobal ? globalProject : current || globalProject;
  planningGlobal = globalProject;
  p('planning-project').replaceChildren();
  for (const project of toolProjects('planning')) p('planning-project').add(new Option(project.name,project.name));
  if (selected) p('planning-project').value = selected;
  if (current !== p('planning-project').value) loadPlanning();
}

function planningField(title,value) {
  const group=document.createElement('div'),term=document.createElement('dt'),detail=document.createElement('dd');
  term.textContent=title;detail.textContent=value;group.append(term,detail);p('planning-metadata').append(group);
}
function clearPlanningFields() {
  for (const id of ['sensor','focal','pixels']) {p(id).value='';p(id+'-source').textContent='Não extraído · informe manualmente';}
  p('planning-use-relative').hidden=true;
  p('gsd').textContent='—';p('gsd-method').textContent='Complete os parâmetros para calcular';
}

async function loadPlanning(file) {
  const project=p('planning-project').value;
  if (!project) return;
  const serial=++planningSerial;
  const identity = toolIdentity('planning', project);
  const differentProject=identity!==planningLoadedProject;
  if (differentProject || planningHeightMode==='relative') {
    p('altitude').value='';p('altitude-source').textContent='Informe altura acima do terreno · altitude GPS não é usada';planningHeightMode='manual';
  }
  clearPlanningFields();p('planning-metadata').replaceChildren();planningData=null;
  p('planning-status').textContent='Lendo EXIF/XMP da foto original…';
  try {
    const data=await api('planning',toolArguments('planning',{project,...(typeof file==='string'&&file?{file}:{})}));
    if (serial!==planningSerial) return;
    planningData=data;planningLoadedProject=identity;
    p('planning-photo').replaceChildren();
    for (const name of data.photos) p('planning-photo').add(new Option(name,name));
    p('planning-photo').value=data.photo;
    const camera=[data.make,data.model].filter(Boolean).join(' ');
    planningField('Câmera',camera||'Não identificada');planningField('Referência',data.photo);
    planningField('Imagem original',`${data.width.toLocaleString('pt-BR')} × ${data.height.toLocaleString('pt-BR')} pixels`);
    if (data.captured) planningField('Captura · EXIF',data.captured);
    if (data.focal_35_mm) planningField('Focal equivalente · EXIF',`${data.focal_35_mm} mm (35 mm)`);
    if (Number.isFinite(data.relative_altitude)) {
      planningField('Altura relativa à decolagem · XMP',`${data.relative_altitude.toLocaleString('pt-BR')} m`);
      p('planning-use-relative').hidden=!(data.relative_altitude>0&&data.relative_altitude<=10000);
    }
    if (Number.isFinite(data.gps?.altitude)) planningField('Altitude GPS · EXIF',`${data.gps.altitude.toLocaleString('pt-BR')} m · não é altura sobre terreno`);
    if (Number.isFinite(data.gps?.latitude)&&Number.isFinite(data.gps?.longitude)) planningField('Coordenadas GPS · EXIF',`${data.gps.latitude.toFixed(6)}, ${data.gps.longitude.toFixed(6)}`);
    for (const [id,value,source] of [
      ['sensor',data.sensor_mm,data.sensor_source],['focal',data.focal_mm,'Distância focal registrada no EXIF'],
      ['pixels',data.width,'Largura original do arquivo · antes da orientação EXIF']]) {
      if (Number.isFinite(value)&&value>0) {p(id).value=Number(value.toFixed(6));p(id+'-source').textContent=source;}
    }
    p('planning-status').textContent=`Dados carregados de ${data.photo}. Revise a altura acima do terreno antes de calcular.`;
    calculateGsd();
  } catch(error) {
    if(serial!==planningSerial)return;
    p('planning-status').textContent=error.message+' Parâmetros podem ser informados manualmente.';
  }
}

p('planning-project').addEventListener('change',()=>{p('planning-photo').replaceChildren();loadPlanning();});
p('planning-photo').addEventListener('change',()=>loadPlanning(p('planning-photo').value));
p('planning-reload').addEventListener('click',()=>loadPlanning(p('planning-photo').value));
p('planning-use-relative').addEventListener('click',()=>{
  if(!planningData||!(planningData.relative_altitude>0))return;
  p('altitude').value=planningData.relative_altitude;planningHeightMode='relative';
  p('altitude-source').textContent='Aproximação: altura XMP acima da decolagem. Revise diferença de relevo até o local fotografado.';
  calculateGsd();
});
for (const id of ['sensor','focal','pixels','altitude']) p(id).addEventListener('input',()=>{
  p(id+'-source').textContent=id==='altitude'?'Altura acima do terreno informada manualmente':'Valor editado manualmente';
  if(id==='altitude')planningHeightMode='manual';
  p('gsd').textContent='—';p('gsd-method').textContent='Parâmetros alterados · clique Calcular GSD';
});

let missionPolygon = null;

function toggleMissionMode() {
  const inverse = p('mission-mode').value === 'inverse';
  p('mission-altitude-wrap').hidden = inverse;
  p('mission-gsd-wrap').hidden = !inverse;
  p('mission-altitude').required = !inverse;
  p('mission-gsd-target').required = inverse;
}

function buildMissionPayload() {
  const mode = p('mission-mode').value;
  const cameraProfile = p('mission-camera').value;
  const headingRaw = p('mission-heading').value.trim();
  const payload = {
    name: p('mission-name').value.trim() || 'missao-001',
    camera_profile: cameraProfile,
    mode,
    front_overlap: Number(p('mission-front').value) / 100,
    side_overlap: Number(p('mission-side').value) / 100,
    speed_ms: Number(p('mission-speed').value),
    heading_deg: headingRaw === '' ? null : Number(headingRaw),
    overshoot_m: Number(p('mission-overshoot').value),
    vertical_reference: p('mission-vref').value,
  };
  if (mode === 'inverse') {
    payload.target_gsd_cm = Number(p('mission-gsd-target').value);
  } else {
    payload.altitude_m = Number(p('mission-altitude').value);
  }
  if (cameraProfile === 'custom') {
    payload.width_px = Number(p('pixels').value) || 4000;
    payload.height_px = Math.round(payload.width_px * 0.75);
    payload.sensor_width_mm = Number(p('sensor').value) || 9.7;
    payload.focal_mm = Number(p('focal').value) || 6.72;
    payload.min_interval_s = 2.0;
  }
  if (missionPolygon) {
    payload.polygon = missionPolygon;
  } else {
    payload.center = {lat: Number(p('mission-lat').value), lon: Number(p('mission-lon').value)};
    payload.width_m = Number(p('mission-width').value);
    payload.height_m = Number(p('mission-height').value);
    payload.area_angle_deg = Number(p('mission-area-angle').value);
  }
  return payload;
}

function drawMissionCanvas(plan) {
  const canvas = p('mission-canvas');
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height, pad = 34;
  ctx.clearRect(0, 0, W, H);

  const ring = plan.study_area || [];
  const wps = plan.waypoints || [];
  const all = ring.concat(wps);
  if (!all.length) return;

  const cosLat = Math.cos((plan.projection_origin?.lat || 0) * Math.PI / 180);
  const toXY = ([lon, lat]) => [lon * cosLat, lat];
  const pts = all.map(toXY);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [x, y] of pts) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  const spanX = Math.max(1e-9, maxX - minX);
  const spanY = Math.max(1e-9, maxY - minY);
  const scale = Math.min((W - 2 * pad) / spanX, (H - 2 * pad) / spanY);
  const ox = (W - spanX * scale) * 0.5;
  const oy = (H - spanY * scale) * 0.5;
  const px = pt => {
    const [x, y] = toXY(pt);
    return [ox + (x - minX) * scale, H - (oy + (y - minY) * scale)];
  };

  if (ring.length >= 3) {
    ctx.beginPath();
    ring.forEach((pt, idx) => {
      const [cx, cy] = px(pt);
      if (idx === 0) ctx.moveTo(cx, cy);
      else ctx.lineTo(cx, cy);
    });
    ctx.closePath();
    ctx.fillStyle = '#087f8018';
    ctx.fill();
    ctx.strokeStyle = '#087f80';
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  if (wps.length >= 2) {
    ctx.beginPath();
    wps.forEach((pt, idx) => {
      const [cx, cy] = px(pt);
      if (idx === 0) ctx.moveTo(cx, cy);
      else ctx.lineTo(cx, cy);
    });
    ctx.strokeStyle = '#157fba';
    ctx.lineWidth = 1.6;
    ctx.stroke();

    const [sx, sy] = px(wps[0]);
    ctx.fillStyle = '#0f8a4b';
    ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2); ctx.fill();
    const [ex, ey] = px(wps[wps.length - 1]);
    ctx.fillStyle = '#b43d2a';
    ctx.beginPath(); ctx.arc(ex, ey, 5, 0, Math.PI * 2); ctx.fill();
  }

  ctx.fillStyle = '#18334b66';
  for (const pt of plan.photo_points || []) {
    const [cx, cy] = px(pt);
    ctx.fillRect(cx - 1.5, cy - 1.5, 3, 3);
  }
}

function renderMissionPlan(plan) {
  p('mission-output').hidden = false;
  p('mission-stat-gsd').textContent = `${plan.gsd_nominal_cm.toFixed(2)} cm/px`;
  p('mission-stat-alt').textContent = `Altura ${plan.altitude_m.toFixed(1)} m · ${plan.vertical_reference}`;
  p('mission-stat-area').textContent = `${plan.area_ha.toFixed(2)} ha`;
  p('mission-stat-strips').textContent = `${plan.strip_count} faixas · ${plan.heading_deg.toFixed(0)}°`;
  p('mission-stat-photos').textContent = `${plan.estimated_photos} fotos`;
  p('mission-stat-speed').textContent = `${plan.waypoint_count} waypoints · ${plan.speed_ms.toFixed(1)} m/s`;
  p('mission-stat-time').textContent = `${(plan.estimated_flight_time_s / 60).toFixed(1)} min`;
  p('mission-stat-dist').textContent = `${(plan.total_distance_m / 1000).toFixed(2)} km · ${plan.estimated_batteries} bat.`;

  const warnList = p('mission-warnings');
  warnList.replaceChildren();
  if (plan.warnings && plan.warnings.length) {
    warnList.hidden = false;
    for (const msg of plan.warnings) {
      const li = document.createElement('li');
      li.textContent = msg;
      warnList.append(li);
    }
  } else {
    warnList.hidden = true;
  }
  drawMissionCanvas(plan);
}

async function generateMissionPlan() {
  p('mission-status').textContent = 'Calculando grade e parâmetros…';
  try {
    const plan = await api('flight-plan/preview', buildMissionPayload());
    renderMissionPlan(plan);
    p('mission-status').textContent = `Plano "${p('mission-name').value}" gerado (${plan.strip_count} faixas, ${plan.estimated_photos} fotos estimadas).`;
  } catch (error) {
    p('mission-status').textContent = error.message;
  }
}

async function exportMission(format, saveToProject = false) {
  try {
    const payload = buildMissionPayload();
    payload.format = format;
    if (saveToProject) {
      const proj = p('planning-project').value || p('project').value;
      if (!proj) throw new Error('Selecione um levantamento para salvar na pasta do projeto.');
      payload.save_to_project = true;
      payload.project = proj;
    }
    const res = await api('flight-plan/export', payload);
    if (saveToProject) {
      p('mission-status').textContent = `Missão salva em: ${res.saved_dir}`;
      return;
    }
    const blob = new Blob([res.content], {type: res.mime});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = res.filename;
    document.body.append(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
    p('mission-status').textContent = `Arquivo ${res.filename} exportado.`;
  } catch (error) {
    p('mission-status').textContent = error.message;
  }
}

p('mission-mode').addEventListener('change', toggleMissionMode);
p('mission-form').addEventListener('submit', event => { event.preventDefault(); generateMissionPlan(); });
p('mission-geojson-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const geo = JSON.parse(await file.text());
    const geom = geo.type === 'FeatureCollection' ? geo.features?.[0]?.geometry : geo.type === 'Feature' ? geo.geometry : geo;
    if (!geom || geom.type !== 'Polygon' || !Array.isArray(geom.coordinates?.[0])) {
      throw new Error('GeoJSON deve conter uma geometria Polygon.');
    }
    missionPolygon = geom.coordinates[0].map(c => [c[0], c[1]]);
    p('mission-clear-polygon').hidden = false;
    p('mission-status').textContent = `Polígono carregado de ${file.name} (${missionPolygon.length} vértices).`;
  } catch (error) {
    missionPolygon = null;
    p('mission-status').textContent = error.message;
  }
});
p('mission-clear-polygon').addEventListener('click', () => {
  missionPolygon = null;
  p('mission-geojson-file').value = '';
  p('mission-clear-polygon').hidden = true;
  p('mission-status').textContent = 'Área redefinida para retângulo pelos campos manuais.';
});
p('export-mission-json').addEventListener('click', () => exportMission('json', false));
p('export-mission-geojson').addEventListener('click', () => exportMission('geojson', false));
p('export-mission-kml').addEventListener('click', () => exportMission('kml', false));
p('save-mission-project').addEventListener('click', () => exportMission('json', true));
toggleMissionMode();
