"""Read-only provider checks. No prompts, completions or embeddings."""
import json
from time import monotonic
from urllib.parse import urlsplit
import httpx

_ENDPOINTS = {
    'openai': 'https://api.openai.com/v1/models',
    'anthropic': 'https://api.anthropic.com/v1/models',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/models',
    'openrouter': 'https://openrouter.ai/api/v1/key',
    'mistral': 'https://api.mistral.ai/v1/models',
}


def _get(url, headers):
    # Never forward credentials through a redirect to another host.
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        return client.get(url, headers=headers)


def _vertex_login(credential):
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    info = json.loads(credential['vertex_credentials'])
    if info.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('Unexpected token endpoint')
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/cloud-platform'])
    transport = Request()
    def request(*args, **kwargs):
        kwargs['timeout'] = 10
        return transport(*args, **kwargs)
    credentials.refresh(request)
    if not credentials.token:
        raise ValueError('No token')


def check_provider(app, provider):
    start = monotonic()
    def result(ok, message, **extra):
        return {'ok': ok, 'message': message, 'elapsed_ms': round((monotonic()-start)*1000),
                'generation_tested': False, **extra}
    try:
        credential = app.credentials.get_credential(provider) or {}
        key = credential.get('api_key')
        if provider == 'vertex_ai':
            _vertex_login(credential)
            return result(True, 'Anmeldung erfolgreich. Projekt- und Modellzugriff wurden nicht geprüft.')
        headers = {}
        if provider in ('custom', 'ollama'):
            url = credential.get('api_base')
            if not url and provider == 'ollama':
                url = app.memory.get('default', 'system', 'ollama_url')
            parsed = urlsplit(url or '')
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                return result(False, 'Bitte zuerst eine gültige Server-Adresse speichern.')
            url = url.rstrip('/') + ('/models' if provider == 'custom' else '/api/tags')
        else:
            url = _ENDPOINTS[provider]
        if provider != 'ollama' and not key:
            return result(False, 'Bitte zuerst den Zugang speichern.')
        if provider == 'anthropic':
            headers = {'x-api-key':key, 'anthropic-version':'2023-06-01'}
        elif provider == 'gemini':
            headers = {'x-goog-api-key':key}
        elif key:
            headers = {'Authorization': 'Bearer ' + key}
        response = _get(url, headers)
        code = response.status_code
        if code in (401,403):
            return result(False, 'Der Anbieter lehnt den Zugang ab. Token und Berechtigungen prüfen.')
        if code == 404:
            return result(False, 'Der Prüfpfad fehlt. Server-Adresse und API-Basis prüfen. Ein Modellaufruf wurde nicht getestet.')
        if code == 429:
            return result(False, 'Der Anbieter begrenzt gerade die Anfragen. Bitte später erneut testen.')
        if not 200 <= code < 300:
            return result(False, f'Der Anbieter antwortet mit HTTP {code}.')
        body = response.json()
        if not isinstance(body, dict):
            return result(False, 'Der Server liefert keine passende API-Antwort.')
        if provider == 'openrouter':
            if not isinstance(body.get('data'), dict):
                return result(False, 'Der Server liefert keine passende API-Antwort.')
            return result(True, 'Zugang bestätigt. Modellaufrufe und Guthaben wurden nicht geprüft.')
        models = body.get('models' if provider in ('ollama','gemini') else 'data')
        if not isinstance(models, list):
            return result(False, 'Der Server liefert keine passende Modellliste.')
        return result(True, f'Verbindung erfolgreich. {len(models)} Modelle in der Antwort. Keine Textgenerierung geprüft.', model_count=len(models))
    except httpx.TimeoutException:
        return result(False, 'Zeitüberschreitung. Der Anbieter antwortet nicht rechtzeitig.')
    except httpx.RequestError:
        return result(False, 'Verbindung fehlgeschlagen. Adresse, Netzwerk und Zertifikat prüfen.')
    except Exception:
        # Remote errors and credential failures can contain secrets. Never echo them.
        return result(False, 'Der Test ist fehlgeschlagen. Zugang und Anbieter-Konfiguration prüfen.')


def discover_custom_models(app):
    """Fetch the server's model aliases without any completion or assignment."""
    from mycelos.llm.custom import custom_completion_kwargs
    try:
        settings = custom_completion_kwargs('custom/discovery', app.credentials.get_credential('custom'))
        response = _get(settings['api_base'].rstrip('/') + '/models',
                        {'Authorization': 'Bearer ' + settings['api_key']})
        if response.status_code == 405:
            raise ValueError('Der Server erlaubt GET /models an dieser API-Basis nicht. Die Adresse soll auf /v1 enden, nicht auf /chat/completions.')
        if response.status_code in (401, 403):
            raise ValueError('Der Server lehnt den Zugang zur Modellliste ab. Token und Berechtigungen prüfen.')
        if response.status_code != 200:
            raise ValueError(f'Die Modellliste ist nicht verfügbar (HTTP {response.status_code}). API-Basis prüfen.')
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise ValueError('Der Server liefert keine OpenAI-kompatible Modellliste.')
        models, seen = [], set()
        for item in data['data']:
            name = item.get('id') if isinstance(item, dict) else None
            if not isinstance(name, str) or not name or len(name) > 256 or any(c.isspace() for c in name):
                raise ValueError('Die Modellliste enthält eine ungültige Modell-ID.')
            if name in seen:
                continue
            seen.add(name)
            identifier = 'custom/' + name
            existing = app.model_registry.get_model(identifier) or {}
            models.append({'id': identifier, 'name': name, 'tier': existing.get('tier', 'sonnet')})
        return models
    except httpx.TimeoutException:
        raise ValueError('Zeitüberschreitung beim Laden der Modellliste.') from None
    except httpx.RequestError:
        raise ValueError('Die Modellliste ist nicht erreichbar. Adresse und Netzwerk prüfen.') from None
