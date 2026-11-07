"""Explicit OpenAI-compatible routing, independent of public OpenAI settings."""
from urllib.parse import urlsplit


def custom_generation_kwargs(model, options):
    """Let the remote server validate options for its opaque model aliases."""
    if not model.startswith('custom/'):
        return {}
    forwarded = [key for key in ('reasoning_effort', 'max_completion_tokens') if key in (options or {})]
    return {'allowed_openai_params': forwarded} if forwarded else {}


def custom_completion_kwargs(model, credential):
    credential = credential or {}
    url = credential.get('api_base', '')
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not credential.get('api_key')):
        raise ValueError('Bitte die Server-Adresse und das Bearer-Token für Custom einrichten.')
    return {'model': 'openai/' + model.split('/', 1)[1],
            'api_base': url, 'api_key': credential['api_key']}
