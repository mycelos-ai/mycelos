"""Which hosts a stored credential may be sent to.

The SecurityProxy injects secrets into outbound HTTP requests on behalf
of the gateway (``POST /http`` with ``inject_credential``). Without a
binding, a compromised gateway could ask the proxy to send the OpenAI
key or the Telegram bot token to any public server it controls — the
key would never be "visible" to the gateway, but it would still leak.

Every credential is therefore bound to the hosts of its own service:

- a fixed list for well-known services (below), always over HTTPS, and
- the host of the credential's own ``api_base`` / ``base_url`` / ``url``
  field, if it has one (self-hosted Ollama, a custom OpenAI-compatible
  server, …), with the scheme stored there.

Anything else is denied (fail-closed, Constitution rule 3).
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

# Service name (as stored in the credential store) → allowed host names.
# Exact host match only; add sub-domains explicitly.
SERVICE_HOSTS: Mapping[str, tuple[str, ...]] = {
    "telegram": ("api.telegram.org",),
    "openai": ("api.openai.com",),
    "anthropic": ("api.anthropic.com",),
    "github": ("api.github.com",),
    "brave": ("api.search.brave.com",),
    "openrouter": ("openrouter.ai",),
    "gemini": ("generativelanguage.googleapis.com",),
    "mistral": ("api.mistral.ai",),
    "groq": ("api.groq.com",),
}

_BASE_FIELDS = ("api_base", "base_url", "url")


def _split(url: str) -> tuple[str, str]:
    """Return (scheme, host) as the HTTP client will see them."""
    parsed = httpx.URL(url)
    return parsed.scheme.lower(), (parsed.host or "").lower().rstrip(".")


def credential_allows_url(service: str, credential: Mapping[str, Any] | None, url: str) -> bool:
    """True when ``credential`` (stored under ``service``) may go to ``url``."""
    try:
        scheme, host = _split(url)
    except Exception:
        return False
    if not host:
        return False

    if scheme == "https" and host in SERVICE_HOSTS.get((service or "").lower(), ()):
        return True

    for field in _BASE_FIELDS:
        base = (credential or {}).get(field)
        if not isinstance(base, str) or not base.strip():
            continue
        try:
            base_scheme, base_host = _split(base.strip())
        except Exception:
            continue
        if base_host and base_host == host and base_scheme == scheme:
            return True
    return False
