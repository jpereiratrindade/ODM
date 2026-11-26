'use strict';
const $ = id => document.getElementById(id);
let token;
let projects = [];
let cursor = 0;
let lastState;
const productInfo = [
  ['Ortofoto', 'Mapa aéreo georreferenciado', 'odm_orthophoto/odm_orthophoto.tif', '▧'],
  ['Nuvem de pontos', 'Estrutura espacial do levantamento', 'odm_georeferencing/odm_georeferenced_model.laz', '⠿'],
  ['Superfície', 'Relevo, vegetação e construções', 'odm_dem/dsm.tif', '≋'],
  ['Terreno', 'Estimativa da superfície do solo', 'odm_dem/dtm.tif', '⌁'],
  ['Modelo 3D', 'Reconstrução com textura', 'odm_texturing/odm_textured_model.obj', '◇'],
  ['Relatório', 'Qualidade e resumo do processamento', 'odm_report/report.pdf', '▤']
];
const productIds = ['ortho','cloud','dsm','dtm','model','report'];
const states = {idle:'Aguardando', running:'Processando', stopping:'Interrompendo', completed:'Concluído', failed:'Falhou', cancelled:'Interrompido'};
function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}
async function api(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json', 'X-ODM-Token': token}, body:JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Falha no servidor local.');
  return result;
}
function selection() {
  const project = projects.find(p => p.name === $('project').value);
  $('project-name').textContent = project ? project.name : '—';
  $('image-count').textContent = project ? project.images.toLocaleString('pt-BR') : '—';
  $('outputs').replaceChildren();
  for (const [title, description, path, icon] of productInfo) {
    const available = project?.outputs.includes(path);
    const li = document.createElement('li');
    li.className = 'result-card' + (available ? ' available' : '');
    const visual = document.createElement('div'); visual.className = 'result-visual'; visual.textContent = icon;
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
  $('dataset-title').textContent = project ? project.name : 'Onde está seu levantamento?';
  $('project-field').hidden = projects.length < 2;
  if (!lastState || lastState === 'idle') {
    $('progress-title').textContent = project ? 'Tudo pronto para criar' : 'Seu levantamento começa aqui';
    $('progress-note').textContent = project ? 'Escolha perfil e produtos acima' : 'Escolha uma pasta para continuar';
  }
  updateViewerProjects();
  loadHistory();
  $('command').hidden = true;
  $('run').disabled = !project || ['running','stopping'].includes(lastState);
}
function populateProjects(result) {
  const selected = result.selected || $('project').value;
  projects = result.projects;
  token = result.token;
  $('processing-engine').textContent = result.engine === 'podman' ? 'Fotogrametria · ODM em Podman' : 'Fotogrametria · ambiente nativo';
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
    dtm:$('dtm').checked, 'skip-3dmodel':!$('model3d').checked};
}
function render(state) {
  const active = ['running','stopping'].includes(state.state);
  $('state').textContent = states[state.state] || state.state;
  $('state-note').textContent = state.project ? `${state.project}${state.exit_code === null ? '' : ' · saída ' + state.exit_code}` : 'Pronto para configurar';
  $('settings').disabled = active;
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
$('preview').addEventListener('click', async () => {
  if (!$('run-form').reportValidity()) return;
  try {
    const result = await api('preview', configuration());
    $('command').textContent = result.command.map(part => JSON.stringify(part)).join(' ');
    $('command').hidden = false;
  } catch (error) { notice(error.message, true); }
});
$('run-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('run').disabled = true;
  try {
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
      $('gsd').textContent = data.value.toLocaleString('pt-BR', {minimumFractionDigits:2,maximumFractionDigits:2});
      $('gsd-method').textContent = data.engine + ' · Worker local';
    } else { $('gsd-method').textContent = data.error; }
  };
  worker.onerror = () => { $('engine').textContent = 'Worker indisponível'; $('gsd-method').textContent = 'Falha ao iniciar cálculo local.'; };
} catch { $('engine').textContent = 'Worker indisponível'; }
function calculateGsd() {
  if (!$('gsd-form').reportValidity()) return;
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
let folderPath, folderParent;
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
$('choose-folder').addEventListener('click', () => { $('folder-dialog').showModal(); browseFolder($('dataset-path').value || undefined); });
$('folder-close').addEventListener('click', () => $('folder-dialog').close());
$('folder-up').addEventListener('click', () => browseFolder(folderParent));
$('folder-use').addEventListener('click', async () => {
  $('folder-use').disabled = true;
  try {
    const result = await api('dataset', {path:folderPath}); populateProjects(result); $('folder-dialog').close();
    if (projects.length) notice('Levantamento aberto. Escolha como criar seus produtos.');
  } catch (error) { $('folder-error').textContent = error.message; }
  finally { $('folder-use').disabled = false; }
});
function navigate() {
  const view = ['projeto','planejamento','resultados','visualizar'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'projeto';
  for (const id of ['projeto','planejamento','resultados','visualizar']) $(id).hidden = id !== view;
  document.querySelector('.hero').hidden = view !== 'projeto';
  document.querySelector('.stats').hidden = view === 'visualizar';
  document.querySelectorAll('nav a').forEach(link => {
    if (link.hash === '#' + view) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  $('view-title').textContent = {projeto:'Meu levantamento',planejamento:'Planejar um voo',resultados:'Meus produtos',visualizar:'Visualizar resultados'}[view];
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
(async () => {
  try {
    await loadProjects();
    if (projects.length) notice('Projeto pronto. Revise parâmetros antes de iniciar.');
  } catch (error) { notice(error.message, true); }
  poll();
})();
