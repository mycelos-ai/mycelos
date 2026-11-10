"""Chat answers name the notes that were actually put into the LLM context.

The chat injects up to three notes found by ``kb.find_relevant`` as
``[Knowledge Context]``. The client gets exactly those notes as a
``citations`` event (path and title only, never content), and the
assistant message keeps them in its metadata so a reload shows them again.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-chat-citations"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def service(app: App) -> ChatService:
    return ChatService(app)


def _llm(content: str = "Answer.") -> MagicMock:
    mock = MagicMock()
    mock.content = content
    mock.total_tokens = 10
    mock.model = "test-model"
    mock.tool_calls = None
    mock.cost = 0.0
    return mock


NOTES = [
    {"path": "topics/a.md", "title": "Note A", "type": "note", "content": "SECRET BODY A"},
    {"path": "topics/b.md", "title": "Note B", "type": "note", "content": "SECRET BODY B"},
    {"path": "topics/c.md", "title": "", "type": "note", "content": "SECRET BODY C"},
    {"path": "topics/d.md", "title": "Note D", "type": "note", "content": "not injected"},
]


def _run(service: ChatService, app: App, relevant):
    session_id = service.create_session()
    kb = app.knowledge_base
    side = relevant if callable(relevant) else None
    with patch.object(app.llm, "complete", return_value=_llm()) as llm, \
         patch.object(kb, "find_relevant", side_effect=side, return_value=relevant):
        events = service.handle_message("What do I know about A?", session_id)
    return session_id, events, llm


def _citation_events(events):
    return [e for e in events if e.type == "citations"]


def test_citations_are_exactly_the_injected_notes(service, app):
    _, events, llm = _run(service, app, NOTES)

    cited = _citation_events(events)
    assert len(cited) == 1
    assert cited[0].data["citations"] == [
        {"path": "topics/a.md", "title": "Note A"},
        {"path": "topics/b.md", "title": "Note B"},
        {"path": "topics/c.md", "title": "topics/c.md"},
    ]
    # Same notes the LLM saw: three injected, the fourth was not.
    prompt = str(llm.call_args)
    assert "Note A" in prompt and "Note D" not in prompt


def test_citations_never_carry_note_content(service, app):
    _, events, _ = _run(service, app, NOTES)
    payload = str([e.data for e in _citation_events(events)])
    assert "SECRET BODY" not in payload


def test_citations_come_before_text_and_are_stored_on_the_answer(service, app):
    session_id, events, _ = _run(service, app, NOTES)

    types = [e.type for e in events]
    assert types.index("citations") < types.index("text")

    stored = [m for m in app.session_store.load_messages(session_id) if m["role"] == "assistant"]
    assert stored[-1]["metadata"]["citations"][0] == {"path": "topics/a.md", "title": "Note A"}


def test_no_matches_means_no_citations(service, app):
    session_id, events, _ = _run(service, app, [])

    assert _citation_events(events) == []
    stored = [m for m in app.session_store.load_messages(session_id) if m["role"] == "assistant"]
    assert "citations" not in (stored[-1].get("metadata") or {})


def test_broken_search_does_not_break_chat_or_invent_citations(service, app):
    def boom(*_args, **_kwargs):
        raise RuntimeError("index broken")

    _, events, _ = _run(service, app, boom)

    types = [e.type for e in events]
    assert "text" in types and "done" in types
    assert _citation_events(events) == []


def test_notes_without_path_are_not_cited(service, app):
    notes = [{"title": "Orphan", "content": "x"}, NOTES[0]]
    _, events, _ = _run(service, app, notes)
    assert _citation_events(events)[0].data["citations"] == [
        {"path": "topics/a.md", "title": "Note A"},
    ]


# --- Web chat: chips under the answer, restored on reload -----------------

FRONTEND = Path(__file__).resolve().parents[1] / "src/mycelos/frontend"


def _chat_methods() -> str:
    html = (FRONTEND / "pages/chat.html").read_text(encoding="utf-8")
    load = html[html.index("async loadMessages() {"):html.index("async showBriefingIfDue() {")]
    send = html[html.index("async sendMessage() {"):html.index("async toggleRecording() {")]
    return load + send


_HARNESS = r"""
const assert = require('node:assert/strict');
const methods = process.argv[1];
global.t = (k) => k;
function chat(api) {
  global.MycelosAPI = api;
  return eval('({ messages: [], activeAgent: "Mycelos", activeSessionId: "s1", input: "", sending: false,'
    + ' sessions: [], selectedAgentId: "mycelos", pendingHandoffAgentId: null, focusItem: null,'
    + ' $store: { chat: {} }, $el: { querySelector() { return null; } },'
    + ' $nextTick(f) { f(); }, scrollToBottom() {}, ' + methods + '})');
}
const cited = [{ path: 'topics/a.md', title: 'Note A' }];
(async () => {
  // Live answer: the citations event lands on the answer it belongs to.
  const live = chat({ stream: async (_url, _body, on) => {
    on('citations', { citations: cited });
    on('text', { content: 'Answer.' });
    on('done', {});
  } });
  live.input = 'What about A?';
  await live.sendMessage();
  const answer = live.messages.find((m) => m.role === 'assistant');
  assert.equal(answer.content, 'Answer.');
  assert.deepEqual(answer.citations, cited);

  // Answer without context notes: no citations.
  const plain = chat({ stream: async (_u, _b, on) => { on('text', { content: 'Hi.' }); on('done', {}); } });
  plain.input = 'Hi';
  await plain.sendMessage();
  assert.equal((plain.messages.find((m) => m.role === 'assistant').citations || []).length, 0);

  // Reload: citations come back from the stored assistant message.
  const reload = chat({ get: async () => ({ messages: [
    { role: 'user', content: 'What about A?' },
    { role: 'assistant', content: 'Answer.', metadata: { citations: cited } },
    { role: 'assistant', content: 'Old answer.' },
  ] }) });
  await reload.loadMessages();
  assert.deepEqual(reload.messages[1].citations, cited);
  assert.deepEqual(reload.messages[2].citations, []);
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _node(script: str, *args: str) -> None:
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    result = subprocess.run(["node", "-e", script, *args],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_web_chat_shows_and_restores_citations():
    _node(_HARNESS, _chat_methods())


def test_web_chat_renders_citation_chips_as_note_links():
    html = (FRONTEND / "pages/chat.html").read_text(encoding="utf-8")
    block = html[html.index('data-testid="answer-citations"'):]
    block = block[:block.index("<!-- System message -->")]
    assert "t('chat.answer_sources')" in block
    assert "'/pages/knowledge.html?note=' + encodeURIComponent(c.path)" in block
    # Titles go in as text, never as HTML.
    assert 'x-text="c.title || c.path"' in block and "x-html" not in block


def test_answer_sources_label_exists_in_both_languages():
    from mycelos.i18n import get_web_translations

    assert get_web_translations("en")["chat"]["answer_sources"] == "Sources of this answer"
    assert get_web_translations("de")["chat"]["answer_sources"] == "Quellen dieser Antwort"
