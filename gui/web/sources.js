'use strict';
const toolSources = {};
const toolSections = {analysis:'analise', planning:'planejamento', media:'midias', viewer:'visualizar', results:'resultados'};
function toolArguments(tool, data = {}) {
  return {...data, ...(toolSources[tool] ? {source:toolSources[tool].source} : {})};
}
function toolProjects(tool) { return toolSources[tool]?.projects || projects; }
function toolIdentity(tool, project) { return `${toolSources[tool]?.source || ''}:${project}`; }
function refreshTool(tool) {
  if (tool === 'analysis') {
    const select = $('analysis-project'); select.replaceChildren();
    for (const entry of toolProjects(tool)) select.add(new Option(entry.name, entry.name));
    if (!toolSources[tool] && [...select.options].some(o => o.value === $('project').value)) select.value = $('project').value;
    checkedConfiguration = null; $('preflight-report').hidden = true; $('extract-video').replaceChildren();
    $('extract-destination').value = ''; $('analysis-status').textContent = 'Verifique as imagens deste levantamento.';
  }
  if (tool === 'planning') { planningLoadedProject = null; updatePlanningProjects(); loadPlanning(); }
  if (tool === 'media') { clearMediaInspection(); if ($('media-dialog').open) $('media-dialog').close(); updateMediaProjects(); loadMedia(); }
  if (tool === 'results') updateResultProjects();
  if (tool === 'viewer') { updateViewerProjects(); updateViewerProducts(); }
}
function updateToolSourceLabel(tool) {
  $(tool + '-source-path').textContent = toolSources[tool]?.path || 'Usando a pasta de projetos do processamento.';
  $(tool + '-source-reset').hidden = !toolSources[tool];
}
function initializeToolSources() {
  for (const [tool, section] of Object.entries(toolSections)) {
    const box = document.createElement('div'); box.className = 'tool-source-picker';
    const title = document.createElement('strong'); title.textContent = 'Pasta de projeto desta ferramenta';
    const path = document.createElement('p'); path.id = tool + '-source-path'; path.textContent = 'Usando a pasta de projetos do processamento. Você pode escolher outra.';
    const choose = document.createElement('button'); choose.type = 'button'; choose.className = 'outline'; choose.textContent = 'Escolher outra pasta…';
    choose.addEventListener('click', () => {
      folderPurpose = tool; $('folder-dialog').showModal();
      $('folder-dialog').querySelector('h2').textContent = 'Escolha o projeto para esta ferramenta';
      browseFolder(toolSources[tool]?.path || $('dataset-path').value || undefined);
    });
    const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'outline'; reset.textContent = 'Usar pasta do processamento'; reset.hidden = true; reset.id = tool + '-source-reset';
    reset.addEventListener('click', () => {delete toolSources[tool]; reset.hidden = true; path.textContent = 'Usando a pasta de projetos do processamento.'; refreshTool(tool);});
    const note = document.createElement('small'); note.textContent = 'Esta escolha não troca nem interrompe o processamento em andamento.' + (tool === 'analysis' ? ' A extração aguarda a execução ativa terminar e salva o novo projeto na pasta de projetos do processamento.' : '');
    box.append(title,path,choose,reset,note); (tool === 'analysis' ? $('server-analysis') : $(section)).prepend(box);
  }
}
async function chooseToolSource(tool, path) {
  const result = await api('tool-source', {path});
  toolSources[tool] = result;
  $(tool + '-source-path').textContent = result.path;
  $(tool + '-source-reset').hidden = false;
  refreshTool(tool);
}
