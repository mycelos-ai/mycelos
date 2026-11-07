/* Telegram settings shared by the settings page and the chat. */
(() => {
  if (customElements.get('mycelos-telegram')) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  class TelegramSettings extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({mode:'open'});
      this.state = null;
      this.draft = {token:'',chat_id:'',enabled:false};
      this.chats = [];
      this.busy = false;
      this.error = '';
      this.notice = '';
      this.dirty = false;
      this.sharedChange = event => { if (event.detail !== this && !this.dirty && !this.busy) this.load(); };
      this.shadowRoot.addEventListener('input', event => {
        const field = event.target;
        if (!(field.name in this.draft)) return;
        this.draft[field.name] = field.type === 'checkbox' ? field.checked : field.value;
        this.dirty = true;
      });
      this.shadowRoot.addEventListener('change', event => {
        if (event.target.name === 'chat-choice' && event.target.value) {
          this.draft.chat_id = event.target.value;
          this.dirty = true;
          this.shadowRoot.querySelector('[name="chat_id"]').value = event.target.value;
        }
      });
      this.shadowRoot.addEventListener('submit', event => { event.preventDefault(); this.save(); });
      this.shadowRoot.addEventListener('click', event => {
        const action = event.target.closest('button')?.dataset.action;
        if (action === 'check') this.check();
        if (action === 'reload') this.load();
      });
    }
    connectedCallback() {
      window.addEventListener('mycelos-telegram-changed', this.sharedChange);
      this.load();
    }
    disconnectedCallback() {
      window.removeEventListener('mycelos-telegram-changed', this.sharedChange);
      this.draft.token = '';
    }
    async request(path, method = 'GET', body) {
      const response = await fetch('/api/telegram/' + path, {
        method, headers:{Accept:'application/json', ...(body ? {'Content-Type':'application/json'} : {})},
        ...(body ? {body:JSON.stringify(body)} : {})
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.error) {
        if (response.status === 401) throw new Error('Bitte melde dich erneut an.');
        if (path === 'check') throw new Error('Die Prüfung ist fehlgeschlagen. Prüfe den Bot-Token und die Verbindung.');
        throw new Error('Die Daten konnten nicht gespeichert oder geladen werden. Bitte erneut versuchen.');
      }
      return result;
    }
    async load() {
      if (this.busy) return;
      this.busy = true; this.error = ''; this.render();
      try {
        this.state = await this.request('config');
        this.draft = {token:'',chat_id:this.state.chat_id || '',enabled:this.state.enabled};
        this.dirty = false;
      } catch (error) { this.error = error.message; }
      finally { this.busy = false; this.render(); }
    }
    async check() {
      if (this.busy) return;
      if (!this.draft.token.trim() && !this.state?.token_configured) {
        this.error = 'Trage zuerst den Bot-Token ein.'; this.render(); return;
      }
      this.busy = true; this.error = ''; this.notice = 'Die Verbindung wird geprüft.'; this.chats = []; this.render();
      try {
        const result = await this.request('check','POST',{token:this.draft.token.trim()});
        this.chats = result.chats || [];
        if (result.chat_id && !this.draft.chat_id) { this.draft.chat_id = result.chat_id; this.dirty = true; }
        if (result.discovery_error) {
          this.notice = 'Der Bot-Token ist gültig. Die Chat-Liste ist nicht verfügbar. Ein anderer Empfänger kann aktiv sein. Trage die private Chat-ID ein.';
        } else if (!this.chats.length) {
          this.notice = 'Der Bot-Token ist gültig. Kein privater Chat gefunden. Öffne den Bot in Telegram und sende /start. Prüfe danach erneut.';
        } else {
          this.notice = `Der Bot-Token ist gültig. ${this.chats.length > 1 ? 'Wähle deinen privaten Chat aus.' : 'Prüfe die Chat-ID und speichere die Daten.'}`;
        }
      } catch (error) { this.error = error.message; this.notice = ''; }
      finally { this.busy = false; this.render(); }
    }
    async save() {
      if (this.busy) return;
      if (this.draft.enabled && !/^[1-9][0-9]{0,15}$/.test(this.draft.chat_id.trim())) {
        this.error = 'Trage eine gültige private Chat-ID ein. Sie enthält nur Ziffern.'; this.render(); return;
      }
      if (this.draft.enabled && !this.draft.token.trim() && !this.state?.token_configured) {
        this.error = 'Trage den Bot-Token ein.'; this.render(); return;
      }
      this.busy = true; this.error = ''; this.notice = ''; this.render();
      try {
        this.state = await this.request('config','PUT', {...this.draft,token:this.draft.token.trim(),chat_id:this.draft.chat_id.trim()});
        this.draft.token = ''; this.dirty = false;
        this.notice = this.state.runtime_error ? 'Die Daten sind gespeichert. Der Empfänger konnte seinen Zustand nicht ändern.' :
          this.state.enabled && !this.state.running ? 'Die Daten sind gespeichert. Der Empfänger läuft derzeit nicht.' : 'Die Daten sind gespeichert.';
        window.dispatchEvent(new CustomEvent('mycelos-telegram-changed',{detail:this}));
      } catch (error) { this.error = error.message; }
      finally { this.busy = false; this.render(); }
    }
    render() {
      const status = !this.state ? 'Noch nicht geladen' : this.state.running ? 'Empfänger aktiv' : this.state.enabled ? 'Empfänger nicht aktiv' : 'Deaktiviert';
      const webhook = this.state?.mode === 'webhook';
      this.shadowRoot.innerHTML = `<style>
        :host{display:block;min-width:0;color:#202c30;font:400 15px/1.6 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
        *{box-sizing:border-box} [hidden]{display:none!important}
        section{background:#fff;border:1px solid #dce5e6;border-radius:12px;padding:24px}
        header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:8px}
        h3{font-size:18px;font-weight:600;margin:0;line-height:1.4}p{margin:0 0 20px;max-width:68ch}
        .state,small{color:#52666b;font-size:13px}.state{display:inline-flex;align-items:center;gap:7px}.dot{width:7px;height:7px;border-radius:50%;background:${this.state?.running ? '#086573' : '#75858a'}}
        fieldset{border:0;padding:0;margin:0;min-width:0}label{display:block;font-weight:600;font-size:13px;margin-bottom:18px}
        input,select{display:block;width:100%;min-width:0;margin:6px 0 4px;padding:11px 12px;border:1px solid #b9c9cd;border-radius:7px;background:#fcfdfd;color:inherit;font:inherit;font-weight:400;font-size:15px;line-height:1.4}
        small{display:block;font-weight:400;overflow-wrap:anywhere}a{color:#086573;text-underline-offset:3px}
        .enable{display:flex;align-items:flex-start;gap:10px;font-size:14px;margin:22px 0}.enable input{width:18px;height:18px;margin:3px 0 0;accent-color:#086573;flex-shrink:0}
        .actions{display:flex;gap:10px;flex-wrap:wrap;padding-top:4px}button{min-height:42px;padding:9px 14px;border:1px solid #b9c9cd;border-radius:7px;background:#fff;color:#202c30;font:inherit;font-size:14px;cursor:pointer}
        button.primary{background:#086573;border-color:#086573;color:#fff}button:hover{background:#edf5f5}button.primary:hover{background:#075662}button:disabled{opacity:.55;cursor:default}
        :focus-visible{outline:3px solid #278492;outline-offset:3px}.message{margin:16px 0 0;font-size:14px;overflow-wrap:anywhere}.error{color:#a62929}
        @media(max-width:480px){section{padding:18px}.actions button{width:100%}}
      :host([embedded]) section{padding:0;border:0;background:transparent}:host([embedded]) h3{font-size:23px}
      </style><section aria-busy="${this.busy}">
        <header><h3>Telegram</h3><span class="state"><span class="dot" aria-hidden="true"></span>${status}</span></header>
        <p>Sprich mit Mycelos in deinem privaten Telegram-Chat.</p>
        ${webhook ? '<p>Dieser Bot verwendet einen Webhook. Verwalte ihn über seine bestehende Kanaleinrichtung.</p>' : ''}
        <form><fieldset ${this.busy || !this.state || webhook ? 'disabled' : ''}>
          <label>Bot-Token<input name="token" type="password" autocomplete="new-password" spellcheck="false" value="${esc(this.draft.token)}" placeholder="${this.state?.token_configured ? 'Gespeicherter Token bleibt erhalten' : 'Token von BotFather'}"><small>${this.state?.token_configured ? 'Lass dieses Feld leer, um den gespeicherten Token zu behalten.' : 'Erstelle den Bot bei <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">BotFather</a>. Trage seinen Token hier ein.'}</small></label>
          ${this.chats.length > 1 ? `<label>Privaten Chat auswählen<select name="chat-choice"><option value="">Bitte auswählen</option>${this.chats.map(chat=>`<option value="${esc(chat.id)}" ${chat.id === this.draft.chat_id ? 'selected' : ''}>${esc(chat.title)} · ${esc(chat.id)}</option>`).join('')}</select></label>` : ''}
          <label>Private Chat-ID<input name="chat_id" inputmode="numeric" autocomplete="off" value="${esc(this.draft.chat_id)}" placeholder="Zum Beispiel 123456789"><small>Verwende deine private Chat-ID. Gruppen unterstützt diese Einrichtung nicht.</small></label>
          <label class="enable"><input type="checkbox" name="enabled" ${this.draft.enabled ? 'checked' : ''}><span>Telegram aktivieren<small>Mycelos darf Nachrichten aus diesem privaten Chat empfangen.</small></span></label>
          <div class="actions"><button class="primary" type="submit">${this.busy ? 'Bitte warten …' : 'Speichern'}</button><button type="button" data-action="check">Verbindung prüfen</button></div>
          <small style="margin-top:10px">Die Prüfung sendet keine Nachricht.</small>
        </fieldset></form>
        ${!this.state && !this.busy ? '<button type="button" data-action="reload">Erneut laden</button>' : ''}
        <p class="message error" role="alert" ${this.error ? '' : 'hidden'}>${esc(this.error)}</p>
        <p class="message" role="status" ${this.notice ? '' : 'hidden'}>${esc(this.notice)}</p>
      </section>`;
    }
  }
  customElements.define('mycelos-telegram',TelegramSettings);
})();
