# Speech-to-Text — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable voice input via Telegram, Web API, and Frontend — audio is transcribed via Whisper through the SecurityProxy and processed as chat messages.

**Architecture:** New `/stt/transcribe` endpoint on SecurityProxy handles Whisper API calls with credential injection. Gateway exposes `/api/audio` for web clients. Telegram gets a voice message handler. Frontend gets a record button.

**Tech Stack:** OpenAI Whisper API, SecurityProxy (FastAPI), aiogram voice handler, MediaRecorder API (browser)

**Spec:** `docs/superpowers/specs/2026-03-25-speech-to-text-design.md`

---

## File Structure

### New Files
- `tests/test_stt.py` — Unit tests for STT endpoint, client, and Telegram handler

### Modified Files
- `src/mycelos/security/proxy_server.py` — Add `POST /stt/transcribe` endpoint
- `src/mycelos/security/proxy_client.py` — Add `stt_transcribe()` method
- `src/mycelos/protocols.py` — Add `stt_transcribe` to SecurityProxyProtocol
- `src/mycelos/gateway/routes.py` — Add `POST /api/audio` route
- `src/mycelos/channels/telegram.py` — Add voice message handler
- `src/mycelos/frontend/out/index.html` — Add record button (inline JS)

---

## Task 1: SecurityProxy STT Endpoint

**Files:**
- Modify: `src/mycelos/security/proxy_server.py`
- Create: `tests/test_stt.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_stt.py
"""Tests for Speech-to-Text — proxy endpoint, client, Telegram handler."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import BytesIO

import pytest

SESSION_TOKEN = "stt-test-token-" + "x" * 48


@pytest.fixture
def proxy_app():
    os.environ["MYCELOS_PROXY_TOKEN"] = SESSION_TOKEN
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-stt"
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_DB_PATH"] = str(Path(tmp) / "test.db")
        from mycelos.storage.database import SQLiteStorage
        SQLiteStorage(Path(tmp) / "test.db").initialize()
        from mycelos.security.proxy_server import create_proxy_app
        yield create_proxy_app()


@pytest.fixture
def client(proxy_app):
    from starlette.testclient import TestClient
    return TestClient(proxy_app)


AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "default"}


class TestSttEndpoint:
    def test_requires_auth(self, client):
        resp = client.post("/stt/transcribe",
            files={"audio": ("test.ogg", b"fake-audio")})
        assert resp.status_code == 401

    def test_rejects_oversized_audio(self, client):
        big_audio = b"x" * (26 * 1024 * 1024)  # 26MB
        resp = client.post("/stt/transcribe",
            files={"audio": ("test.ogg", big_audio)},
            headers=AUTH)
        assert resp.status_code == 413

    def test_fails_without_credential(self, client):
        resp = client.post("/stt/transcribe",
            files={"audio": ("test.ogg", b"fake-audio")},
            headers=AUTH)
        data = resp.json()
        assert "not configured" in data.get("error", "").lower() or resp.status_code == 400

    def test_transcribes_with_mock_whisper(self, client):
        # Mock credential proxy to return a key
        with patch("mycelos.security.proxy_server._get_credential") as mock_cred, \
             patch("mycelos.security.proxy_server.httpx") as mock_httpx:
            mock_cred.return_value = {"api_key": "sk-test-key"}
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "text": "Hello world",
                "language": "en",
                "duration": 2.5,
            }
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception

            resp = client.post("/stt/transcribe",
                files={"audio": ("voice.ogg", b"fake-ogg-data")},
                data={"language": "auto", "model": "whisper-1"},
                headers=AUTH)
            assert resp.status_code == 200
            data = resp.json()
            assert data["text"] == "Hello world"
            assert data["language"] == "en"
            assert data["duration_seconds"] == 2.5

    def test_empty_transcription_returns_empty(self, client):
        with patch("mycelos.security.proxy_server._get_credential") as mock_cred, \
             patch("mycelos.security.proxy_server.httpx") as mock_httpx:
            mock_cred.return_value = {"api_key": "sk-test"}
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"text": "", "duration": 0}
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception

            resp = client.post("/stt/transcribe",
                files={"audio": ("voice.ogg", b"silence")},
                headers=AUTH)
            assert resp.status_code == 200
            assert resp.json()["text"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stt.py -v`
