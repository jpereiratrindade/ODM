'use strict';
const m = id => document.getElementById(id);
let mediaFiles = [], mediaFiltered = [], mediaPage = 0, mediaIndex = 0;
let mediaListSerial = 0, mediaOpenSerial = 0, mediaBlob = null;
let mediaLastGlobal = null;
const mediaPageSize = 24;

function mediaUrl(name, mode) {
  return '/api/media?' + new URLSearchParams(toolArguments('media',{project:m('media-project').value, file:name, mode}));
}
function mediaSize(bytes) { return (bytes / 1048576).toLocaleString('pt-BR',{maximumFractionDigits:2}) + ' MB'; }

function updateMediaProjects() {
  const globalProject = toolSources.media ? toolSources.media.projects[0].name : m('project').value;
  const current = m('media-project').value;
  const selected = globalProject !== mediaLastGlobal ? globalProject : current || globalProject;
  mediaLastGlobal = globalProject;
  m('media-project').replaceChildren();
  for (const project of toolProjects('media')) m('media-project').add(new Option(project.name, project.name));
  if (selected) m('media-project').value = selected;
  if (current !== m('media-project').value || !mediaFiles.length) loadMedia();
}

async function loadMedia() {
  const serial = ++mediaListSerial;
  mediaFiles = []; renderMedia();
  const project = m('media-project').value;
  if (!project) { m('media-status').textContent = 'Escolha a pasta de um projeto para começar.'; return; }
  m('media-status').textContent = 'Lendo arquivos do projeto…';
  try {
    const result = await api('media-list', toolArguments('media', {project}));
    if (serial !== mediaListSerial) return;
    mediaFiles = result.files; mediaPage = 0; renderMedia();
  } catch (error) { if (serial === mediaListSerial) m('media-status').textContent = error.message; }
}

function renderMedia() {
  const query = m('media-search').value.trim().toLocaleLowerCase('pt-BR');
  const kind = m('media-kind').value;
  mediaFiltered = mediaFiles.filter(file => (kind === 'all' || file.kind === kind) && file.name.toLocaleLowerCase('pt-BR').includes(query));
  const pages = Math.max(1,Math.ceil(mediaFiltered.length / mediaPageSize));
  mediaPage = Math.max(0,Math.min(mediaPage,pages-1));
  m('media-grid').replaceChildren();
  mediaFiltered.slice(mediaPage*mediaPageSize,(mediaPage+1)*mediaPageSize).forEach((file,index) => {
    const button = document.createElement('button');button.type='button';button.className='media-card';button.setAttribute('aria-label',`Inspecionar ${file.name}`);
    const imageBox = document.createElement('div');imageBox.className='media-thumb';
    const placeholder = document.createElement('span');placeholder.append(uiIcon(file.kind==='video'?'video':'photo'));placeholder.setAttribute('aria-hidden','true');imageBox.append(placeholder);
    if (file.kind==='photo') {
      const image=document.createElement('img');image.alt='';image.loading='lazy';image.decoding='async';image.src=mediaUrl(file.name,'thumb');
      image.addEventListener('error',()=>{image.remove();placeholder.textContent='Prévia indisponível';placeholder.className='media-no-preview';});imageBox.append(image);
    }
    const title=document.createElement('strong');title.textContent=file.name;
    const note=document.createElement('small');note.textContent=`${file.kind==='video'?'Vídeo':'Foto'} · ${file.format} · ${mediaSize(file.bytes)}`;
    button.append(imageBox,title,note);button.addEventListener('click',()=>openMedia(mediaPage*mediaPageSize+index));m('media-grid').append(button);
  });
  const photos=mediaFiles.filter(file=>file.kind==='photo').length,videos=mediaFiles.length-photos;
  m('media-status').textContent=mediaFiles.length?`${photos.toLocaleString('pt-BR')} ${photos===1?'foto':'fotos'} · ${videos.toLocaleString('pt-BR')} ${videos===1?'vídeo':'vídeos'} · ${mediaFiltered.length.toLocaleString('pt-BR')} arquivos no filtro`:'Nenhuma foto ou vídeo reconhecido nesta pasta de imagens.';
  if (!mediaFiltered.length) {const empty=document.createElement('p');empty.className='media-empty';empty.textContent=mediaFiles.length?'Nenhum arquivo corresponde à busca.':'As fotos e vídeos aparecerão aqui.';m('media-grid').append(empty);}
  m('media-page').textContent=`Página ${mediaPage+1} de ${pages}`;
  m('media-previous-page').disabled=mediaPage===0;m('media-next-page').disabled=mediaPage>=pages-1;
}

