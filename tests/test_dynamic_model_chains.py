"""Model choices remain current and cannot escape an explicit source model."""
import pytest

from mycelos.app import App
from mycelos.chat.service import ChatService
from mycelos.llm.broker import LiteLLMBroker, LLMResponse


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'test-dynamic-models')
    app = App(tmp_path)
    app.initialize()
    app.agent_registry.register("mycelos", "Mycelos", "test", [], "system")
    for name in ('old', 'new', 'backup', 'classify', 'agent'):
        app.model_registry.add_model('ollama/' + name, 'ollama', 'sonnet')
    yield app
    app.storage.close()


def fake_calls(monkeypatch, broker, fail=()):
    calls = []
    def complete(model, messages, tools=None, stream=False):
        calls.append(model)
        if model in fail:
            raise RuntimeError('rate limit')
        return LLMResponse(content=model, total_tokens=0, model=model)
    monkeypatch.setattr(broker, '_complete_single', complete)
    return calls


def test_explicit_source_model_never_uses_global_fallback(monkeypatch):
    broker = LiteLLMBroker(fallback_models=['openai/unwanted'])
    calls = fake_calls(monkeypatch, broker, fail={'ollama/source'})
    with pytest.raises(RuntimeError, match='rate limit'):
        broker.complete([{'role':'user', 'content':'source'}], model='ollama/source')
    assert calls == ['ollama/source']


def test_cached_app_broker_uses_current_system_chain(app, monkeypatch):
    app.model_registry.set_system_defaults({'execution':['ollama/old']})
    broker = app.llm
    calls = fake_calls(monkeypatch, broker, fail={'ollama/new'})
    app.model_registry.set_system_defaults({'execution':['ollama/new','ollama/backup']})
    result = broker.complete([{'role':'user','content':'hello'}])
    assert result.model == 'ollama/backup'
    assert calls == ['ollama/new','ollama/backup']


def test_purpose_uses_its_own_current_chain(app, monkeypatch):
    app.model_registry.set_system_defaults({'execution':['ollama/old'], 'classification':['ollama/classify']})
    calls = fake_calls(monkeypatch, app.llm)
    result = app.llm.complete([], purpose='classification')
    assert result.model == 'ollama/classify'
    assert calls == ['ollama/classify']


def test_removed_system_choice_does_not_use_old_cloud_default(app, monkeypatch):
    app.model_registry.set_system_defaults({'execution':['ollama/old']})
    broker = app.llm
    calls = fake_calls(monkeypatch, broker)
    app.model_registry.set_system_defaults({})
    with pytest.raises(RuntimeError, match='No models'):
        broker.complete([])
    assert calls == []


def test_chat_uses_agent_fallback_and_reads_new_selection_each_call(app, monkeypatch):
    app.model_registry.set_system_defaults({'execution':['ollama/old']})
    app.model_registry.set_agent_models('mycelos', ['ollama/agent','ollama/backup'])
    calls = fake_calls(monkeypatch, app.llm, fail={'ollama/agent'})
    service = ChatService(app)
    result = service._complete_for_agent([], 'mycelos')
    assert result.model == 'ollama/backup'
    app.model_registry.set_agent_models('mycelos', ['ollama/new'])
    assert service._complete_for_agent([], 'mycelos').model == 'ollama/new'
    assert calls == ['ollama/agent','ollama/backup','ollama/new']


def run_background(app, task):
    from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler
    from mycelos.knowledge.reminder import ReminderService
    from mycelos.knowledge.briefing import synthesize
    from mycelos.scheduler.session_summary import extract_session_memory, review_agent_memory_entries
    messages = [{'role':'user','content':'Remember this preference.'}]
    if task == 'organizer':
        return KnowledgeOrganizerHandler(app)._classify_batch([{'path':'notes/test','title':'Test'}], [])
    if task == 'reminder':
        return ReminderService(app).generate_message([{'title':'Test task','due':'2026-09-06'}])
    if task == 'briefing':
        return synthesize(app, {'tasks': []})
    if task == 'review':
        app.memory.set('default','system','user.preference.test','Test',created_by='agent')
        return review_agent_memory_entries(app, 'test-session', messages)
    return extract_session_memory(app, messages)


@pytest.mark.parametrize('task', ['organizer','reminder','briefing','summary','review'])
def test_background_work_never_uses_execution_models_without_its_own_choice(app, monkeypatch, task):
    app.model_registry.set_system_defaults({'execution':['ollama/old']})
    calls = fake_calls(monkeypatch, app.llm)
    run_background(app, task)
    assert calls == []


@pytest.mark.parametrize('task', ['organizer','reminder','briefing','summary','review'])
def test_background_work_uses_its_own_fallback_chain(app, monkeypatch, task):
    app.model_registry.set_system_defaults({'execution':['ollama/old'], 'classification':['ollama/classify','ollama/backup']})
    calls = fake_calls(monkeypatch, app.llm, fail={'ollama/classify'})
    run_background(app, task)
    assert calls == ['ollama/classify','ollama/backup']


def test_stream_uses_current_system_choice(app, monkeypatch):
    app.model_registry.set_system_defaults({'execution':['ollama/old']})
    broker = app.llm
    app.model_registry.set_system_defaults({'execution':['ollama/new']})
    calls = []
    import litellm
    monkeypatch.setattr(litellm, 'completion', lambda **kwargs: calls.append(kwargs['model']) or iter([]))
    list(broker.complete_stream([]))
    assert calls == ['ollama/new']
