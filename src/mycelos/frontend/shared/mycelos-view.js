/* <mycelos-view>: renders one view from the Package 8 knowledge view catalog.
 *
 * Contract (kept stable for the briefing and later slices):
 *   el.view = viewDict            // exactly the shape of widgets.views.view_to_dict
 *   <mycelos-view view-json="…">  // same, as a JSON string attribute
 *
 * Events (bubbles, composed):
 *   mycelos-view-ref    {path, title}         a source chip was clicked; cancelable.
 *                                             Without preventDefault the note opens in
 *                                             /pages/knowledge.html.
 *   mycelos-view-switch {from, to, view}      the user picked another view; cancelable.
 *                                             "text" is handled here (text fallback).
 *                                             A host that calls preventDefault sets
 *                                             el.view itself. Otherwise the element
 *                                             shows the prebuilt variant from
 *                                             view.variants[to] (slice 4), or the
 *                                             text fallback when there is none.
 *   mycelos-view-action {action, view}        an approval card button was pressed;
 *                                             cancelable. A host that handles it calls
 *                                             preventDefault, which locks the card, and
 *                                             reports the outcome with
 *                                             el.actionResult({ok, message}): ok keeps
 *                                             the card locked with the message, not ok
 *                                             unlocks it and shows the message as an
 *                                             error (slice 5).
 *
 * Security: every text field is data. It is escaped and never interpreted as
 * markup; Mermaid sources are built from escaped labels and rendered with
 * securityLevel "strict". No network access: Mermaid is served from /shared.
 */
