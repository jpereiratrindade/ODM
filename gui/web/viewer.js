'use strict';
const v = id => document.getElementById(id);
let viewerData = null;
let rasterImage = null;
let rasterUrl = null;
let viewSerial = 0;
let zoom = 1, panX = 0, panY = 0, angle = 0.3, tilt = 0.9;
let dragging = null;
let framePending = false;
let previousGlobalProject = null;
const canvas = v('result-canvas');
const context = canvas.getContext('2d');

async function loadHistory() {
  const project = v('result-project').value;
  const identity = toolIdentity('results', project);
  v('result-history').hidden = true;
  if (!project) return;
  try {
    const {history} = await api('results', toolArguments('results', {project}));
    if (toolIdentity('results', v('result-project').value) !== identity || !history) return;
    const profile = {low:'Explorar rápido',medium:'Equilíbrio',high:'Mais detalhes'}[history.quality] || history.quality;
    const box = v('result-history'); box.replaceChildren(); box.hidden = false;
    const heading = document.createElement('strong'); heading.textContent = history.success ? 'Último processamento concluído' : history.finished ? 'Último processamento não concluiu' : 'Processamento sem encerramento registrado';
    const note = document.createElement('p'); note.textContent = `Perfil: ${profile || 'não informado'} · Motor OpenDroneMap ${history.version || '—'}${history.seconds ? ' · ' + history.seconds.toLocaleString('pt-BR') + ' segundos' : ''}`;
    box.append(heading, note);
    if (history.error) { const error = document.createElement('p'); error.textContent = history.error; box.append(error); }
    box.classList.toggle('failed', history.success === false);
  } catch { /* History is optional; product access remains available. */ }
}

function updateViewerProjects() {
  const globalProject = toolSources.viewer ? toolSources.viewer.projects[0].name : v('project').value;
  const previous = v('viewer-project').value;
  const selected = globalProject !== previousGlobalProject ? globalProject : previous || globalProject;
  previousGlobalProject = globalProject;
  const before = [...v('viewer-product').options].map(o => o.value).join(',');
  v('viewer-project').replaceChildren();
  for (const project of toolProjects('viewer')) {
    v('viewer-project').add(new Option(`${project.name} · ${project.outputs.length} produtos`, project.name));
  }
  if (selected) v('viewer-project').value = selected;
  const project = toolProjects('viewer').find(p => p.name === v('viewer-project').value);
  const after = productInfo.flatMap((p,i) => project?.outputs.includes(p[2]) ? [productIds[i]] : []).join(',');
  if (previous !== v('viewer-project').value || before !== after) updateViewerProducts();
}

function updateViewerProducts() {
  const selected = v('viewer-product').value;
  const project = toolProjects('viewer').find(p => p.name === v('viewer-project').value);
  v('viewer-product').replaceChildren();
  productInfo.forEach(([title,,path], index) => {
    if (project?.outputs.includes(path)) v('viewer-product').add(new Option(title, productIds[index]));
  });
  if ([...v('viewer-product').options].some(o => o.value === selected)) v('viewer-product').value = selected;
  v('viewer-load').disabled = !v('viewer-product').value;
  clearViewer();
  v('viewer-status').textContent = project?.outputs.length ? 'Escolha um produto e clique Visualizar.' : 'Este levantamento ainda não tem produtos disponíveis. Você pode selecionar outro levantamento acima.';
}

function clearViewer() {
  viewSerial++;
  viewerData = null; rasterImage = null;
  if (rasterUrl) URL.revokeObjectURL(rasterUrl);
  rasterUrl = null;
  v('result-pdf').hidden = true; v('result-pdf').removeAttribute('src');
  canvas.hidden = false; v('viewer-empty').hidden = false;
  v('viewer-download').hidden = true; v('viewer-info').replaceChildren();
  setCanvasControls(false);
  drawViewer();
}

function outputUrl(product, inline = false) {
  return '/api/output?' + new URLSearchParams(toolArguments('viewer',{project:v('viewer-project').value, product, ...(inline ? {inline:'1'} : {})}));
}

function setCanvasControls(enabled) {
  ['viewer-in','viewer-out','viewer-fit'].forEach(id => { v(id).disabled = !enabled; });
}

