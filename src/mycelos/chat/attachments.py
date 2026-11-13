"""Session attachments as Multi-Part content blocks with token-budget eviction.

Mixin for :class:`mycelos.chat.service.ChatService`; split out of
``chat/service.py`` without behavior changes.
"""

from __future__ import annotations


class AttachmentContextMixin:
    """Session attachments as Multi-Part content blocks with token-budget eviction."""

    def mark_force_include(self, session_id: str, filename: str) -> None:
        """Force a specific attachment to stay in the LLM context next turn.

        Called by the attachment_load tool. The flag is consumed (popped)
        on the next handle_message call.
        """
        self._session_force_include.setdefault(session_id, set()).add(filename)

    def _build_attachment_blocks(
        self,
        session_id: str,
        budget_tokens: int,
    ) -> tuple[list[dict], list[str]]:
        """Build Multi-Part content blocks for the session's attachments.

        Returns (blocks, evicted_filenames). Each block is an Anthropic
        Multi-Part content dict (document / image / text). Eviction is
        oldest-first, except attachments the agent flagged via
        attachment_load (force_include) — those stay in even under
        budget pressure.

        Tokens are estimated cheaply: ~4 chars/token for text, and
        ~bytes/50 for base64-encoded binary (PDF/image), tuned to the
        actual cost of image-rendering for image-heavy PDFs at standard
        resolution.
        """
        import base64
        from pathlib import Path

        from mycelos.files.session_attachments import (
            SessionAttachmentStore,
            content_kind,
            media_type,
        )

        def _binary_block(kind: str, path: Path, raw: bytes) -> dict:
            return {
                "type": kind,
                "source": {
                    "type": "base64",
                    "media_type": media_type(path),
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            }

        store = SessionAttachmentStore(self._app.data_dir / "sessions")
        attachments = store.list(session_id)
        if not attachments:
            return [], []

        # Read the flag but do NOT clear it yet — we want it to persist
        # if the downstream LLM call fails. handle_message clears the
        # flag once the LLM call has successfully produced output.
        force_include = self._session_force_include.get(session_id, set()).copy()

        # Build candidate blocks in upload order.
        candidates: list[tuple[Path, dict]] = []
        for path in attachments:
            kind = content_kind(path)
            if kind == "unsupported":
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if kind == "document":
                candidates.append((path, _binary_block("document", path, raw)))
            elif kind == "image":
                candidates.append((path, _binary_block("image", path, raw)))
            elif kind == "text":
                try:
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    text = ""
                candidates.append((path, {
                    "type": "text",
                    "text": f"[Attachment: {path.name}]\n{text[:50_000]}",
                }))

        def _block_tokens(block: dict) -> int:
            if block.get("type") == "text":
                return len(block.get("text", "")) // 4
            data = block.get("source", {}).get("data", "")
            # Anthropic costs ~1 token per ~50 bytes for image-rendered PDFs
            # at standard resolution. Tighter than bytes/100 to ensure
            # eviction triggers before context-window overflow.
            return len(data) * 3 // 4 // 50

        # Eviction loop: kick oldest non-force-included until we fit.
        evicted: list[str] = []
        while candidates and sum(_block_tokens(b) for _, b in candidates) > budget_tokens:
            kicked_one = False
            for i, (path, _) in enumerate(candidates):
                if path.name in force_include:
                    continue
                evicted.append(path.name)
                candidates.pop(i)
                kicked_one = True
                break
            if not kicked_one:
                # Everything left is force-included — can't shrink further
                break

        return [b for _, b in candidates], evicted
