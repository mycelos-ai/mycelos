/* Shared source UI. All data comes from the same-origin source service. */
(() => {
  const stylesheet = new URL('local-sources.css', document.currentScript.src).href;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const kinds = {obsidian:'Obsidian-Vault', file:'Datei', git:'Git-Projekt', mercurial:'Mercurial-Projekt', folder:'Ordner', codex:'Codex-Gespräche', claude:'Claude-Code-Gespräche'};
  const states = {ready:'Bereit', queued:'Wartet auf Verarbeitung', running:'Import läuft', error:'Import fehlgeschlagen', paused:'Pausiert'};
  const active = source => ['running', 'queued'].includes(source?.status);
  async function request(path = '', method = 'GET', body) {
    const response = await fetch('/api/local-sources' + path, {
      method, headers:{'Accept':'application/json', ...(body ? {'Content-Type':'application/json'} : {})},
      ...(body ? {body:JSON.stringify(body)} : {})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : '';
      throw new Error(detail || (response.status === 401 ? 'Bitte melde dich erneut an.' : 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.'));
    }
    return data;
  }
  class SourceCard extends HTMLElement {
    static get observedAttributes() { return ['source-id', 'view']; }
    constructor() {
      super();
      this.attachShadow({mode:'open'});
      this.models = [];
      this.modelError = '';
      this.sources = [];
      this.mode = 'list';
      this.draft = {};
      this.error = '';
      this.notice = '';
      this.busy = false;
      this.result = null;
      this.onSharedChange = () => {
        if (!this.busy && !['edit','add','review'].includes(this.mode)) this.load(false);
      };
      this.shadowRoot.addEventListener('click', event => this.click(event));
      this.shadowRoot.addEventListener('submit', event => this.submit(event));
      this.shadowRoot.addEventListener('input', event => this.capture(event));
      this.shadowRoot.addEventListener('toggle', event => {
        if (event.target.classList?.contains('rules')) this.rulesOpen = event.target.open;
        if (event.target.classList?.contains('check-schedule')) this.scheduleOpen = event.target.open;
        if (event.target.classList?.contains('pending')) this.pendingOpen = event.target.open;
      }, true);
      this.shadowRoot.addEventListener('change', event => {
        this.capture(event);
        if (['kind','mode','model'].includes(event.target.name)) { const name = event.target.name; this.render(); this.shadowRoot.querySelector('[name="' + name + '"]')?.focus(); }
      });
    }
    connectedCallback() {
      window.addEventListener('mycelos-sources-changed', this.onSharedChange);
      this.start();
    }
    disconnectedCallback() {
      window.removeEventListener('mycelos-sources-changed', this.onSharedChange);
      clearTimeout(this.timer);
      this.generation = (this.generation || 0) + 1;
    }
    attributeChangedCallback(name, oldValue, newValue) {
      if (this.isConnected && oldValue !== newValue) this.start();
    }
    start() {
      this.sourceId = this.getAttribute('source-id') || '';
      this.mode = this.sourceId ? 'card' : this.getAttribute('view') === 'add' ? 'add' : 'list';
      this.source = null;
      this.result = null;
      this.error = '';
      this.notice = '';
      this.rulesOpen = false;
      this.resetChecks();
      this.rulesChanged = false;
      this.draft = {kind:'folder', root:'', name:'', purpose:'', mode:'analyze', model:''};
      this.load(true);
    }
    async load(withModels = false) {
      const generation = this.generation = (this.generation || 0) + 1;
      const sourceId = this.sourceId;
      if (withModels || (this.mode === 'card' && !this.source)) { this.loading = true; this.render(); }
      try {
        if (withModels) {
          this.loading = true;
          this.render();
          try {
            const responses = await Promise.all(['/api/models', '/api/models/settings'].map(path => fetch(path, {headers:{Accept:'application/json'}})));
            if (responses.some(response => !response.ok)) throw new Error();
            const [data, settings] = await Promise.all(responses.map(response => response.json()));
            const configured = new Set((settings.providers || []).filter(provider => provider.configured).map(provider => provider.id));
            this.models = (data.models || []).filter(model => configured.has(model.provider)
              && model.enabled !== false && model.enabled !== 0
              && !/(embed|tts|transcrib|image|realtime|audio|sora|veo|imagen|container)/i.test(model.id));
            this.modelError = '';
          } catch { this.models = []; this.modelError = 'Die Modellliste ist nicht erreichbar. Bitte erneut laden.'; }
        }
        const data = sourceId ? await request('/' + encodeURIComponent(sourceId)) : await request();
        if (!this.isConnected || generation !== this.generation) return;
        if (sourceId) this.source = data; else this.sources = data;
        if (sourceId && !String(this.source.root).startsWith('collector://')) await this.loadChecks(generation, sourceId);
        this.error = '';
      } catch (error) {
        if (generation !== this.generation) return;
        this.error = error.message;
      } finally {
        if (this.isConnected && generation === this.generation) {
          this.loading = false;
          this.render();
          this.poll();
        }
      }
    }
    poll() {
      clearTimeout(this.timer);
      if (this.isConnected && (active(this.source) || active(this.checkStatus) || this.checkStatus?.interval_minutes > 0 || this.sources.some(active))) {
        this.timer = setTimeout(() => {
          if (!this.busy && !['add','review','edit'].includes(this.mode)) this.load(false);
          else this.poll();
        }, active(this.source) || active(this.checkStatus) || this.sources.some(active) ? 2500 : 15000);
      }
    }
    capture(event) {
      const {name, value} = event.target;
      if (name === 'interval_minutes') { this.scheduleDraft = Number(value); return; }
      if (['include', 'exclude', 'project_documents_only'].includes(name) && this.draft.workflow) {
        this.draft.workflow[name] = name === 'project_documents_only' ? event.target.checked : value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
        const projectHint = this.shadowRoot.querySelector('[data-project-hint]');
        if (projectHint) projectHint.hidden = this.draft.workflow.project_documents_only;
        this.rulesChanged = true;
        this.result = null;
        this.shadowRoot.querySelector('.result')?.remove();
        const status = this.shadowRoot.querySelector('[data-rules-status]');
        if (status) status.textContent = 'Die Regeln sind geändert. Prüfe die Auswahl erneut.';
      } else if (name) this.draft[name] = value;
    }
    feedback() {
      return `<div role="alert" ${this.error ? '' : 'hidden'}>${esc(this.error)}</div><div role="status" aria-live="polite">${esc(this.busy ? 'Einen Moment …' : this.notice)}</div>`;
    }
    render() {
      const focus = this.shadowRoot.activeElement?.dataset.action;
      this.shadowRoot.innerHTML = `<link rel="stylesheet" href="${esc(stylesheet)}"><section lang="de" aria-label="Wissensquellen" aria-busy="${!!(this.busy || this.loading)}">
        ${this.loading ? '<p role="status">Quellen laden …</p>' : this.content()}
        ${this.feedback()}
      </section>`;
      if (this.busy) this.shadowRoot.querySelectorAll('button,input,textarea,select').forEach(el => el.disabled = true);
      if (focus) [...this.shadowRoot.querySelectorAll('[data-action]')].find(el => el.dataset.action === focus)?.focus({preventScroll:true});
    }
    button(action, label, primary = false, disabled = false) {
      return `<button type="button" data-action="${action}" ${disabled ? 'disabled' : ''} class="${primary ? 'primary' : ''}">${label}</button>`;
    }
    content() {
      if (this.mode === 'add') return `<div class="heading"><h3>Quelle hinzufügen</h3>${this.button('list','Schließen')}</div>
        <form data-form="inspect"><label>Quellenart<select name="kind" aria-label="Quellenart">${Object.entries(kinds).map(([id,label]) => `<option value="${id}" ${this.draft.kind === id ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
        <label>${this.draft.kind === 'file' ? 'Dateipfad' : 'Ordnerpfad'}<input name="root" required value="${esc(this.draft.root)}" placeholder="/Pfad/zu/deinem/Projekt" aria-describedby="path-help" autocomplete="off" spellcheck="false"></label>
        <p class="hint" id="path-help">Ein Pfad auf dem Gerät, auf dem myCelos läuft. Der Browser lädt hier keine Dateien hoch.</p>
        <button class="primary" type="submit">Inhalte ansehen</button></form>`;
      if (this.mode === 'review' || this.mode === 'edit') return this.editor();
      if (this.mode === 'card' && this.source) return this.card();
      return `<div class="heading"><h3>Wissensquellen</h3>${this.button('add','Quelle hinzufügen',true)}</div>
        ${this.sources.length ? `<div class="source-list">${this.sources.map(s => `<button type="button" data-source="${esc(s.id)}"><span><strong>${esc(s.name)}</strong><small>${esc(kinds[s.kind])}</small></span><span class="state">${esc(states[s.status] || s.status)}</span><span aria-hidden="true">›</span></button>`).join('')}</div>` : '<p class="hint">Füge eine Datei, einen Projektordner oder deine Gespräche hinzu.</p>'}
        ${this.error ? this.button('reload','Erneut laden') : ''}`;
    }
    editor() {
      const d = this.draft;
      const modelMissing = d.mode === 'analyze' && !this.models.some(m => m.id === d.model);
      return `<div class="heading"><h3>${this.mode === 'edit' ? 'Quelle bearbeiten' : 'Was ist dir wichtig?'}</h3>${this.button(this.mode === 'edit' ? 'cancel-edit' : 'back','Zurück')}</div>
        <p class="path">${esc(d.root)}</p>${this.previewMarkup(this.result)}
        <form data-form="save">
        ${this.workflowMarkup(d.workflow, true)}
        <label>Name<input name="name" value="${esc(d.name)}" required maxlength="200"></label>
        <label>Was möchtest du daraus erfahren?<textarea name="purpose" rows="2" placeholder="Zum Beispiel: Architektur und wichtige Entscheidungen.">${esc(d.purpose)}</textarea></label>
        <label>Verarbeitung<select name="mode" aria-label="Verarbeitung"><option value="analyze" ${d.mode === 'analyze' ? 'selected' : ''}>Zusammenfassung erstellen</option><option value="direct" ${d.mode === 'direct' ? 'selected' : ''}>Text direkt übernehmen</option></select></label>
        ${d.mode === 'analyze' ? `<label>Modell<select name="model" aria-label="Modell" required><option value="">Modell wählen</option>${d.model && modelMissing ? `<option value="${esc(d.model)}" selected disabled>${esc(d.model)} · Nicht verfügbar</option>` : ''}${this.models.map(m => `<option value="${esc(m.id)}" ${m.id === d.model ? 'selected' : ''}>${esc(m.name || m.id)}${m.provider ? ' · ' + esc(m.provider) : ''}</option>`).join('')}</select></label>
        ${d.model && modelMissing ? '<p class="hint">Das gespeicherte Modell ist nicht verfügbar. Wähle ein eingerichtetes Textmodell.</p>' : ''}
        ${!this.models.length ? '<p class="hint">Kein Modell eingerichtet. <a href="/pages/settings.html#models">Modelle einrichten</a></p>' : '<p class="hint">Das gewählte Modell erhält den Inhalt zur Analyse.</p>'}
        ${this.modelError ? `<p class="hint">${esc(this.modelError)} ${this.button('models','Erneut laden')}</p>` : ''}` : '<p class="hint">Der Originaltext bleibt erhalten. myCelos erstellt dabei keine zusätzliche Zusammenfassung.</p>'}
        <p class="hint">myCelos prüft Änderungen auf Auftrag. Jeder weitere Import nutzt dieselben gespeicherten Regeln.</p>
        <button type="submit" class="primary" ${modelMissing ? 'disabled' : ''}>${this.mode === 'edit' ? 'Änderungen speichern' : 'Quelle speichern'}</button>
        <span class="hint">Der Import startet separat.</span></form>`;
    }
    workflowMarkup(workflow, editable = false) {
      if (!workflow) return '';
      const rules = key => (workflow[key] || []).join('\n');
      return `<div class="workflow"><h4>Regeln für diese Quelle</h4>
        <p class="hint">${esc(workflow.description || 'Diese Regeln bestimmen die Auswahl der Texte.')}</p>
        <details class="rules" ${editable && this.rulesOpen ? 'open' : ''}><summary>${editable ? 'Auswahl anpassen' : 'Gespeicherte Auswahl ansehen'}</summary>
        <p class="hint" id="rules-help">Ein Pfadmuster pro Zeile, ausgehend vom Quellenpfad. Beispiel: **/*.md für Markdown-Dateien in allen Unterordnern.</p>
        ${editable ? `<label>Einbeziehen<textarea name="include" aria-label="Einbeziehen" rows="3" spellcheck="false" aria-describedby="rules-help">${esc(rules('include'))}</textarea></label>
        <label>Ausschließen<textarea name="exclude" aria-label="Ausschließen" rows="3" spellcheck="false" aria-describedby="rules-help">${esc(rules('exclude'))}</textarea></label>
        ${['folder','git','mercurial'].includes(this.draft.kind) ? `<label class="check-rule"><input type="checkbox" name="project_documents_only" ${workflow.project_documents_only ? 'checked' : ''}>In Projekten nur README, Architektur und Dokumentation auswählen</label>
        <p class="hint" data-project-hint ${workflow.project_documents_only ? 'hidden' : ''}>Die Auswahl umfasst auch weitere Markdown-, Text- und PDF-Dateien. Programmcode bleibt ausgeschlossen.</p>` : ''}
        ${this.button('recheck', 'Auswahl erneut prüfen')}
        <p class="hint" data-rules-status aria-live="polite">${this.rulesChanged ? 'Die Regeln sind geändert. Prüfe die Auswahl erneut.' : 'Die Prüfung nutzt kein Sprachmodell.'}</p>` :
        `<p class="hint">Einbeziehen</p><pre>${esc(rules('include') || 'Keine Muster')}</pre><p class="hint">Ausschließen</p><pre>${esc(rules('exclude') || 'Keine Muster')}</pre>`}
        ${!editable && ['folder','git','mercurial'].includes(this.source?.kind) ? `<p class="hint">${workflow.project_documents_only ? 'In Projekten: nur README, Architektur und Dokumentation.' : 'In Projekten: auch weitere Markdown-, Text- und PDF-Dateien. Programmcode bleibt ausgeschlossen.'}</p>` : ''}
        ${workflow.steps?.length ? `<ol class="workflow-steps">${workflow.steps.map(step => `<li>${esc(step.description)}</li>`).join('')}</ol>` : ''}
        <p class="hint">Vorlage: ${esc(workflow.template_id)} · Version ${esc(workflow.version)}. Änderungen an der Vorlage ändern diese Quelle nicht.</p>
        </details></div>`;
    }
    originMarkup(origins) {
      const linkStates = {resolved:'Notiz', attachment:'Anhang', external:'Externer Verweis', excluded:'Ausgeschlossen', outside:'Außerhalb der Quelle', ambiguous:'Nicht eindeutig', missing:'Nicht gefunden'};
      return origins.map(origin => {
        const metadata = origin.source_metadata || {};
        const properties = Object.entries(metadata.properties || {});
        const links = metadata.links || [];
        return `<details><summary>Herkunft</summary><p class="text">${esc(metadata.path || origin.external_id)}</p>
          ${properties.length ? `<dl>${properties.map(([key,value]) => `<dt>${esc(key)}</dt><dd class="text">${esc(typeof value === 'object' ? JSON.stringify(value) : value)}</dd>`).join('')}</dl>` : ''}
          ${links.length ? `<ul>${links.map(link => `<li><span class="text">${esc(link.reference)}</span> <span class="hint">· ${esc(linkStates[link.state] || 'Verweis')}</span></li>`).join('')}</ul>` : ''}
          ${links.some(link => link.state === 'attachment') ? '<p class="hint">Anhänge bleiben in der Quelle. myCelos zeigt hier den Dateiverweis.</p>' : ''}</details>`;
      }).join('');
    }
    previewMarkup(result) {
      if (!result) return '';
      if (result.articles) return `<div class="result"><h4>Wissen aus dieser Quelle</h4>${result.articles.length ? result.articles.map(a => `<details><summary>${esc(a.title)}</summary>
        <p><a href="/pages/knowledge.html?note=${encodeURIComponent(a.path)}">Öffnen</a></p>
        ${a.relation_state === 'pending' ? '<p class="hint">Querverbindungen noch offen</p>' : ''}
        ${(a.evidence || []).map(e => `<blockquote>${esc(e.quote)}</blockquote>`).join('')}
        ${(a.relations || []).map(r => `<p><a href="/pages/knowledge.html?note=${encodeURIComponent(r.to_path)}">${esc(({related:'Verwandt',supports:'Bestätigt',contradicts:'Widerspricht',extends:'Ergänzt',duplicate:'Gleiche Aussage'})[r.kind] || r.kind)}</a>: ${esc(r.reason)}</p>`).join('')}
        ${this.originMarkup(a.origins || [])}</details>`).join('') : '<p>Noch kein Wissen aus dieser Quelle vorhanden.</p>'}
        ${result.articles.length === 200 ? '<p class="hint">Die Ansicht zeigt die ersten 200 Einträge.</p>' : ''}</div>`;
      if (result.samples?.some(s => 'summary' in s)) return `<div class="result"><h4>Analyseprobe</h4>${result.samples.map(s => `<p class="text">${esc(s.summary)}</p>${(s.articles||[]).map(a=>`<details><summary>${esc(a.title)}</summary><p class="text">${esc(a.content)}</p>${a.evidence.map(e=>`<blockquote>${esc(e.quote)}</blockquote>`).join('')}</details>`).join('')}`).join('')}<p class="hint">Eine begrenzte Textprobe. Noch kein Wissenseintrag.</p></div>`;
      return `<div class="result"><p>${result.truncated ? 'Begrenzte Vorschau: ' : ''}${result.counts_estimated ? 'Etwa ' : ''}${esc(result.units)} ${result.units === 1 ? 'Inhaltseinheit' : 'Inhaltseinheiten'} · ${esc(result.files)} ${result.files === 1 ? 'Datei' : 'Dateien'}</p>
        ${result.units ? '<p class="hint">Auswahl anhand der Quellenregeln. Die Vorschau nutzt kein Sprachmodell.</p>' : result.truncated ? '<p class="hint">Im geprüften Teil fehlen passende Texte. Die Prüfung nutzt kein Sprachmodell.</p>' : '<p class="hint">Keine passenden Texte gefunden. Prüfe den Pfad, die Quellenart und die Regeln.</p>'}
        ${result.inventory_truncated ? '<p class="hint">Die Prüfung erreichte ihre Grenze. Die Zahlen zeigen nur den geprüften Teil der Quelle.</p>' : result.truncated ? '<p class="hint">Die Vorschau zeigt nur ausgewählte Textausschnitte. Der Import prüft alle passenden Dateien.</p>' : ''}
        ${(result.samples || []).map(s => `<details><summary>${esc((s.path || s.files?.[0] || '').split('/').pop() || s.title)}</summary><p class="text">${esc(s.excerpt)}</p></details>`).join('')}</div>`;
    }
    resetChecks() {
      this.checkStatus = null;
      this.checkError = '';
      this.pendingItems = [];
      this.scheduleDraft = null;
      this.scheduleOpen = false;
      this.pendingOpen = false;
    }
    async loadChecks(generation = this.generation, sourceId = this.sourceId) {
      try {
        const path = '/' + encodeURIComponent(sourceId);
        const [status, pending] = await Promise.all([request(path + '/check-status'), request(path + '/pending')]);
        if (!this.isConnected || generation !== this.generation || sourceId !== this.sourceId) return;
        this.checkStatus = status;
        this.pendingItems = pending;
        this.checkError = '';
      } catch (error) {
        if (generation === this.generation && sourceId === this.sourceId) this.checkError = 'Die Prüfwerte sind nicht erreichbar. Bitte erneut laden.';
      }
    }
    checkMarkup() {
      const c = this.checkStatus;
      const date = value => value ? esc(new Date(value).toLocaleString('de-AT')) : 'Noch nicht';
      const interval = this.scheduleDraft ?? c?.interval_minutes ?? 0;
      const labels = {idle:'Prüfung bereit',queued:'Prüfung vorgemerkt',running:'Prüfung läuft',error:'Prüfung fehlgeschlagen'};
      const changes = {new:'Neu',changed:'Geändert',removed:'Entfernt',reprocess:'Erneut verarbeiten'};
      return `<div class="source-checks" aria-label="Quellenprüfung">
        <div class="heading"><h4>Änderungen prüfen</h4><span class="state" role="status">${esc(labels[c?.status] || '')}</span></div>
        <p class="hint">Die Prüfung merkt Änderungen vor. Sie nutzt kein Modell und erzeugt keine Zusammenfassung. Sie ändert kein gespeichertes Wissen.</p>
        ${this.checkError ? `<p class="error">${esc(this.checkError)}</p>${this.button('reload-checks','Erneut laden')}` : ''}
        ${c ? `<p class="hint">Letzte Prüfung: ${date(c.last_check)}<br>Nächste Prüfung: ${this.source.status === 'paused' ? 'Quelle pausiert' : c.next_check ? date(c.next_check) : 'Auf Auftrag'}</p>
        ${c.error ? `<p class="error">${esc(c.error)} · Fehler in Folge: ${esc(c.failure_count)}. Bitte prüfe den Quellenpfad und versuche es erneut.</p>` : ''}
        <p class="hint">${esc(c.pending_count)} Inhalte vorgemerkt · ${esc(c.removed_count)} entfernt · ${esc(c.checked_units)} geprüft</p>
        <p class="hint">${esc(c.pending_count - (c.reprocess_count || 0))} Dateiänderungen · ${esc(c.reprocess_count || 0)} Inhalte erneut verarbeiten</p>
        <p class="hint">${esc(c.relations_pending_count || 0)} Artikel mit offenen Querverbindungen</p>` : ''}
        ${this.button('check','Jetzt prüfen',true,active(this.source) || active(c) || this.source.status === 'paused')}
        <details class="check-schedule" ${this.scheduleOpen ? 'open' : ''}><summary>Zeitplan</summary>
        <form data-form="check-schedule"><label>Prüfabstand<select name="interval_minutes">${[[0,'Nur auf Auftrag'],[15,'Alle 15 Minuten'],[60,'Jede Stunde'],[360,'Alle 6 Stunden'],[1440,'Täglich']].map(([value,label]) => `<option value="${value}" ${interval === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
        <button type="submit">Zeitplan speichern</button></form>
        <p class="hint">Die Prüfung läuft auch bei pausierter Modellautomatik. Eine pausierte Quelle bleibt ausgenommen.</p></details>
        ${this.pendingItems.length ? `<details class="pending" ${this.pendingOpen ? 'open' : ''}><summary>Vorgemerkte Inhalte ansehen</summary>
        <p class="hint">Die Liste zeigt höchstens 200 Inhalte. Entfernte Dateien löschen kein gespeichertes Wissen.</p>
        <ul>${this.pendingItems.slice(0,200).map(item => `<li><strong>${esc(item.title || item.external_id)}</strong><span class="state">${esc(changes[item.change] || item.change)}</span>${item.project ? `<small>${esc(item.project)}</small>` : ''}${item.files?.length ? `<small>${esc(item.files.join(' · '))}</small>` : ''}</li>`).join('')}</ul></details>` : ''}
      </div>`;
    }
    card() {
      const s = this.source;
      if (String(s.root).startsWith('collector://')) return `<div class="heading"><h3>${esc(s.name)}</h3><span class="state">${esc(states[s.status] || s.status)}</span></div><p>Diese Quelle liegt auf einem Sammler-Gerät. Der Sammler prüft Änderungen und liefert bekannte Originalversionen.</p><p><a href="/pages/collectors.html">Sammler und Verarbeitung verwalten</a></p>${this.workflowMarkup(s.workflow)}<div class="actions">${this.button('articles','Wissen ansehen')}${this.button('list','Alle Quellen')}</div>${this.previewMarkup(this.result)}`;
      return `<div class="heading"><h3>${esc(s.name)}</h3><span class="state">${esc(states[s.status] || s.status)}</span></div>
        <p class="path">${esc(kinds[s.kind])} · ${esc(s.root)}</p><p>${esc(s.purpose || 'Noch kein Zweck angegeben.')}</p>
        <p class="hint">${s.mode === 'analyze' ? 'Wissensartikel und Querverbindungen · ' + esc(s.model) : 'Direkte Textübernahme'} · Auf Auftrag</p>
        ${this.workflowMarkup(s.workflow)}
        ${this.checkMarkup()}
        <p class="hint">Der Import übernimmt neue und geänderte Inhalte. Er verarbeitet Inhalte bei geänderter Analyse erneut und ergänzt offene Querverbindungen.</p>
        <p class="hint">Der Import läuft auch bei pausierter Modellautomatik. Dabei können Modellkosten entstehen.</p>
        ${s.last_run ? `<p class="hint">Letzter Import: ${esc(s.last_run)}</p>` : ''}
        ${s.error ? `<p class="error">${esc(s.error)}</p>` : ''}
        <div class="actions">${s.status === 'paused' ? this.button('resume','Fortsetzen',true) : this.button('import',s.last_run ? 'Erneut importieren' : 'Import starten',false,active(s) || active(this.checkStatus))}
        ${this.button('edit','Bearbeiten',false,active(s))}
        <details class="more"><summary aria-label="Weitere Aktionen" title="Weitere Aktionen">···</summary><div>${this.button('articles','Wissen ansehen')}${this.button('preview','Inhalte ansehen')}${s.mode === 'analyze' ? this.button('probe','Analyseprobe') : ''}${s.status !== 'paused' ? this.button('pause','Pausieren',false,active(s)) : ''}${this.button('list','Alle Quellen')}</div></details></div>
        ${this.previewMarkup(this.result)}`;
    }
    async work(fn) {
      if (this.busy) return;
      this.busy = true; this.error = ''; this.notice = ''; this.render();
      try { await fn(); } catch (error) { this.error = error.message || 'Die Verbindung ist unterbrochen. Bitte erneut versuchen.'; }
      finally { this.busy = false; if (this.isConnected) { this.render(); this.poll(); } }
    }
    announce() { window.dispatchEvent(new CustomEvent('mycelos-sources-changed')); }
    async submit(event) {
      event.preventDefault();
      const form = event.target;
      if (form.dataset.form === 'check-schedule') {
        await this.work(async () => {
          const interval = Number(new FormData(form).get('interval_minutes'));
          this.checkStatus = await request('/' + encodeURIComponent(this.sourceId) + '/check-schedule', 'PUT', {interval_minutes:interval});
          this.scheduleDraft = null;
          this.notice = 'Zeitplan gespeichert.';
          this.announce();
        });
        return;
      }
      for (const [name, value] of new FormData(form)) {
        if (!['include','exclude','project_documents_only'].includes(name)) this.draft[name] = value;
      }
      if (form.dataset.form === 'inspect') await this.work(async () => {
        this.result = await request('/inspect', 'POST', {kind:this.draft.kind, root:this.draft.root});
        this.draft.workflow = this.result.workflow;
        this.rulesChanged = false;
        this.draft.name ||= this.draft.root.replace(/\/$/,'').split('/').pop();
        this.mode = 'review';
      });
      if (form.dataset.form === 'save') await this.work(async () => {
        const {name,purpose,mode,model,kind,root,workflow} = this.draft;
        const body = {name,purpose,mode,model:mode === 'analyze' ? model : null, ...(workflow ? {workflow} : {})};
        this.source = this.mode === 'edit' ? await request('/' + encodeURIComponent(this.sourceId),'PUT',body) : await request('','POST',{...body,kind,root});
        this.sourceId = this.source.id;
        this.resetChecks();
        await this.loadChecks();
        this.mode = 'card'; this.result = null;
        this.notice = 'Gespeichert.';
        this.announce();
      });
      this.shadowRoot.querySelector(this.error ? '[role="alert"]' : 'h3')?.setAttribute('tabindex','-1');
      this.shadowRoot.querySelector(this.error ? '[role="alert"]' : 'h3')?.focus({preventScroll:true});
    }
    async click(event) {
      const button = event.target.closest('button');
      if (!button || this.busy || button.disabled) return;
      if (button.dataset.source) {
        this.resetChecks();
        this.sourceId = button.dataset.source; this.source = null; this.mode = 'card'; this.result = null; this.notice = ''; await this.load(); return;
      }
      const action = button.dataset.action;
      if (action === 'add') { this.resetChecks(); this.generation++; this.loading = false; this.mode = 'add'; this.sourceId = ''; this.source = null; this.draft = {kind:'folder',root:'',name:'',purpose:'',mode:'analyze',model:''}; this.rulesOpen = false; this.rulesChanged = false; this.error = ''; this.notice = ''; this.render(); }
      if (action === 'list') { this.resetChecks(); this.mode = 'list'; this.sourceId = ''; this.source = null; this.result = null; this.notice = ''; await this.load(); }
      if (action === 'reload' || action === 'models') await this.load(true);
      if (action === 'reload-checks') await this.work(() => this.loadChecks());
      if (action === 'check') await this.work(async () => {
        this.checkStatus = await request('/' + encodeURIComponent(this.sourceId) + '/check', 'POST');
        this.notice = 'Prüfung vorgemerkt. Der Import startet separat.';
        this.announce();
      });
      if (action === 'back') { this.mode = 'add'; this.render(); }
      if (action === 'edit') { this.draft = JSON.parse(JSON.stringify(this.source)); this.rulesOpen = false; this.rulesChanged = false; this.mode = 'edit'; this.result = null; this.notice = ''; this.render(); }
      if (action === 'recheck') await this.work(async () => {
        const {kind, root, workflow} = this.draft;
        this.result = await request('/inspect', 'POST', {kind, root, workflow});
        if (this.result.workflow) this.draft.workflow = this.result.workflow;
        this.rulesChanged = false;
        this.notice = 'Auswahl geprüft. Die Quelle bleibt bis zum Speichern unverändert.';
      });
      if (action === 'cancel-edit') { this.mode = 'card'; this.error = ''; await this.load(); }
      if (['import','preview','probe','articles','pause','resume'].includes(action)) await this.work(async () => {
        const path = '/' + encodeURIComponent(this.sourceId);
        if (action === 'articles') { this.result = {articles:await request('/'+encodeURIComponent(this.source.id)+'/articles')}; }
        else if (action === 'preview' || action === 'probe') {
          this.result = await request(path + '/' + action, action === 'preview' ? 'GET' : 'POST');
        } else {
          await request(path + (action === 'import' ? '/import' : '/pause?paused=' + (action === 'pause')), 'POST');
          this.source = await request(path);
          this.notice = action === 'import' ? 'Import vorgemerkt.' : action === 'pause' ? 'Quelle pausiert.' : 'Quelle fortgesetzt. Der Import startet auf Auftrag.';
          this.announce();
        }
      });
    }
  }
  customElements.define('mycelos-sources', SourceCard);
})();