Expected: failures (endpoint doesn't exist yet)

- [ ] **Step 3: Implement /stt/transcribe endpoint**

Add to `src/mycelos/security/proxy_server.py`:

1. Add Pydantic model (or use Form fields since it's multipart):
```python
# No Pydantic model needed — multipart uses UploadFile + Form params
```

2. Add a helper `_get_credential(service)` that wraps the credential proxy lookup (if not already extracted — check if the existing code already has this pattern, and reuse it).

3. Add the endpoint:
```python
@app.post("/stt/transcribe")
async def stt_transcribe(
    audio: UploadFile,
    language: str = Form("auto"),
    model: str = Form("whisper-1"),
    request: Request,
):
    authorized, user_id = _check_auth(request)
    if not authorized:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    audio_bytes = await audio.read()
    if len(audio_bytes) > 25 * 1024 * 1024:
        return JSONResponse({"error": "Audio file too large (max 25MB)"}, status_code=413)

    provider = _state.get("stt_provider", "openai")
    cred = _get_credential(provider)
    if not cred or not cred.get("api_key"):
        return JSONResponse({"error": "STT credential not configured"}, status_code=400)

    data = {"model": model, "response_format": "verbose_json"}
    if language != "auto":
        data["language"] = language

    try:
        resp = httpx.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {cred['api_key']}"},
            files={"file": (audio.filename or "audio.ogg", audio_bytes)},
            data=data,
            timeout=30,
        )
        result = resp.json()
    except Exception as e:
        return JSONResponse({"error": f"STT failed: {e}", "status": 0})

    duration = result.get("duration", 0)
    _audit_log("proxy.stt_transcribe", user_id, {
        "provider": provider, "model": model, "audio_size": len(audio_bytes),
        "language": result.get("language", "unknown"), "duration": duration,
    })

    return {
        "text": result.get("text", ""),
        "language": result.get("language", "unknown"),
        "duration_seconds": duration,
    }
```

You'll also need to add `from fastapi import Form, UploadFile` to the imports if not already there.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_stt.py -v`
Expected: All pass

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/security/proxy_server.py tests/test_stt.py
git commit -m "feat: SecurityProxy /stt/transcribe — Whisper API endpoint"
```

---

## Task 2: SecurityProxyClient + Protocol Update

**Files:**
- Modify: `src/mycelos/security/proxy_client.py`
- Modify: `src/mycelos/protocols.py`
- Modify: `tests/test_stt.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_stt.py`:

```python
class TestSttClient:
    def test_client_sends_multipart(self):
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"text": "hello", "language": "en", "duration_seconds": 1.0}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            result = proxy.stt_transcribe(b"audio-bytes", filename="voice.ogg", user_id="stefan")
            assert result["text"] == "hello"
            # Verify multipart was sent
            call_args = mock_client.request.call_args
            assert "files" in call_args[1] or "files" in call_args.kwargs

    def test_client_returns_dict(self):
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"text": "test", "language": "de", "duration_seconds": 2.0}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            result = proxy.stt_transcribe(b"data")
            assert result["language"] == "de"
```

- [ ] **Step 2: Add stt_transcribe to SecurityProxyProtocol**

In `src/mycelos/protocols.py`, add to `SecurityProxyProtocol`:

```python
def stt_transcribe(self, audio: bytes, filename: str = "audio.ogg",
                   language: str = "auto", model: str = "whisper-1",
                   user_id: str = "default") -> dict: ...
```

- [ ] **Step 3: Implement stt_transcribe on SecurityProxyClient**

In `src/mycelos/security/proxy_client.py`:

```python
def stt_transcribe(self, audio: bytes, filename: str = "audio.ogg",
                   language: str = "auto", model: str = "whisper-1",
                   user_id: str = "default") -> dict:
    """Transcribe audio via SecurityProxy."""
    resp = self._request("POST", "/stt/transcribe",
        files={"audio": (filename, audio)},
        data={"language": language, "model": model},
        headers={"X-User-Id": user_id},
    )
    return resp.json()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_stt.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/proxy_client.py src/mycelos/protocols.py tests/test_stt.py
