"""SecurityProxy client — synchronous HTTP client over Unix Domain Socket or TCP."""

from __future__ import annotations

import httpx


class ProxyUnavailableError(Exception):
    """Raised when the SecurityProxy is unreachable."""
    pass


class SecurityProxyClient:
    """Gateway-side client for the SecurityProxy.

    Synchronous — uses httpx.Client with either Unix socket transport (UDS)
    or plain TCP. All external network access, MCP calls, and LLM completions
    flow through the SecurityProxy, keeping credentials out of agent processes.
    """

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        url: str | None = None,
        token: str,
    ) -> None:
        """Connect to a SecurityProxy over Unix socket OR TCP.

        socket_path: path to AF_UNIX socket (in-process / single-container).
        url: http://host:port base URL (cross-container TCP).
        Exactly one must be given.
        """
        if bool(socket_path) == bool(url):
            raise ValueError(
                "SecurityProxyClient needs exactly one of socket_path= or url="
            )

        self._socket_path = socket_path
        self._token = token

        if socket_path:
            self._client = httpx.Client(
                transport=httpx.HTTPTransport(uds=socket_path),
                base_url="http://proxy",  # arbitrary hostname, routed via socket
                headers={"Authorization": f"Bearer {token}"},
                timeout=60.0,
            )
            self.base_url = "http://proxy"
        else:
            self._client = httpx.Client(
                base_url=url.rstrip("/"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=60.0,
                trust_env=False,  # ignore system proxy settings for direct TCP
            )
            self.base_url = url.rstrip("/")

    def _request(self, method: str, path: str, **kwargs):
        """Make request to proxy, raise ProxyUnavailableError on connection failure.

        Also handles HTTP 500 errors gracefully — returns an error dict
        instead of crashing on non-JSON responses.
        """
        try:
            resp = self._client.request(method, path, **kwargs)
            if resp.status_code >= 500:
                try:
                    return type("R", (), {"json": lambda self: resp.json(), "status_code": resp.status_code})()
                except Exception:
                    # Non-JSON error response — wrap it
                    error_resp = {"error": f"Proxy error {resp.status_code}: {resp.text[:200]}"}
                    return type("R", (), {"json": lambda self, e=error_resp: e, "status_code": resp.status_code})()
            return resp
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise ProxyUnavailableError(f"SecurityProxy unreachable: {e}")

    def http_get(self, url: str, headers: dict | None = None,
                 credential: str | None = None, inject_as: str | None = None,
                 inline_credential: str | None = None,
                 timeout: int = 30,
                 user_id: str = "default", agent_id: str | None = None) -> dict:
        """Proxy a GET request through the SecurityProxy.

        ``credential`` / ``inject_as`` let the proxy inject the raw secret
        without ever revealing it to the gateway:

        - ``inject_as="bearer"`` (default) — adds ``Authorization: Bearer <key>``
        - ``inject_as="header:X-Name"`` — adds a custom header
        - ``inject_as="url_path"`` — replaces the literal ``{credential}``
          token in the URL (e.g. Telegram's ``https://api.telegram.org/bot{credential}/…``)

        ``inline_credential`` is for stateless verification flows — Telegram
        setup-wizard testing a token before it's stored. The proxy
        substitutes it the same way, just without a credential-store
        lookup. Caller supplies the secret; proxy never persists it.
        """
        extra_headers: dict = {"X-User-Id": user_id}
        if agent_id:
            extra_headers["X-Agent-Id"] = agent_id
        resp = self._request("POST", "/http", json={
            "method": "GET",
            "url": url,
            "headers": headers or {},
            "timeout": timeout,
            "inject_credential": credential,
            "inject_as": inject_as,
            "inline_credential": inline_credential,
        }, headers=extra_headers)
        return resp.json()

    def http_post(self, url: str, body=None, headers: dict | None = None,
                  credential: str | None = None, inject_as: str | None = None,
                  inline_credential: str | None = None,
                  timeout: int = 30,
                  user_id: str = "default", agent_id: str | None = None) -> dict:
        """Proxy a POST request through the SecurityProxy.

        See :meth:`http_get` for ``credential`` / ``inject_as`` /
        ``inline_credential`` semantics.
        """
        extra_headers: dict = {"X-User-Id": user_id}
        if agent_id:
            extra_headers["X-Agent-Id"] = agent_id
        resp = self._request("POST", "/http", json={
            "method": "POST",
            "url": url,
            "headers": headers or {},
            "body": body,
            "timeout": timeout,
            "inject_credential": credential,
            "inject_as": inject_as,
            "inline_credential": inline_credential,
        }, headers=extra_headers)
        return resp.json()

    def mcp_start(self, connector_id: str, command: list[str],
                  env_vars: dict, transport: str = "stdio",
                  user_id: str = "default") -> dict:
        """Start an MCP session via the SecurityProxy."""
        resp = self._request("POST", "/mcp/start", json={
            "connector_id": connector_id,
            "command": command,
            "env_vars": env_vars,
            "transport": transport,
        }, headers={"X-User-Id": user_id})
        return resp.json()

    def mcp_call(self, session_id: str, tool: str, arguments: dict,
                 user_id: str = "default", agent_id: str | None = None) -> dict:
        """Invoke a tool on an active MCP session."""
        extra_headers: dict = {"X-User-Id": user_id}
        if agent_id:
            extra_headers["X-Agent-Id"] = agent_id
        resp = self._request("POST", "/mcp/call", json={
            "session_id": session_id,
            "tool": tool,
            "arguments": arguments,
        }, headers=extra_headers)
        return resp.json()

    def mcp_stop(self, session_id: str) -> None:
        """Terminate an active MCP session."""
        self._request("POST", "/mcp/stop", json={"session_id": session_id})

    def oauth_start(self, oauth_cmd: str, env_vars: dict, user_id: str = "default") -> dict:
        """Spawn an OAuth auth subprocess in the proxy. Returns {session_id}.

        Pair this with the WebSocket at /oauth/stream/{session_id} to
        actually interact with the process. Always call oauth_stop when
        done — subprocesses don't auto-clean on client disconnect.
        """
        resp = self._request("POST", "/oauth/start", json={
            "oauth_cmd": oauth_cmd,
            "env_vars": env_vars,
        }, headers={"X-User-Id": user_id})
        return resp.json()

    def oauth_stop(self, session_id: str, user_id: str = "default") -> dict:
        """Terminate an OAuth session. Idempotent — stopping an unknown
        or already-stopped session returns status=not_found (200)."""
        resp = self._request("POST", "/oauth/stop", json={
            "session_id": session_id,
        }, headers={"X-User-Id": user_id})
        return resp.json()

    def oauth_stream_url(self, session_id: str) -> str:
        """Return the ws:// URL for the OAuth streaming endpoint.

        The gateway doesn't itself *use* the WebSocket — it mints the
        URL for the browser, which opens it through the gateway's own
        WS passthrough (Task 6). Exposing it here keeps the URL-
        construction logic in one place.
        """
        base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{base}/oauth/stream/{session_id}"

    def llm_complete(self, model: str, messages: list[dict],
                     tools: list[dict] | None = None, stream: bool = False,
                     user_id: str = "default", agent_id: str | None = None,
                     purpose: str = "chat"):
        """Request an LLM completion via the SecurityProxy.

        Streaming (stream=True) is reserved for Task 4; currently non-streaming only.
        """
        extra_headers: dict = {"X-User-Id": user_id}
        if agent_id:
            extra_headers["X-Agent-Id"] = agent_id
        resp = self._request("POST", "/llm/complete", json={
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": stream,
            "purpose": purpose,
        }, headers=extra_headers)
        return resp.json()

    def stt_transcribe(self, audio: bytes, filename: str = "audio.ogg",
                       language: str = "auto", model: str = "whisper-1",
                       user_id: str = "default",
                       provider: str | None = None) -> dict:
        """Transcribe audio via SecurityProxy.

        Returns: {"text": "...", "language": "...", "duration_seconds": N}
        """
        payload = {"language": language, "model": model}
        if provider:
            payload["provider"] = provider
        resp = self._request("POST", "/stt/transcribe",
            files={"audio": (filename, audio)},
            data=payload,
            headers={"X-User-Id": user_id},
        )
        return resp.json()

    def health(self) -> dict:
        """Check SecurityProxy health."""
        resp = self._request("GET", "/health")
        return resp.json()

    def credential_store(
        self,
        service: str,
        payload: dict,
        *,
        label: str = "default",
        description: str | None = None,
    ) -> dict:
        """Encrypt and store a credential via the proxy.

        The plaintext payload leaves THIS process encrypted-at-rest only after
        it reaches the proxy, but within this single RPC the payload is sent
        in cleartext HTTPS/HTTP body over the shared bearer-authenticated
        channel to the proxy container.
        """
        resp = self._request(
            "POST",
            "/credential/store",
            json={
                "service": service,
                "label": label,
                "payload": payload,
                "description": description,
            },
        )
        return resp.json() if hasattr(resp, "json") else {}

    def credential_delete(self, service: str, label: str = "default") -> dict:
        """Remove a credential via the proxy."""
        resp = self._request("DELETE", f"/credential/{service}/{label}")
        return resp.json() if hasattr(resp, "json") else {}

    def credential_list(self) -> list[dict]:
        """Return credential METADATA only — never plaintext."""
        resp = self._request("GET", "/credential/list")
        if not hasattr(resp, "json"):
            return []
        data = resp.json()
        return data.get("credentials", []) if isinstance(data, dict) else []

    def credential_rotate(self, service: str, label: str = "default") -> dict:
        """Mark a credential as security-rotated."""
        resp = self._request(
            "POST",
            "/credential/rotate",
            json={"service": service, "label": label},
        )
        return resp.json() if hasattr(resp, "json") else {}

    def credential_materialize(self, service: str) -> dict:
        """Fetch a plaintext credential (long-lived session token).

        Only works for a narrow allow-list of services (currently
        ``telegram``) and only inside the bootstrap window. Used by the
        gateway to hold a token that aiogram's authenticated long-poll
        session requires. Returns ``{"service": "...", "api_key": "..."}``
        on success, or ``{"error": "..."}`` on refusal.
        """
        resp = self._request(
            "POST",
            "/credential/materialize",
            json={"service": service},
        )
        return resp.json() if hasattr(resp, "json") else {}

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
