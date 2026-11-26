'use strict';
const p = id => document.getElementById(id);
let planningGlobal = null, planningLoadedProject = null, planningData = null, planningSerial = 0;
let planningHeightMode = 'manual';

function updatePlanningProjects() {
  const globalProject = p('project').value, current = p('planning-project').value;
  const selected = globalProject !== planningGlobal ? globalProject : current || globalProject;
  planningGlobal = globalProject;
  p('planning-project').replaceChildren();
  for (const project of projects) p('planning-project').add(new Option(project.name,project.name));
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
  const differentProject=project!==planningLoadedProject;
  if (differentProject || planningHeightMode==='relative') {
    p('altitude').value='';p('altitude-source').textContent='Informe altura acima do terreno · altitude GPS não é usada';planningHeightMode='manual';
  }
  clearPlanningFields();p('planning-metadata').replaceChildren();planningData=null;
  p('planning-status').textContent='Lendo EXIF/XMP da foto original…';
  try {
    const data=await api('planning',{project,...(typeof file==='string'&&file?{file}:{})});
    if (serial!==planningSerial) return;
    planningData=data;planningLoadedProject=project;
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