git commit -m "feat: SecurityProxyClient.stt_transcribe + Protocol update"
```

---

## Task 3: Gateway /api/audio Route

**Files:**
- Modify: `src/mycelos/gateway/routes.py`
- Modify: `tests/test_stt.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_stt.py`:

```python
class TestGatewayAudioRoute:
    def test_audio_route_exists(self):
        """Verify /api/audio route is registered."""
        import os, tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        from mycelos.app import App

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-route"
            app = App(Path(tmp))
            app.initialize()

            # Mock proxy_client
            mock_proxy = MagicMock()
            mock_proxy.stt_transcribe.return_value = {
                "text": "Hello from audio",
                "language": "en",
                "duration_seconds": 1.0,
            }
            app.set_proxy_client(mock_proxy)

            from mycelos.gateway.routes import setup_routes
            from fastapi import FastAPI
            api = FastAPI()
            api.state.mycelos = app
            api.state.chat_service = MagicMock()
            api.state.chat_service.handle_message.return_value = []
            api.state.chat_service.create_session.return_value = "test-sess"
            api.state.debug = False
            setup_routes(api)

            from starlette.testclient import TestClient
            client = TestClient(api)

            resp = client.post("/api/audio",
                files={"audio": ("voice.ogg", b"fake-audio")},
                params={"session_id": "test-sess", "user_id": "default"})
            # Should process without error
            assert resp.status_code == 200
```

- [ ] **Step 2: Implement /api/audio route**

In `src/mycelos/gateway/routes.py`, inside `setup_routes()`, add:

```python
@api.post("/api/audio")
async def handle_audio(
    request: Request,
    audio: UploadFile,
    session_id: str = "",
    user_id: str = "default",
):
    """Accept audio upload, transcribe via proxy, process as chat message."""
    from mycelos.chat.events import system_response_event, text_event, done_event, session_event, error_event

    service = api.state.chat_service
    mycelos = api.state.mycelos

    if not mycelos.proxy_client:
        async def no_proxy():
            yield error_event("Voice transcription not available — SecurityProxy not running").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(no_proxy(), media_type="text/event-stream")

    # Create session if needed
    if not session_id:
        session_id = service.create_session(user_id=user_id)

    # Read audio
    audio_bytes = await audio.read()
    if len(audio_bytes) > 25 * 1024 * 1024:
        async def too_large():
            yield error_event("Audio file too large (max 25MB)").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(too_large(), media_type="text/event-stream")

    # Transcribe
    try:
        result = mycelos.proxy_client.stt_transcribe(
            audio=audio_bytes,
            filename=audio.filename or "audio.webm",
            user_id=user_id,
        )
        text = result.get("text", "")
    except Exception as e:
        async def stt_error():
            yield error_event(f"Transcription failed: {e}").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(stt_error(), media_type="text/event-stream")

    if not text.strip():
        async def empty():
            yield system_response_event("Could not understand the audio.").to_sse()
            yield done_event().to_sse()
        return StreamingResponse(empty(), media_type="text/event-stream")

    # Process as chat message with [Voice] prefix
    message = f"[Voice] {text}"
    try:
        events = service.handle_message(message, session_id, user_id)
    except Exception as exc:
        from mycelos.chat.events import error_event as ee
        events = [ee("An internal error occurred."), done_event()]

    async def stream():
        yield session_event(session_id).to_sse()
        for event in events:
            yield event.to_sse()
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
```

Also add `from fastapi import UploadFile` to imports at top of routes.py.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_stt.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_stt.py
git commit -m "feat: Gateway /api/audio — voice upload + transcription + chat"
```

---

## Task 4: Telegram Voice Handler

**Files:**
- Modify: `src/mycelos/channels/telegram.py`
- Modify: `tests/test_stt.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_stt.py`:

