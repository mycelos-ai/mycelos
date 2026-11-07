"""Product navigation and knowledge-card behavior without model calls."""
from pathlib import Path
import re
import subprocess

FRONTEND = Path(__file__).resolve().parents[1] / "src/mycelos/frontend"


def test_product_links_reach_existing_pages():
    missing = []
    for path in FRONTEND.rglob("*.html"):
        # Only literal HTML links. Runtime IDs are covered by the API tests.
        for target in re.findall(r'href="/(pages/[^"?#]+\.html)', path.read_text()):
            if not (FRONTEND / target).is_file():
                missing.append((path.name, target))
    assert not missing


def test_knowledge_card_navigation_and_untrusted_content():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let Card;
global.HTMLElement = class {
 attachShadow(){this.shadowRoot={innerHTML:'',addEventListener(){}};}
 getAttribute(){return null;}
 dispatchEvent(event){this.lastEvent=event;return true;}
};
global.CustomEvent = class {constructor(type,options){this.type=type;this.detail=options.detail;}};
global.customElements={define(name,Class){Card=Class;}};
global.fetch=async url=>({ok:true,json:async()=>({content:'<script>bad()</script>'})});
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
(async()=>{
 const card=new Card();
 card.nodes=[
  {id:'a',title:'Sharewise',type:'topic'},
  {id:'b',title:'Import',type:'topic',parent_path:'a'},
  {id:'c',title:'<img onerror="bad()">',type:'note',parent_path:'b'}
 ];
 card.edges=[{source:'c',target:'a',kind:'related'}];
 card.loading=false;
 assert.deepEqual(card.children(null).map(n=>n.id),['a']);
 assert.deepEqual(card.children('b').map(n=>n.id),['c']);
 assert.equal(card.isDescendant(card.node('c'),'a'),true);
 assert.equal(card.isDescendant(card.node('a'),'c'),false);
 await card.select('c');
 assert.deepEqual(card.ancestors().map(n=>n.id),['a','b','c']);
 assert.deepEqual(card.lastEvent.detail,{path:'c',title:'<img onerror="bad()">'});
 assert.ok(card.shadowRoot.innerHTML.includes('&lt;script&gt;'));
 assert.ok(!card.shadowRoot.innerHTML.includes('<script>bad'));
 assert.ok(!card.shadowRoot.innerHTML.includes('<img onerror='));
 await card.select('');
 assert.equal(card.lastEvent.detail,null);
 assert.equal(card.note,null);
 // Existing malformed hierarchies must not hang the interface.
 card.nodes[0].parent_path='b';
 await card.select('a');
 assert.equal(card.ancestors().length,2);
 console.log('knowledge-card: passed');
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run(
        ["node", "-e", script, str(FRONTEND / "shared/knowledge-card.js")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_chat_focus_does_not_change_commands():
    html = (FRONTEND / "pages/chat.html").read_text()
    expression = re.search(r"message: (this.focusItem[^\n]+),", html).group(1)
    script = """
const assert=require('node:assert/strict');
const messageFor=new Function('message','return '+process.argv[1]);
const state={focusItem:{title:'Sharewise',path:'notes/sharewise'}};
assert.equal(messageFor.call(state,'/memory set name Stefan'),'/memory set name Stefan');
assert.equal(messageFor.call(state,'  /memory'),'  /memory');
assert.ok(messageFor.call(state,'Was gehört dazu?').includes('notes/sharewise'));
assert.equal(messageFor.call({focusItem:null},'Hallo'),'Hallo');
"""
    result = subprocess.run(["node", "-e", script, expression], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
