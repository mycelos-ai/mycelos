"""Speech-to-text service abstraction used by the SecurityProxy.

Supports multiple providers behind one common interface so channels (web,
Telegram, API) don't need provider-specific logic.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import httpx


# STT alias a LiteLLM proxy serves when the settings name none.
CUSTOM_STT_DEFAULT_MODEL = "stt"


class SttError(RuntimeError):
    """Raised when transcription fails."""


@dataclass
class SttRequest:
    """Normalized transcription input."""

    audio: bytes
    filename: str
    mime_type: str
    model: str
    language: str


@dataclass
class SttResult:
    """Normalized transcription output."""

    text: str
    language: str = ""
    duration_seconds: float = 0.0


class BaseTranscriber:
    """Provider implementation contract."""

    def transcribe(self, request: SttRequest) -> SttResult:
        raise NotImplementedError


class OpenAITranscriber(BaseTranscriber):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def transcribe(self, request: SttRequest) -> SttResult:
        files_payload = {
            "file": (request.filename or "audio", request.audio, request.mime_type),
        }
        data_payload: dict[str, str] = {
            "model": request.model or "whisper-1",
            "response_format": "verbose_json",
        }
        if request.language and request.language != "auto":
            data_payload["language"] = request.language

        try:
            resp = httpx.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files_payload,
                data=data_payload,
                timeout=120,
            )
        except httpx.TimeoutException as exc:
            raise SttError("OpenAI STT timed out") from exc
        except httpx.RequestError as exc:
            raise SttError(f"OpenAI STT request failed: {exc}") from exc

        if resp.status_code != 200:
            raise SttError(f"OpenAI STT error: {resp.status_code}")

        payload = resp.json()
        return SttResult(
            text=payload.get("text", ""),
            language=payload.get("language", ""),
            duration_seconds=float(payload.get("duration", 0) or 0),
        )


class GoogleGeminiTranscriber(BaseTranscriber):
    """Audio transcription via Gemini generateContent endpoint."""

    def __init__(self, api_key: str, default_model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.default_model = default_model

    def transcribe(self, request: SttRequest) -> SttResult:
        model = request.model if request.model and request.model != "whisper-1" else self.default_model
        audio_b64 = base64.b64encode(request.audio).decode("ascii")
        body = {
            "contents": [{
                "parts": [
                    {"text": "Transcribe this audio. Return only the transcription text."},
                    {"inlineData": {"mimeType": request.mime_type, "data": audio_b64}},
                ]
            }],
            "generationConfig": {"temperature": 0},
        }
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        try:
            resp = httpx.post(
                endpoint,
                params={"key": self.api_key},
                json=body,
                timeout=120,
            )
        except httpx.TimeoutException as exc:
            raise SttError("Google STT timed out") from exc
        except httpx.RequestError as exc:
            raise SttError(f"Google STT request failed: {exc}") from exc

        if resp.status_code != 200:
            raise SttError(f"Google STT error: {resp.status_code}")

        payload = resp.json()
        parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
        return SttResult(text=text, language=request.language if request.language != "auto" else "")


class OpenAICompatibleTranscriber(BaseTranscriber):
    """Local/custom OpenAI-compatible STT backends (e.g. faster-whisper servers)."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def transcribe(self, request: SttRequest) -> SttResult:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data_payload = {"model": request.model or "whisper-1"}
        if request.language and request.language != "auto":
            data_payload["language"] = request.language

        try:
            resp = httpx.post(
                f"{self.base_url}/audio/transcriptions",
                headers=headers,
                files={"file": (request.filename, request.audio, request.mime_type)},
                data=data_payload,
                timeout=120,
            )
        except httpx.TimeoutException as exc:
            raise SttError("Local STT timed out") from exc
        except httpx.RequestError as exc:
            raise SttError(f"Local STT request failed: {exc}") from exc

        if resp.status_code != 200:
            raise SttError(f"Local STT error: {resp.status_code}")

        payload = resp.json()
        return SttResult(
            text=payload.get("text", ""),
            language=payload.get("language", ""),
            duration_seconds=float(payload.get("duration", payload.get("duration_seconds", 0)) or 0),
        )


