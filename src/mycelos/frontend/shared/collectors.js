'use strict';
const $=id=>document.getElementById(id);
const states={active:'Verbunden',pending:'Bestätigung ausstehend',paused:'Pausiert',revoked:'Verbindung widerrufen'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let currentDevices=[];
let originalJob=null;
let availableModels=[];
let modelProviders=new Map();
let modelError='';
async function loadModels(){
  try{
    const responses=await Promise.all(['/api/models','/api/models/settings'].map(path=>fetch(path,{headers:{Accept:'application/json'}})));
    if(responses.some(response=>!response.ok))throw new Error();
    const [data,settings]=await Promise.all(responses.map(response=>response.json()));
    modelProviders=new Map((settings.providers||[]).filter(provider=>provider.configured).map(provider=>[provider.id,provider.id==='custom'?'Eigener Anbieter':provider.name||provider.id]));
    availableModels=(data.models||[]).filter(model=>modelProviders.has(model.provider)&&model.enabled!==false&&model.enabled!==0&&!/(embed|tts|transcrib|image|realtime|audio|sora|veo|imagen|container)/i.test(model.id));
    modelError='';
  }catch{
    availableModels=[];
    modelError='Die Modellliste ist nicht erreichbar. Lade sie erneut oder speichere den Text direkt.';
  }
}
function modelField(source){
  const id=esc(source.source_id);
  const missing=source.model&&!availableModels.some(model=>model.id===source.model);
  const options=availableModels.map(model=>`<option value="${esc(model.id)}" ${model.id===source.model?'selected':''}>${esc(model.name||model.id.replace(/^custom\//,''))} · ${esc(modelProviders.get(model.provider))}</option>`).join('');
  return `<div data-model-field><label for="model-${id}">Modell</label><select id="model-${id}" name="model" aria-describedby="model-help-${id}"><option value="">Modell wählen</option>${missing?`<option value="${esc(source.model)}" selected disabled>${esc(source.model)} · Nicht verfügbar</option>`:''}${options}</select><div id="model-help-${id}" class="hint">${modelError?`<p role="status">${esc(modelError)}</p><button type="button" class="secondary" data-reload-models>Modelle erneut laden</button>`:!availableModels.length?'<p>Kein Textmodell verfügbar. Richte ein Modell ein oder speichere den Text direkt.</p><a href="/pages/settings.html#models">Modelle einrichten</a>':missing?'<p data-model-missing>Das gespeicherte Modell ist nicht verfügbar. Wähle ein anderes Modell.</p>':'<p>Wähle ein eingerichtetes Textmodell.</p>'}</div></div>`;
}
function syncSourceForm(form){
  const analyze=form.elements.mode.value==='analyze';
  const model=form.elements.model;
  form.querySelector('[data-model-field]').hidden=!analyze;
  model.required=analyze;
  model.disabled=!analyze||!availableModels.length;
  const validModel=availableModels.some(item=>item.id===model.value);
  form.querySelector('button[type="submit"]').disabled=analyze&&!validModel;
  const missing=form.querySelector('[data-model-missing]');
  if(missing)missing.hidden=validModel;
}
function syncSourceForms(){document.querySelectorAll('form[data-source]').forEach(syncSourceForm);}
async function reloadModels(){
  await loadModels();
  document.querySelectorAll('form[data-source]').forEach(form=>{
    const source={source_id:form.dataset.source,model:form.elements.model.value};
    form.querySelector('[data-model-field]').outerHTML=modelField(source);
    syncSourceForm(form);
  });
}
async function api(path,body){const response=await fetch('/api/collectors'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok){let message='Der Auftrag ist fehlgeschlagen. Bitte erneut versuchen.';if(response.status===401)message='Bitte melde dich erneut am Server an.';throw new Error(message);}return response.json();}
async function act(button,fn){button.disabled=true;$('error').hidden=true;try{await fn();}catch(error){$('error').textContent=error.message;$('error').hidden=false;}finally{button.disabled=false;syncSourceForms();}}
function sourceForm(device,source){const id=esc(source.source_id);return `<div class="source-settings"><h4>${esc(source.name)}</h4><p class="hint">${esc(source.kind)} · ${source.status==='paused'?'Verarbeitung noch nicht freigegeben':'Verarbeitung freigegeben'}</p><button type="button" class="secondary" data-interview="${id}" data-device="${esc(device.id)}">Textprobe für das Quelleninterview</button><div data-samples="${id}"></div><form data-source="${id}" data-device="${esc(device.id)}"><div class="two-fields"><div><label for="mode-${id}">Verarbeitung</label><select id="mode-${id}" name="mode"><option value="analyze" ${source.mode==='analyze'?'selected':''}>Wissensartikel mit einem Modell</option><option value="direct" ${source.mode==='direct'?'selected':''}>Ausgewählten Text direkt speichern</option></select></div>${modelField(source)}</div><label for="purpose-${id}">Wofür dient die Quelle?</label><input id="purpose-${id}" name="purpose" value="${esc(source.purpose)}"><p class="hint">Das Modell kann Inhalte an seinen Anbieter senden. Die direkte Speicherung übernimmt den ausgewählten Text unverändert.</p><button type="submit">Verarbeitung freigeben</button></form><details><summary>Quelleneinträge und Originale</summary><button class="secondary" data-items="${id}" data-device="${esc(device.id)}">Einträge anzeigen</button><div data-item-list="${id}"></div></details></div>`;}
async function refresh(){const [devices]=await Promise.all([api(''),loadModels()]);currentDevices=devices;$('devices').innerHTML=currentDevices.length?currentDevices.map(device=>`<article class="device"><h3>${esc(device.name)}</h3><p class="status">${device.status==='active'&&!device.online?'Offline':states[device.status]||esc(device.status)}${device.last_contact?' · Letzter Kontakt: '+esc(new Date(device.last_contact*1000).toLocaleString('de-AT')):''}</p>${device.status==='pending'?'<p>Vergleiche diesen vollständigen Fingerabdruck mit der Sammler-App auf deinem Gerät.</p>':''}<output>${esc(device.fingerprint)}</output><details><summary>Öffentlicher Geräteschlüssel</summary><output>${esc(device.public_key)}</output></details><div class="actions">${device.status==='pending'?`<button data-confirm="${esc(device.id)}">Der Fingerabdruck stimmt überein</button>`:''}${['active','paused'].includes(device.status)?`<button class="secondary" data-state="${device.status==='paused'?'active':'paused'}" data-device="${esc(device.id)}">${device.status==='paused'?'Fortsetzen':'Pausieren'}</button>`:''}${device.status!=='revoked'?`<button class="secondary" data-state="revoked" data-device="${esc(device.id)}">Verbindung widerrufen</button>`:''}</div>${device.status==='revoked'?'<p>Bereits gespeichertes Wissen bleibt erhalten.</p>':(device.sources||[]).map(source=>sourceForm(device,source)).join('')}</article>`).join(''):'<p>Noch kein Gerät verbunden. Erstelle eine Einladung und öffne sie in der Sammler-App.</p>';syncSourceForms();}
$('server-url').value=location.origin;
$('invite-form').onsubmit=event=>{event.preventDefault();act(event.submitter,async()=>{const invitation=await api('/invitations',{url:$('server-url').value});$('invitation').value=JSON.stringify(invitation);$('invitation-panel').hidden=false;});};
$('copy').onclick=event=>act(event.target,async()=>{await navigator.clipboard.writeText($('invitation').value);event.target.textContent='Einladung kopiert';});
$('refresh').onclick=event=>act(event.target,refresh);
$('devices').onchange=event=>{if(['mode','model'].includes(event.target.name))syncSourceForm(event.target.form);};
$('devices').onsubmit=event=>{if(!event.target.dataset.source)return;event.preventDefault();act(event.submitter,async()=>{const form=event.target;const analyze=form.elements.mode.value==='analyze';if(analyze&&!availableModels.some(model=>model.id===form.elements.model.value))throw new Error('Wähle ein verfügbares Modell.');await api(`/${form.dataset.device}/sources/${form.dataset.source}/configure`,{mode:form.elements.mode.value,model:analyze?form.elements.model.value:null,purpose:form.elements.purpose.value});await refresh();});};
$('devices').onclick=event=>{const button=event.target.closest('button');if(!button)return;if(button.hasAttribute('data-reload-models'))act(button,reloadModels);if(button.dataset.interview)act(button,async()=>{const sample=await api(`/${button.dataset.device}/sources/${button.dataset.interview}/interview`);const target=document.querySelector(`[data-samples="${CSS.escape(button.dataset.interview)}"]`);target.replaceChildren();for(const item of sample.samples){const heading=document.createElement('h4');heading.textContent=item.title;const text=document.createElement('pre');text.textContent=item.excerpt;target.append(heading,text);}const help=document.createElement('p');help.textContent='Was möchtest du aus dieser Quelle erfahren? Trage den Zweck unten ein oder führe das Quelleninterview im Gespräch. Regeln änderst du in der Sammler-App.';target.append(help);});if(button.dataset.confirm)act(button,async()=>{const device=currentDevices.find(d=>d.id===button.dataset.confirm);await api(`/${device.id}/confirm`,{fingerprint:device.fingerprint,attempt:device.attempt});await refresh();});if(button.dataset.state)act(button,async()=>{await api(`/${button.dataset.device}/state`,{status:button.dataset.state});await refresh();});if(button.dataset.items)act(button,async()=>{const items=await api(`/${button.dataset.device}/sources/${button.dataset.items}/items`);const target=document.querySelector(`[data-item-list="${CSS.escape(button.dataset.items)}"]`);target.replaceChildren();for(const item of items){const row=document.createElement('p');row.textContent=item.external_id+' ';const fetch=document.createElement('button');fetch.className='secondary';fetch.textContent='Original abrufen';fetch.onclick=()=>act(fetch,async()=>{const job=await api(`/${button.dataset.device}/jobs`,{type:'original',payload:{source_id:button.dataset.items,external_id:item.external_id,fingerprint:item.fingerprint}});originalJob=job.id;$('original-panel').hidden=false;$('original-content').textContent='';$('original-state').textContent='Der Sammler prüft die angeforderte Version.';await pollOriginal();});row.append(fetch);target.append(row);}if(!items.length)target.textContent='Noch keine verarbeiteten Einträge.';});};
async function pollOriginal(){if(!originalJob)return;const id=originalJob;const job=await api('/jobs/'+id);if(id!==originalJob)return;if(job.status==='complete'){originalJob=null;const result=job.result;$('original-state').textContent=result.status==='version_mismatch'?'Das Original hat sich geändert. Diese Version passt nicht zum gespeicherten Wissen.':result.content?'Die angeforderte Version ist verfügbar.':'Der Sammler kann dieses Original nicht liefern.';$('original-content').textContent=result.content||'';}else if(['expired','cancelled'].includes(job.status)){originalJob=null;$('original-state').textContent='Der Abruf ist abgelaufen oder abgebrochen.';}else setTimeout(()=>pollOriginal().catch(error=>{$('original-state').textContent=error.message;}),2000);}
$('close-original').onclick=event=>act(event.target,async()=>{if(originalJob)await api('/jobs/'+originalJob+'/cancel',{});originalJob=null;$('original-content').textContent='';$('original-panel').hidden=true;});
api('/identity').then(identity=>{$('server-fingerprint').textContent=identity.fingerprint;$('server-key').textContent=identity.public_key;}).catch(()=>{});
refresh().catch(error=>{$('error').textContent=error.message;$('error').hidden=false;});
// Refresh pending pairing state, but preserve source forms while a user edits them.
setInterval(()=>{if(!document.hidden&&!$('devices').contains(document.activeElement)&&!currentDevices.some(d=>d.sources?.length))refresh().catch(()=>{});},5000);
