"""Host-header allowlist against DNS rebinding.

Threat: Mycelos runs on ``127.0.0.1:9100`` without a password (the
default). The user opens a malicious page on ``evil.example``. The page
lowers its DNS TTL and re-points ``evil.example`` to ``127.0.0.1``. From
then on the browser treats requests to ``http://evil.example:9100`` as
same-origin: the page can read every API response and POST to the API,
and both the localhost check (the TCP peer *is* 127.0.0.1) and the CSRF
check (Origin equals Host) pass.

Defence: the ``Host`` header still says ``evil.example``. The gateway only
answers for host names it knows: loopback names, the bind address, the
LAN names and addresses it detected for a ``0.0.0.0`` bind, the hosts
from ``MYCELOS_ALLOWED_ORIGINS`` and any extra names in
``MYCELOS_ALLOWED_HOSTS`` (comma-separated; ``*`` turns the check off).

With a password set the check is skipped: the attacker's origin never
holds the session cookie or the Basic-Auth credentials, so rebinding
gains nothing, and LAN / reverse-proxy setups keep working without extra
configuration.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlsplit

logger = logging.getLogger("mycelos.gateway")

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Paths that must stay reachable under a public host name. The Telegram
# webhook arrives through a tunnel / reverse proxy and authenticates with
# its own secret token (see routers/telegram_webhook.py).
EXEMPT_PATHS = frozenset({"/telegram/webhook"})


def normalize_host(value: str) -> str:
    """Return the lower-cased host part of a Host header or netloc.

    ``"[::1]:9100"`` → ``"::1"``, ``"Pi5.local:9100"`` → ``"pi5.local"``,
    ``"example.com."`` → ``"example.com"``. Returns ``""`` when the value
    cannot be parsed.
    """
    value = (value or "").strip().lower()
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return ""
        return value[1:end]
    # Bare IPv6 without brackets (no port possible).
    if value.count(":") > 1:
        try:
            return str(ipaddress.IPv6Address(value))
        except ValueError:
            return ""
    host = value.split(":", 1)[0]
    return host.rstrip(".")


def build_allowed_hosts(
    bind_host: str,
    origins: Iterable[str] = (),
    extra_hosts: str | None = None,
) -> frozenset[str] | None:
    """Collect the host names the gateway answers for.

    Returns ``None`` when the operator disabled the check with ``*``.
    """
    hosts: set[str] = set(LOOPBACK_HOSTS)
    bind = normalize_host(bind_host)
    if bind and bind not in ("0.0.0.0", "::"):
        hosts.add(bind)
    for origin in origins:
        try:
            netloc = urlsplit(origin.strip()).netloc
        except ValueError:
            continue
        host = normalize_host(netloc)
        if host:
            hosts.add(host)
    raw = extra_hosts if extra_hosts is not None else os.environ.get("MYCELOS_ALLOWED_HOSTS", "")
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if item == "*":
            return None
        host = normalize_host(item)
        if host:
            hosts.add(host)
    return frozenset(hosts)


class HostGuardMiddleware:
    """Pure ASGI middleware that rejects requests for unknown Host names."""

    def __init__(
        self,
        app: Callable[..., Awaitable[Any]],
        allowed_hosts: frozenset[str] | None,
        enforce: bool = True,
    ) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.enforce = enforce and allowed_hosts is not None
        self._warned: set[str] = set()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if not self.enforce or scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope.get("path") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        raw_host = ""
        for name, value in scope.get("headers") or []:
            if name == b"host":
                raw_host = value.decode("latin-1")
                break
        host = normalize_host(raw_host)

        # Fail closed: a missing or unparsable Host is rejected too.
        if host and self.allowed_hosts is not None and host in self.allowed_hosts:
            await self.app(scope, receive, send)
            return

        if host not in self._warned and len(self._warned) < 64:
            self._warned.add(host)
            logger.warning(
                "Rejected request for unknown host %r. If this is how you reach "
                "Mycelos, add it to MYCELOS_ALLOWED_HOSTS.", host or raw_host[:80],
            )

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({
            "error": "Host not allowed",
            "hint": "Add this host name to MYCELOS_ALLOWED_HOSTS or set MYCELOS_PASSWORD.",
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 421,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
