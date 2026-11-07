/* Shared MCP connections. Queries and imports require separate user actions. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const styles = `
    :host{display:block;min-width:0;color:var(--on-surface,#202c30);font:400 15px/1.6 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    *{box-sizing:border-box}[hidden]{display:none!important}
    :host([embedded]) section{padding:0;border:0;background:transparent}:host([embedded]) h3{font-size:23px}
    section{padding:22px;border:1px solid var(--outline-variant,#dce5e6);border-radius:12px;background:var(--surface-container-low,#fff)}
    header,.actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
    h3{font-size:18px;line-height:1.4;font-weight:600;margin:0}p{margin:10px 0 16px;max-width:70ch}
    .hint,small,.state{color:var(--on-surface-variant,#57696e);font-size:13px}.state{display:block}
    button,input,select{font:inherit;color:inherit;border:1px solid var(--outline-variant,#bdcccf);border-radius:7px}
    button{cursor:pointer;background:transparent;font-size:13px;padding:9px 12px;min-height:40px}
    button:hover{background:var(--surface-container-high,#edf5f5)}button:disabled{opacity:.55;cursor:default}
    button.primary{background:#086573;color:#fff;border-color:#086573}button.primary:hover{background:#075662}
    button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #278492;outline-offset:3px}
    input,select{display:block;width:100%;min-width:0;margin-top:6px;padding:10px 12px;background:var(--surface,#fcfdfd);font-size:15px;font-weight:400}
    label{display:block;font-size:13px;font-weight:600;margin:0 0 16px}label small{display:block;font-weight:400}
    form{margin-top:20px;padding-top:20px;border-top:1px solid var(--outline-variant,#dce5e6)}
    .connection{padding:16px 0;border-top:1px solid var(--outline-variant,#dce5e6)}
    strong{font-weight:600;overflow-wrap:anywhere}.connection p{margin:6px 0}.connection:last-child{padding-bottom:0}
    details{margin-top:8px}summary{cursor:pointer;font-size:13px;min-height:32px;padding:4px 0}
    ul{padding-left:20px;margin:10px 0}li{margin:8px 0;overflow-wrap:anywhere}li small{display:block}
    a{color:var(--primary,#086573);text-underline-offset:3px;font-size:13px}
    .message{margin:12px 0;overflow-wrap:anywhere}.error{color:var(--error,#a12b32)}
    @media(max-width:480px){section{padding:16px}header{align-items:flex-start}.actions{justify-content:flex-start}}
  `;

  class MycelosConnectors extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({mode:'open'});
      this.connections = [];
      this.recipes = [];
      this.reload = () => this.refresh();
    }

    connectedCallback() {
      if (!this.shadowRoot.children.length) this.render();
      window.addEventListener('mycelos-connectors-changed', this.reload);
      this.refresh();
    }

    disconnectedCallback() { window.removeEventListener('mycelos-connectors-changed', this.reload); }

    async request(path, options = {}) {
      const response = await fetch(path, {credentials:'same-origin', ...options});
      const data = await response.json();
      if (!response.ok || data?.error) throw new Error('Die Verbindung ist derzeit nicht verfügbar. Bitte erneut versuchen.');
      return data;
    }

    render() {
      this.shadowRoot.innerHTML = `<style>${styles}</style><section aria-label="MCP-Verbindungen">
        <header><h3>Verbindungen</h3><button type="button" id="add" aria-expanded="false">Verbindung hinzufügen</button></header>
        <p class="hint">Frage Inhalte aus verbundenen Diensten im Chat ab. Ausgewählte Ergebnisse kannst du als Wissen speichern.</p>
        <div id="message" class="message hint" role="status" aria-live="polite"></div>
        <div id="list" aria-busy="true"><p class="hint">Die Verbindungen laden …</p></div>
        <form hidden><label>Dienst<select name="recipe" aria-label="Dienst" required><option value="">Dienst auswählen</option></select></label>
          <div id="fields"></div><div class="actions"><button class="primary" type="submit">Verbindung speichern</button>
          <button type="button" id="cancel">Abbrechen</button><a href="/pages/connectors-advanced.html">Weitere Optionen</a></div>
          <p class="hint">Die Verbindung speichert keine Inhalte im Wissensspeicher.</p></form>
        <p><a href="/pages/connectors-advanced.html">Verbindungen verwalten</a></p>
      </section>`;
      const root = this.shadowRoot;
      root.querySelector('#add').addEventListener('click', () => this.showForm(true));
      root.querySelector('#cancel').addEventListener('click', () => this.showForm(false));
      root.querySelector('select').addEventListener('change', () => this.renderFields());
      root.querySelector('form').addEventListener('submit', event => {event.preventDefault(); this.save();});
    }

    message(text, error = false) {
      const el = this.shadowRoot.querySelector('#message');
      el.textContent = text;
      el.classList.toggle('error', error);
      el.setAttribute('role', error ? 'alert' : 'status');
    }

    async refresh() {
      const list = this.shadowRoot.querySelector('#list');
      list.setAttribute('aria-busy', 'true');
      try {
        const [connections, recipes] = await Promise.all([this.request('/api/connectors'), this.request('/api/connectors/recipes')]);
        this.connections = connections.filter(c => c.connector_type === 'mcp');
        this.recipes = recipes.mcp || [];
        this.renderList();
        this.renderOptions();
      } catch (_) {
        list.innerHTML = '<p class="error">Die Verbindungen konnten nicht laden.</p><button type="button">Erneut versuchen</button>';
        list.querySelector('button').addEventListener('click', () => this.refresh());
      } finally { list.setAttribute('aria-busy', 'false'); }
    }

    renderList() {
      const states = {healthy:'Bereit', failing:'Verbindung gestört', unknown:'Noch nicht geprüft', ready:'Eingerichtet', setup_incomplete:'Einrichtung offen', inactive:'Nicht aktiv', disabled:'Nicht aktiv', pending:'Noch nicht bereit'};
      const list = this.shadowRoot.querySelector('#list');
      list.innerHTML = this.connections.length ? this.connections.map(c => `<article class="connection">
        <strong>${escape(c.name || c.id)}</strong><span class="state">${escape(states[c.operational_state] || 'Noch nicht geprüft')}</span>
        ${['failing','setup_incomplete'].includes(c.operational_state)?'<a href="/pages/connectors-advanced.html">Einrichtung prüfen</a>':''}
        <details data-id="${escape(c.id)}"><summary>Werkzeuge anzeigen</summary><div class="tools"></div></details>
      </article>`).join('') : '<p class="hint">Es gibt noch keine MCP-Verbindung. Füge einen Dienst hinzu, um ihn im Chat abzufragen.</p>';
      list.querySelectorAll('details').forEach(details => details.addEventListener('toggle', () => {
        if (details.open && !details.dataset.loaded) this.loadTools(details);
      }));
    }

    async loadTools(details) {
      details.dataset.loaded = 'true';
      const target = details.querySelector('.tools');
      target.innerHTML = '<p class="hint">Die Werkzeuge laden …</p>';
      try {
        const data = await this.request(`/api/connectors/${encodeURIComponent(details.dataset.id)}/tools`);
        target.innerHTML = data.tools?.length ? `<ul>${data.tools.map(tool => `<li><strong>${escape(tool.name)}</strong>${tool.blocked ? ' · Gesperrt' : ''}<small>${escape(tool.description)}</small></li>`).join('')}</ul>` : '<p class="hint">Der Dienst meldet noch keine Werkzeuge. Prüfe die Einrichtung unter „Weitere Optionen“.</p>';
      } catch (_) {
        target.innerHTML = '<p class="error">Die Werkzeuge konnten nicht laden.</p><button type="button">Erneut versuchen</button>';
        target.querySelector('button').addEventListener('click', () => this.loadTools(details));
      }
    }

    renderOptions() {
      const select = this.shadowRoot.querySelector('select');
      const selected = select.value;
      const installed = new Set(this.connections.map(c => c.id));
      select.innerHTML = '<option value="">Dienst auswählen</option>' + this.recipes.filter(r => !installed.has(r.id)).map(r => `<option value="${escape(r.id)}">${escape(r.name)}</option>`).join('');
      select.value = selected;
    }

    showForm(show) {
      const root = this.shadowRoot;
      root.querySelector('form').hidden = !show;
      root.querySelector('#add').setAttribute('aria-expanded', String(show));
      if (show) root.querySelector('select').focus();
      else {
        root.querySelector('form').reset();
        this.renderFields();
        root.querySelector('#add').focus();
      }
    }

    renderFields() {
      const root = this.shadowRoot;
      const recipe = this.recipes.find(r => r.id === root.querySelector('select').value);
      const advanced = recipe && (recipe.setup_flow === 'oauth_http' || ['filesystem','sqlite'].includes(recipe.id));
      root.querySelector('button[type="submit"]').hidden = !!advanced;
      root.querySelector('#fields').innerHTML = advanced ? `<p>Dieser Dienst braucht weitere Angaben.</p><p><a href="/pages/connectors-advanced.html#recipe-${encodeURIComponent(recipe.id)}">${escape(recipe.name)} einrichten</a></p>` : (recipe?.credentials || []).map((c, i) => `<label>Zugangsschlüssel${recipe.credentials.length > 1 ? ' ' + (i + 1) : ''}<small>${escape(c.name || c.env_var)}</small><input name="credential-${i}" type="password" autocomplete="new-password" required></label>`).join('');
    }

    async save() {
      const root = this.shadowRoot;
      const recipe = this.recipes.find(r => r.id === root.querySelector('select').value);
      if (!recipe) return;
      const button = root.querySelector('button[type="submit"]');
      button.disabled = true;
      button.textContent = 'Mycelos speichert …';
      this.message('');
      const body = {name:recipe.id};
      if (recipe.credentials?.length) body.env_vars = Object.fromEntries(recipe.credentials.map((c,i) => [c.env_var, root.querySelector(`[name="credential-${i}"]`).value]));
      try {
        await this.request('/api/connectors', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
        this.showForm(false);
        this.message('Die Verbindung ist gespeichert. Der Dienst startet.');
        window.dispatchEvent(new CustomEvent('mycelos-connectors-changed'));
      } catch (_) { this.message('Mycelos konnte die Verbindung nicht speichern. Prüfe die Angaben unter „Weitere Optionen“.', true); }
      finally { button.disabled = false; button.textContent = 'Verbindung speichern'; }
    }
  }
  if (!customElements.get('mycelos-connectors')) customElements.define('mycelos-connectors', MycelosConnectors);
})();