```python
class TestTelegramVoiceHandler:
    def test_voice_handler_calls_stt(self):
        """Verify voice handler downloads audio and calls stt_transcribe."""
        # This is a unit test of the handler logic, not a full Telegram integration
        from unittest.mock import AsyncMock
        import asyncio

        from mycelos.channels import telegram

        # Setup mocks
        mock_bot = MagicMock()
        mock_file = MagicMock()
        mock_file.file_path = "voice/file_123.ogg"
        mock_bot.get_file = AsyncMock(return_value=mock_file)

        mock_audio_bytes = MagicMock()
        mock_audio_bytes.read.return_value = b"fake-ogg-audio"
        mock_bot.download_file = AsyncMock(return_value=mock_audio_bytes)

        mock_proxy = MagicMock()
        mock_proxy.stt_transcribe.return_value = {
            "text": "Test transcription",
            "language": "en",
            "duration_seconds": 2.0,
        }

        mock_chat_service = MagicMock()
        mock_chat_service.handle_message.return_value = []

        # Set module-level state
        telegram._bot = mock_bot
        telegram._app = MagicMock()
        telegram._app.proxy_client = mock_proxy
        telegram._chat_service = mock_chat_service
        telegram._allowed_users = set()

        # Create a mock message
        mock_message = MagicMock()
        mock_message.from_user.id = 123456
        mock_message.voice.file_id = "file_xyz"
        mock_message.voice.duration = 2
        mock_message.answer_chat_action = AsyncMock()

        # Call the handler
        asyncio.run(telegram.handle_voice_message(mock_message))

        # Verify proxy was called
        mock_proxy.stt_transcribe.assert_called_once()
        call_args = mock_proxy.stt_transcribe.call_args
        assert call_args.kwargs.get("user_id") == "telegram:123456" or \
               "telegram:123456" in str(call_args)
```

- [ ] **Step 2: Implement voice handler**

In `src/mycelos/channels/telegram.py`:

1. Add `from aiogram import F` to imports (alongside existing `from aiogram import Bot, Dispatcher, types`)

2. Add voice handler BEFORE the existing `@dp.message()` handler (line 148). The order matters — aiogram matches handlers in registration order:

```python
@dp.message(F.voice)
async def handle_voice_message(message: types.Message) -> None:
    """Handle incoming voice messages — transcribe and process as text."""
    if not _chat_service or not _bot:
        return

    user_id = message.from_user.id if message.from_user else 0

    # Check allowlist
    if _allowed_users and user_id not in _allowed_users:
        logger.warning("Unauthorized Telegram voice from: %s", user_id)
        return

    # Check proxy is available
    if not _app or not _app.proxy_client:
        await _safe_answer(message, "Voice transcription not available.")
        return

    logger.debug("Telegram voice from %s (%ds)", user_id, message.voice.duration or 0)

    # Typing indicator
    await message.answer_chat_action("typing")

    try:
        # Download voice file
        file = await _bot.get_file(message.voice.file_id)
        audio_data = await _bot.download_file(file.file_path)
        audio_bytes = audio_data.read()

        # Transcribe
        result = _app.proxy_client.stt_transcribe(
            audio=audio_bytes,
            filename="voice.ogg",
            language="auto",
            user_id=f"telegram:{user_id}",
        )
        text = result.get("text", "").strip()

        if not text:
            await _safe_answer(message, "Could not understand the audio.")
            return

        # Show transcription
        await _safe_answer(message, f"_{text}_")

        # Process as chat message
        session_id = _get_session(user_id)
        events = _chat_service.handle_message(
            f"[Voice] {text}", session_id, f"telegram:{user_id}"
        )

        response_text = _render_events(events)
        if response_text:
            await _safe_answer(message, response_text)

    except Exception as e:
        logger.error("Voice transcription error: %s", e, exc_info=True)
        await _safe_answer(message, "Voice transcription failed. Please try again.")
```

3. Update the existing `@dp.message()` handler (currently at line 148) to add a guard for voice messages that might slip through:

```python
@dp.message()
async def handle_message(message: types.Message) -> None:
    """Handle incoming Telegram messages."""
    if not message.text or not _chat_service or not _bot:
        return  # This also catches voice messages without text
    # ... rest unchanged
```

This guard already exists (`if not message.text`) so no change needed.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_stt.py -v`
Expected: All pass

- [ ] **Step 4: Run full suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/channels/telegram.py tests/test_stt.py
git commit -m "feat: Telegram voice message handler — transcribe + respond"
```

---

## Task 5: Web Frontend Record Button

**Files:**
- Modify: `src/mycelos/frontend/out/index.html`

Note: The frontend is currently a static Next.js export. The chat functionality
is built into the compiled JS bundles. Adding a record button requires
injecting a small inline script that adds the MediaRecorder functionality.
This is a pragmatic approach for Day 1 — a proper React component would
require rebuilding the frontend from source.

- [ ] **Step 1: Add record button script to index.html**

Add before the closing `</body>` tag in `src/mycelos/frontend/out/index.html`:

