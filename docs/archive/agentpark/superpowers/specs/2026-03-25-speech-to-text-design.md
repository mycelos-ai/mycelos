# Speech-to-Text — Design Specification

**Date:** 2026-03-25
**Status:** DRAFT
**Scope:** Voice input via Telegram, Web Frontend, and API

---

## 1. Overview

Voice messages are transcribed to text via the SecurityProxy and processed
as normal chat messages. Users see their transcription before Mycelos responds.

### Core Principle

All external API calls (including STT) go through the SecurityProxy.
The STT endpoint is a generic service — Telegram and Web Frontend are
both consumers of the same proxy endpoint.

### Provider Model

The STT interface is a service abstraction with pluggable providers:

- `openai` (default): Whisper API (`whisper-1`)
- `google`: Gemini audio transcription via `generateContent`
- `local`: OpenAI-compatible local endpoint (e.g. faster-whisper server)

Provider selection is resolved server-side via:

- `MYCELOS_STT_PROVIDER` (default provider)
- or per-request `provider` form field override

Local mode can be configured via:

- `MYCELOS_STT_LOCAL_BASE_URL` (default `http://127.0.0.1:8000/v1`)
- `MYCELOS_STT_LOCAL_API_KEY` (optional)

---

## 2. Architecture

```
Web Frontend                    Telegram
  |                               |
  | MediaRecorder (.webm)         | Voice Message (.ogg)
  |                               |
  v                               v
POST /api/audio               telegram.py voice handler
  |                               |
  |     +-------------------------+
  |     |
  v     v
SecurityProxy POST /stt/transcribe
  |
  | Whisper API (with API key from CredentialProxy)
  |
  v
Transcription text
  |
  v
ChatService.handle_message("[Voice] {text}")
  |
  v
Mycelos responds
```

---

## 3. SecurityProxy Endpoint

### POST /stt/transcribe

Transcribe an audio file to text.

```
Request: multipart/form-data
  - audio: binary file (.ogg, .webm, .mp3, .wav, .m4a)
  - language: string (optional, default "auto")
  - model: string (optional, default "whisper-1")

Headers:
  - Authorization: Bearer {MYCELOS_PROXY_TOKEN}  (internal proxy auth)
  - X-User-Id: {user_id}

Response:
{
  "text": "Ich möchte einen Agent der PDFs liest",
  "language": "de",
  "duration_seconds": 3.2
}
```

### Implementation

```python
@app.post("/stt/transcribe")
async def stt_transcribe(
    audio: UploadFile,
    language: str = "auto",
    model: str = "whisper-1",
    request: Request,
):
    # 0. Auth check (same pattern as all other proxy endpoints)
    authorized, user_id = _check_auth(request)
    if not authorized:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # 1. Read audio bytes + size check
    audio_bytes = await audio.read()
    if len(audio_bytes) > 25 * 1024 * 1024:
        return JSONResponse({"error": "Audio file too large (max 25MB)"}, status_code=413)

    # 2. Resolve API key for STT provider (fail-closed if missing)
    provider = _state.get("stt_provider", "openai")
    cred = _credential_proxy.get_credential(provider)
    if not cred or not cred.get("api_key"):
        return JSONResponse({
            "error": f"STT credential '{provider}' not configured",
            "status": 0,
        }, status_code=400)

    # 3. Call Whisper API
    # Note: language="auto" means omit the language param (Whisper auto-detects)
    import httpx
    data = {"model": model, "response_format": "verbose_json"}
    if language != "auto":
        data["language"] = language

    resp = httpx.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {cred['api_key']}"},
        files={"file": (audio.filename or "audio.ogg", audio_bytes)},
        data=data,
        timeout=30,
    )
    result = resp.json()

    # 4. Audit log
    duration = result.get("duration", 0)
    _audit("proxy.stt_transcribe", user_id=user_id, details={
        "provider": provider,
        "model": model,
        "audio_size": len(audio_bytes),
        "language": result.get("language", "unknown"),
        "duration": duration,
    })

    # 5. Return transcription
    return {
        "text": result.get("text", ""),
        "language": result.get("language", "unknown"),
        "duration_seconds": duration,
    }
```

**Notes:**
- Auth check is mandatory (same `_check_auth` pattern as all other endpoints)
- `language="auto"` means: omit the `language` parameter from the Whisper API
  call, letting it auto-detect. This is NOT passed as a literal value.