(() => {
  if (customElements.get('mycelos-view')) return;

  const MERMAID_SRC = '/shared/mermaid.min.js';
  const TYPES = ['timeline', 'mind_map', 'graph_slice', 'comparison', 'flow', 'counts', 'evidence', 'tree_change', 'approval_card'];

  // English defaults; the page's t() (web.views.* in the locale files) wins when present.
  const EN = {
    'views.type.timeline': 'Timeline',
    'views.type.mind_map': 'Mind map',
    'views.type.graph_slice': 'Connections',
    'views.type.comparison': 'Comparison',
    'views.type.flow': 'Flow',
    'views.type.counts': 'Counts',
    'views.type.evidence': 'Evidence',
    'views.type.tree_change': 'Filing',
    'views.type.approval_card': 'Approval',
    'views.type.text': 'Text',
    'views.why': 'Why this view',
    'views.switch_to': 'Show as',
    'views.sources': 'Sources',
    'views.supports': 'supports',
    'views.contradicts': 'contradicts',
    'views.neutral': 'neutral',
    'views.new': 'new',
    'views.confidence': 'confidence',
    'views.diagram_failed': 'The diagram could not be drawn. Showing the text version.',
    'views.loading_diagram': 'Drawing diagram…',
    'views.action_sent': 'Sent',
    'views.unknown': 'This view cannot be shown here. Showing the text version.',
  };

  function tr(key) {
    try {
      if (typeof window.t === 'function') {
        const value = window.t(key);
        if (value && value !== key) return value;
      }
    } catch (_) { /* fall back to English */ }
    return EN[key] || key;
  }

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // Mermaid label: one line, quoted, with every character that Mermaid could read
  // as syntax, markup or markdown replaced by its entity code.
  function mLabel(value) {
    const text = String(value ?? '').replace(/[\r\n\t]+/g, ' ').trim().slice(0, 300) || ' ';
    return '"' + text.replace(/[#"<>`&\[\]{}()|;]/g, (c) => '#' + c.charCodeAt(0) + ';') + '"';
  }

  let mermaidReady = null;
  function loadMermaid() {
    if (window.mermaid) return Promise.resolve(window.mermaid);
    if (mermaidReady) return mermaidReady;
    mermaidReady = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = MERMAID_SRC;
      script.onload = () => {
        if (!window.mermaid) return reject(new Error('mermaid missing'));
        window.mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' });
        resolve(window.mermaid);
      };
      script.onerror = () => { mermaidReady = null; reject(new Error('mermaid failed to load')); };
      document.head.appendChild(script);
    });
    return mermaidReady;
  }

  let renderCounter = 0;

  // Per-diagram theme so mind maps and flows match the Mycelos palette and stay
  // readable whatever theme the page initialised Mermaid with.
  const SOFT = ['#e3f1ee', '#f6ead6', '#e6ebf6', '#f3e3ea', '#e8f0dc', '#efe6f6'];
  const THEME = '---\nconfig:\n  theme: base\n  themeVariables:\n'
    + "    fontFamily: 'system-ui, sans-serif'\n    primaryColor: '#e3f1ee'\n    primaryTextColor: '#233638'\n"
    + "    primaryBorderColor: '#9ab9ad'\n    lineColor: '#7c9a90'\n    edgeLabelBackground: '#ffffff'\n"
    + Array.from({ length: 12 }, (_, i) => `    cScale${i}: '${i === 0 ? '#176366' : SOFT[(i - 1) % SOFT.length]}'\n    cScaleLabel${i}: '${i === 0 ? '#ffffff' : '#233638'}'\n`).join('')
    + '---\n';

  function mindMapSource(root) {
    const lines = [THEME + 'mindmap'];
    let n = 0;
    const walk = (node, depth) => {
      const id = 'm' + (n++);
      lines.push('  '.repeat(depth + 1) + (depth === 0 ? id + '((' + mLabel(node.label) + '))' : id + '[' + mLabel(node.label) + ']'));
      (node.children || []).forEach((child) => walk(child, depth + 1));
    };
    walk(root || { label: '' }, 0);
    return lines.join('\n');
  }

  function flowSource(data) {
    const ids = new Map();
    const lines = [THEME + 'flowchart TD'];
    (data.steps || []).forEach((step, i) => {
      ids.set(step.id, 's' + i);
      lines.push('  s' + i + '[' + mLabel(step.text) + ']');
    });
    (data.edges || []).forEach((edge) => {
      const a = ids.get(edge.source), b = ids.get(edge.target);
      if (!a || !b) return;
      lines.push('  ' + a + ' -->' + (edge.label ? '|' + mLabel(edge.label) + '|' : '') + ' ' + b);
    });
    if (!(data.edges || []).length) {
      for (let i = 1; i < (data.steps || []).length; i++) lines.push('  s' + (i - 1) + ' --> s' + i);
    }
    return lines.join('\n');
  }

  // Small deterministic layout for a graph slice: the best connected node in the
  // middle, the rest on one or two rings. Good enough for the 2–30 nodes an answer
  // carries; the full graph stays on the home page.
  function graphLayout(nodes, edges) {
    const degree = new Map(nodes.map((n) => [n.id, 0]));
    edges.forEach((e) => { degree.set(e.source, (degree.get(e.source) || 0) + 1); degree.set(e.target, (degree.get(e.target) || 0) + 1); });
    // Most connected first; ties keep the order of the data (the builder ranks them).
    const order = [...nodes].sort((a, b) => degree.get(b.id) - degree.get(a.id));
    const W = 640, H = nodes.length > 12 ? 520 : 400, cx = W / 2, cy = H / 2;
    const pos = new Map();
    if (order.length === 2) {
      pos.set(order[0].id, { x: W * 0.3, y: cy });
      pos.set(order[1].id, { x: W * 0.7, y: cy });
      return { pos, W, H };
    }
    pos.set(order[0].id, { x: cx, y: cy });
    const rest = order.slice(1);
    const inner = rest.slice(0, 10), outer = rest.slice(10);
    const ring = (list, rx, ry, offset) => list.forEach((n, i) => {
      const a = offset + (2 * Math.PI * i) / list.length - Math.PI / 2;
      pos.set(n.id, { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) });
    });
    ring(inner, W * 0.32, H * 0.36, 0);
    ring(outer, W * 0.46, H * 0.46, Math.PI / Math.max(outer.length, 1));
    return { pos, W, H };
  }

  const CSS = `
    :host{display:block;color:#233638;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
    *{box-sizing:border-box}
    .view{background:#fff;border:1px solid #dce6e1;border-radius:14px;padding:18px 20px;max-width:100%;overflow:hidden}
    header{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 12px}
    h3{font-size:17px;font-weight:600;margin:0;overflow-wrap:anywhere}
    .kind{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#536568}
    .why{margin:4px 0 14px;color:#536568;font-size:13px;overflow-wrap:anywhere}
    .why b{font-weight:600;color:#233638}
    button{font:inherit;color:#176366;background:#fff;border:1px solid #dce6e1;border-radius:8px;padding:6px 11px;cursor:pointer}
    button:hover{background:#f0f5f3}
    button[disabled]{opacity:.55;cursor:default}
    button:focus-visible{outline:3px solid #4a9297;outline-offset:2px}
    .switch{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:14px;font-size:13px;color:#536568}
    .switch button{font-size:13px;padding:4px 10px}
    .switch button[aria-pressed="true"]{background:#176366;border-color:#176366;color:#fff}
    .refs{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:12px;font-size:13px;color:#536568}
    .refs button{font-size:13px;padding:3px 10px;border-radius:999px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .text{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:inherit}
    .hint{color:#536568;font-size:13px;margin:0 0 8px}
    ol.timeline{list-style:none;margin:0;padding:0 0 0 18px;border-left:2px solid #dce6e1}
    ol.timeline li{position:relative;padding:0 0 14px 12px}
    ol.timeline li::before{content:"";position:absolute;left:-26px;top:6px;width:12px;height:12px;border-radius:50%;background:#9ab9ad;border:2px solid #fff}
    ol.timeline li.warning::before{background:#c27c0e}
    ol.timeline li.due::before{background:#176366}
    .date{display:block;font-size:12px;color:#536568;font-variant-numeric:tabular-nums}
    .tag{display:inline-block;font-size:11px;border-radius:999px;padding:0 7px;margin-left:6px;vertical-align:1px}
    .tag.warning{background:#fbefd9;color:#7a4b00}.tag.due{background:#dcefee;color:#0f4c4e}
    .diagram{overflow-x:auto;text-align:center}
    .diagram svg{max-width:100%;height:auto}
    svg.graph{width:100%;height:auto;display:block}
    svg.graph line{stroke:#9ab9ad;stroke-width:1.5}
    svg.graph .edge-label{font-size:11px;fill:#536568}
    svg.graph circle{fill:#e9f2ef;stroke:#176366;stroke-width:1.5}
    svg.graph g.center circle{fill:#176366}
    svg.graph g.center text{font-weight:600}
    svg.graph .node-label{font-size:12px;fill:#233638}
    .table-wrap{overflow-x:auto}
    table{border-collapse:collapse;width:100%;font-size:14px}
    th,td{border-bottom:1px solid #e6eeea;padding:8px 10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
    th{font-weight:600;background:#f6faf8}
    td.good{background:#eaf6ef}td.bad{background:#fbeeee}
    .mark{font-weight:700;margin-right:4px}.good .mark{color:#1e7a45}.bad .mark{color:#ad3333}.neutral .mark{color:#536568}
    .bars{display:grid;grid-template-columns:minmax(90px,max-content) 1fr auto;gap:8px 10px;align-items:center}
    .bar-label{overflow-wrap:anywhere;font-size:14px}
    .bar{height:12px;border-radius:6px;background:#e6eeea;overflow:hidden}
    .bar i{display:block;height:100%;background:#176366;border-radius:6px;min-width:2px}
    .bar-value{font-variant-numeric:tabular-nums;font-size:14px;text-align:right}
    .claims{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:10px}
    .claim{border:1px solid #dce6e1;border-left-width:4px;border-radius:10px;padding:10px 12px}
    .claim.supports{border-left-color:#1e7a45}.claim.contradicts{border-left-color:#ad3333}
    .claim blockquote{margin:0 0 6px;overflow-wrap:anywhere}
    .claim small{color:#536568;display:block}
    ul.tree{list-style:none;margin:0;padding:0}
    ul.tree li{padding:6px 0;border-bottom:1px solid #eef3f1;overflow-wrap:anywhere}
    .path{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
    .path.before{color:#7a5555;text-decoration:line-through}
    .path.after{color:#0f4c4e;background:#e3f1ee;border-radius:4px;padding:0 4px}
    .conf{font-size:12px;color:#536568;margin-left:6px}
    .effects{margin:8px 0 12px;padding-left:20px}
    .actions{display:flex;flex-wrap:wrap;gap:8px}
    .actions button:first-child{background:#176366;border-color:#176366;color:#fff}
    .sent{font-size:13px;color:#536568;margin-top:8px}
    .sent.error{color:#a3361f}
    @media (max-width:600px){.view{padding:14px}.bars{grid-template-columns:1fr auto}.bars .bar{grid-column:1 / -1;order:3}}
  `;

  class MycelosView extends HTMLElement {
    static get observedAttributes() { return ['view-json']; }

    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._view = null;
      this._variants = {};
      this._showText = false;
      this._sentAction = '';
      this._actionNote = null;   // {ok, message} once the host reported back
      this._generation = 0;
      this.shadowRoot.addEventListener('click', (event) => this._click(event));
    }

    get view() { return this._view; }
    set view(value) {
      const v = value && typeof value === 'object' ? value : null;
      // Prebuilt alternatives for the same answer ({type: viewDict}). Only
      // catalog views whose own type matches their key are kept.
      this._variants = {};
      if (v && v.variants && typeof v.variants === 'object') {
        for (const [name, alt] of Object.entries(v.variants)) {
          if (alt && typeof alt === 'object' && alt.type === name && TYPES.includes(name)) this._variants[name] = alt;
        }
      }
      if (v && TYPES.includes(v.type)) this._variants[v.type] = v;
      this._show(v);
    }

    _show(v) {
      this._view = v;
      this._showText = false;
      this._sentAction = '';
      this._actionNote = null;
      this.render();
    }

    /** The host's answer to a handled mycelos-view-action. */
    actionResult(result) {
      const ok = !!(result && result.ok);
      const message = result && typeof result.message === 'string' ? result.message : '';
      if (!ok) this._sentAction = '';
      this._actionNote = { ok, message };
      this.render();
    }

    attributeChangedCallback(name, _old, value) {
      if (name !== 'view-json') return;
      try { this.view = value ? JSON.parse(value) : null; } catch (_) { this.view = null; }
    }

    connectedCallback() { this.render(); }

    _emit(name, detail, cancelable) {
      const event = new CustomEvent(name, { detail, bubbles: true, composed: true, cancelable: !!cancelable });
      this.dispatchEvent(event);
      return event;
    }

    _click(event) {
      const button = event.target.closest('button');
      if (!button || button.disabled) return;
      const v = this._view;
      if (button.dataset.ref !== undefined) {
        const ref = (v.refs || [])[Number(button.dataset.ref)] || {};
        const e = this._emit('mycelos-view-ref', { path: ref.path, title: ref.title || '' }, true);
        if (!e.defaultPrevented && ref.path) window.location.href = '/pages/knowledge.html?note=' + encodeURIComponent(ref.path);
        return;
      }
      if (button.dataset.switch !== undefined) {
        const to = button.dataset.switch;
        if (to === v.type) { this._showText = false; return this.render(); }
        if (to === 'text') { this._showText = true; return this.render(); }
        const e = this._emit('mycelos-view-switch', { from: v.type, to, view: v }, true);
        if (e.defaultPrevented) return;
        if (this._variants[to]) return this._show(this._variants[to]);
        this._showText = true; this.render();
        return;
      }
      if (button.dataset.action !== undefined) {
        const e = this._emit('mycelos-view-action', { action: button.dataset.action, view: v }, true);
        // Only a host that handles the action (preventDefault) locks the card.
        if (e.defaultPrevented) { this._sentAction = button.dataset.action; this._actionNote = null; this.render(); }
      }
    }

    render() {
      const v = this._view;
      const root = this.shadowRoot;
      this._generation++;
      if (!v) { root.innerHTML = ''; return; }
      const known = TYPES.includes(v.type);
      const showText = this._showText || !known || !v.data;
      const kind = tr('views.type.' + (known ? v.type : 'text'));
      const body = showText ? this._textBody(known) : this._body(v);
      const alts = (Array.isArray(v.alternatives) ? v.alternatives : []).filter((a) => typeof a === 'string' && a !== v.type);
      if (known && !alts.includes('text')) alts.push('text');
      const current = showText ? 'text' : v.type;
      const switcher = known && alts.length ? `<div class="switch" role="group" aria-label="${esc(tr('views.switch_to'))}"><span>${esc(tr('views.switch_to'))}:</span>
          ${[v.type, ...alts].map((a) => `<button type="button" data-switch="${esc(a)}" aria-pressed="${a === current}">${esc(tr('views.type.' + a))}</button>`).join('')}
        </div>` : '';
      const refs = Array.isArray(v.refs) && v.refs.length ? `<div class="refs"><span>${esc(tr('views.sources'))}:</span>
          ${v.refs.map((r, i) => `<button type="button" data-ref="${i}" title="${esc(r.path)}">${esc(r.title || r.path)}</button>`).join('')}
        </div>` : '';
      root.innerHTML = `<style>${CSS}</style>
        <section class="view" data-type="${esc(v.type)}" aria-label="${esc(kind + ': ' + (v.title || ''))}">
          <header><h3>${esc(v.title || kind)}</h3><span class="kind">${esc(kind)}</span></header>
          ${v.reason ? `<p class="why"><b>${esc(tr('views.why'))}:</b> ${esc(v.reason)}</p>` : '<div style="height:10px"></div>'}
          <div class="body">${body}</div>
          ${refs}
          ${switcher}
        </section>`;
      if (!showText && (v.type === 'mind_map' || v.type === 'flow')) this._drawMermaid(v);
    }

    _textBody(known) {
      const note = known ? '' : `<p class="hint">${esc(tr('views.unknown'))}</p>`;
      return note + `<pre class="text">${esc(this._view.fallback_text || '')}</pre>`;
    }

    _body(v) {
      const d = v.data || {};
      switch (v.type) {
        case 'timeline':
          return `<ol class="timeline">${(d.events || []).map((e) => `<li class="${esc(e.kind || 'normal')}">
              <span class="date">${esc(e.date)}</span>${esc(e.text)}${e.kind && e.kind !== 'normal' ? `<span class="tag ${esc(e.kind)}">${esc(e.kind === 'warning' ? '!' : '→')}</span>` : ''}
            </li>`).join('')}</ol>`;
        case 'mind_map':
        case 'flow':
          return `<div class="diagram" role="img" aria-label="${esc(v.fallback_text || '')}"><p class="hint" role="status">${esc(tr('views.loading_diagram'))}</p></div>`;
        case 'graph_slice':
          return this._graph(d);
        case 'comparison':
          return `<div class="table-wrap"><table><thead><tr><th></th>${(d.options || []).map((o) => `<th scope="col">${esc(o)}</th>`).join('')}</tr></thead>
            <tbody>${(d.rows || []).map((r) => `<tr><th scope="row">${esc(r.criterion)}</th>${(r.cells || []).map((c) => {
              const verdict = ['good', 'bad'].includes(c.verdict) ? c.verdict : 'neutral';
              const mark = { good: '+', bad: '−', neutral: '' }[verdict];
              return `<td class="${verdict}">${mark ? `<span class="mark" aria-hidden="true">${mark}</span>` : ''}${esc(c.value)}</td>`;
            }).join('')}</tr>`).join('')}</tbody></table></div>`;
        case 'counts': {
          const items = d.items || [];
          const max = Math.max(1, ...items.map((i) => Number(i.value) || 0));
          const unit = d.unit ? ' ' + d.unit : '';
          return `<div class="bars">${items.map((i) => {
            const pct = Math.round(((Number(i.value) || 0) / max) * 100);
            return `<span class="bar-label">${esc(i.label)}</span><span class="bar" aria-hidden="true"><i style="width:${pct}%"></i></span><span class="bar-value">${esc(String(i.value) + unit)}</span>`;
          }).join('')}</div>`;
        }
        case 'evidence':
          return `<div class="claims">${(d.claims || []).map((c) => {
            const rel = ['supports', 'contradicts'].includes(c.relation) ? c.relation : 'neutral';
            return `<article class="claim ${rel}"><blockquote>„${esc(c.quote)}“</blockquote>
              <small>${esc(c.source)}${c.date ? ' · ' + esc(c.date) : ''}${rel !== 'neutral' ? ' · ' + esc(tr('views.' + rel)) : ''}</small></article>`;
          }).join('')}</div>`;
        case 'tree_change':
          return this._tree(d.changes);
        case 'approval_card': {
          const sent = this._sentAction;
          const label = sent ? ((d.actions || []).find((a) => a.id === sent) || {}).label || sent : '';
          const note = this._actionNote;
          let status = '';
          if (note && note.message) status = `<p class="sent ${note.ok ? '' : 'error'}" role="${note.ok ? 'status' : 'alert'}">${esc(note.message)}</p>`;
          else if (sent) status = `<p class="sent" role="status">${esc(tr('views.action_sent'))}: ${esc(label)}</p>`;
          return `<p class="text">${esc(d.summary)}</p>
            ${(d.changes || []).length ? this._tree(d.changes) : ''}
            ${(d.effects || []).length ? `<ul class="effects">${d.effects.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
            <div class="actions">${(d.actions || []).map((a) => `<button type="button" data-action="${esc(a.id)}" ${sent ? 'disabled' : ''}>${esc(a.label)}</button>`).join('')}</div>
            ${status}`;
        }
        default:
          return this._textBody(false);
      }
    }

    _tree(changes) {
      return `<ul class="tree">${(changes || []).map((c) => `<li><strong>${esc(c.title)}</strong><br>
              ${c.before ? `<span class="path before">${esc(c.before)}</span> → ` : `<span class="tag due">${esc(tr('views.new'))}</span> `}<span class="path after">${esc(c.after)}</span>
              ${typeof c.confidence === 'number' ? `<span class="conf">${esc(tr('views.confidence'))} ${Math.round(c.confidence * 100)}%</span>` : ''}
            </li>`).join('')}</ul>`;
    }

    _graph(d) {
      const nodes = d.nodes || [], edges = d.edges || [];
      if (nodes.length < 2) return this._textBody(true);
      const { pos, W, H } = graphLayout(nodes, edges);
      const center = nodes.length > 2 ? [...pos.keys()][0] : null;
      const label = (s) => { const t = String(s ?? ''); return t.length > 28 ? t.slice(0, 27) + '…' : t; };
      const lines = edges.map((e) => {
        const a = pos.get(e.source), b = pos.get(e.target);
        if (!a || !b) return '';
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        return `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"><title>${esc(e.relation)}</title></line>
          ${e.relation && e.relation !== 'related' ? `<text class="edge-label" x="${mx.toFixed(1)}" y="${(my - 4).toFixed(1)}" text-anchor="middle">${esc(label(e.relation))}</text>` : ''}`;
      }).join('');
      const circles = nodes.map((n) => {
        const p = pos.get(n.id);
        const isCenter = n.id === center;
        const r = isCenter ? 16 : 9;
        const text = `<text class="node-label" x="${p.x.toFixed(1)}" y="${(p.y + r + 15).toFixed(1)}" text-anchor="middle">${esc(label(n.label))}</text>`;
        return `<g class="node${isCenter ? ' center' : ''}"><title>${esc(n.label)}</title><circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r}"></circle>${text}</g>`;
      }).join('');
      // Crop the drawing to what is used, with room for labels.
      const xs = [...pos.values()].map((q) => q.x), ys = [...pos.values()].map((q) => q.y);
      const x0 = Math.max(0, Math.min(...xs) - 90), x1 = Math.min(W, Math.max(...xs) + 90);
      const y0 = Math.max(0, Math.min(...ys) - 30), y1 = Math.min(H, Math.max(...ys) + 40);
      return `<svg class="graph" viewBox="${x0.toFixed(0)} ${y0.toFixed(0)} ${(x1 - x0).toFixed(0)} ${(y1 - y0).toFixed(0)}" role="img" aria-label="${esc(this._view.fallback_text || '')}">${lines}${circles}</svg>`;
    }

    async _drawMermaid(v) {
      const generation = this._generation;
      const source = v.type === 'mind_map' ? mindMapSource((v.data || {}).root) : flowSource(v.data || {});
      const target = () => this.shadowRoot.querySelector('.diagram');
      const id = 'mycelos-view-' + (++renderCounter);
      try {
        const mermaid = await loadMermaid();
        const { svg } = await mermaid.render(id, source);
        if (generation !== this._generation || !target()) return;
        target().innerHTML = svg;
        target().dataset.rendered = 'true';
      } catch (error) {
        document.getElementById('d' + id)?.remove();
        if (generation !== this._generation || !target()) return;
        target().innerHTML = `<p class="hint" role="status">${esc(tr('views.diagram_failed'))}</p><pre class="text">${esc(v.fallback_text || '')}</pre>`;
        target().dataset.rendered = 'fallback';
      }
    }
  }

  customElements.define('mycelos-view', MycelosView);
  window.MycelosView = { TYPES: TYPES.slice(), mindMapSource, flowSource };
})();