function mediaField(name,value) {
  const group=document.createElement('div'),term=document.createElement('dt'),detail=document.createElement('dd');term.textContent=name;detail.textContent=value;group.append(term,detail);m('media-metadata').append(group);
}
function clearMediaInspection() {
  mediaOpenSerial++;
  const video=m('media-video');video.pause();video.removeAttribute('src');video.load();video.hidden=true;
  m('media-photo').hidden=true;m('media-photo').removeAttribute('src');
  m('media-screen').classList.remove('zoomed');m('media-zoom').textContent='Ampliar foto';
  if (mediaBlob) URL.revokeObjectURL(mediaBlob);
  mediaBlob=null;
}
async function openMedia(index) {
  if (index<0 || index>=mediaFiltered.length) return;
  clearMediaInspection();const serial=mediaOpenSerial;mediaIndex=index;
  const file=mediaFiltered[index];
  m('media-title').textContent=file.name;m('media-metadata').replaceChildren();m('media-detail-status').textContent='';m('media-loading').hidden=false;
  m('media-download').href=mediaUrl(file.name,'download');
  mediaField('Arquivo',file.name);mediaField('Formato',file.format);mediaField('Tamanho',mediaSize(file.bytes));
  m('media-back').disabled=index===0;m('media-forward').disabled=index>=mediaFiltered.length-1;m('media-zoom').hidden=file.kind==='video';
  if (!m('media-dialog').open) m('media-dialog').showModal();
  if (file.kind==='video') {
    const video=m('media-video');video.hidden=false;
    video.onloadedmetadata=()=>{if(serial!==mediaOpenSerial)return;m('media-loading').hidden=true;mediaField('Dimensões',`${video.videoWidth} × ${video.videoHeight} pixels`);if(Number.isFinite(video.duration))mediaField('Duração',video.duration.toLocaleString('pt-BR',{maximumFractionDigits:1})+' segundos');};
    video.onerror=()=>{if(serial!==mediaOpenSerial)return;m('media-loading').hidden=true;m('media-detail-status').textContent='Navegador não conseguiu reproduzir este formato/codec. Baixe o original para abrir em outro reprodutor.';};
    video.src=mediaUrl(file.name,'original');return;
  }
  try {
    const data=await api('photo',toolArguments('media',{project:m('media-project').value,file:file.name}));
    if(serial!==mediaOpenSerial)return;
    mediaBlob=URL.createObjectURL(new Blob([Uint8Array.from(atob(data.image),c=>c.charCodeAt(0))],{type:'image/jpeg'}));
    const image=m('media-photo');image.alt=file.name;image.src=mediaBlob;await image.decode();
    if(serial!==mediaOpenSerial)return;
    image.hidden=false;m('media-loading').hidden=true;
    mediaField('Dimensões originais',`${data.width.toLocaleString('pt-BR')} × ${data.height.toLocaleString('pt-BR')} pixels`);
    for(const [name,value] of Object.entries(data.metadata))mediaField(name,value);
    if(!Object.keys(data.metadata).length)m('media-detail-status').textContent='Sem metadados EXIF de captura disponíveis.';
  } catch(error) {if(serial!==mediaOpenSerial)return;m('media-loading').hidden=true;m('media-detail-status').textContent=error.message+' Use Baixar original se a prévia não estiver disponível.';}
}

m('media-project').addEventListener('change',loadMedia);
m('media-refresh').addEventListener('click',loadMedia);
for(const id of ['media-search','media-kind'])m(id).addEventListener('input',()=>{mediaPage=0;renderMedia();});
m('media-previous-page').addEventListener('click',()=>{mediaPage--;renderMedia();});
m('media-next-page').addEventListener('click',()=>{mediaPage++;renderMedia();});
m('media-close').addEventListener('click',()=>{clearMediaInspection();m('media-dialog').close();});
m('media-dialog').addEventListener('close',clearMediaInspection);
m('media-back').addEventListener('click',()=>openMedia(mediaIndex-1));
m('media-forward').addEventListener('click',()=>openMedia(mediaIndex+1));
m('media-zoom').addEventListener('click',()=>{const zoom=m('media-screen').classList.toggle('zoomed');m('media-zoom').textContent=zoom?'Ajustar foto':'Ampliar foto';});
m('media-dialog').addEventListener('keydown',event=>{if(event.target.tagName==='VIDEO')return;if(event.key==='ArrowLeft'){event.preventDefault();openMedia(mediaIndex-1);}if(event.key==='ArrowRight'){event.preventDefault();openMedia(mediaIndex+1);}});
