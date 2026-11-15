/* Shared source UI. All data comes from the same-origin source service. */
(() => {
  const stylesheet = new URL('local-sources.css', document.currentScript.src).href;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // Strings come from web.local_sources in the locale files (loaded by i18n.js).
  const T = (key, vars) => (typeof globalThis.t === 'function' ? globalThis.t('local_sources.' + key, vars) : key);
  const lang = () => window._mycelos_lang || document.documentElement.lang || 'en';
  const kindIds = ['obsidian', 'file', 'git', 'mercurial', 'folder', 'codex', 'claude'];
  const kindLabel = id => kindIds.includes(id) ? T('kind_' + id) : (id || '');
  const stateLabel = id => ['ready', 'queued', 'running', 'error', 'paused'].includes(id) ? T('state_' + id) : (id || '');
  const active = source => ['running', 'queued'].includes(source?.status);
  async function request(path = '', method = 'GET', body) {
    const response = await fetch('/api/local-sources' + path, {
      method, headers:{'Accept':'application/json', ...(body ? {'Content-Type':'application/json'} : {})},
      ...(body ? {body:JSON.stringify(body)} : {})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : '';
      throw new Error(detail || (response.status === 401 ? T('error_login') : T('error_action')));
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
      this.filing = null;
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
          } catch { this.models = []; this.modelError = T('error_models'); }
        }
        const data = sourceId ? await request('/' + encodeURIComponent(sourceId)) : await request();
        if (!this.isConnected || generation !== this.generation) return;
        if (sourceId) this.source = data; else this.sources = data;
        if (sourceId && !String(this.source.root).startsWith('collector://')) await this.loadChecks(generation, sourceId);
        if (sourceId) {
          // Shared source model: attachments and rule under local:<id>.
          try { this.filing = await request('/' + encodeURIComponent(sourceId) + '/filing'); }
          catch { this.filing = null; }
          if (!this.isConnected || generation !== this.generation) return;
        }
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
        if (status) status.textContent = T('rules_changed');
      } else if (name) this.draft[name] = value;
    }
    feedback() {
      return `<div role="alert" ${this.error ? '' : 'hidden'}>${esc(this.error)}</div><div role="status" aria-live="polite">${esc(this.busy ? T('busy') : this.notice)}</div>`;
    }
    render() {
      const focus = this.shadowRoot.activeElement?.dataset.action;
      this.shadowRoot.innerHTML = `<link rel="stylesheet" href="${esc(stylesheet)}"><section lang="${esc(lang())}" aria-label="${esc(T('sources_title'))}" aria-busy="${!!(this.busy || this.loading)}">
        ${this.loading ? `<p role="status">${T('loading')}</p>` : this.content()}
        ${this.feedback()}
      </section>`;
      if (this.busy) this.shadowRoot.querySelectorAll('button,input,textarea,select').forEach(el => el.disabled = true);
      if (focus) [...this.shadowRoot.querySelectorAll('[data-action]')].find(el => el.dataset.action === focus)?.focus({preventScroll:true});
    }
    button(action, label, primary = false, disabled = false) {
      return `<button type="button" data-action="${action}" ${disabled ? 'disabled' : ''} class="${primary ? 'primary' : ''}">${label}</button>`;
    }
    content() {
      if (this.mode === 'add') return `<div class="heading"><h3>${T('add_source')}</h3>${this.button('list',T('close'))}</div>
        <form data-form="inspect"><label>${T('kind')}<select name="kind" aria-label="${esc(T('kind'))}">${kindIds.map(id => `<option value="${id}" ${this.draft.kind === id ? 'selected' : ''}>${esc(kindLabel(id))}</option>`).join('')}</select></label>
        <label>${this.draft.kind === 'file' ? T('file_path') : T('folder_path')}<input name="root" required value="${esc(this.draft.root)}" placeholder="${esc(T('path_placeholder'))}" aria-describedby="path-help" autocomplete="off" spellcheck="false"></label>
        <p class="hint" id="path-help">${T('path_help')}</p>
        <button class="primary" type="submit">${T('view_contents')}</button></form>`;
      if (this.mode === 'review' || this.mode === 'edit') return this.editor();
      if (this.mode === 'card' && this.source) return this.card();
      return `<div class="heading"><h3>${T('sources_title')}</h3>${this.button('add',T('add_source'),true)}</div>
        ${this.sources.length ? `<div class="source-list">${this.sources.map(s => `<button type="button" data-source="${esc(s.id)}"><span><strong>${esc(s.name)}</strong><small>${esc(kindLabel(s.kind))}</small></span><span class="state">${esc(stateLabel(s.status))}</span><span aria-hidden="true">›</span></button>`).join('')}</div>` : `<p class="hint">${T('empty_hint')}</p>`}
        ${this.error ? this.button('reload',T('reload')) : ''}`;
    }
    editor() {
      const d = this.draft;
      const modelMissing = d.mode === 'analyze' && !this.models.some(m => m.id === d.model);
      return `<div class="heading"><h3>${this.mode === 'edit' ? T('edit_source') : T('what_matters')}</h3>${this.button(this.mode === 'edit' ? 'cancel-edit' : 'back',T('back'))}</div>
        <p class="path">${esc(d.root)}</p>${this.previewMarkup(this.result)}
        <form data-form="save">
        ${this.workflowMarkup(d.workflow, true)}
        <label>${T('name')}<input name="name" value="${esc(d.name)}" required maxlength="200"></label>
        <label>${T('purpose')}<textarea name="purpose" rows="2" placeholder="${esc(T('purpose_placeholder'))}">${esc(d.purpose)}</textarea></label>
        <label>${T('mode')}<select name="mode" aria-label="${esc(T('mode'))}"><option value="analyze" ${d.mode === 'analyze' ? 'selected' : ''}>${T('mode_analyze')}</option><option value="direct" ${d.mode === 'direct' ? 'selected' : ''}>${T('mode_direct')}</option></select></label>
        ${d.mode === 'analyze' ? `<label>${T('model')}<select name="model" aria-label="${esc(T('model'))}" required><option value="">${T('choose_model')}</option>${d.model && modelMissing ? `<option value="${esc(d.model)}" selected disabled>${esc(d.model)} · ${T('unavailable')}</option>` : ''}${this.models.map(m => `<option value="${esc(m.id)}" ${m.id === d.model ? 'selected' : ''}>${esc(m.name || m.id)}${m.provider ? ' · ' + esc(m.provider) : ''}</option>`).join('')}</select></label>
        ${d.model && modelMissing ? `<p class="hint">${T('model_missing')}</p>` : ''}
        ${!this.models.length ? `<p class="hint">${T('no_model')} <a href="/pages/settings.html#models">${T('setup_models')}</a></p>` : `<p class="hint">${T('model_receives')}</p>`}
        ${this.modelError ? `<p class="hint">${esc(this.modelError)} ${this.button('models',T('reload'))}</p>` : ''}` : `<p class="hint">${T('direct_hint')}</p>`}
        <p class="hint">${T('rules_reused')}</p>
        <button type="submit" class="primary" ${modelMissing ? 'disabled' : ''}>${this.mode === 'edit' ? T('save_changes') : T('save_source')}</button>
        <span class="hint">${T('import_separate')}</span></form>`;
    }
    workflowMarkup(workflow, editable = false) {
      if (!workflow) return '';
      const rules = key => (workflow[key] || []).join('\n');
      return `<div class="workflow"><h4>${T('rules_title')}</h4>
        <p class="hint">${esc(workflow.description || T('rules_default'))}</p>
        <details class="rules" ${editable && this.rulesOpen ? 'open' : ''}><summary>${editable ? T('adjust_selection') : T('view_selection')}</summary>
        <p class="hint" id="rules-help">${T('rules_help')}</p>
        ${editable ? `<label>${T('include')}<textarea name="include" aria-label="${esc(T('include'))}" rows="3" spellcheck="false" aria-describedby="rules-help">${esc(rules('include'))}</textarea></label>
        <label>${T('exclude')}<textarea name="exclude" aria-label="${esc(T('exclude'))}" rows="3" spellcheck="false" aria-describedby="rules-help">${esc(rules('exclude'))}</textarea></label>
        ${['folder','git','mercurial'].includes(this.draft.kind) ? `<label class="check-rule"><input type="checkbox" name="project_documents_only" ${workflow.project_documents_only ? 'checked' : ''}>${T('project_docs_only')}</label>
        <p class="hint" data-project-hint ${workflow.project_documents_only ? 'hidden' : ''}>${T('project_docs_more')}</p>` : ''}
        ${this.button('recheck', T('recheck'))}
        <p class="hint" data-rules-status aria-live="polite">${this.rulesChanged ? T('rules_changed') : T('check_no_model')}</p>` :
        `<p class="hint">${T('include')}</p><pre>${esc(rules('include') || T('no_patterns'))}</pre><p class="hint">${T('exclude')}</p><pre>${esc(rules('exclude') || T('no_patterns'))}</pre>`}
        ${!editable && ['folder','git','mercurial'].includes(this.source?.kind) ? `<p class="hint">${workflow.project_documents_only ? T('projects_docs_only') : T('projects_more')}</p>` : ''}
        ${workflow.steps?.length ? `<ol class="workflow-steps">${workflow.steps.map(step => `<li>${esc(step.description)}</li>`).join('')}</ol>` : ''}
        <p class="hint">${esc(T('template_line', {template: workflow.template_id, version: workflow.version}))}</p>
        </details></div>`;
    }
    originMarkup(origins) {
      const linkStates = ['resolved', 'attachment', 'external', 'excluded', 'outside', 'ambiguous', 'missing'];
      return origins.map(origin => {
        const metadata = origin.source_metadata || {};
        const properties = Object.entries(metadata.properties || {});
        const links = metadata.links || [];
        return `<details><summary>${T('origin')}</summary><p class="text">${esc(metadata.path || origin.external_id)}</p>
          ${properties.length ? `<dl>${properties.map(([key,value]) => `<dt>${esc(key)}</dt><dd class="text">${esc(typeof value === 'object' ? JSON.stringify(value) : value)}</dd>`).join('')}</dl>` : ''}
          ${links.length ? `<ul>${links.map(link => `<li><span class="text">${esc(link.reference)}</span> <span class="hint">· ${esc(linkStates.includes(link.state) ? T('link_' + link.state) : T('link_reference'))}</span></li>`).join('')}</ul>` : ''}
          ${links.some(link => link.state === 'attachment') ? `<p class="hint">${T('attachments_stay')}</p>` : ''}</details>`;
      }).join('');
    }
    previewMarkup(result) {
      if (!result) return '';
      if (result.articles) return `<div class="result"><h4>${T('knowledge_from_source')}</h4>${result.articles.length ? result.articles.map(a => `<details><summary>${esc(a.title)}</summary>
        <p><a href="/pages/knowledge.html?note=${encodeURIComponent(a.path)}">${T('open')}</a></p>
        ${a.relation_state === 'pending' ? `<p class="hint">${T('relations_pending')}</p>` : ''}
        ${(a.evidence || []).map(e => `<blockquote>${esc(e.quote)}</blockquote>`).join('')}
        ${(a.relations || []).map(r => `<p><a href="/pages/knowledge.html?note=${encodeURIComponent(r.to_path)}">${esc(['related','supports','contradicts','extends','duplicate'].includes(r.kind) ? T('relation_' + r.kind) : r.kind)}</a>: ${esc(r.reason)}</p>`).join('')}
        ${this.originMarkup(a.origins || [])}</details>`).join('') : `<p>${T('no_knowledge')}</p>`}
        ${result.articles.length === 200 ? `<p class="hint">${T('first_200')}</p>` : ''}</div>`;
      if (result.samples?.some(s => 'summary' in s)) return `<div class="result"><h4>${T('probe')}</h4>${result.samples.map(s => `<p class="text">${esc(s.summary)}</p>${(s.articles||[]).map(a=>`<details><summary>${esc(a.title)}</summary><p class="text">${esc(a.content)}</p>${a.evidence.map(e=>`<blockquote>${esc(e.quote)}</blockquote>`).join('')}</details>`).join('')}`).join('')}<p class="hint">${T('probe_hint')}</p></div>`;
      return `<div class="result"><p>${result.truncated ? esc(T('preview_limited')) + ' ' : ''}${result.counts_estimated ? esc(T('about')) + ' ' : ''}${esc(T(result.units === 1 ? 'units_one' : 'units_many', {count: result.units}))} · ${esc(T(result.files === 1 ? 'files_one' : 'files_many', {count: result.files}))}</p>
        <p class="hint">${result.units ? T('preview_by_rules') : result.truncated ? T('preview_none_in_part') : T('preview_none')}</p>
        ${result.inventory_truncated ? `<p class="hint">${T('inventory_limit')}</p>` : result.truncated ? `<p class="hint">${T('preview_excerpts')}</p>` : ''}
        ${(result.samples || []).map(s => `<details><summary>${esc((s.path || s.files?.[0] || '').split('/').pop() || s.title)}</summary><p class="text">${esc(s.excerpt)}</p></details>`).join('')}</div>`;
    }
    filingMarkup() {
      const f = this.filing;
      if (!f) return '';
      const folders = (f.attachments || []).map(path => path ? esc(path) : esc(T('filing_root')));
      return `<div class="filing"><p class="hint"><strong>${esc(T('filing_title'))}</strong> ${folders.length ? folders.join(' · ') : esc(T('filing_none'))}</p>
        ${f.rule_text ? `<p class="hint">${esc(T('filing_rule'))} <span class="text">${esc(f.rule_text)}</span></p>` : ''}</div>`;
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
        if (generation === this.generation && sourceId === this.sourceId) this.checkError = T('error_checks');
      }
    }
    checkMarkup() {
      const c = this.checkStatus;
      const date = value => value ? esc(new Date(value).toLocaleString(lang())) : esc(T('not_yet'));
      const interval = this.scheduleDraft ?? c?.interval_minutes ?? 0;
      const label = id => ['idle', 'queued', 'running', 'error'].includes(id) ? T('check_' + id) : '';
      const change = id => ['new', 'changed', 'removed', 'reprocess'].includes(id) ? T('change_' + id) : id;
      return `<div class="source-checks" aria-label="${esc(T('checks_label'))}">
        <div class="heading"><h4>${T('checks_title')}</h4><span class="state" role="status">${esc(label(c?.status))}</span></div>
        <p class="hint">${T('checks_hint')}</p>
        ${this.checkError ? `<p class="error">${esc(this.checkError)}</p>${this.button('reload-checks',T('reload'))}` : ''}
        ${c ? `<p class="hint">${esc(T('last_check'))} ${date(c.last_check)}<br>${esc(T('next_check'))} ${this.source.status === 'paused' ? esc(T('source_paused')) : c.next_check ? date(c.next_check) : esc(T('on_request'))}</p>
        ${c.error ? `<p class="error">${esc(c.error)} · ${esc(T('failures_in_row', {count: c.failure_count}))}</p>` : ''}
        <p class="hint">${esc(T('counts_pending', {pending: c.pending_count, removed: c.removed_count, checked: c.checked_units}))}</p>
        <p class="hint">${esc(T('counts_changes', {changes: c.pending_count - (c.reprocess_count || 0), reprocess: c.reprocess_count || 0}))}</p>
        <p class="hint">${esc(T('counts_relations', {count: c.relations_pending_count || 0}))}</p>` : ''}
        ${this.button('check',T('check_now'),true,active(this.source) || active(c) || this.source.status === 'paused')}
        <details class="check-schedule" ${this.scheduleOpen ? 'open' : ''}><summary>${T('schedule')}</summary>
        <form data-form="check-schedule"><label>${T('interval')}<select name="interval_minutes">${[0,15,60,360,1440].map(value => `<option value="${value}" ${interval === value ? 'selected' : ''}>${T('interval_' + value)}</option>`).join('')}</select></label>
        <button type="submit">${T('save_schedule')}</button></form>
        <p class="hint">${T('schedule_hint')}</p></details>
        ${this.pendingItems.length ? `<details class="pending" ${this.pendingOpen ? 'open' : ''}><summary>${T('view_pending')}</summary>
        <p class="hint">${T('pending_hint')}</p>
        <ul>${this.pendingItems.slice(0,200).map(item => `<li><strong>${esc(item.title || item.external_id)}</strong><span class="state">${esc(change(item.change))}</span>${item.project ? `<small>${esc(item.project)}</small>` : ''}${item.files?.length ? `<small>${esc(item.files.join(' · '))}</small>` : ''}</li>`).join('')}</ul></details>` : ''}
      </div>`;
    }
    card() {
      const s = this.source;
      if (String(s.root).startsWith('collector://')) return `<div class="heading"><h3>${esc(s.name)}</h3><span class="state">${esc(stateLabel(s.status))}</span></div><p>${T('collector_source')}</p><p><a href="/pages/collectors.html">${T('manage_collectors')}</a></p>${this.filingMarkup()}${this.workflowMarkup(s.workflow)}<div class="actions">${this.button('articles',T('view_knowledge'))}${this.button('list',T('all_sources'))}</div>${this.previewMarkup(this.result)}`;
      return `<div class="heading"><h3>${esc(s.name)}</h3><span class="state">${esc(stateLabel(s.status))}</span></div>
        <p class="path">${esc(kindLabel(s.kind))} · ${esc(s.root)}</p><p>${esc(s.purpose || T('no_purpose'))}</p>
        <p class="hint">${s.mode === 'analyze' ? esc(T('mode_articles')) + ' · ' + esc(s.model) : esc(T('mode_direct_short'))} · ${esc(T('on_request'))}</p>
        ${this.filingMarkup()}
        ${this.workflowMarkup(s.workflow)}
        ${this.checkMarkup()}
        <p class="hint">${T('import_hint')}</p>
        <p class="hint">${T('import_cost_hint')}</p>
        ${s.last_run ? `<p class="hint">${esc(T('last_import'))} ${esc(s.last_run)}</p>` : ''}
        ${s.error ? `<p class="error">${esc(s.error)}</p>` : ''}
        <div class="actions">${s.status === 'paused' ? this.button('resume',T('resume'),true) : this.button('import',s.last_run ? T('import_again') : T('import_start'),false,active(s) || active(this.checkStatus))}
        ${this.button('edit',T('edit'),false,active(s))}
        <details class="more"><summary aria-label="${esc(T('more_actions'))}" title="${esc(T('more_actions'))}">···</summary><div>${this.button('articles',T('view_knowledge'))}${this.button('preview',T('view_contents'))}${s.mode === 'analyze' ? this.button('probe',T('probe')) : ''}${s.status !== 'paused' ? this.button('pause',T('pause'),false,active(s)) : ''}${this.button('list',T('all_sources'))}</div></details></div>
        ${this.previewMarkup(this.result)}`;
    }
    async work(fn) {
      if (this.busy) return;
      this.busy = true; this.error = ''; this.notice = ''; this.render();
      try { await fn(); } catch (error) { this.error = error.message || T('error_connection'); }
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
          this.notice = T('schedule_saved');
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
        this.notice = T('saved');
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
        this.notice = T('check_queued_notice');
        this.announce();
      });
      if (action === 'back') { this.mode = 'add'; this.render(); }
      if (action === 'edit') { this.draft = JSON.parse(JSON.stringify(this.source)); this.rulesOpen = false; this.rulesChanged = false; this.mode = 'edit'; this.result = null; this.notice = ''; this.render(); }
      if (action === 'recheck') await this.work(async () => {
        const {kind, root, workflow} = this.draft;
        this.result = await request('/inspect', 'POST', {kind, root, workflow});
        if (this.result.workflow) this.draft.workflow = this.result.workflow;
        this.rulesChanged = false;
        this.notice = T('selection_checked');
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
          this.notice = action === 'import' ? T('import_queued') : action === 'pause' ? T('paused_notice') : T('resumed_notice');
          this.announce();
        }
      });
    }
  }
  customElements.define('mycelos-sources', SourceCard);
})();
