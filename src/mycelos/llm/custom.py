"""Explicit OpenAI-compatible routing, independent of public OpenAI settings.

The custom provider is typically a self-hosted LiteLLM proxy. Like the other
apps that share such a proxy, Mycelos falls back to the environment variables
``LITELLM_HOST`` (proxy root, with or without ``/v1``) and ``LITELLM_SECRET``
when the stored credential leaves the address or the token blank. Stored
values always win.
"""
import os
from urllib.parse import urlsplit

ENV_HOST = 'LITELLM_HOST'
ENV_SECRET = 'LITELLM_SECRET'


def env_api_base(environ=None):
    """Return the API base derived from LITELLM_HOST, always ending in /v1."""
    environ = os.environ if environ is None else environ
    host = (environ.get(ENV_HOST) or '').strip().rstrip('/')
    if not host:
        return ''
    return host if host.endswith('/v1') else host + '/v1'


def env_has_secret(environ=None):
    environ = os.environ if environ is None else environ
    return bool((environ.get(ENV_SECRET) or '').strip())


def resolve_custom_credential(credential, environ=None):
    """Fill a blank api_base / api_key from LITELLM_HOST / LITELLM_SECRET."""
    environ = os.environ if environ is None else environ
    resolved = dict(credential or {})
    if not str(resolved.get('api_base') or '').strip():
        base = env_api_base(environ)
        if base:
            resolved['api_base'] = base
    if not str(resolved.get('api_key') or '').strip():
        secret = (environ.get(ENV_SECRET) or '').strip()
        if secret:
            resolved['api_key'] = secret
    return resolved


def custom_generation_kwargs(model, options):
    """Let the remote server validate options for its opaque model aliases."""
    if not model.startswith('custom/'):
        return {}
    forwarded = [key for key in ('reasoning_effort', 'max_completion_tokens') if key in (options or {})]
    return {'allowed_openai_params': forwarded} if forwarded else {}


def custom_completion_kwargs(model, credential):
    credential = resolve_custom_credential(credential)
    url = credential.get('api_base', '')
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not credential.get('api_key')):
        raise ValueError('Bitte die Server-Adresse und das Bearer-Token für Custom einrichten.')
    return {'model': 'openai/' + model.split('/', 1)[1],
            'api_base': url, 'api_key': credential['api_key']}
