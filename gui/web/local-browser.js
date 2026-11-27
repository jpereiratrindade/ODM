'use strict';
const local = id => document.getElementById(id);
const localState = {files:[], outputs:[], report:null, worker:null, sequence:0, pending:new Map(), busy:false, cancelled:false, previewUrl:null, shown:50};
const localLabels = {stationary:'Pouco movimento',homography_dominant:'Giro ou cena plana: ambíguo',tracked:'Correspondências disponíveis',insufficient_features:'Poucas correspondências',different_dimensions:'Dimensões diferentes',low_planar_consistency:'Baixa consistência do modelo planar'};
function localKind(file) {
  if(file.type.startsWith('video/')||/\.(mp4|mov|webm|mkv|avi|m4v)$/i.test(file.name))return 'video';
  if(file.type.startsWith('image/')||/\.(jpg|jpeg|png|webp|tif|tiff|bmp)$/i.test(file.name))return 'photo';
  return null;
}
function localMode(client) {
  local('local-browser').hidden=!client;local('server-analysis').hidden=client;
  for(const [id,selected] of [['analysis-local-mode',client],['analysis-server-mode',!client]]) {
    local(id).setAttribute('aria-pressed',String(selected));local(id).classList.toggle('selected',selected);local(id).classList.toggle('outline',!selected);
  }
}
local('analysis-local-mode').addEventListener('click',()=>localMode(true));
local('analysis-server-mode').addEventListener('click',()=>localMode(false));
function localStatus(message) {local('local-status').textContent=message;}
function localSelected() {return localState.outputs.filter(x=>x.selected);}
function localControls() {
  const busy=localState.busy, files=localState.files.length>0, selected=localSelected().length;
  for(const id of ['local-files','local-folder','local-preview-file','local-video-file','local-select-all','local-select-none','local-upload-name'])local(id).disabled=busy;
  for(const input of local('local-browser').querySelectorAll('.form-grid input'))input.disabled=busy;
  local('local-analyze').disabled=busy||!files;local('local-extract').disabled=busy||!local('local-video-file').value;
  local('local-cancel').hidden=!busy;local('local-save-report').disabled=busy||!localState.report;
  for(const id of ['local-save-zip','local-upload'])local(id).disabled=busy||!selected;
  local('local-save-folder').disabled=busy||!selected||!window.isSecureContext||!window.showDirectoryPicker;
  local('local-save-note').textContent=window.isSecureContext&&window.showDirectoryPicker?'Escolha uma pasta para gravar as fotos selecionadas. ZIP e JSON também estão disponíveis.':'Salvar diretamente em pasta requer HTTPS ou localhost e um navegador compatível. Neste acesso, use o download ZIP/JSON.';
}
function localResetWorker() {
  localState.worker?.terminate();localState.worker=null;
  for(const request of localState.pending.values()) {clearTimeout(request.timer);request.reject(new Error('Tarefa local cancelada.'));}
  localState.pending.clear();
}
function localRpc(data,transfer=[]) {
  if(!localState.worker) {
    const worker=new Worker('/local-worker.js');localState.worker=worker;
    worker.onmessage=({data:result})=>{const request=localState.pending.get(result.id);if(!request)return;clearTimeout(request.timer);localState.pending.delete(result.id);result.error?request.reject(new Error(result.error)):request.resolve(result);};
    worker.onerror=()=>{localResetWorker();localStatus('Não foi possível carregar o motor de análise local. Atualize a página e tente novamente.');};
  }
  const id=++localState.sequence;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{localState.pending.delete(id);reject(new Error('O motor local não respondeu. Cancele e tente novamente.'));},90000);
    localState.pending.set(id,{resolve,reject,timer});localState.worker.postMessage({...data,id},transfer);
  });
}
function localCheckCancel() {if(localState.cancelled)throw new Error('Tarefa local cancelada; resultados parciais foram preservados.');}
async function localTask(action) {
  if(localState.busy)return;
  localState.busy=true;localState.cancelled=false;localState.controller=new AbortController();localControls();local('local-progress').removeAttribute('value');local('local-progress').hidden=false;
  try {await action();} catch(error){localStatus(error.message||String(error));}
  finally {localState.busy=false;local('local-progress').hidden=true;localRenderOutputs();localControls();}
}
local('local-cancel').addEventListener('click',()=>{localState.cancelled=true;localState.controller?.abort();localResetWorker();localStatus('Cancelando tarefa local…');});
function localParameters() {
  const fields={start:'local-start',end:'local-end',interval:'local-interval',limit:'local-limit',resolution:'local-resolution',blur:'local-blur',min_motion:'local-motion',max_gap:'local-gap'},values={};
  for(const [key,id] of Object.entries(fields)) {if(!local(id).reportValidity())throw new Error('Revise os parâmetros locais.');values[key]=key==='end'&&local(id).value===''?null:Number(local(id).value);}
  if(values.end!==null&&values.end<=values.start)throw new Error('O fim do trecho deve ser posterior ao início.');
  if(values.max_gap<values.interval)throw new Error('O intervalo de segurança deve ser maior ou igual ao intervalo mínimo.');
  return values;
}
function localAnchors(length,limit) {if(length<=0)return [];if(length<=limit)return Array.from({length},(_,i)=>i);return Array.from({length:limit},(_,i)=>Math.round(i*(length-1)/(limit-1)));}
function localSafeName(name,index) {return `${String(index+1).padStart(5,'0')}_${name.replace(/[^a-zA-Z0-9._-]/g,'_').slice(-150)}`;}
function localGood(metrics,options,extraction=false) {return metrics.sharpness>=options.blur&&Math.max(metrics.dark_fraction,metrics.bright_fraction)<(extraction ? .8 : .6);}
async function localPixels(source,remember=true) {
  localCheckCancel();const width=source.videoWidth||source.width,height=source.videoHeight||source.height,scale=Math.min(1,800/Math.max(width,height));
  const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
  const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(source,0,0,canvas.width,canvas.height);const image=ctx.getImageData(0,0,canvas.width,canvas.height);
  const result=await localRpc({type:'frame',width:canvas.width,height:canvas.height,pixels:image.data.buffer,remember},[image.data.buffer]);canvas.width=canvas.height=1;return result;
}
function localEvent(element,event,trigger,timeout=30000) {
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>finish(new Error('Tempo esgotado ao decodificar a mídia.')),timeout);
    const signal=localState.controller?.signal;
    function abort(){finish(new Error('Tarefa local cancelada.'));}
    signal?.addEventListener('abort',abort,{once:true});
    function finish(error){signal?.removeEventListener('abort',abort);clearTimeout(timer);element.removeEventListener(event,success);element.removeEventListener('error',failure);error?reject(error):resolve();}
    function success(){finish();}function failure(){finish(new Error('Formato ou codec não suportado pelo navegador.'));}
    element.addEventListener(event,success,{once:true});element.addEventListener('error',failure,{once:true});trigger();
  });
}
async function localVideo(file,action) {
  const video=document.createElement('video'),url=URL.createObjectURL(file);video.muted=true;video.preload='auto';
  try {
    await localEvent(video,'loadeddata',()=>{video.src=url;video.load();});
    if(!Number.isFinite(video.duration)||video.duration<=0)throw new Error('Não foi possível determinar a duração do vídeo.');
    return await action(video,async seconds=>{localCheckCancel();const target=Math.min(seconds,Math.max(0,video.duration-.05));if(Math.abs(video.currentTime-target)<.001&&video.readyState>=2)return;await localEvent(video,'seeked',()=>{video.currentTime=target;});localCheckCancel();});
  } finally {video.pause();video.removeAttribute('src');video.load();URL.revokeObjectURL(url);}
}
function localReportBase(type,options) {return {type,engine:'OpenCV.js 4.13.0 / WebAssembly',created:new Date().toISOString(),parameters:options,complete:false,files:localState.files.map(f=>({name:f.name,bytes:f.size,kind:localKind(f)})),samples:[],motion_pairs:[],warnings:[],errors:[],limitations:'Amostragem e limiares heurísticos. ORB e homografia RANSAC no navegador não medem paralaxe 3D nem distinguem giro de cena plana. Não usa o teste de matriz fundamental do servidor. Formatos dependem dos codecs do navegador. Nenhum vídeo é enviado; fotos extraídas não recebem GPS/SRT.'};}
function localRenderReport() {
  const report=localState.report,box=local('local-report');box.replaceChildren();box.hidden=!report;if(!report)return;
  const title=document.createElement('h3');title.textContent=report.type==='extraction'?'Extração local de quadros':'Análise local de fotos e vídeo';box.append(title);
  const text=document.createElement('p');text.textContent=`${report.complete?'Concluída':'Parcial'} · ${report.samples.length} amostras · ${report.motion_pairs.length} pares · ${localState.outputs.filter(x=>x.verified).length} fotos verificadas disponíveis.`;box.append(text);
  const list=document.createElement('ul');list.className='analysis-findings';
  for(const [severity,values] of [['error',report.errors],['warning',report.warnings]])for(const value of values){const item=document.createElement('li');item.className=severity;item.textContent=value;list.append(item);}box.append(list);
  if(report.motion_pairs.length) {const details=document.createElement('details'),heading=document.createElement('summary');heading.textContent='Movimento entre quadros';details.append(heading);const table=document.createElement('table');table.className='motion-table';for(const pair of report.motion_pairs){const row=document.createElement('tr');for(const value of [pair.first+' → '+pair.second,localLabels[pair.status]||pair.status,`${pair.matches} correspondências`,pair.motion_percent===null?'Indeterminado':pair.motion_percent.toFixed(2)+'% da diagonal']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);}details.append(table);box.append(details);}
  const note=document.createElement('p');note.className='muted';note.textContent=report.limitations;box.append(note);
}
function localWarnings(report) {
  const counts={};for(const pair of report.motion_pairs)counts[pair.status]=(counts[pair.status]||0)+1;
  for(const [status,count] of Object.entries(counts))if(status!=='tracked')report.warnings.push(`${localLabels[status]||status}: ${count} pares.`);
  const bad=report.samples.filter(x=>!x.quality_ok);if(bad.length)report.warnings.push(`${bad.length} amostras abaixo dos critérios de nitidez ou exposição. Confira o diagnóstico JSON.`);
}
async function localAnalyze() {
  const options=localParameters(),report=localReportBase('analysis',options);localState.report=report;
  const photos=localState.files.filter(f=>localKind(f)==='photo');
  localState.outputs=photos.map((file,index)=>({name:localSafeName(file.name,index),blob:file,selected:false,verified:false,original:file.name}));
  const indices=new Set();for(const anchor of localAnchors(Math.max(0,photos.length-1),32)){indices.add(anchor);indices.add(anchor+1);}if(photos.length===1)indices.add(0);
  let previousIndex=-2,previousName=null,done=0;const ordered=[...indices].sort((a,b)=>a-b);
  localStatus('Carregando motor WebAssembly e analisando fotos localmente…');
  try {
    await localRpc({type:'reset'});
    for(const index of ordered) {
      localCheckCancel();if(index!==previousIndex+1)await localRpc({type:'reset'});
      let bitmap;
      try {
        bitmap=await createImageBitmap(photos[index]);const result=await localPixels(bitmap);const file=photos[index];
        const good=localGood(result.metrics,options);report.samples.push({file:file.name,...result.metrics,quality_ok:good});
        const output=localState.outputs[index];output.verified=true;output.selected=good;
        if(result.motion&&index===previousIndex+1)report.motion_pairs.push({...result.motion,first:previousName,second:file.name});
        previousIndex=index;previousName=file.name;
      } catch(error){if(localState.cancelled)throw error;report.errors.push(`${photos[index].name}: ${error.message}`);await localRpc({type:'reset'});previousIndex=-2;}
      finally {bitmap?.close();}
      done++;local('local-progress').max=Math.max(1,ordered.length);local('local-progress').value=done;localStatus(`Fotos locais: ${done}/${ordered.length} amostras.`);
    }
    const file=localState.files[Number(local('local-video-file').value)];
    if(file&&localKind(file)==='video')await localVideo(file,async(video,seek)=>{
      const end=Math.min(options.end??video.duration,video.duration-.05),span=end-options.start;
      if(span<=0)throw new Error('Trecho de vídeo vazio.');const step=Math.max(options.interval,span/(options.limit-1));
      for(const anchor of localAnchors(Math.max(1,Math.floor((span-step)/step)+1),12)) {
        await localRpc({type:'reset'});let first=null;
        for(const seconds of [options.start+anchor*step,Math.min(end,options.start+(anchor+1)*step)]) {
          await seek(seconds);const result=await localPixels(video);const name=`${file.name} · ${seconds.toFixed(2)}s`;
          report.samples.push({file:name,...result.metrics,quality_ok:localGood(result.metrics,options)});
          if(first&&result.motion)report.motion_pairs.push({...result.motion,first,second:name});first=name;
          localStatus('Analisando vídeo local: '+seconds.toFixed(1)+' s');
        }
      }
    });
    report.complete=true;localWarnings(report);localStatus(`Análise local concluída: ${report.samples.length} amostras. Nenhum arquivo foi enviado.`);
  } finally {localWarningsIfPartial(report);localRenderReport();}
}
function localWarningsIfPartial(report){if(!report.complete)report.warnings.push('Tarefa interrompida ou incompleta. Resultados parciais não validam todo o conjunto.');}
async function localExtract() {
  const options=localParameters(),file=localState.files[Number(local('local-video-file').value)];if(!file||localKind(file)!=='video')throw new Error('Selecione um vídeo local.');
  if(localState.outputs.length&& !confirm('Substituir a seleção local de fotos pelos novos quadros? Os arquivos originais permanecem intactos.'))return;
  const report=localReportBase('extraction',options);report.frames=[];localState.report=report;localState.outputs=[];
  const canvas=document.createElement('canvas');let lastTime=null;
  try {
    await localRpc({type:'reset'});
    await localVideo(file,async(video,seek)=>{
      const end=Math.min(options.end??video.duration,video.duration-.05),span=end-options.start;if(span<=0)throw new Error('Trecho de vídeo vazio.');
      const step=Math.max(options.interval,span/(options.limit-1));const times=Array.from({length:Math.max(1,Math.ceil(span/step))},(_,i)=>options.start+i*step);times.push(end);
      report.effective_interval_seconds=step;local('local-progress').max=times.length;
      for(const [index,seconds] of times.entries()) {
        await seek(seconds);const result=await localPixels(video,false),row={seconds,metrics:result.metrics,motion:result.motion,accepted:false};report.frames.push(row);
        const name=`frame_${String(index).padStart(6,'0')}_${Math.round(seconds*1000)}ms.jpg`;
        const good=localGood(result.metrics,options,true),low=result.motion?.motion_percent!==null&&result.motion?.motion_percent!==undefined&&result.motion.motion_percent<options.min_motion;
        row.reason=!good?'quality':low&&seconds-lastTime<options.max_gap?'low_motion':low?'safety_gap':'accepted';
        if(good&&row.reason!=='low_motion') {
          const scale=Math.min(1,options.resolution/Math.max(video.videoWidth,video.videoHeight));canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
          const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.95));if(!blob)throw new Error('O navegador não conseguiu criar o JPEG.');
          if(localState.outputs.reduce((sum,x)=>sum+x.blob.size,0)+blob.size>512*1048576)throw new Error('As fotos extraídas atingiram 512 MB de memória. Salve a seleção parcial e extraia outro trecho.');
          localState.outputs.push({name,blob,selected:true,verified:true});row.accepted=true;row.file=name;
          report.samples.push({file:name,...result.metrics,quality_ok:true});
          if(result.motion)report.motion_pairs.push({...result.motion,first:`Último quadro aceito · ${lastTime.toFixed(2)}s`,second:`${seconds.toFixed(2)}s`});
          await localRpc({type:'commit'});lastTime=seconds;
        }
        local('local-progress').value=index+1;localStatus(`Extração local: ${index+1}/${times.length} candidatos · ${localState.outputs.length} fotos.`);
      }
    });
    report.complete=localState.outputs.length>=3;report.accepted=localState.outputs.length;localWarnings(report);
    if(!report.complete)report.errors.push('Menos de três quadros passaram pelos filtros. Revise o trecho e os limiares.');
    localStatus(`Extração local ${report.complete?'concluída':'insuficiente'}: ${localState.outputs.length} fotos. O vídeo não foi enviado.`);
  } finally {canvas.width=canvas.height=1;localWarningsIfPartial(report);localRenderReport();}
}
function localRenderOutputs() {
  const list=local('local-output-list');list.replaceChildren();local('local-selection').hidden=!localState.outputs.length;
  for(const entry of localState.outputs.slice(0,localState.shown)) {const label=document.createElement('label'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=entry.selected;checkbox.disabled=localState.busy;checkbox.addEventListener('change',()=>{entry.selected=checkbox.checked;localRenderSelectionCount();localControls();});const text=document.createElement('span');text.textContent=`${entry.name} · ${(entry.blob.size/1048576).toFixed(2)} MB · ${entry.verified?'analisada':'não amostrada'}`;label.append(checkbox,text);list.append(label);}
  local('local-more').hidden=localState.outputs.length<=localState.shown;localRenderSelectionCount();
}
function localRenderSelectionCount() {local('local-selection-summary').textContent=`${localSelected().length} de ${localState.outputs.length} fotos selecionadas. Fotos importadas não amostradas começam desmarcadas; selecioná-las não significa que foram verificadas.`;}
function localDownload(blob,name) {const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
function localManifest(){return {...localState.report,selected_files:localSelected().map(x=>({file:x.name,bytes:x.blob.size,verified:x.verified}))};}
local('local-save-report').addEventListener('click',()=>localDownload(new Blob([JSON.stringify(localManifest(),null,2)],{type:'application/json'}),'diagnostico-local.json'));
local('local-save-zip').addEventListener('click',()=>localTask(async()=>{
  const files=localSelected();if(files.reduce((sum,x)=>sum+x.blob.size,0)>512*1048576)throw new Error('O ZIP excederia 512 MB. Salve em pasta ou exporte uma seleção menor.');
  const chunks=[];let failure=null;const zip=new fflate.Zip((error,chunk)=>{if(error)failure=error;else chunks.push(chunk);});
  for(const [index,entry] of files.entries()) {localCheckCancel();const file=new fflate.ZipPassThrough('images/'+entry.name);zip.add(file);file.push(new Uint8Array(await entry.blob.arrayBuffer()),true);localStatus(`Preparando ZIP: ${index+1}/${files.length}`);}
  const report=new fflate.ZipPassThrough('diagnostico-local.json');zip.add(report);report.push(fflate.strToU8(JSON.stringify(localManifest(),null,2)),true);zip.end();if(failure)throw failure;
  localDownload(new Blob(chunks,{type:'application/zip'}),'fotos-locais.zip');localStatus('Download ZIP preparado neste computador.');
}));
local('local-save-folder').addEventListener('click',async()=>{
  let directory;try {directory=await window.showDirectoryPicker({mode:'readwrite'});}catch(error){if(error.name!=='AbortError')localStatus(error.message);return;}
  localTask(async()=>{
    const name='odm-local-'+new Date().toISOString().replace(/[:.]/g,'-')+'-'+crypto.getRandomValues(new Uint32Array(1))[0].toString(16);
    const target=await directory.getDirectoryHandle(name,{create:true}),images=await target.getDirectoryHandle('images',{create:true});
    for(const entry of localSelected()) {localCheckCancel();const file=await images.getFileHandle(entry.name,{create:true}),stream=await file.createWritable();await stream.write(entry.blob);await stream.close();}
    const file=await target.getFileHandle('diagnostico-local.json',{create:true}),stream=await file.createWritable();await stream.write(JSON.stringify(localManifest(),null,2));await stream.close();localStatus('Fotos e diagnóstico salvos na nova pasta '+name+'.');
  });
});
async function localUpload() {
  const files=localSelected(),name=local('local-upload-name').value;if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(name))throw new Error('Informe um nome de projeto válido, sem espaços ou acentos.');
  if(files.length<3)throw new Error('Selecione pelo menos três fotos para enviar.');
  if(files.some(x=>!/[.](jpe?g|png|webp|bmp|tiff?)$/i.test(x.name)))throw new Error('O envio ao ODM aceita fotos JPEG, PNG, WebP, BMP e TIFF. Exporte ou converta os demais formatos antes de enviar.');
  if(files.some(x=>x.blob.size>50*1048576))throw new Error('Cada foto enviada deve ter até 50 MB.');
  const session=await api('local-upload-start',{name});
  for(const [index,entry] of files.entries()) {
    localCheckCancel();const ext=entry.name.split('.').pop().toLowerCase();
    const response=await fetch('/api/local-upload-file?'+new URLSearchParams({upload:session.upload,name:`photo_${String(index).padStart(6,'0')}.${ext}`}),{method:'POST',headers:{'X-ODM-Token':token,'Content-Type':'application/octet-stream'},body:entry.blob,signal:localState.controller.signal});
    if(response.status===401)throw new Error('Autenticação necessária para enviar fotos. Entre novamente e tente outro projeto.');
    if(!response.ok){const data=await response.json();throw new Error(data.error||'Falha ao enviar foto.');}
    localStatus(`Enviando somente fotos selecionadas: ${index+1}/${files.length}.`);
  }
  await api('local-upload-finish',{upload:session.upload,report:{engine:localState.report?.engine,parameters:localState.report?.parameters,warnings:localState.report?.warnings||[],selection:files.map(x=>({file:x.name,verified:x.verified}))}});
  await loadProjects();localStatus('Fotos enviadas para o novo projeto '+name+'. Nenhuma reconstrução foi iniciada.');
}
local('local-upload').addEventListener('click',()=>localTask(localUpload));
local('local-analyze').addEventListener('click',()=>localTask(localAnalyze));local('local-extract').addEventListener('click',()=>localTask(localExtract));
local('local-select-all').addEventListener('click',()=>{for(const entry of localState.outputs)entry.selected=true;localRenderOutputs();localControls();});local('local-select-none').addEventListener('click',()=>{for(const entry of localState.outputs)entry.selected=false;localRenderOutputs();localControls();});
local('local-more').addEventListener('click',()=>{localState.shown+=50;localRenderOutputs();});
async function localPreview() {
  const image=local('local-preview-image'),video=local('local-preview-video');video.pause();video.removeAttribute('src');video.load();image.removeAttribute('src');image.hidden=video.hidden=true;if(localState.previewUrl)URL.revokeObjectURL(localState.previewUrl);
  const file=localState.files[Number(local('local-preview-file').value)];if(!file)return;const url=URL.createObjectURL(file);localState.previewUrl=url;
  const target=localKind(file)==='video'?video:image;target.hidden=false;target.src=url;
}
function localChooseFiles(event) {
  if(localState.busy)return;
  localState.files=[...event.target.files].filter(localKind).sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));localState.outputs=[];localState.report=null;localState.shown=50;localResetWorker();
  for(const id of ['local-preview-file','local-video-file'])local(id).replaceChildren();
  for(const [index,file] of localState.files.entries()) {local('local-preview-file').add(new Option(file.name,String(index)));if(localKind(file)==='video')local('local-video-file').add(new Option(file.name,String(index)));}
  local('local-input-summary').textContent=`${localState.files.filter(f=>localKind(f)==='photo').length} fotos · ${localState.files.filter(f=>localKind(f)==='video').length} vídeos · arquivos somente neste computador.`;
  localPreview();localRenderReport();localRenderOutputs();localControls();
}
for(const id of ['local-files','local-folder'])local(id).addEventListener('change',localChooseFiles);
local('local-preview-file').addEventListener('change',localPreview);local('local-video-file').addEventListener('change',localControls);
localControls();