```html
<script>
// Voice Recording — adds a microphone button to the chat input
(function() {
  'use strict';

  function initVoiceRecorder() {
    const inputArea = document.querySelector('textarea');
    const sendBtn = document.querySelector('button');
    if (!inputArea || !sendBtn) {
      setTimeout(initVoiceRecorder, 500);
      return;
    }

    // Create mic button
    const micBtn = document.createElement('button');
    micBtn.innerHTML = '🎤';
    micBtn.title = 'Record voice message';
    micBtn.className = sendBtn.className.replace('bg-[var(--accent)]', 'bg-transparent');
    micBtn.style.cssText = 'font-size: 1.2em; opacity: 0.7; cursor: pointer;';
    sendBtn.parentNode.insertBefore(micBtn, sendBtn);

    let mediaRecorder = null;
    let chunks = [];
    let startTime = 0;
    let timerInterval = null;

    micBtn.addEventListener('click', async () => {
      if (mediaRecorder && mediaRecorder.state === 'recording') {
        // Stop recording
        mediaRecorder.stop();
        micBtn.innerHTML = '🎤';
        micBtn.style.opacity = '0.7';
        clearInterval(timerInterval);
        return;
      }

      // Start recording
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream, {
          mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus' : 'audio/webm'
        });
        chunks = [];

        mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
        mediaRecorder.onstop = async () => {
          stream.getTracks().forEach(t => t.stop());
          const blob = new Blob(chunks, { type: 'audio/webm' });

          // Show uploading state
          micBtn.innerHTML = '⏳';
          inputArea.placeholder = 'Transcribing...';
          inputArea.disabled = true;

          try {
            const form = new FormData();
            form.append('audio', blob, 'recording.webm');
            const sessionId = window.__mycelosSessionId || '';
            const resp = await fetch(
              '/api/audio?session_id=' + sessionId + '&user_id=default',
              { method: 'POST', body: form }
            );

            // Read SSE response and display
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let fullText = '';
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              const text = decoder.decode(value);
              // Parse SSE events
              for (const line of text.split('\n')) {
                if (line.startsWith('data: ')) {
                  try {
                    const evt = JSON.parse(line.slice(6));
                    if (evt.type === 'text') fullText += evt.content;
                  } catch(e) {}
                }
              }
            }
            // The chat UI should auto-update from SSE events
          } catch(err) {
            console.error('Voice upload failed:', err);
          }

          micBtn.innerHTML = '🎤';
          micBtn.style.opacity = '0.7';
          inputArea.placeholder = 'Message Mycelos...';
          inputArea.disabled = false;
        };

        mediaRecorder.start();
        micBtn.innerHTML = '⏹️';
        micBtn.style.opacity = '1';
        startTime = Date.now();
        timerInterval = setInterval(() => {
          const elapsed = Math.floor((Date.now() - startTime) / 1000);
          micBtn.title = 'Recording... ' + elapsed + 's (click to stop)';
        }, 1000);

      } catch(err) {
        if (err.name === 'NotAllowedError') {
          alert('Microphone access denied. Please allow microphone access in your browser settings.');
        } else {
          console.error('MediaRecorder error:', err);
        }
      }
    });
  }

  // Wait for DOM to be ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVoiceRecorder);
  } else {
    initVoiceRecorder();
  }
})();
</script>
```

- [ ] **Step 2: Test manually**

Start gateway: `mycelos gateway --debug`
Open: `http://localhost:8080`
Click mic button → speak → click stop → verify transcription appears

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/frontend/out/index.html
git commit -m "feat: Web Frontend record button — MediaRecorder + /api/audio upload"
```

---

## Task 6: CHANGELOG + Final Tests

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 2: Update CHANGELOG**

Add under today's date:

```markdown
### Speech-to-Text
- SecurityProxy `/stt/transcribe` endpoint (Whisper API via OpenAI)
- SecurityProxyClient `stt_transcribe()` method
- Gateway `/api/audio` route for web clients
- Telegram voice message handler (download → transcribe → respond)
- Web Frontend record button (MediaRecorder API)
- `[Voice]` prefix shows transcription to user before response
- Audio never stored — bytes discarded after transcription
```

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "feat: Speech-to-Text — voice input via Telegram, Web, and API"
git push origin main
```
