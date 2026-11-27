'use strict';
const $ = id => document.getElementById(id);
let token;
let projects = [];
let cursor = 0;
let lastState;
const productInfo = [
  ['Ortofoto', 'Mapa aéreo georreferenciado', 'odm_orthophoto/odm_orthophoto.tif', 'orthophoto'],
  ['Nuvem de pontos', 'Estrutura espacial do levantamento', 'odm_georeferencing/odm_georeferenced_model.laz', 'cloud'],
  ['Superfície', 'Relevo, vegetação e construções', 'odm_dem/dsm.tif', 'surface'],
  ['Terreno', 'Estimativa da superfície do solo', 'odm_dem/dtm.tif', 'terrain'],
  ['Modelo 3D', 'Reconstrução com textura', 'odm_texturing/odm_textured_model.obj', 'model'],
  ['Relatório', 'Qualidade e resumo do processamento', 'odm_report/report.pdf', 'report']
];
const productIds = ['ortho','cloud','dsm','dtm','model','report'];
const states = {idle:'Aguardando', running:'Processando', stopping:'Interrompendo', completed:'Concluído', failed:'Falhou', cancelled:'Interrompido'};
function uiIcon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'ui-icon'); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '/assets/icons.svg#' + name); svg.append(use); return svg;
}
function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}
async function api(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json', 'X-ODM-Token': token}, body:JSON.stringify(data)
  });
  if (response.status === 401) throw new Error('Autenticação necessária. Entre com o usuário paralaxe e a senha configurada; depois atualize a página.');
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) throw new Error('O servidor retornou uma resposta inesperada. Atualize a página para reconectar.');
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Falha no servidor local.');
  return result;
}
function selection() {
  const project = projects.find(p => p.name === $('project').value);
  const analysisSelected = $('analysis-project').value || $('project').value;
  $('analysis-project').replaceChildren();
  for (const entry of toolProjects('analysis')) $('analysis-project').add(new Option(entry.name, entry.name));
  if ([...$('analysis-project').options].some(o => o.value === analysisSelected)) $('analysis-project').value = analysisSelected;
  if (checkedConfiguration && checkedConfiguration !== JSON.stringify(analysisConfiguration())) {
    $('preflight-report').hidden = true; $('analysis-status').textContent = 'Verifique as imagens deste levantamento.';
  }
  $('project-name').textContent = project ? project.name : '—';
  $('image-count').textContent = project ? project.images.toLocaleString('pt-BR') : '—';
  updateResultProjects();
  $('dataset-title').textContent = project ? project.name : 'Onde está seu levantamento?';
  $('project-field').hidden = projects.length < 2;
  if (!lastState || lastState === 'idle') {
    $('progress-title').textContent = project ? 'Tudo pronto para criar' : 'Seu levantamento começa aqui';
    $('progress-note').textContent = project ? 'Escolha perfil e produtos acima' : 'Escolha uma pasta para continuar';
  }
  updateViewerProjects();
  updateMediaProjects();
  updatePlanningProjects();
  loadHistory();
  $('command').hidden = true;
  $('run').disabled = !project || ['running','stopping'].includes(lastState);
  syncCleanupControls(['running','stopping'].includes(lastState));
}
function updateResultProjects() {
  const current = $('result-project').value;
  $('result-project').replaceChildren();
  for (const entry of toolProjects('results')) $('result-project').add(new Option(entry.name, entry.name));
  const selected = toolSources.results ? toolSources.results.projects[0].name : current || $('project').value;
  if ([...$('result-project').options].some(o => o.value === selected)) $('result-project').value = selected;
  const project = toolProjects('results').find(p => p.name === $('result-project').value);
  $('outputs').replaceChildren();
  for (const [title, description, path, icon] of productInfo) {
    const available = project?.outputs.includes(path);
    const li = document.createElement('li');
    li.className = 'result-card' + (available ? ' available' : '');
    const visual = document.createElement('div'); visual.className = 'result-visual'; visual.append(uiIcon(icon));
    const heading = document.createElement('strong'); heading.textContent = title;
    const note = document.createElement('p'); note.textContent = description;
    const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = available ? '✓ Arquivo disponível' : 'Ainda não gerado';
    li.append(visual, heading, note, badge);
    if (available) {
      const file = document.createElement('small'); file.textContent = path; li.append(file);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'outline result-open'; button.textContent = 'Visualizar →';
      button.addEventListener('click', () => openViewer(productIds[productInfo.findIndex(p => p[2] === path)])); li.append(button);
    }
    $('outputs').append(li);
  }
  loadHistory();
}
$('result-project').addEventListener('change', updateResultProjects);
function populateProjects(result) {
  const selected = result.selected || $('project').value;
  projects = result.projects;
  token = result.token;
  $('processing-engine').textContent = result.engine === 'podman' ? 'Motor: OpenDroneMap · Podman' : 'Motor: OpenDroneMap · nativo';
  $('dataset-path').value = result.datasets || '';
  $('dataset-note').textContent = result.datasets ? 'Pasta de projetos: ' + result.datasets : 'Escolha a pasta do projeto. Seus arquivos ficam neste computador.';
  $('project').replaceChildren();
  for (const project of projects) {
    $('project').add(new Option(project.name, project.name));
  }
  if (projects.some(p => p.name === selected)) $('project').value = selected;
  if (!projects.length) {
    $('project').add(new Option('Nenhum projeto com images/', ''));
    notice(result.datasets ? 'Nenhum projeto com images/ encontrado nesta pasta.' : 'Escolha uma pasta para começar seu levantamento.');
  }
  selection();
}
async function loadProjects() {
  populateProjects(await api('projects'));
}
$('dataset-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('open-dataset').disabled = true;
  try {
    const result = await api('dataset', {path:$('dataset-path').value.trim()});
    populateProjects(result);
    if (projects.length) notice('Dataset aberto. Revise parâmetros antes de iniciar.');
  } catch (error) { notice(error.message, true); }
  finally { $('open-dataset').disabled = ['running','stopping'].includes(lastState); }
});
function configuration() {
  return {project:$('project').value, quality:$('quality').value,
    resolution:Number($('resolution').value), dsm:$('dsm').checked,
    dtm:$('dtm').checked, 'skip-3dmodel':!$('model3d').checked, 'video-limit':Number($('video-limit').value),
    'video-resolution':Number($('video-resolution').value), 'max-concurrency':Number($('max-concurrency').value), extraction: {
      start:Number($('extract-start').value), end:$('extract-end').value === '' ? null : Number($('extract-end').value),
      interval:Number($('extract-interval').value), blur:Number($('extract-blur').value),
      min_motion:Number($('extract-motion').value), max_gap:Number($('extract-gap').value)}};
}
function render(state) {
  const active = ['running','stopping'].includes(state.state);
  $('state').textContent = states[state.state] || state.state;
  $('state-note').textContent = state.project ? `${state.project}${state.external ? ' · execução externa' : ''}${state.exit_code === null ? '' : ' · saída ' + state.exit_code}` : 'Pronto para configurar';
  $('settings').disabled = active;
  $('extract-video-run').disabled = active;
  syncCleanupControls(active);
  $('dataset-settings').disabled = active;
  $('run').disabled = active || !$('project').value;
  $('stop').disabled = state.state !== 'running';
  $('stop').hidden = !active;
  $('run-error').hidden = state.state !== 'failed';
  $('run-error-message').textContent = state.error || 'Confira o diagnóstico técnico para identificar a causa.';
  const stages = {dataset:0, split:0, merge:0, opensfm:1, openmvs:1, odm_filterpoints:2, odm_meshing:2, mvs_texturing:2, mvstex:2, odm_georeferencing:3, odm_dem:3, odm_orthophoto:3, odm_report:4, odm_postprocess:4};
  const index = stages[state.stage] ?? -1;
  document.querySelectorAll('#pipeline li').forEach((item, position) => {
    item.classList.toggle('done', state.state === 'completed' || (active && position < index));
    item.classList.toggle('current', active && position === index);
    if (active && position === index) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
  });
  if (active || ['completed','failed','cancelled'].includes(state.state)) {
    $('progress-title').textContent = {running:'Seu território está tomando forma',stopping:'Encerrando processamento…',completed:'Seus produtos estão prontos',failed:'Não foi possível concluir',cancelled:'Processamento interrompido'}[state.state];
    $('progress-note').textContent = state.state === 'running' ? (index >= 0 ? document.querySelectorAll('#pipeline li')[index].textContent : 'Iniciando processamento…') : state.state === 'failed' ? 'Confira diagnóstico em Detalhes técnicos abaixo' : state.state === 'completed' ? 'Veja os arquivos em Meus produtos' : 'Você pode iniciar novamente quando quiser';
  }
  const fresh = state.logs.filter(line => line.id > cursor);
  if (fresh.length) {
    if (!cursor) $('logs').textContent = '';
    $('logs').textContent += fresh.map(line => line.text).join('\n') + '\n';
    cursor = fresh.at(-1).id;
    const lines = $('logs').textContent.split('\n');
    if (lines.length > 2000) $('logs').textContent = lines.slice(-2000).join('\n');
    $('logs').scrollTop = $('logs').scrollHeight;
  }
  const changed = state.state !== lastState;
  lastState = state.state;
  return changed;
}
$('project').addEventListener('change', async () => {
  try { render(await api('project', {project:$('project').value}));selection(); }
  catch (error) { notice(error.message, true); }
});
$('analysis-project').addEventListener('change', () => {
  checkedConfiguration = null; $('preflight-report').hidden = true; $('extract-video').replaceChildren();
  $('extract-destination').value = ''; $('analysis-status').textContent = 'Verifique as imagens deste levantamento.';
});
$('preview').addEventListener('click', async () => {
  if (!$('run-form').reportValidity()) return;
  try {
    const result = await api('preview', configuration());
    $('command').textContent = result.command.map(part => JSON.stringify(part)).join(' ');
    $('command').hidden = false;
  } catch (error) { notice(error.message, true); }
});
let checkedConfiguration = null;
function analysisConfiguration() { return toolArguments('analysis', {...configuration(), project:$('analysis-project').value}); }
async function checkInputs(forProcessing = false) {
  localMode(false);
  location.hash = 'analise';
  for (const input of $('server-analysis').querySelectorAll('input')) {
    if (!input.reportValidity()) throw new Error('Revise os parâmetros de análise e extração.');
  }
  checkedConfiguration = null;
  const config = forProcessing ? configuration() : analysisConfiguration(), fingerprint = JSON.stringify(config);
  $('analysis-status').textContent = 'Analisando amostras. Isso pode levar alguns instantes…';
  const box = $('preflight-report'); box.hidden = false; box.textContent = 'Verificando amostras das fotos e vídeos…';
  const report = await api('preflight', config);
  if (JSON.stringify(forProcessing ? configuration() : analysisConfiguration()) !== fingerprint) throw new Error('Configuração alterada durante a verificação. Verifique novamente.');
  box.replaceChildren();
  $('extract-video').replaceChildren();
  for (const video of report.videos) $('extract-video').add(new Option(video.file, video.file));
  if (!$('extract-destination').value) $('extract-destination').value = config.project + '-quadros';
  const title = document.createElement('h3'); title.textContent = 'Verificação de imagens · ' + config.project; box.append(title);
  const summary = document.createElement('p'); summary.textContent = `${report.photos} fotos · ${report.videos.length} vídeos · ${report.sample_count} amostras verificadas. Avisos indicam riscos, sem prever com certeza o resultado.`; box.append(summary);
  const list = document.createElement('ul'); list.className = 'analysis-findings';
  for (const [severity, entries] of [['error', report.errors], ['warning', report.warnings]]) {
    for (const text of entries) {const item = document.createElement('li'); item.className = severity; const label = document.createElement('strong'); label.textContent = severity === 'error' ? 'Corrigir antes de iniciar: ' : 'Revisar: '; item.append(label, document.createTextNode(text)); list.append(item);}
  }
  box.dataset.severity = report.errors.length ? 'error' : report.warnings.length ? 'warning' : 'success';
  $('analysis-status').textContent = `${report.sample_count} amostras analisadas · ${report.errors.length} impedimentos · ${report.warnings.length} avisos para revisar.`;
  box.append(list);
  for (const video of report.videos) {
    const text = document.createElement('p'); text.textContent = `${video.file}: ${video.seconds?.toFixed(1) ?? '?'} s. Pares testados a aproximadamente ${video.probe_interval_seconds?.toFixed(2) ?? '?'} s. Intervalo nominal para o limite escolhido: ${video.nominal_interval_seconds?.toFixed(2) ?? '?'} s. Não é garantia de sobreposição ou intervalo real de extração.`; box.append(text);
  }
  if (report.geometry?.components) {
    for (const component of report.geometry.components) {
      const text = document.createElement('p');
      const ratio = component.radius_p99 > 0 ? (component.max_radius / component.radius_p99).toFixed(1) : 'indefinida';
      text.textContent = `Geometria existente: ${component.shots} câmeras, ${component.points} pontos, ${component.extreme_points} pontos extremos, ${component.nonfinite} não finitos. Razão entre raio máximo e raio de 99% dos pontos: ${ratio}. Distâncias relativas ao centro da nuvem, sem assumir escala em metros.`;
      box.append(text);
    }
    const source = document.createElement('p'); source.textContent = report.geometry.source; box.append(source);
  }
  if (report.extraction_report) {
    const text = document.createElement('p');
    const diagnostics = report.extraction_report;
    text.textContent = `Extração anterior: ${diagnostics.accepted ?? '?'} fotos; ${diagnostics.complete ? 'concluída' : 'incompleta'}. Motivos registrados: ${JSON.stringify(diagnostics.reasons)}. ${diagnostics.notice || ''}`;
    box.append(text);
  }
  if (report.motion_pairs?.length) {
    const details = document.createElement('details'), heading = document.createElement('summary');
    heading.textContent = `Movimento e correspondências · ${report.motion_pairs.length} pares`; details.append(heading);
    const table = document.createElement('table'); table.className = 'motion-table';
    const caption = document.createElement('caption'); caption.textContent = 'Pares amostrados, interpretação, correspondências e movimento visual'; table.append(caption);
    const labels = {stationary:'Pouco movimento', homography_dominant:'Giro ou cena plana: ambíguo', discontinuity:'Baixa consistência', insufficient_features:'Poucas correspondências', different_dimensions:'Dimensões diferentes', tracked:'Correspondências disponíveis'};
    for (const pair of report.motion_pairs) {
      const row = document.createElement('tr');
      for (const value of [`${pair.first} → ${pair.second}`, labels[pair.status] || pair.status, `${pair.matches} pares; ${pair.inliers} consistentes`, pair.motion_percent === null ? 'Movimento indeterminado' : `${pair.motion_percent}% da diagonal`]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      table.append(row);
    }
    details.append(table); box.append(details);
  }
  const note = document.createElement('p'); note.className = 'muted'; note.textContent = report.limitations; box.append(note);
  box.scrollIntoView({behavior:'smooth', block:'start'});
  if (!report.errors.length) {
    const independent = !!config.source || config.project !== $('project').value;
    if (independent) {const hint = document.createElement('p'); hint.textContent = 'Esta análise é independente do processamento. Para processar este projeto depois, selecione sua pasta em Meu levantamento.'; box.append(hint);}
    const next = document.createElement('a'); next.href = '#projeto'; next.className = 'analysis-continue'; next.textContent = independent ? 'Voltar ao processamento atual →' : 'Ir para configuração do processamento →'; box.append(next);
  }
  checkedConfiguration = report.errors.length ? null : fingerprint;
  return report;
}
$('check-inputs').addEventListener('click', async () => {
  $('check-inputs').disabled = true;
  try {await checkInputs();} catch (error) {$('preflight-report').textContent = error.message; $('analysis-status').textContent = 'Não foi possível concluir a análise.'; checkedConfiguration = null; notice(error.message, true);}
  finally {$('check-inputs').disabled = false;}
});
$('extract-video-run').addEventListener('click', async () => {
  const config = analysisConfiguration();
  if (checkedConfiguration !== JSON.stringify(config)) {
    try {await checkInputs(); $('extract-status').textContent = 'Revise a verificação e selecione o vídeo antes de extrair.';} catch (error) {notice(error.message, true);}
    return;
  }
  try {
    const result = await api('extract-video', {...config, file:$('extract-video').value, destination:$('extract-destination').value});
    cursor = 0; $('logs').textContent = ''; render(result);
    await loadProjects();
    location.hash = 'projeto';
    notice('Extração iniciada em um novo projeto. Acompanhe os registros abaixo.');
  } catch (error) {notice(error.message, true);}
});
$('run-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('run').disabled = true;
  try {
    if (checkedConfiguration !== JSON.stringify(configuration())) {
      const report = await checkInputs(true);
      notice(report.errors.length ? 'Corrija os arquivos indicados antes de iniciar.' : 'Verificação concluída. Revise os avisos acima e clique novamente para iniciar.');
      selection(); return;
    }
    const result = await api('run', configuration());
    cursor = 0;
    $('logs').textContent = '';
    render(result);
    notice('Processamento iniciado. Você pode acompanhar as etapas abaixo.');
  } catch (error) { notice(error.message, true); selection(); }
});
$('stop').addEventListener('click', async () => {
  try { render(await api('stop', {})); notice('Interrupção solicitada. Aguarde encerramento.'); }
  catch (error) { notice(error.message, true); }
});
$('refresh').addEventListener('click', () => loadProjects().catch(error => notice(error.message, true)));
let worker;
try {
  worker = new Worker('/worker.js');
  worker.onmessage = ({data}) => {
    if (data.type === 'ready') {
      $('engine').textContent = data.engine === 'WebAssembly' ? 'WASM ativo · GSD' : 'GSD · JavaScript';
      calculateGsd();
    } else if (data.type === 'result') {
      if (!$('gsd-form').checkValidity()) return;
      $('gsd').textContent = data.value.toLocaleString('pt-BR', {minimumFractionDigits:2,maximumFractionDigits:2});
      $('gsd-method').textContent = data.engine + ' · Worker local';
    } else { $('gsd-method').textContent = data.error; }
  };
  worker.onerror = () => { $('engine').textContent = 'Worker indisponível'; $('gsd-method').textContent = 'Falha ao iniciar cálculo local.'; };
} catch { $('engine').textContent = 'Worker indisponível'; }
function calculateGsd() {
  if (!$('gsd-form').checkValidity()) { $('gsd').textContent='—'; $('gsd-method').textContent='Complete os parâmetros para calcular'; return; }
  worker?.postMessage({values:['altitude','sensor','focal','pixels'].map(id => Number($(id).value))});
}
$('gsd-form').addEventListener('submit', event => { event.preventDefault(); calculateGsd(); });
document.querySelectorAll('[data-quality]').forEach(button => button.addEventListener('click', () => {
  $('quality').value = button.dataset.quality;
  document.querySelectorAll('[data-quality]').forEach(item => {
    const selected = item === button;
    item.classList.toggle('selected', selected); item.setAttribute('aria-pressed', String(selected));
  });
}));
let folderPath, folderParent, folderPurpose = null;
async function browseFolder(path) {
  $('folder-error').textContent = '';
  $('folder-use').disabled = true;
  try {
    const result = await api('browse', path ? {path} : {});
    folderPath = result.path; folderParent = result.parent;
    $('folder-location').textContent = result.path;
    $('folder-up').disabled = folderPath === folderParent;
    $('folder-hint').textContent = result.dataset ? '✓ Projeto com pasta de imagens encontrado' : 'Abra seu projeto ou selecione uma pasta com vários projetos';
    $('folder-shortcuts').replaceChildren();
    for (const shortcut of result.shortcuts) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'outline'; button.textContent = shortcut.name;
      button.addEventListener('click', () => browseFolder(shortcut.path)); $('folder-shortcuts').append(button);
    }
    $('folder-list').replaceChildren();
    for (const folder of result.folders) {
      const li = document.createElement('li');
      const button = document.createElement('button'); button.type = 'button'; button.className = 'folder-entry';
      const icon = document.createElement('span'); icon.className = 'folder-icon'; icon.textContent = folder.dataset ? '▧' : '▱';
      const name = document.createElement('span'); name.textContent = folder.name;
      const hint = document.createElement('small'); hint.textContent = folder.dataset ? 'Levantamento  →' : 'Abrir  →';
      button.append(icon, name, hint); button.addEventListener('click', () => browseFolder(folder.path));
      li.append(button); $('folder-list').append(li);
    }
    if (!result.folders.length) { const empty = document.createElement('li'); empty.textContent = 'Nenhuma subpasta aqui.'; $('folder-list').append(empty); }
    $('folder-use').disabled = false;
  } catch (error) { $('folder-error').textContent = error.message; }
}
$('choose-folder').addEventListener('click', () => { folderPurpose = null; $('folder-dialog').querySelector('h2').textContent = 'Escolha seu levantamento'; $('folder-dialog').showModal(); browseFolder($('dataset-path').value || undefined); });
$('folder-close').addEventListener('click', () => $('folder-dialog').close());
$('folder-up').addEventListener('click', () => browseFolder(folderParent));
$('folder-use').addEventListener('click', async () => {
  $('folder-use').disabled = true;
  try {
    if (folderPurpose) {
      await chooseToolSource(folderPurpose, folderPath); $('folder-dialog').close(); return;
    }
    const result = await api('dataset', {path:folderPath}); populateProjects(result); $('folder-dialog').close();
    if (projects.length) notice('Levantamento aberto. Escolha como criar seus produtos.');
  } catch (error) { $('folder-error').textContent = error.message; }
  finally { $('folder-use').disabled = false; }
});
function navigate() {
  const view = ['projeto','analise','planejamento','resultados','visualizar','midias'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'projeto';
  for (const id of ['projeto','analise','planejamento','resultados','visualizar','midias']) $(id).hidden = id !== view;
  document.querySelector('.hero').hidden = view !== 'projeto';
  document.querySelector('.stats').hidden = ['visualizar','midias'].includes(view);
  document.querySelectorAll('nav a').forEach(link => {
    if (link.hash === '#' + view) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  $('view-title').textContent = {projeto:'Meu levantamento',analise:'Analisar imagens',planejamento:'Planejar um voo',resultados:'Meus produtos',visualizar:'Visualizar resultados',midias:'Fotos e vídeos'}[view];
  window.scrollTo(0,0);
}
window.addEventListener('hashchange', navigate);
navigate();
async function poll() {
  try {
    const result = await api('status?since=' + cursor);
    if (render(result) && ['completed','failed','cancelled'].includes(result.state)) {
      notice(result.error || 'Execução: ' + states[result.state] + '.', result.state === 'failed');
      await loadProjects();
    }
  } catch (error) { notice('Servidor indisponível: ' + error.message, true); $('run').disabled = true; }
  setTimeout(poll, 1500);
}
initializeToolSources();
(async () => {
  try {
    await loadProjects();
    if (location.hash === '#analise') navigate();
    if (projects.length) notice('Projeto pronto. Revise parâmetros antes de iniciar.');
  } catch (error) { notice(error.message, true); }
  poll();
})();