- `response_format=verbose_json` is requested to get `duration` and `language`
  in the response (default `json` only returns `text`)
- Credential check is fail-closed: missing credential returns 400, not 500
- The `httpx.post` call is synchronous (blocking). This is consistent with the
  existing proxy pattern where all handlers use sync httpx. For Day 1 this is
  acceptable (single worker). Future: move to async httpx for concurrency.

### Supported Formats

Whisper accepts: `.ogg`, `.webm`, `.mp3`, `.wav`, `.m4a`, `.flac`.
Telegram sends `.ogg` (Opus codec). Web MediaRecorder sends `.webm` (Opus).
Both work without conversion.

### File Handling

- Audio is read into memory (max 25MB, enforced by size check)
- No temp file written to disk (FastAPI `UploadFile` uses `SpooledTemporaryFile`
  which spills to disk above 1MB — this is acceptable as it's a system temp
  file cleaned up by Python's GC, not persistent storage)
- Bytes are discarded after the API call returns
- No audio is stored or logged

---

## 4. SecurityProxyClient Extension

```python
# In proxy_client.py
def stt_transcribe(self, audio: bytes, filename: str = "audio.ogg",
                   language: str = "auto", model: str = "whisper-1",
                   user_id: str = "default") -> dict:
    """Transcribe audio via SecurityProxy.

    Returns: {"text": "...", "language": "...", "duration_seconds": N}
    """
    resp = self._request("POST", "/stt/transcribe",
        files={"audio": (filename, audio)},
        data={"language": language, "model": model},
        headers={"X-User-Id": user_id},
    )
    return resp.json()
```

Add `stt_transcribe` to `SecurityProxyProtocol` in `protocols.py`.

---

## 5. Gateway Audio Endpoint

### POST /api/audio

Gateway route that accepts audio upload and processes it as a chat message.
Authentication follows the same pattern as `/api/chat` (session_id validation).

```python
@router.post("/api/audio")
async def handle_audio(
    audio: UploadFile,
    session_id: str,
    user_id: str = "default",
    # Same auth as /api/chat — session_id must be valid
):
    # 1. Read audio
    audio_bytes = await audio.read()

    # 2. Transcribe via proxy
    result = app.proxy_client.stt_transcribe(
        audio=audio_bytes,
        filename=audio.filename or "audio.webm",
        user_id=user_id,
    )
    text = result["text"]

    # 3. Process as chat message with [Voice] prefix
    message = f"[Voice] {text}"
    events = chat_service.handle_message(message, session_id, user_id)

    # 4. Return as SSE stream (same as /api/chat)
    return StreamingResponse(
        _stream_events(events),
        media_type="text/event-stream",
    )
```

Also testable via curl:
```bash
curl -F "audio=@voice.ogg" "http://localhost:8080/api/audio?session_id=test&user_id=default"
```

---

## 6. Telegram Voice Handler

In `telegram.py`, add a voice message handler. Requires adding `from aiogram import F`
to the imports. The voice handler MUST be registered before the existing general
`@dp.message()` handler, so aiogram matches it first. Alternatively, add
`if message.voice:` guard at the top of the existing handler to redirect.

```python
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id

    # 1. Check allowlist
    if _allowed_users and user_id not in _allowed_users:
        return

    # 2. Show typing indicator
    await message.answer_chat_action("typing")

    # 3. Download voice file
    file = await _bot.get_file(message.voice.file_id)
    audio_bytes = await _bot.download_file(file.file_path)

    # 4. Transcribe via proxy
    result = _app.proxy_client.stt_transcribe(
        audio=audio_bytes.read(),
        filename="voice.ogg",
        language="auto",
        user_id=f"telegram:{user_id}",
    )
    text = result["text"]

    # 5. Show transcription
    await _safe_answer(message, f"[Voice] _{text}_")

    # 6. Process as chat message
    session_id = _get_session(user_id)
    events = _chat_service.handle_message(
        f"[Voice] {text}", session_id, f"telegram:{user_id}"
    )

    # 7. Send response
    response_text = _render_events(events)
    if response_text:
        await _safe_answer(message, response_text)
```

---

## 7. Web Frontend: Record Button

### UI Component

A microphone button next to the text input field in the chat interface.

**States:**
1. **Idle** — Microphone icon (grey). Click to start recording.
2. **Recording** — Red pulsing indicator + elapsed timer ("0:03"). Click to stop.
3. **Uploading** — Spinner. Auto-transitions after stop.
4. **Done** — Transcription appears as user message in chat.

### Implementation

```tsx
// AudioRecorder component (React)
const AudioRecorder = ({ sessionId, onTranscript }) => {
  const [recording, setRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const mediaRecorder = useRef(null);
  const chunks = useRef([]);

  const startRecording = async () => {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      // Handle permission denied or no microphone
      alert("Microphone access denied. Please allow microphone access.");
      return;
    }
    mediaRecorder.current = new MediaRecorder(stream, {
      mimeType: "audio/webm;codecs=opus",
    });
    chunks.current = [];

    mediaRecorder.current.ondataavailable = (e) => chunks.current.push(e.data);
    mediaRecorder.current.onstop = async () => {
      const blob = new Blob(chunks.current, { type: "audio/webm" });
      stream.getTracks().forEach(t => t.stop());

      // Upload to /api/audio
      const form = new FormData();
      form.append("audio", blob, "recording.webm");

      const resp = await fetch(
        `/api/audio?session_id=${sessionId}&user_id=default`,
        { method: "POST", body: form }
      );
      // Handle SSE response same as text chat
      onTranscript(resp);
    };

    mediaRecorder.current.start();
    setRecording(true);
  };

  const stopRecording = () => {
    mediaRecorder.current?.stop();
    setRecording(false);
  };

  return (
    <button onClick={recording ? stopRecording : startRecording}>
      {recording ? <StopIcon /> : <MicrophoneIcon />}
      {recording && <span className="recording-timer">{formatTime(duration)}</span>}
    </button>
  );
};
```

### Browser Compatibility

`MediaRecorder` with `audio/webm;codecs=opus` is supported in all modern
browsers (Chrome, Firefox, Safari 14.1+, Edge). No polyfill needed.

---

## 8. Configuration

In NixOS-style config:

```python
{
    "stt_provider": "openai",     # Default provider
    "stt_model": "whisper-1",     # Default model
}
```

Provider-specific credential lookup: `credential:{stt_provider}` (e.g.,
`credential:openai` for the OpenAI API key). This reuses the same `openai`
credential entry as LLM calls — no separate credential needed. Users who
want separate API keys for STT can configure a dedicated `stt` credential
and set `stt_provider: stt` in config.

---

## 9. Privacy and Security

- Audio bytes exist only in memory during transcription
- No audio files written to disk (neither proxy nor gateway)
- No audio stored in database or object store
- Transcription text is stored as a normal chat message (with `[Voice]` prefix)
- Audio is sent to OpenAI's Whisper API — same privacy policy as LLM calls
- Audit event logs metadata only (size, language), never audio content

---

## 10. Error Handling

| Error | Behavior |
|-------|----------|
| No STT credential configured | "Voice transcription not available — configure OpenAI credentials" |
| Audio too large (>25MB) | "Audio file too large (max 25MB)" |
| Whisper API error | "Transcription failed: {error}" — message not processed |
| Empty transcription | "Could not understand the audio" — no chat message created |
| Unsupported format | Whisper handles most formats; if rejected, show error |
| Proxy unavailable | Standard fail-closed: "External services unavailable" |

---

## 11. Audit Events

| Event | Details |
|-------|---------|
| `proxy.stt_transcribe` | provider, model, audio_size, language, user_id, duration |

---

## 12. Testing Strategy

### Unit Tests
- Proxy endpoint: mock Whisper API, verify request format
- Proxy client: `stt_transcribe()` sends correct multipart request
- Telegram voice handler: mock bot.download_file + proxy, verify flow
- Error cases: no credential, too large, empty transcription

### Integration Tests
- Gateway `/api/audio`: upload audio file, verify chat message created
- curl test: `curl -F "audio=@test.ogg" localhost:8080/api/audio`

### Manual Tests
- Telegram: send voice message, see transcription + response
- Web: click record, speak, see transcription + response

---

## 13. Scope Boundary

### In Scope
- SecurityProxy `/stt/transcribe` endpoint
- SecurityProxyClient `stt_transcribe()` method
- Gateway `/api/audio` route
- Telegram voice message handler
- Web Frontend record button + upload
- OpenAI Whisper as Day 1 provider
- `[Voice]` prefix for transparency

### Out of Scope (Future)
- Text-to-Speech (audio responses)
- Other STT providers (Deepgram, Google, local Whisper)
- Audio archiving / retention
- Real-time streaming transcription
- Audio waveform visualization in chat history
