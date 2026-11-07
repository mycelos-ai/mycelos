"""Task-level generation settings. Proxy aliases remain opaque."""
import json
from mycelos.memory.service import SQLiteMemoryService

TASKS = {'execution': 'Gespräch und allgemeine Aufgaben',
         'source_analysis': 'Wissensaufbereitung', 'source_relations': 'Verknüpfung',
         'classification': 'Weitere Hintergrundaufgaben'}
EFFORTS = ('default', 'none', 'low', 'medium', 'high')


def read(storage, purpose):
    defaults = {'reasoning': 'default', 'fallbacks': [], 'max_output_tokens': None}
    if storage is None:
        return defaults
    raw = SQLiteMemoryService(storage).get('default', 'system', 'llm.task.' + purpose)
    return {**defaults, **(raw if isinstance(raw, dict) else json.loads(raw))} if raw else defaults


def save(app, purpose, data):
    if purpose not in TASKS:
        raise ValueError('Unbekannte Aufgabe')
    if set(data) - {'reasoning', 'fallbacks', 'max_output_tokens'}:
        raise ValueError('Unbekannte Einstellung')
    reasoning = data.get('reasoning', 'default')
    if reasoning not in EFFORTS:
        raise ValueError('Ungültige Einstellung für Denkschritte')
    limit = data.get('max_output_tokens')
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 1000000):
        raise ValueError('Das Ausgabelimit muss zwischen 1 und 1000000 liegen')
    fallbacks = data.get('fallbacks', [])
    if not isinstance(fallbacks, list) or len(fallbacks) > 8 or any(
            not isinstance(m, str) or not app.model_registry.get_model(m) for m in fallbacks):
        raise ValueError('Wähle höchstens acht registrierte Ersatzmodelle')
    value = {'reasoning': reasoning, 'fallbacks': list(dict.fromkeys(fallbacks)), 'max_output_tokens': limit}
    app.memory.set('default', 'system', 'llm.task.' + purpose, json.dumps(value))
    app.audit.log('llm.task_settings_changed', details={'purpose': purpose, **value})
    return value


def options(settings):
    result = {}
    if settings['reasoning'] != 'default':
        result['reasoning_effort'] = settings['reasoning']
    if settings['max_output_tokens'] is not None:
        result['max_completion_tokens'] = settings['max_output_tokens']
    if result:
        result['drop_params'] = False
    return result