function infoRow(title, value) {
  const term = document.createElement('dt'); term.textContent = title;
  const description = document.createElement('dd'); description.textContent = value;
  const group = document.createElement('div'); group.append(term, description); v('viewer-info').append(group);
}

async function showProduct() {
  const project = v('viewer-project').value, product = v('viewer-product').value;
  if (!project || !product) return;
  clearViewer(); const serial = viewSerial;
  const link = v('viewer-download'); link.href = outputUrl(product); link.hidden = false;
  v('viewer-status').textContent = 'Preparando prévia local…';
  v('viewer-empty').hidden = true;
  zoom = 1; panX = panY = 0; angle = 0.3; tilt = 0.9;
  try {
    if (product === 'report') {
      canvas.hidden = true; v('result-pdf').hidden = false; v('result-pdf').src = outputUrl(product, true);
      v('viewer-status').textContent = 'Relatório PDF do processamento. Se o navegador não exibir, use Baixar arquivo original.';
      return;
    }
    const data = await api('view', toolArguments('viewer', {project,product}));
    if (serial !== viewSerial) return;
    viewerData = data;
    if (data.kind === 'raster') {
      const bytes = Uint8Array.from(atob(data.image), c => c.charCodeAt(0));
      rasterUrl = URL.createObjectURL(new Blob([bytes], {type:'image/png'}));
      rasterImage = new Image(); rasterImage.src = rasterUrl; await rasterImage.decode();
      if (serial !== viewSerial) return;
      v('viewer-status').textContent = 'Prévia reduzida do raster real. O download mantém a resolução original.';
      infoRow('Dimensões originais', `${data.width.toLocaleString('pt-BR')} × ${data.height.toLocaleString('pt-BR')} pixels`);
      infoRow('Sistema de coordenadas', data.epsg ? `EPSG:${data.epsg}` : 'Sem código EPSG identificado');
      infoRow('Tamanho do pixel', `${Math.abs(data.pixel_size[0]).toLocaleString('pt-BR',{maximumFractionDigits:6})} × ${Math.abs(data.pixel_size[1]).toLocaleString('pt-BR',{maximumFractionDigits:6})} ${data.unit || ''}`);
      if (data.range) infoRow('Tons da prévia', `${data.range[0].toFixed(2)} a ${data.range[1].toFixed(2)} · escala de cinza`);
    } else {
      if (!data.points.length) throw new Error('O arquivo não contém pontos para visualizar.');
      const min = [Infinity,Infinity,Infinity], max = [-Infinity,-Infinity,-Infinity];
      for (const point of data.points) point.forEach((value,i) => { min[i] = Math.min(min[i],value); max[i] = Math.max(max[i],value); });
      data.center = min.map((value,i) => (value + max[i]) / 2);
      data.span = Math.max(...max.map((value,i) => value - min[i]), 1);
      data.zRange = [min[2],max[2]];
      v('viewer-status').textContent = data.note || 'Amostra espacial da nuvem real. Arraste para girar; Shift + arraste para deslocar; use a roda para aproximar. Cores representam altura relativa.';
      infoRow('Pontos exibidos', `${data.points.length.toLocaleString('pt-BR')} de ${data.total.toLocaleString('pt-BR')}`);
      infoRow('Visualização', product === 'model' ? 'Vértices amostrados; sem faces ou texturas' : 'Amostra 3D · cores por altura');
    }
    infoRow('Arquivo', data.file); infoRow('Tamanho', `${(data.bytes / 1048576).toLocaleString('pt-BR',{maximumFractionDigits:1})} MB`);
    setCanvasControls(true); drawViewer();
  } catch (error) {
    if (serial !== viewSerial) return;
    viewerData = null; v('viewer-empty').hidden = false;
    v('viewer-status').textContent = error.message;
  }
}

function openViewer(product) {
  if (toolSources.results) toolSources.viewer = toolSources.results; else delete toolSources.viewer;
  updateToolSourceLabel('viewer'); updateViewerProjects();
  v('viewer-project').value = v('result-project').value;
  updateViewerProducts(); v('viewer-product').value = product;
  location.hash = 'visualizar'; showProduct();
}

