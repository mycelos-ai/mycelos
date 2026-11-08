/* Shared knowledge view. Reads existing knowledge; never starts an LLM call. */
(() => {
 // Strings come from web.knowledge_card in the locale files (loaded by i18n.js).
 const T = (key, vars) => (typeof globalThis.t === 'function' ? globalThis.t('knowledge_card.' + key, vars) : key);
 const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 async function request(path, method='GET', body) {
  const response = await fetch('/api/knowledge/'+path,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
  if (!response.ok) throw new Error(T('error_action'));
  return response.json();
 }
 class KnowledgeCard extends HTMLElement {
  constructor(){
   super(); this.attachShadow({mode:'open'}); this.nodes=[];this.edges=[];this.current=null;this.note=null;this.error='';this.loading=true;this.expanded=false;this.moving=false;
   this.shadowRoot.addEventListener('click',event => this.click(event));
   this.shadowRoot.addEventListener('keydown',event => {
    if(!this.expanded)return;
    if(event.key==='Escape'){this.expanded=false;this.render();this.shadowRoot.querySelector('[data-action="zoom"]').focus();}
    if(event.key==='Tab'){
     const controls=[...this.shadowRoot.querySelectorAll('button,a,select,summary')].filter(el=>el.getClientRects().length&&!el.disabled);
     const first=controls[0],last=controls[controls.length-1];
     if(event.shiftKey&&this.shadowRoot.activeElement===first){event.preventDefault();last?.focus();}
     if(!event.shiftKey&&this.shadowRoot.activeElement===last){event.preventDefault();first?.focus();}
    }
   });
  }
  connectedCallback(){this.load();}
  disconnectedCallback(){this.generation=(this.generation||0)+1;}
  async load(){
   this.loading=true;this.error='';this.render();
   const generation=this.generation=(this.generation||0)+1;
   try{
    const data=await request('graph');
    if(generation!==this.generation)return;
    this.nodes=data.nodes||[];this.edges=data.edges||[];
   }catch(error){this.error=error.message;}
   finally{if(generation===this.generation){this.loading=false;this.render();}}
  }
  node(id){return this.nodes.find(n=>n.id===id);}
  children(id){return this.nodes.filter(n=> id ? n.parent_path===id : !n.parent_path || !this.node(n.parent_path));}
  ancestors(){
   const list=[];let item=this.current;const seen=new Set();
   while(item && !seen.has(item.id)){seen.add(item.id);list.unshift(item);item=this.node(item.parent_path);}
   return list;
  }
  async select(id){
   this.current=this.node(id)||null;this.note=null;this.moving=false;this.error='';
   const current=this.current;
   this.dispatchEvent(new CustomEvent('knowledge-focus',{detail:current?{path:current.id,title:current.title}:null,bubbles:true,composed:true}));
   this.render();
   if(current){
    try{const note=await request('notes/'+encodeURIComponent(current.id));if(this.current?.id===current.id)this.note=note;}
    catch(error){if(this.current?.id===current.id)this.error=error.message;}
    this.render();
   }
  }
  item(node){
   return '<button class="node" data-select="'+esc(node.id)+'"><span>'+esc(node.title)+'</span><small>'+ (node.type==='topic' ? this.children(node.id).length+' ›' : '↗')+'</small></button>';
  }
  render(){
   const path=this.ancestors(), parent=path.length>1?path[path.length-2]:null;
   const children=this.children(this.current?.id);
   const recent=this.getAttribute('view')==='recent' && !this.current;
   const items=recent?[...this.nodes].sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at))).slice(0,20):children;
   const connections=this.current?this.edges.filter(e=>e.kind!=='parent'&&(e.source===this.current.id||e.target===this.current.id)):[];
   const content=this.note?.content ?? this.note?.body ?? '';
   this.shadowRoot.innerHTML=`<style>
    :host{display:block;color:#233638;font:15px/1.5 system-ui,sans-serif}*{box-sizing:border-box}
    .view{background:white;border:1px solid #dce6e1;border-radius:14px;padding:22px}
    .expanded{position:fixed;inset:18px;z-index:90;overflow:auto;box-shadow:0 12px 40px #23363830}
    header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}
    h3{font-size:18px;font-weight:600;margin:0}button,a{font:inherit;color:#176366}button{background:white;border:1px solid #dce6e1;border-radius:8px;padding:8px 12px;cursor:pointer}
    button:hover{background:#f0f5f3}button:focus-visible,a:focus-visible,select:focus-visible{outline:3px solid #4a9297;outline-offset:3px}
    .up{display:flex;align-items:center;gap:12px;margin-bottom:18px}.depth{display:flex;gap:4px}.depth i{width:7px;height:7px;background:#9ab9ad;border-radius:50%}.depth i:last-child{background:#176366}
    .items{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:8px;max-height:360px;overflow:auto}
    .node{display:flex;text-align:left;align-items:center;justify-content:space-between;gap:14px;color:#233638;min-height:48px}.node span{overflow-wrap:anywhere}.node small{color:#536568;white-space:nowrap}
    .preview{margin-top:20px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:340px;overflow:auto}
    .expanded .preview{max-height:none}.hint{color:#536568;font-size:13px}.error{color:#ad3333}
    .tools{display:flex;gap:8px;align-items:center}details{margin-top:16px}summary{cursor:pointer;color:#176366}
    select{display:block;max-width:100%;margin:12px 0;padding:8px;border:1px solid #9ab9ad;border-radius:6px}
    @media(max-width:600px){.view{padding:16px}.expanded{inset:8px}.items{grid-template-columns:1fr 1fr}.node{padding:8px;font-size:14px}}
   </style><section class="view ${this.expanded?'expanded':''}" aria-label="${esc(T('label'))}" ${this.expanded?'role="dialog" aria-modal="true"':''}>
    <header><h3>${esc(this.current?.title || (recent?T('recent'):T('my_knowledge')))}</h3><div class="tools">
     <button data-action="zoom" aria-label="${esc(this.expanded?T('view_small'):T('view_large'))}" title="${esc(this.expanded?T('shrink'):T('enlarge'))}">${this.expanded?'↙':'⤢'}</button>
    </div></header>
    ${this.error?'<p class="error" role="alert">'+esc(this.error)+'</p><button data-action="reload">'+esc(T('retry'))+'</button>':''}
    ${this.loading?`<p role="status">${T('loading')}</p>`:`
      ${this.current?`<div class="up"><button data-select="${esc(parent?.id||'')}">↑ ${esc(parent?.title||T('my_knowledge'))}</button><span class="depth" aria-label="${esc(T('level', {level: path.length}))}">${path.map(()=>'<i></i>').join('')}</span></div>`:''}
      <div class="items">${items.map(n=>this.item(n)).join('')}</div>
      ${!items.length&&!this.current?`<p class="hint">${T('empty')}</p>`:''}
      ${content?(this.current?.type==='topic'?'<details><summary>'+esc(T('content'))+'</summary><div class="preview">'+esc(content)+'</div></details>':'<div class="preview">'+esc(content)+'</div>'):''}
      ${this.current?`<details><summary>${T('actions')}</summary><p><a href="/pages/knowledge.html?note=${encodeURIComponent(this.current.id)}">${T('edit')}</a></p><button data-action="move">${T('move')}</button></details>`:''}
      ${this.moving?`<label>${T('move_under')}<select aria-label="${esc(T('target_topic'))}"><option value="">${T('my_knowledge')}</option>${this.nodes.filter(n=>n.type==='topic'&&n.id!==this.current.id&&!this.isDescendant(n,this.current.id)).map(n=>'<option value="'+esc(n.id)+'" '+(n.id===this.current.parent_path?'selected':'')+'>'+esc(n.title)+' · '+esc(n.id)+'</option>').join('')}</select></label><button data-action="save-move">${T('confirm_move')}</button>`:''}
      ${connections.length?`<details><summary>${esc(T('connections', {count: connections.length}))}</summary><div class="items">${connections.map(e=>{const n=this.node(e.source===this.current.id?e.target:e.source);return n?this.item(n):'';}).join('')}</div></details>`:''}
      ${this.nodes.length>=5000?`<p class="hint">${T('limit')}</p>`:''}
    `}
   </section>`;
  }
  isDescendant(node,id){const seen=new Set();while(node&&!seen.has(node.id)){if(node.parent_path===id)return true;seen.add(node.id);node=this.node(node.parent_path);}return false;}
  async click(event){
   const button=event.target.closest('button');if(!button)return;
   if(button.hasAttribute('data-select'))return this.select(button.dataset.select);
   switch(button.dataset.action){
    case 'zoom':this.expanded=!this.expanded;this.render();this.shadowRoot.querySelector('[data-action="zoom"]').focus();break;
    case 'reload':await this.load();break;
    case 'move':this.moving=!this.moving;this.render();break;
    case 'save-move':
     button.disabled=true;
     try{
      const parent=this.shadowRoot.querySelector('select').value;
      await request('notes/'+encodeURIComponent(this.current.id),'PUT',{parent_path:parent});
      this.current.parent_path=parent;this.moving=false;await this.load();
     }catch(error){this.error=error.message;this.render();}
     break;
   }
  }
 }
 customElements.define('mycelos-knowledge',KnowledgeCard);
})();
