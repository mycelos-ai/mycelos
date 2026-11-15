(() => {
 // Strings come from web.notifications in the locale files (loaded by i18n.js).
 const T = (key, vars) => (typeof globalThis.t === 'function' ? globalThis.t('notifications.' + key, vars) : key);
 const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 class Notifications extends HTMLElement {
  connectedCallback(){
   if(this.ready)return;this.ready=true;
   this.attachShadow({mode:'open'});
   this.shadowRoot.innerHTML=`<style>:host{position:relative;display:block;font:14px system-ui;color:#233638}button{font:inherit;background:white;color:#536568;border:0;border-radius:8px;padding:10px;cursor:pointer;display:flex;align-items:center;gap:4px}button:hover{background:#edf4f0}svg{width:22px;height:22px}small{background:#ad3333;color:white;border-radius:9px;padding:2px 5px}section{position:absolute;right:0;top:44px;background:white;border:1px solid #dce6e1;border-radius:12px;width:min(320px,calc(100vw - 32px));padding:16px;box-shadow:0 8px 24px #23363818;z-index:60}section[hidden]{display:none}a{display:block;padding:9px 0;color:#176366}p{color:#536568;line-height:1.5}button:focus-visible,a:focus-visible{outline:3px solid #4a9297}</style><button aria-label="${esc(T('title'))}" aria-expanded="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zM10 20h4M12 2v2"/></svg><small hidden></small></button><section hidden><strong>${esc(T('title'))}</strong><div role="status"></div><a href="/pages/inbox.html">${esc(T('all'))}</a></section>`;
   this.shadowRoot.querySelector('button').onclick=()=>{
    const panel=this.shadowRoot.querySelector('section');panel.hidden=!panel.hidden;
    this.shadowRoot.querySelector('button').setAttribute('aria-expanded',String(!panel.hidden));
    if(!panel.hidden)this.refresh();
   };
   this.outside=e=>{if(!e.composedPath().includes(this)){this.shadowRoot.querySelector('section').hidden=true;this.shadowRoot.querySelector('button').setAttribute('aria-expanded','false');}};
   document.addEventListener('click',this.outside);
   this.shadowRoot.addEventListener('keydown',e=>{if(e.key==='Escape'){this.shadowRoot.querySelector('section').hidden=true;const b=this.shadowRoot.querySelector('button');b.setAttribute('aria-expanded','false');b.focus();}});
   this.refresh();this.timer=setInterval(()=>{if(!document.hidden)this.refresh();},60000);
  }
  disconnectedCallback(){clearInterval(this.timer);document.removeEventListener('click',this.outside);}
  async refresh(){
   const area=this.shadowRoot.querySelector('[role=status]');
   try{
    const results=await Promise.all([fetch('/api/inbox'),fetch('/api/local-sources')]);
    if(results.some(r=>!r.ok))throw Error();
    const [inbox,sources]=await Promise.all(results.map(r=>r.json()));
    const failures=(Array.isArray(sources)?sources:sources.sources||[]).filter(s=>s.status==='error');
    const entries=inbox.entries||[];
    const count=entries.length+failures.length;
    const badge=this.shadowRoot.querySelector('small');badge.hidden=!count;badge.textContent=count>99?'99+':String(count);
    area.replaceChildren();
    if(!count){const p=document.createElement('p');p.textContent=T('none');area.append(p);}
    for(const source of failures){const a=document.createElement('a');a.href='/pages/settings.html#sources';a.textContent=T('import_failed',{name:source.name});area.append(a);}
    for(const entry of entries.slice(0,5)){const a=document.createElement('a');a.href='/pages/inbox.html';a.textContent=entry.title;area.append(a);}
   }catch{area.textContent=T('unavailable');}
  }
 }
 customElements.define('mycelos-notifications',Notifications);
})();