# STT backends that keep audio inside EU-resident / on-device infrastructure.
# Cloud backends (OpenAI Whisper, Google) are not residency-guaranteed.
_EU_RESIDENT_STT_BACKENDS = {"local", "openai_compatible"}


def stt_backend_allowed_in_eu(provider: str) -> bool:
    """True if an STT backend may be used while EU mode is on."""
    return (provider or "").strip().lower() in _EU_RESIDENT_STT_BACKENDS


class SttService:
    """Factory + router for multiple STT providers."""

    def __init__(self, credential_lookup, eu_mode_check=None, settings_lookup=None):
        self._credential_lookup = credential_lookup
        self._default_provider = os.environ.get("MYCELOS_STT_PROVIDER", "openai").strip().lower()
        # Optional callable returning True when EU mode is on. When True, only
        # EU-resident STT backends are permitted.
        self._eu_mode_check = eu_mode_check
        # Optional callable(user_id) -> {"provider": ..., "model": ...} with the
        # choice saved in the web settings. It wins over MYCELOS_STT_PROVIDER.
        self._settings_lookup = settings_lookup

    @property
    def default_provider(self) -> str:
        return self._default_provider

    def _settings(self, user_id: str) -> dict:
        if self._settings_lookup is None:
            return {}
        try:
            return self._settings_lookup(user_id) or {}
        except Exception:
            return {}

    def resolve_provider(self, requested: str | None, user_id: str = "default") -> str:
        saved = str(self._settings(user_id).get("provider") or "")
        return (requested or saved or self._default_provider or "openai").strip().lower()

    def transcribe(self, request: SttRequest, provider: str | None = None, user_id: str = "default") -> SttResult:
        resolved = self.resolve_provider(provider, user_id=user_id)
        backend = self._create_backend(resolved, user_id=user_id)
        if resolved == "custom" and request.model in ("", "whisper-1"):
            # Channels ask for "whisper-1"; a proxy serves its own STT alias.
            request.model = str(self._settings(user_id).get("model") or "") or CUSTOM_STT_DEFAULT_MODEL
        return backend.transcribe(request)

    def _create_backend(self, provider: str, user_id: str = "default") -> BaseTranscriber:
        # EU-mode enforcement: refuse cloud STT backends fail-closed.
        if self._eu_mode_check is not None and self._eu_mode_check():
            if not stt_backend_allowed_in_eu(provider):
                raise SttError(
                    f"STT provider '{provider}' is not EU-resident; EU mode is on. "
                    "Use a local/openai_compatible STT backend."
                )

        if provider == "openai":
            cred = self._credential_lookup("openai", user_id=user_id)
            api_key = (cred or {}).get("api_key")
            if not api_key:
                raise SttError("STT provider 'openai' not configured — store credential first")
            return OpenAITranscriber(api_key=api_key)

        if provider in {"google", "gemini"}:
            cred = self._credential_lookup("gemini", user_id=user_id)
            api_key = (cred or {}).get("api_key")
            if not api_key:
                raise SttError("STT provider 'google' not configured — store gemini credential first")
            return GoogleGeminiTranscriber(
                api_key=api_key,
                default_model=os.environ.get("MYCELOS_STT_GOOGLE_MODEL", "gemini-2.0-flash"),
            )

        if provider == "custom":
            # The same OpenAI-compatible server (usually a LiteLLM proxy) that
            # serves the custom/* chat models, including the env fallback.
            from mycelos.llm.custom import resolve_custom_credential
            cred = resolve_custom_credential(self._credential_lookup("custom", user_id=user_id))
            base_url = str(cred.get("api_base") or "")
            if not base_url:
                raise SttError("STT provider 'custom' not configured — set up the custom provider first")
            return OpenAICompatibleTranscriber(base_url=base_url, api_key=cred.get("api_key") or None)

        if provider in {"local", "openai_compatible"}:
            base_url = os.environ.get("MYCELOS_STT_LOCAL_BASE_URL", "http://127.0.0.1:8000/v1")
            api_key = os.environ.get("MYCELOS_STT_LOCAL_API_KEY", "") or None
            return OpenAICompatibleTranscriber(base_url=base_url, api_key=api_key)

        raise SttError(f"Unsupported STT provider: {provider}")
