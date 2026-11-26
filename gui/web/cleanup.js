'use strict';
const c = id => document.getElementById(id);
let cleanupPlan = null, cleanupProject = null, cleanupBusy = false, cleanupActive = false;

function syncCleanupControls(active = cleanupActive) {
  cleanupActive = active;
  c('cleanup-open').disabled = active || cleanupBusy || !c('project').value;
  c('cleanup-execute').disabled = active || cleanupBusy || !cleanupPlan?.entries.length || c('cleanup-confirmation').value !== cleanupProject;
  c('cleanup-confirmation').disabled = active || cleanupBusy;
  c('cleanup-close').disabled = cleanupBusy;
  c('cleanup-backup-list').querySelectorAll('button').forEach(button => { button.disabled = active || cleanupBusy; });
}

function cleanupList(id, entries) {
  c(id).replaceChildren();
  for (const entry of entries) {
    const li=document.createElement('li');li.textContent=entry;c(id).append(li);
  }
}

async function reviewCleanup() {
  cleanupPlan = null;cleanupProject=c('project').value;
  c('cleanup-confirmation').value='';c('cleanup-review').hidden=true;
  c('cleanup-backup-list').replaceChildren();c('cleanup-status').textContent='Preparando lista dos gerados e itens preservados…';
  syncCleanupControls();
  if(!cleanupProject)return;
  cleanupBusy=true;syncCleanupControls();
  try {
    const result=await api('cleanup-preview',{project:cleanupProject});cleanupPlan=result;
    c('cleanup-project').textContent=result.project;
    c('cleanup-size').textContent=`${result.entries.length} itens · ${(result.bytes/1048576).toLocaleString('pt-BR',{maximumFractionDigits:1})} MB para backup`;
    cleanupList('cleanup-entries',result.entries.map(entry=>`${entry.directory?'Pasta':'Arquivo'} · ${entry.name} · ${(entry.bytes/1048576).toLocaleString('pt-BR',{maximumFractionDigits:1})} MB`));
    cleanupList('cleanup-preserved',result.preserved);
    c('cleanup-review').hidden=false;c('cleanup-confirmation').placeholder=result.project;
    c('cleanup-status').textContent=result.entries.length?'Revise os itens e confirme pelo nome do levantamento.':'Nenhum gerado conhecido para limpar. Originais e arquivos de apoio permanecem no projeto.';
    for(const backup of result.backups){
      const article=document.createElement('article');article.className='cleanup-backup';
      const text=document.createElement('p');text.textContent=`${new Date(backup.created).toLocaleString('pt-BR')} · ${backup.entries.length} itens`;
      const path=document.createElement('small');path.textContent=backup.path;
      const button=document.createElement('button');button.type='button';button.className='outline';button.textContent='Restaurar este backup';button.addEventListener('click',()=>restoreCleanup(backup.id));
      article.append(text,path,button);c('cleanup-backup-list').append(article);
    }
    if(!result.backups.length){const empty=document.createElement('p');empty.textContent='Nenhum backup de limpeza disponível.';c('cleanup-backup-list').append(empty);}
  }catch(error){c('cleanup-status').textContent=error.message;}
  finally{cleanupBusy=false;syncCleanupControls();}
}

async function executeCleanup() {
  if(!cleanupPlan||cleanupBusy||cleanupActive)return;
  cleanupBusy=true;syncCleanupControls();
  c('cleanup-status').textContent='Movendo gerados para backup. Aguarde concluir…';
  try{
    const result=await api('cleanup',{project:cleanupProject,plan:cleanupPlan.plan,confirmation:c('cleanup-confirmation').value});
    cursor=0;c('logs').textContent='Processamento limpo. Originais preservados.';lastState=undefined;
    await loadProjects();render(await api('status?since=0'));
    cleanupBusy=false;await reviewCleanup();
    c('cleanup-status').textContent='Limpeza concluída. Backup recuperável em: '+result.path;
    notice('Processamento limpo. Fotos, vídeos e arquivos de apoio preservados.');
  }catch(error){c('cleanup-status').textContent=error.message;}
  finally{cleanupBusy=false;syncCleanupControls();}
}

async function restoreCleanup(id){
  if(cleanupBusy||cleanupActive)return;
  cleanupBusy=true;syncCleanupControls();c('cleanup-status').textContent='Restaurando backup sem sobrescrever arquivos…';
  try{
    await api('cleanup-restore',{project:cleanupProject,backup:id});
    await loadProjects();render(await api('status?since=0'));
    cleanupBusy=false;await reviewCleanup();c('cleanup-status').textContent='Backup restaurado. Arquivos originais preservados.';
    notice('Backup de processamento restaurado.');
  }catch(error){c('cleanup-status').textContent=error.message;}
  finally{cleanupBusy=false;syncCleanupControls();}
}

c('cleanup-open').addEventListener('click',()=>{c('cleanup-dialog').showModal();reviewCleanup();});
c('cleanup-close').addEventListener('click',()=>c('cleanup-dialog').close());
c('cleanup-confirmation').addEventListener('input',()=>syncCleanupControls());
c('cleanup-execute').addEventListener('click',executeCleanup);
c('cleanup-dialog').addEventListener('cancel',event=>{if(cleanupBusy)event.preventDefault();});