function drawViewer() {
  if (framePending) return;
  framePending = true;
  requestAnimationFrame(() => {
    framePending = false;
    const stage = v('viewer-stage');
    const width = stage.clientWidth, height = stage.clientHeight;
    if (!width || !height) return;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    context.setTransform(ratio,0,0,ratio,0,0); context.clearRect(0,0,width,height);
    if (!viewerData) return;
    if (viewerData.kind === 'raster' && rasterImage?.complete) {
      const scale = Math.min(width/rasterImage.width, height/rasterImage.height) * zoom;
      const w = rasterImage.width * scale, h = rasterImage.height * scale;
      context.drawImage(rasterImage,(width-w)/2+panX,(height-h)/2+panY,w,h);
    } else if (viewerData.kind === 'points' && viewerData.center) {
      const data = viewerData, scale = Math.min(width,height) * .78 * zoom / data.span;
      const cos = Math.cos(angle), sin = Math.sin(angle), ct = Math.cos(tilt), st = Math.sin(tilt);
      for (const point of data.points) {
        const x=point[0]-data.center[0],y=point[1]-data.center[1],z=point[2]-data.center[2];
        const px=(x*cos+y*sin)*scale+width/2+panX, py=((y*cos-x*sin)*ct-z*st)*scale+height/2+panY;
        const hue=190-(point[2]-data.zRange[0])/Math.max(data.zRange[1]-data.zRange[0],.001)*130;
        context.fillStyle=`hsl(${hue} 68% 47%)`; context.fillRect(px,py,1.6,1.6);
      }
    }
  });
}

// Keep the point under the cursor fixed while changing magnification.
function zoomViewer(factor, x, y) {
  if (!viewerData) return;
  const rect = canvas.getBoundingClientRect();
  const next = Math.max(.05, Math.min(256, zoom * factor));
  const relative = next / zoom;
  x = (x ?? rect.width / 2) - rect.width / 2;
  y = (y ?? rect.height / 2) - rect.height / 2;
  panX = x - (x - panX) * relative;
  panY = y - (y - panY) * relative;
  zoom = next;
  drawViewer();
}

v('viewer-project').addEventListener('change', updateViewerProducts);
v('viewer-product').addEventListener('change', clearViewer);
v('viewer-load').addEventListener('click', showProduct);
v('viewer-in').addEventListener('click', () => { zoomViewer(1.5); });
v('viewer-out').addEventListener('click', () => { zoomViewer(1/1.5); });
v('viewer-fit').addEventListener('click', () => { zoom=1;panX=panY=0;angle=.3;tilt=.9;drawViewer(); });
canvas.addEventListener('pointerdown', event => { if (!viewerData) return; dragging=[event.clientX,event.clientY,event.shiftKey || event.button === 2];canvas.setPointerCapture(event.pointerId); });
canvas.addEventListener('pointermove', event => {
  if (!dragging) return;
  const dx=event.clientX-dragging[0],dy=event.clientY-dragging[1];dragging=[event.clientX,event.clientY,dragging[2]];
  if (viewerData?.kind === 'points' && !dragging[2]) { angle+=dx*.008;tilt=Math.max(.05,Math.min(1.55,tilt+dy*.008)); }
  else { panX+=dx;panY+=dy; }
  drawViewer();
});
canvas.addEventListener('pointerup', () => { dragging=null; });
canvas.addEventListener('pointercancel', () => { dragging=null; });
canvas.addEventListener('keydown', event => {
  if (!viewerData) return;
  if (event.key === '+' || event.key === '=') zoomViewer(1.5);
  else if (event.key === '-') zoomViewer(1/1.5);
  else if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) {
    if (viewerData.kind === 'points') {
      angle += event.key === 'ArrowLeft' ? -.1 : event.key === 'ArrowRight' ? .1 : 0;
      tilt=Math.max(.05,Math.min(1.55,tilt+(event.key==='ArrowUp'? .1:event.key==='ArrowDown'?-.1:0)));
    } else { panX+=event.key==='ArrowLeft'?-25:event.key==='ArrowRight'?25:0;panY+=event.key==='ArrowUp'?-25:event.key==='ArrowDown'?25:0; }
  } else return;
  event.preventDefault();drawViewer();
});
canvas.addEventListener('contextmenu', event => event.preventDefault());
canvas.addEventListener('wheel', event => {
  if (!viewerData) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
  zoomViewer(Math.exp(-Math.max(-500, Math.min(500, delta)) * .003),
             event.clientX - rect.left, event.clientY - rect.top);
}, {passive:false});
new ResizeObserver(drawViewer).observe(v('viewer-stage'));
