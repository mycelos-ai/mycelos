/* Model configuration shared by settings and chat. No completion is sent here. */
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const taskNames={execution:'Gespräch und allgemeine Aufgaben',source_analysis:'Wissensaufbereitung',source_relations:'Verknüpfung',classification:'Weitere Hintergrundaufgaben'};
  const efforts={default:'Modellstandard',none:'Aus',low:'Wenig',medium:'Mittel',high:'Viel'};
  const tiers = {haiku:'Schnell',sonnet:'Ausgewogen',opus:'Gründlich'};
  class ModelSettings extends HTMLElement {
    constructor() {
      super(); this.attachShadow({mode:'open'});
      this.taskSettings = {}; this.data = null; this.providers = []; this.form = ''; this.draft = {}; this.catalog = [];
      this.error = ''; this.notice = ''; this.busy = false; this.testResults = {}; this.testingProvider = '';
      this.shadowRoot.addEventListener('input', e => { if(e.target.name) this.draft[e.target.name] = e.target.value; });
      this.shadowRoot.addEventListener('change', e => {
        if(e.target.name) this.draft[e.target.name] = e.target.value;
        if(e.target.name === 'provider') { this.draft={provider:e.target.value,tier:'sonnet',model_id:''}; this.catalog=[]; this.render(); }
        if(e.target.name === 'catalog' && e.target.value) {
          this.draft.model_id=e.target.value; this.draft.tier=this.catalog.find(m=>m.id===e.target.value)?.tier || 'sonnet'; this.render();
        }
      });
      this.shadowRoot.addEventListener('submit', e=>{e.preventDefault();this.save();});
      this.shadowRoot.addEventListener('click', e=>this.click(e));
      this.shared = e=>{if(e.detail!==this && !this.form && !this.busy)this.load();};
    }
    connectedCallback(){window.addEventListener('mycelos-models-changed',this.shared);this.load();}
    disconnectedCallback(){window.removeEventListener('mycelos-models-changed',this.shared);this.draft={};}
    async request(url,method='GET',body){
      const r=await fetch(url,{method,headers:{Accept:'application/json',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
      const data=await r.json().catch(()=>({}));
      if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:data.error||'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.');
      return data;
    }
    async load(){
      try{const [data,config,tasks,stt]=await Promise.all([this.request('/api/models'),this.request('/api/models/settings'),this.request('/api/models/task-settings'),this.request('/api/models/stt')]);this.data=data;this.providers=config.providers;this.taskSettings=tasks;this.stt=stt;}
      catch(e){this.error=e.message;} this.render();
    }
    async click(e){
      const b=e.target.closest('button[data-action]');if(!b||this.busy)return;
      this.error='';this.notice='';const a=b.dataset.action;
      if(a==='discover'){
        this.busy=true;this.render();
        try{await this.discoverCustom();await this.load();}
        catch(e){this.error=e.message;}finally{this.busy=false;this.render();}return;
      }
      if(a==='test-provider'){
        const id=b.dataset.id;this.busy=true;this.testingProvider=id;delete this.testResults[id];this.render();
        try{this.testResults[id]=await this.request('/api/models/providers/'+encodeURIComponent(id)+'/test','POST');if(id==='custom'&&this.testResults[id].ok){await this.discoverCustom();await this.load();}}
        catch(e){this.testResults[id]={ok:false,message:e.message};}
        finally{this.busy=false;this.testingProvider='';this.render();}return;
      }
      if(a==='delete-provider'){
        this.busy=true;this.error='';this.form='';this.render();
        try{this.deletion=await this.request('/api/models/providers/'+encodeURIComponent(b.dataset.id)+'/delete-preview');}
        catch(e){this.error=e.message;}finally{this.busy=false;this.render();}return;
      }
      if(a==='cancel-delete'){this.deletion=null;this.render();return;}
      if(a==='confirm-delete'){
        if(this.busy||!this.deletion)return;
        this.busy=true;this.error='';this.render();
        try{
          await this.request('/api/models/providers/'+encodeURIComponent(this.deletion.provider),'DELETE',{token:this.deletion.token});
          this.deletion=null;this.notice='Anbieter gelöscht. Die Modellauswahl ist angepasst.';
          await this.load();window.dispatchEvent(new CustomEvent('mycelos-models-changed',{detail:this}));
        }catch(e){this.error=e.message;this.deletion=null;}
        finally{this.busy=false;this.render();}return;
      }
      if(a==='reload')return this.load();
      if(a==='cancel'){this.form='';this.draft={};this.render();return;}
      if(a==='provider'){this.form='provider';this.draft={provider:b.dataset.id||this.providers[0]?.id};this.render();return;}
      if(a==='model'){this.form='model';this.catalog=[];this.draft={provider:b.dataset.id||this.providers.find(p=>p.configured)?.id||'ollama',tier:'sonnet',model_id:''};this.render();return;}
      if(a==='stt'){this.form='stt';this.draft={stt_provider:this.stt?.provider||'',stt_model:this.stt?.model||'stt'};this.render();return;}
      if(a==='task'){this.form='task';this.draft={purpose:b.dataset.purpose,...this.taskSettings[b.dataset.purpose]};this.draft.fallbacks=(this.draft.fallbacks||[]).join('\n');this.render();return;}
      if(a==='assignment'){
        this.form='assignment';this.draft={agent:b.dataset.agent||'',purpose:b.dataset.purpose};
        const chain=this.data.assignments.filter(x=>(x.agent_id||'')===this.draft.agent&&x.purpose===this.draft.purpose).sort((a,b)=>a.priority-b.priority);
        this.draft.model_id=chain[0]?.model_id||'';this.draft.fallbacks=chain.slice(1).map(x=>x.model_id).join('\n');this.render();return;
      }
      if(a==='catalog'){
        this.busy=true;this.render();
        try{this.catalog=(await this.request('/api/models/catalog/'+encodeURIComponent(this.draft.provider))).models; if(!this.catalog.length)this.notice='Keine Modelle gefunden. Prüfe den Server oder gib die Modell-ID ein.';}
        catch(e){this.error=e.message;}finally{this.busy=false;this.render();}
      }
    }
    async discoverCustom(){
      const result=await this.request('/api/models/providers/custom/discover','POST');
      this.notice=result.count+' Modelle erkannt und verfügbar. Neue Modelle erhalten zunächst „Ausgewogen“. Die Modellzuweisungen bleiben unverändert.';
      window.dispatchEvent(new CustomEvent('mycelos-models-changed',{detail:this}));
      return result;
    }
    async save(){
      if(this.busy)return;this.busy=true;this.error='';this.notice='';this.render();
      try{
        const d=this.draft;
        if(this.form==='provider'){
          const provider=this.providers.find(p=>p.id===d.provider);
          const credentials=Object.fromEntries((provider.credential_fields||[]).map(f=>[f.key,d[f.key]||f.default||'']));
          await this.request('/api/models/providers/'+encodeURIComponent(d.provider),'PUT',{api_key:d.api_key||'',url:d.url||provider.url||provider.default_url,credentials});
          delete this.testResults[d.provider];
          this.notice='Anbieter gespeichert. Du kannst jetzt Modelle hinzufügen.';
          if(d.provider==='custom'){try{await this.discoverCustom();}catch(e){this.error='Anbieter gespeichert. '+e.message;}}
        }else if(this.form==='stt'){
          await this.request('/api/models/stt','PUT',{provider:d.stt_provider||'',model:d.stt_model||''});this.notice='Spracheingabe gespeichert.';
        }else if(this.form==='task'){
          await this.request('/api/models/task-settings/'+encodeURIComponent(d.purpose),'PUT',{reasoning:d.reasoning,max_output_tokens:d.max_output_tokens?Number(d.max_output_tokens):null,fallbacks:String(d.fallbacks||'').split('\n').map(x=>x.trim()).filter(Boolean)});this.notice='Einstellungen für die Aufgabe gespeichert.';
        }else if(this.form==='model'){
          await this.request('/api/models','POST',{provider:d.provider,model_id:d.model_id,tier:d.tier});this.notice='Modell gespeichert. Wähle unten, wofür myCelos es verwenden soll.';
        }else{
          const ids=[d.model_id,...String(d.fallbacks||'').split('\n')].map(x=>x.trim()).filter(Boolean);
          await this.request(d.agent?'/api/models/assignments/'+encodeURIComponent(d.agent):'/api/models/system-defaults','PUT',{purpose:d.purpose,model_ids:[...new Set(ids)]});
          this.notice='Modellauswahl gespeichert.';
        }
        this.form='';this.draft={};await this.load();window.dispatchEvent(new CustomEvent('mycelos-models-changed',{detail:this}));
      }catch(e){this.error=e.message;}finally{this.busy=false;this.render();}
    }
    field(label,name,value='',type='text'){return `<label>${esc(label)}<input name="${name}" type="${type}" value="${esc(value)}" autocomplete="${type==='password'?'new-password':'off'}"></label>`;}
    available(){const configured=new Set(this.providers.filter(p=>p.configured).map(p=>p.id));return (this.data?.models||[]).filter(m=>configured.has(m.provider)&&!/(embed|tts|transcrib|image|realtime|audio|sora|veo|imagen|container)/i.test(m.id));}
    render(){
      const d=this.draft,p=this.providers.find(p=>p.id===d.provider),disabled=this.busy?'disabled':'';
      let form='';
      if(this.form==='provider'||this.form==='model'){
        form=`<label>Anbieter<select name="provider">${this.providers.map(p=>`<option value="${esc(p.id)}" ${p.id===d.provider?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>`;
        if(this.form==='provider'){
          form+=p?.id==='custom'?this.field('Server-Adresse (API-Basis, meist mit /v1)','url',d.url||p.url||'')+this.field(p.configured?'Bearer-Token (leer lassen: behalten)':p.env_secret?'Bearer-Token (leer lassen: LITELLM_SECRET verwenden)':'Bearer-Token','api_key',d.api_key||'','password')+ '<p class="muted">Token ohne „Bearer “ eingeben. Beispiel-Adresse: http://localhost:4000/v1. Für einen LiteLLM-Proxy liest myCelos auch LITELLM_HOST und LITELLM_SECRET aus der Umgebung, wenn hier nichts eingetragen ist.</p>':p?.id==='ollama'?this.field('Server-Adresse','url',d.url||p.url||p.default_url):p?.credential_fields?.length?p.credential_fields.map(f=>f.type==='select'?`<label>${esc(f.label)}<select name="${esc(f.key)}">${f.options.map(o=>`<option ${o===(d[f.key]||f.default)?'selected':''}>${esc(o)}</option>`).join('')}</select></label>`:f.type==='textarea'?`<label>${esc(f.label)}<textarea name="${esc(f.key)}" rows="4">${esc(d[f.key]||'')}</textarea></label>`:this.field(f.label,f.key,d[f.key]||f.default||'',f.type)).join(''):this.field(p?.configured?'Neuer API-Schlüssel (leer lassen: behalten)':'API-Schlüssel','api_key',d.api_key||'','password');
          form+='<p class="muted">Speichern sendet keine Anfrage an ein Sprachmodell.</p>';
        }else{
          form+=`<button type="button" data-action="catalog" ${disabled}>${p?.id==='ollama'?'Installierte Modelle laden':'Modellkatalog laden'}</button>`;
          if(this.catalog.length)form+=`<label>Modell aus dem Katalog<select name="catalog"><option value="">Bitte wählen</option>${this.catalog.map(m=>`<option value="${esc(m.id)}" ${m.id===d.model_id?'selected':''}>${esc(m.name||m.id)}</option>`).join('')}</select></label>`;
          form+=this.field('Modell-ID','model_id',d.model_id||'');
          form+=`<label>Leistungsstufe<select name="tier">${Object.entries(tiers).map(([k,v])=>`<option value="${k}" ${k===d.tier?'selected':''}>${v}</option>`).join('')}</select></label>`;
        }
      }else if(this.form==='stt'){
        const custom=this.providers.find(p=>p.id==='custom');
        form=`<label>Spracherkennung<select name="stt_provider"><option value="" ${!d.stt_provider?'selected':''}>Standard (${esc(this.stt?.env_default||'openai')})</option><option value="custom" ${d.stt_provider==='custom'?'selected':''}>Eigener Server (Custom-Anbieter)</option></select></label>`;
        form+=this.field('Modellname im Proxy (Alias)','stt_model',d.stt_model||'stt');
        form+=`<p class="muted">Der eigene Server ist derselbe wie beim Custom-Anbieter, zum Beispiel dein LiteLLM-Proxy. Die Aufnahme geht an /audio/transcriptions.${custom?.configured?'':' Richte zuerst den Custom-Anbieter ein.'}</p>`;
      }else if(this.form==='task'){
        form=`<p><strong>${esc(taskNames[d.purpose])}</strong></p><label>Denkschritte<select name="reasoning">${Object.entries(efforts).map(([key,label])=>`<option value="${key}" ${key===d.reasoning?'selected':''}>${label}</option>`).join('')}</select></label><p class="muted">Der Modellserver muss die Auswahl unterstützen. „Aus“ darf er nicht stillschweigend ignorieren.</p>`;
        form+=this.field('Ausgabelimit in Tokens · leer für Modellstandard','max_output_tokens',d.max_output_tokens||'','number');
        form+=`<p class="muted">Das Limit umfasst je nach Modell auch interne Denkschritte.</p><details><summary>Ersatzmodelle</summary><p class="muted">Eine registrierte Modell-ID pro Zeile. Bei nicht unterstützten Einstellungen oder vorübergehenden Fehlern versucht myCelos diese Modelle. Die Denkschritte bleiben gleich eingestellt.</p><textarea name="fallbacks" rows="3">${esc(d.fallbacks||'')}</textarea></details>`;
      }else if(this.form==='assignment'){
        form=`<label>Modell<select name="model_id"><option value="">${d.agent?'Systemauswahl verwenden':'Keine Auswahl'}</option>${this.available().map(m=>`<option value="${esc(m.id)}" ${m.id===d.model_id?'selected':''}>${esc(m.id)}</option>`).join('')}</select></label>`;
        if(d.model_id&&!this.available().some(m=>m.id===d.model_id))form+=`<p>Die bisherige Auswahl ${esc(d.model_id)} ist nicht verfügbar. Bitte ein Modell wählen.</p>`;
        form+=`<details><summary>Ersatzmodelle</summary><p class="muted">Eine registrierte Modell-ID pro Zeile. myCelos versucht sie der Reihe nach bei einem Fehler.</p><textarea name="fallbacks" rows="3">${esc(d.fallbacks||'')}</textarea></details>`;
      }
      const slot=(title,agent,purpose)=>{const chain=(this.data?.assignments||[]).filter(x=>(x.agent_id||'')===agent&&x.purpose===purpose);return `<div class="row"><div><strong>${title}</strong><p>${esc(chain.sort((a,b)=>a.priority-b.priority).map(x=>x.model_id).join(' → ')||(agent?'Systemauswahl':'Noch kein Modell'))}</p></div><button data-action="assignment" data-agent="${agent}" data-purpose="${purpose}" aria-label="Modell für ${title} ändern">Ändern</button></div>`;};
      this.shadowRoot.innerHTML=`<style>
      :host{display:block;color:#233638;font:15px/1.5 system-ui,sans-serif}*{box-sizing:border-box}h2{font-size:23px;margin:0 0 6px}h3{font-size:17px;margin:28px 0 10px}p{margin:4px 0 12px;overflow-wrap:anywhere}.muted,.row p{color:#536568;font-size:14px}button,input,select,textarea{font:inherit}button{cursor:pointer;background:#fff;color:#17595c;border:1px solid #b8cbcc;border-radius:8px;padding:9px 13px;white-space:nowrap}button:hover{background:#edf4f3}button:disabled{opacity:.55;cursor:wait}button.primary{background:#176366;color:white;border-color:#176366}a{color:#176366}:focus-visible{outline:3px solid #4a9297;outline-offset:3px}.row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 0;border-bottom:1px solid #e0e8e7}.row>div{min-width:0}.row p{margin:3px 0 0}.actions{display:flex;gap:8px;flex-wrap:wrap}.error{color:#9b2929;background:#fff0ef;padding:12px;border-radius:8px}.notice{color:#17595c;background:#edf5f3;padding:12px;border-radius:8px}form{background:#f4f8f7;padding:20px;border-radius:12px;margin:16px 0}label{display:block;font-weight:600;margin:12px 0}input,select,textarea{display:block;width:100%;min-width:0;border:1px solid #aebfc0;border-radius:7px;padding:10px;background:#fff;color:#233638;margin-top:5px}summary{cursor:pointer;padding:10px 0}fieldset{border:0;padding:0;margin:0} @media(max-width:520px){.row{align-items:flex-start}.row .actions{flex-direction:column}form{padding:14px}}
      </style><h2>Modelle</h2><p class="muted">Lege fest, welche Modelle dein Wissen verarbeiten.</p>
      ${this.error?`<p class="error" role="alert">${esc(this.error)}</p>`:''}${this.notice?`<p class="notice" role="status">${esc(this.notice)}</p>`:''}
      ${!this.data?'<button data-action="reload">Erneut laden</button>':`<h3>Anbieter</h3>${this.providers.filter(p=>p.configured).map(p=>`<div class="row"><div><strong>${esc(p.name)}</strong><p>${['ollama','custom'].includes(p.id)?esc(p.url):'Zugang gespeichert'}</p>${this.testingProvider===p.id?'<p role="status">Verbindung wird geprüft …</p>':this.testResults[p.id]?`<p role="status" ${this.testResults[p.id].ok?'':'style="color:var(--danger,#b42318)"'}>${esc(this.testResults[p.id].message)}</p>`:''}</div><div class="actions">${p.id==='custom'?`<button data-action="discover" ${disabled}>Modelle laden</button>`:''}<button data-action="test-provider" data-id="${esc(p.id)}" aria-label="${esc(p.name)} testen" ${disabled}>Testen</button><button data-action="provider" data-id="${esc(p.id)}" ${disabled}>Bearbeiten</button><button data-action="delete-provider" data-id="${esc(p.id)}" aria-label="${esc(p.name)} löschen" ${disabled}>Löschen</button></div></div>`).join('')||'<p>Noch kein Anbieter eingerichtet.</p>'}<div class="actions" style="margin-top:12px"><button data-action="provider">Anbieter hinzufügen</button><button data-action="model">Modell hinzufügen</button></div>`}
      ${this.deletion?`<section class="panel" role="region" aria-label="Anbieter löschen"><h3>${esc(this.deletion.name)} löschen?</h3><p>myCelos entfernt den Zugang und ${this.deletion.models.length} registrierte Modelle.</p>${this.deletion.changes.length?`<p>Diese Zuweisungen verwenden den Anbieter noch. Nach dem Löschen nutzt myCelos die folgenden Ersatzmodelle. Dadurch können sich Kosten und Antwortqualität ändern.</p>${this.deletion.changes.map(c=>`<div class="row"><div><strong>${esc(c.label)}</strong><p>${esc(c.before.join(' → '))}</p><p>Danach: ${esc(c.after.join(' → ')||'Kein Ersatz verfügbar')}</p></div></div>`).join('')}`:'<p>Keine Modellzuweisung verwendet diesen Anbieter.</p>'}${this.deletion.blockers.map(message=>`<p role="alert">${esc(message)}</p>`).join('')}<div class="actions"><button data-action="cancel-delete" ${disabled}>Abbrechen</button><button data-action="confirm-delete" ${disabled} ${this.deletion.blockers.length?'disabled':''}>Anbieter endgültig löschen</button></div></section>`:''}
      ${this.form?`<form><fieldset ${disabled}><h3 style="margin-top:0">${this.form==='provider'?'Anbieter einrichten':this.form==='model'?'Modell hinzufügen':this.form==='task'?'Aufgabe einstellen':this.form==='stt'?'Spracheingabe einstellen':'Modell auswählen'}</h3>${form}<div class="actions"><button class="primary" type="submit">${this.busy?'Speichert …':'Speichern'}</button><button type="button" data-action="cancel">Abbrechen</button></div></fieldset></form>`:''}
      ${this.data?`<h3>Verwendung</h3>${this.data.agents.some(a=>a.id==='mycelos')?slot('Gespräch','mycelos','execution'):''}${slot('Systemauswahl','','execution')}${slot('Hintergrundaufgaben','','classification')}<p class="muted" style="margin-top:12px">Für eine Quellenanalyse wählst du das Modell direkt an der Quelle.</p><h3>Spracheingabe</h3><div class="row"><div><strong>Spracherkennung</strong><p>${this.stt?.provider==='custom'?'Eigener Server · '+esc(this.stt.model):'Standard ('+esc(this.stt?.env_default||'openai')+')'}</p></div><button data-action="stt" aria-label="Spracheingabe ändern">Ändern</button></div><h3>Denkschritte nach Aufgabe</h3>${Object.entries(taskNames).map(([key,label])=>`<div class="row"><div><strong>${label}</strong><p>${esc(efforts[this.taskSettings[key]?.reasoning]||'Modellstandard')}${this.taskSettings[key]?.max_output_tokens?' · '+esc(this.taskSettings[key].max_output_tokens)+' Ausgabetokens':''}</p></div><button data-action="task" data-purpose="${key}" aria-label="Denkschritte für ${label} ändern">Ändern</button></div>`).join('')}<p class="muted">Der Modellname kann beim Anbieter mehrere Modelle vertreten. Fehler stehen im Protokoll und in der Systemprüfung.</p><details><summary>Registrierte Modelle (${this.available().length})</summary>${this.available().map(m=>`<div class="row"><div>${esc(m.id)}<p>${tiers[m.tier]||esc(m.tier)}</p></div></div>`).join('')||'<p>Noch keine passenden Modelle eingerichtet.</p>'}</details><p><a href="/pages/ai-providers.html">Erweiterte Modellzuweisungen</a></p>`:''}`;
    }
  }
  customElements.define('mycelos-models',ModelSettings);
})();
