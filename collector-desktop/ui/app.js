'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=name=>`<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const kinds={folder:'Ordner mit Unterordnern',obsidian:'Obsidian',claude:'Claude-Code-Sitzungen',codex:'Codex-Sitzungen',file:'Datei',git:'Git-Projekt',mercurial:'Mercurial-Projekt'};
let state=null;
let view='sources';
let refreshTask=null;
let startAttempted='';
let historyGeneration=0;
let historyCursor=null;
let historyEntries=[];
let historyHasOlder=false;
let historyUpdatedAt=0;
let historySource=null;
let historyLoading=false;
let sourceMarkup=null;
let toastTimer;
let confirmationAction=null;
let commandBusy=false;
async function invoke(action,payload=null){
  if(!window.__TAURI__)throw new Error('Öffne diese Oberfläche in der myCelos-Sammler-App.');
  return window.__TAURI__.core.invoke('collector',{action,payload});
}
function errorText(error){return String(error?.message||error||'Der Auftrag ist fehlgeschlagen. Bitte erneut versuchen.');}
function historyError(error){
  return new Map([
    ['Delivery expired','Die Frist für die Übertragung ist abgelaufen.'],
    ['Source rules changed','Die Dateiauswahl hat sich geändert.'],
    ['Source removed','Die Freigabe der Quelle ist entfernt.'],
    ['Server disconnected','Die Verbindung zum Server ist getrennt.']
  ]).get(error)||'Die Übertragung ist fehlgeschlagen.';
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500);}
async function action(button,fn,errorId='error'){
  if(commandBusy)return;
  commandBusy=true;button.disabled=true;$(errorId).hidden=true;
  try{await fn();}catch(error){$(errorId).textContent=errorText(error);$(errorId).hidden=false;}
  finally{commandBusy=false;button.disabled=false;}
}
function when(value){
  if(!value)return 'Noch nicht synchronisiert';
  const date=new Date(value*1000);
  if(Number.isNaN(date.getTime()))return 'Zeitpunkt unbekannt';
  const today=date.toDateString()===new Date().toDateString();
  return (today?'Heute':date.toLocaleDateString('de-AT'))+', '+date.toLocaleTimeString('de-AT',{hour:'2-digit',minute:'2-digit'});
}
function online(){return state?.daemon_running&&['active','paused'].includes(state.runtime?.status)&&!state.runtime?.error;}
function showView(next){
  view=next;
  render();
  if(next==='history')loadHistory(false);
}
function render(){
  if(!state)return;
  const ready=state.onboarding_complete===true;
  const configured=Boolean(state.server_url);
  const pending=configured&&!ready;
  $('settings-button').hidden=!configured;
  $('main-nav').hidden=!ready||view==='settings';
  $('connection-label').textContent=state.paused?'Pausiert':online()?'Verbunden':pending&&!state.runtime?.error?'Bestätigung fehlt':'Nicht verbunden';
  $('status-dot').className='dot '+(!state.paused&&online()?'online':pending?'pending':'');
  for(const name of ['welcome','confirmation','sources','history','settings']){
    $(name+'-view').hidden=name!==(view==='settings'&&configured?'settings':!ready?(configured?'confirmation':'welcome'):view);
  }
  $('sources-tab').setAttribute('aria-selected',String(view==='sources'));
  $('history-tab').setAttribute('aria-selected',String(view==='history'));
  $('sources-tab').tabIndex=view==='sources'?0:-1;
  $('history-tab').tabIndex=view==='history'?0:-1;
  $('confirmation-server').textContent=state.server_url||'';
  $('confirmation-fingerprint').textContent=state.fingerprint||'';
  $('confirmation-state').textContent=state.runtime?.status==='revoked'?'Der Server hat dieses Gerät widerrufen. Verwende eine neue Einladung.':state.runtime?.error?'Der Server ist derzeit nicht erreichbar. Der Sammler versucht es erneut.':'Die Bestätigung am Server steht noch aus.';
  $('settings-server').textContent=state.server_url||'';
  for(const [element,key] of [['fingerprint','fingerprint'],['server-fingerprint','server_fingerprint'],['public-key','public_key'],['server-key','server_key']])$(element).textContent=state[key]||'Noch nicht verbunden';
  $('process-state').textContent=state.paused?'Sammlung pausiert':state.daemon_running?'Der Sammler läuft im Hintergrund.':'Der Sammler ist beendet.';
  $('start-process').hidden=Boolean(state.daemon_running);
  $('pause-process').hidden=Boolean(state.paused)||!state.daemon_running;
  $('resume-process').hidden=!state.paused||!state.daemon_running;
  $('stop-process').hidden=!state.daemon_running;
  if(ready)renderSources();
}
function renderSources(){
  const sources=state.sources||[];
  $('source-count').textContent=`${sources.length} ${sources.length===1?'Quelle':'Quellen'} auf diesem Gerät`;
  $('source-footnote').hidden=!sources.length;
  $('source-notice').hidden=!state.paused&&online();
  $('source-notice-text').textContent=state.paused?'Der Sammler pausiert. Setze ihn fort, um Quellen abzugleichen.':'Der Server ist nicht verbunden. Ein neuer Abgleich bleibt vorgemerkt, bis die Verbindung zurückkehrt.';
  $('source-resume').hidden=!state.paused;
  const markup=sources.length?`<div class="source-list-head"><span>Quelle</span><span>Zuletzt synchronisiert</span><span></span></div>${sources.map(source=>{
    const messages={scanning:'Dateien werden geprüft.',transferring:'Dateien werden übertragen.',received:'Dateien am Server angekommen.',processed:'Abgleich abgeschlossen.',processing:'Der Server verarbeitet die Dateien.',unchanged:'Keine neuen oder geänderten Dateien.',awaiting_configuration:'Verarbeitung am Server freigeben',incomplete:'Abgleich unvollständig. Prüfe die Dateiauswahl und den Zugriff auf die Dateien.',transfer_failed:'Übertragung fehlgeschlagen. Der Sammler versucht es erneut.'};
    messages.processing_failed='Übertragen. Die Wissensaufbereitung am Server ist fehlgeschlagen.';
    const message=source.sync_requested?'Abgleich vorgemerkt':messages[source.status]||(source.error?'Quelle nicht verfügbar':'');
    const problem=message?`<small class="${source.sync_requested?'pending':source.error?'fault':''}">${esc(message)}</small>`:'';
    return `<article class="source-row"><button class="source-open" data-edit="${esc(source.source_id)}" aria-label="${esc(source.name)} bearbeiten">${icon('folder')}<span><strong>${esc(source.name)}</strong><small>${esc(kinds[source.kind]||source.kind)} · ${esc(source.root)}</small></span></button><div class="source-time" role="status"><span>${esc(when(source.last_synced))}</span>${problem}</div><button class="icon" data-sync="${esc(source.source_id)}" aria-label="${esc(source.name)} jetzt synchronisieren" title="Jetzt synchronisieren" ${state.paused||source.sync_requested?'disabled':''}>${icon('sync')}</button></article>`;
  }).join('')}`:`<div class="empty">${icon('folder')}<h2>Füge deine erste Quelle hinzu.</h2><p class="muted">Wähle einen Ordner, Obsidian oder frühere Claude-Code- und Codex-Sitzungen.</p></div>`;
  if(markup===sourceMarkup)return;
  const list=$('sources');
  const active=list.contains(document.activeElement)?document.activeElement:null;
  const control=active?.dataset.edit?'edit':'sync';
  const sourceId=active?.dataset[control];
  list.innerHTML=markup;
  sourceMarkup=markup;
  if(sourceId){
    const buttons=[...list.querySelectorAll('button:not(:disabled)')];
    const next=buttons.find(button=>button.dataset[control]===sourceId)
      ||buttons.find(button=>button.dataset.edit===sourceId)
      ||buttons.find(button=>button.dataset.edit)
      ||$('add-source');
    next.focus();
  }
}
async function refresh(){
  if(refreshTask)return refreshTask;
  refreshTask=(async()=>{
    state=await invoke('status');
    render();
    const profile=state.server_url+'|'+state.public_key;
    if(state.server_url&&!state.daemon_running&&!state.paused&&startAttempted!==profile){
      startAttempted=profile;
      await invoke('start');
    }
  })();
  try{await refreshTask;}finally{refreshTask=null;}
}
async function pair(button,input){
  await action(button,async()=>{
    const invitation=JSON.parse(input.value);
    await invoke('pair',invitation);
    input.value='';view='sources';startAttempted='';
    await invoke('start');
    await refresh();
  });
}
function defaults(kind){return {include:['codex','claude'].includes(kind)?['**/*.jsonl']:['**/*.md','**/*.txt','**/*.pdf'],exclude:kind==='obsidian'?['Templates/**','Vorlagen/**']:[]};}
function sourceDialog(source=null){
  const editing=Boolean(source);
  $('source-dialog-title').textContent=editing?source.name:'Quelle hinzufügen';
  $('source-error').hidden=true;
  const workflow=source?.workflow||defaults('folder');
  $('source-form-content').innerHTML=`<form id="source-form">${editing?`<p class="hint">${esc(source.root)}</p><p>Freigabe und Dateiauswahl</p>`:`<label for="kind">Quellenart</label><select id="kind">${Object.entries(kinds).map(([id,name])=>`<option value="${id}">${esc(name)}</option>`).join('')}</select><button type="button" id="discover" class="quiet">Bekannte Ablageorte anzeigen</button><div id="suggestions"></div><label for="root">Ordner oder Datei auf diesem Gerät</label><div class="path-row"><input id="root" required placeholder="Pfad auswählen"><button id="choose" type="button">Auswählen</button></div><label for="name">Name der Quelle</label><input id="name" required maxlength="100" placeholder="Zum Beispiel: Meine Notizen">`}
    <details ${editing?'open':''}><summary>Dateiauswahl prüfen</summary><p class="hint">Der Sammler liest passende Dateien auch in den Unterordnern. Ausschlüsse haben Vorrang.</p><label for="include">Dateimuster</label><textarea id="include" rows="3">${esc((workflow.include||[]).join('\n'))}</textarea><label for="exclude">Ausschlüsse</label><textarea id="exclude" rows="3">${esc((workflow.exclude||[]).join('\n'))}</textarea></details>
    ${editing?'':`<label class="consent"><input id="consent" type="checkbox" required><span>Ich gebe diese Quelle frei. Ausgewählte Inhalte gelangen zu meinem Server. Die Modellverarbeitung am Server kann Inhalte an einen Modellanbieter senden.</span></label>`}
    <div class="actions end"><button type="button" data-close="source-dialog">Abbrechen</button><button class="primary" type="submit">${editing?'Regeln auf diesem Gerät ändern':'Quelle freigeben'}</button></div></form>${editing?'<div class="dialog-footer"><p class="hint">Der Sammler liest diese Quelle nach dem Entzug nicht mehr. Die Originaldateien bleiben erhalten.</p><button id="remove-source" class="danger">Freigabe entziehen</button></div>':''}`;
  $('source-dialog').showModal();
  if(!editing){
    $('kind').onchange=()=>{const rules=defaults($('kind').value);$('include').value=rules.include.join('\n');$('exclude').value=rules.exclude.join('\n');};
    $('choose').onclick=event=>action(event.currentTarget,async()=>{
      const path=await window.__TAURI__.core.invoke('choose_source',{file:$('kind').value==='file'});
      if(path){$('root').value=path;if(!$('name').value)$('name').value=path.split(/[\\/]/).filter(Boolean).pop()||'';}
    },'source-error');
    $('discover').onclick=event=>action(event.currentTarget,async()=>{
      const list=await invoke('discover');
      $('suggestions').replaceChildren();
      for(const suggestion of list){
        const button=document.createElement('button');button.type='button';button.className='suggestion';
        button.textContent=(kinds[suggestion.kind]||suggestion.kind)+': '+suggestion.root;
        button.onclick=()=>{$('kind').value=suggestion.kind;$('kind').onchange();$('root').value=suggestion.root;if(!$('name').value)$('name').value=suggestion.name||kinds[suggestion.kind];};
        $('suggestions').append(button);
      }
      if(!list.length)$('suggestions').textContent='Keine bekannten Ablageorte gefunden. Wähle einen Ordner selbst aus.';
    },'source-error');
  }
  $('source-form').onsubmit=event=>{event.preventDefault();action(event.submitter,async()=>{
    const lines=id=>$(id).value.split('\n').map(value=>value.trim()).filter(Boolean);
    const rules={...workflow,include:lines('include'),exclude:lines('exclude')};
    if(editing)await invoke('rules',{source_id:source.source_id,workflow:rules});
    else await invoke('add',{name:$('name').value,kind:$('kind').value,root:$('root').value,workflow:{include:rules.include,exclude:rules.exclude}});
    $('source-dialog').close();await refresh();toast(editing?'Die Dateiauswahl wurde geändert.':'Die Quelle ist freigegeben.');
  },'source-error');};
  if(editing)$('remove-source').onclick=event=>action(event.currentTarget,async()=>{
    await invoke('remove',{source_id:source.source_id});$('source-dialog').close();await refresh();toast('Die lokale Freigabe ist entfernt.');
  },'source-error');
}
async function loadHistory(append=false){
  const selected=$('history-source').value;
  if(append&&(historyLoading||!historyCursor||selected!==historySource))return;
  const generation=++historyGeneration;
  historyLoading=true;
  historyUpdatedAt=Date.now();
  if(!append){
    historySource=selected;historyEntries=[];historyCursor=null;historyHasOlder=false;
    $('history-list').replaceChildren();$('history-more').hidden=true;
  }
  const query={limit:100};
  if(selected)query.source_id=selected;
  if(append&&historyCursor)query.before=historyCursor;
  $('history-error').hidden=true;$('history-state').textContent='Das Protokoll lädt …';
  $('history-more').disabled=true;
  try{
    const result=await invoke('history',query);
    if(generation!==historyGeneration)return;
    const choices=new Map((state?.sources||[]).map(source=>[source.source_id,source.name]));
    for(const source of result.sources||[])choices.set(source.source_id,source.name);
    if(selected&&!choices.has(selected))choices.set(selected,'Entfernte Quelle');
    $('history-source').innerHTML='<option value="">Alle Quellen</option>'+[...choices].map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
    $('history-source').value=selected;
    historyEntries=append?historyEntries.concat(result.entries):result.entries;
    historyHasOlder=append;
    historyCursor=result.next_before;
    $('history-list').innerHTML=historyEntries.map(entry=>`<tr><td>${esc(when(entry.at))}</td><td><span class="file">${esc(entry.file||'Quellenabgleich')}</span><small>${esc(entry.source_name)}</small></td><td class="${['transferred','failed','pending'].includes(entry.status)?entry.status:''}">${esc({transferred:'Übertragen',failed:'Fehlgeschlagen',pending:'Ausstehend'}[entry.status]||entry.status)}${entry.error?`<small>${esc(historyError(entry.error))}</small>`:''}</td></tr>`).join('');
    $('history-state').textContent=historyEntries.length?'':selected?'Für diese Quelle gibt es noch keine protokollierten Übertragungen.':'Noch keine protokollierten Übertragungen. Neue Übertragungen erscheinen hier automatisch nach einem Abgleich.';
    $('history-more').hidden=!historyCursor;
  }catch(error){
    if(generation!==historyGeneration)return;
    $('history-error').textContent='Das Protokoll ist nicht erreichbar. Versuche es mit „Protokoll aktualisieren“ erneut.';$('history-error').hidden=false;$('history-state').textContent='';
  }finally{if(generation===historyGeneration){historyLoading=false;$('history-more').disabled=!historyCursor;}}
}
function askDisconnect(){
  $('confirm-title').textContent='Verbindung trennen?';
  $('confirm-description').textContent='Der Sammler beendet die Übertragung und entfernt die lokalen Freigaben. Deine Dateien und das Wissen am Server bleiben erhalten. Für einen neuen Server musst du deine Quellen erneut freigeben.';
  $('confirm-cancel').textContent='Verbindung behalten';$('confirm-accept').textContent='Server trennen';$('confirm-error').hidden=true;
  confirmationAction=async()=>{await invoke('disconnect');$('confirm-dialog').close();view='sources';startAttempted='';historyEntries=[];historyCursor=null;$('history-list').replaceChildren();await refresh();toast('Die Serververbindung ist getrennt.');};
  $('confirm-dialog').showModal();
}
$('pair-form').onsubmit=event=>{event.preventDefault();pair(event.submitter,$('invitation'));};
$('replacement-form').onsubmit=event=>{event.preventDefault();pair(event.submitter,$('replacement-invitation'));};
$('check-confirmation').onclick=event=>action(event.currentTarget,refresh);
$('settings-button').onclick=()=>showView('settings');
$('back-sources').onclick=()=>showView('sources');
$('sources-tab').onclick=()=>showView('sources');
$('history-tab').onclick=()=>showView('history');
$('main-nav').onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=['ArrowRight','End'].includes(event.key)?'history':'sources';showView(next);$(next+'-tab').focus();}};
$('add-source').onclick=()=>sourceDialog();
$('sources').onclick=event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.edit){const source=state.sources.find(item=>item.source_id===button.dataset.edit);if(source)sourceDialog(source);}
  if(button.dataset.sync)action(button,async()=>{await invoke('sync',{source_id:button.dataset.sync});await refresh();toast(online()?'Der Abgleich startet. Den Fortschritt siehst du bei der Quelle.':'Der Abgleich wartet auf die Verbindung zum Server.');});
};
for(const [id,command] of [['start-process','start'],['pause-process','pause'],['resume-process','resume'],['stop-process','stop'],['source-resume','start']])$(id).onclick=event=>action(event.currentTarget,async()=>{await invoke(command);await refresh();});
$('disconnect').onclick=askDisconnect;
$('confirm-cancel').onclick=()=>$('confirm-dialog').close();
$('confirm-accept').onclick=event=>action(event.currentTarget,confirmationAction,'confirm-error');
$('refresh-history').onclick=()=>loadHistory(false);
$('history-more').onclick=()=>loadHistory(true);
$('history-source').onchange=()=>loadHistory(false);
// Fixed native commands never accept an executable or a server URL from this page.
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.close)$(button.dataset.close).close();
  if(button.dataset.action==='open-server')action(button,()=>invoke('open-server'));
});
refresh().catch(error=>{$('error').textContent=errorText(error);$('error').hidden=false;});
setInterval(()=>{
  if(document.hidden||commandBusy)return;
  refresh().catch(()=>{});
  if(view==='history'&&!historyLoading&&!historyHasOlder&&document.activeElement!==$('history-source')&&Date.now()-historyUpdatedAt>5000)loadHistory(false);
},2000);
