"""Open shared configuration forms without exposing secrets to the model."""
from mycelos.chat.events import ChatEvent
from mycelos.tools.registry import ToolPermission


def execute(args, context):
    section = args.get('section')
    if section not in ('models', 'telegram', 'connectors'):
        raise ValueError('Unbekannter Einstellungsbereich')
    context.setdefault('_pending_events', []).append(
        ChatEvent(type='widget', data={'widget': {'type':'settings', 'section':section}}))
    return {'status':'form_opened', 'section':section}


def register(registry):
    registry.register('configure_settings', {'type':'function', 'function':{
        'name':'configure_settings',
        'description':'Open the shared settings form in web chat: models, Telegram, or MCP connections. This does not save settings, call a model, or import knowledge. Ask the user to enter secrets only in the form, never in a chat message.',
        'parameters':{'type':'object','properties':{'section':{'type':'string','enum':['models','telegram','connectors']}},'required':['section'],'additionalProperties':False}
    }}, execute, ToolPermission.OPEN, category='knowledge_manage')
