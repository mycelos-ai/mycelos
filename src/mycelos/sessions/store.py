"""SessionStore — persists conversations as JSONL files.

Each session is a single JSONL file. Append-only — messages never modified.
This is the Source of Truth for conversation history.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionStore:
    """Manages conversation sessions as JSONL files."""

    def __init__(self, conversations_dir: Path) -> None:
        self._conversations_dir = conversations_dir
        self._conversations_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, user_id: str = "default") -> str:
        session_id = str(uuid.uuid4())
        meta = {
            "type": "session_start",
            "session_id": session_id,
            "user_id": user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._session_path(session_id).write_text(json.dumps(meta) + "\n")
        return session_id

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "type": "message",
            "session_id": session_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            record["metadata"] = metadata
        with open(self._session_path(session_id), "a") as f:
            f.write(json.dumps(record) + "\n")

    def append_llm_round(
        self,
        session_id: str,
        round_num: int,
        model: str,
        tokens_in: int,
        tokens_out: int,
        stop_reason: str,
    ) -> None:
        record = {
            "type": "llm_round",
            "session_id": session_id,
            "round": round_num,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "stop_reason": stop_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._session_path(session_id), "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to append llm_round for session %s: %s", session_id, exc
            )

    def append_tool_call(
        self,
        session_id: str,
        tool_call_id: str,
        name: str,
        args: dict,
        agent: str = "mycelos",
    ) -> None:
        record = {
            "type": "tool_call",
            "session_id": session_id,
            "tool_call_id": tool_call_id,
            "name": name,
            "args": args,
            "agent": agent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._session_path(session_id), "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to append tool_call for session %s: %s", session_id, exc
            )

    def append_tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        name: str,
        result: Any,
        duration_ms: int,
    ) -> None:
        record = {
            "type": "tool_result",
            "session_id": session_id,
            "tool_call_id": tool_call_id,
            "name": name,
            "result": result,
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._session_path(session_id), "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to append tool_result for session %s: %s", session_id, exc
            )

    def append_tool_error(
        self,
        session_id: str,
        tool_call_id: str,
        name: str,
        error: str,
        traceback: str | None = None,
    ) -> None:
        record = {
            "type": "tool_error",
            "session_id": session_id,
            "tool_call_id": tool_call_id,
            "name": name,
            "error": error,
            "traceback": traceback,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._session_path(session_id), "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to append tool_error for session %s: %s", session_id, exc
            )

    def load_all_events(self, session_id: str) -> list[dict]:
        """Load ALL events from a session JSONL file — messages, tool calls, errors, etc.

        Used by the admin Session Inspector. Not used by chat resume (see load_messages).
        """
        path = self._session_path(session_id)
        if not path.exists():
            return []
        events = []
        for line in path.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def load_messages(self, session_id: str) -> list[dict]:
        path = self._session_path(session_id)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text().strip().split("\n"):
            if not line:
                continue
            record = json.loads(line)
            if record.get("type") == "message":
                messages.append(record)
        return messages

    def update_session(self, session_id: str, title: str | None = None, topic: str | None = None) -> bool:
        """Update session metadata (title, topic). Appends a metadata record."""
        path = self._session_path(session_id)
        if not path.exists():
            return False
        record: dict[str, Any] = {
            "type": "session_meta",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if title is not None:
            record["title"] = title
        if topic is not None:
            record["topic"] = topic
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
        return True

    def get_session_meta(self, session_id: str) -> dict[str, Any]:
        """Get merged session metadata (title, topic from latest meta record)."""
        path = self._session_path(session_id)
        if not path.exists():
            return {}
        meta: dict[str, Any] = {}
        for line in path.read_text().strip().split("\n"):
            if not line:
                continue
            record = json.loads(line)
            if record.get("type") == "session_start":
                meta.update(record)
            elif record.get("type") == "session_meta":
                if "title" in record:
                    meta["title"] = record["title"]
                if "topic" in record:
                    meta["topic"] = record["topic"]
        return meta

    def session_exists(self, session_id: str) -> bool:
        return self._session_path(session_id).exists()

    def backfill_titles_from_first_message(self, max_len: int = 60) -> int:
        """Set a title on every untitled session based on its first user message.

        Walks every session JSONL, and for sessions that currently have no
        ``title`` in their metadata, extracts the first message with
        ``role=user`` and uses the first ``max_len`` characters (ellipsised)
        as the title. Sessions that already have a title are skipped.
        Sessions without any user message yet are also skipped.

        Returns the number of sessions that were updated.
        """
        updated = 0
        for path in self._conversations_dir.glob("*.jsonl"):
            session_id = path.stem
            meta = self.get_session_meta(session_id)
            if (meta.get("title") or "").strip():
                continue
            # Find first user message
            first_user_content: str | None = None
            try:
                for line in path.read_text().splitlines():
                    if not line:
                        continue
                    record = json.loads(line)
                    if record.get("type") == "message" and record.get("role") == "user":
                        first_user_content = record.get("content", "")
                        break
            except (json.JSONDecodeError, OSError):
                continue
            if not first_user_content:
                continue
            stripped = first_user_content.strip().replace("\n", " ")
            if not stripped:
                continue
            if len(stripped) > max_len:
                title = stripped[:max_len].rstrip() + "…"
            else:
                title = stripped
            if self.update_session(session_id, title=title):
                updated += 1
        return updated

    def list_sessions(self) -> list[dict]:
        """List sessions, newest first (sorted by file modification time)."""
        sessions = []
        for path in sorted(
            self._conversations_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                lines = path.read_text().strip().split("\n")
                meta: dict[str, Any] = {}
                msg_count = 0
                for line in lines:
                    if not line:
                        continue
                    record = json.loads(line)
                    rtype = record.get("type")
                    if rtype == "session_start":
                        meta.update(record)
                    elif rtype == "session_meta":
                        if "title" in record:
                            meta["title"] = record["title"]
                        if "topic" in record:
                            meta["topic"] = record["topic"]
                    elif rtype == "message":
                        msg_count += 1
                meta["file"] = path.name
                meta["message_count"] = msg_count
                sessions.append(meta)
            except (json.JSONDecodeError, IndexError):
                continue
        return sessions

    def get_latest_session(self) -> str | None:
        sessions = self.list_sessions()
        return sessions[0].get("session_id") if sessions else None

    def list_sessions_with_stats(self) -> list[dict]:
        """List all sessions with summary stats for the admin inspector.

        Returns a list of dicts with: session_id, title, created_at, last_event_at,
        event_count, has_errors, duration_seconds.
        """
        results = []
        for path in sorted(self._conversations_dir.glob("*.jsonl"), reverse=True):
            session_id = path.stem
            events = self.load_all_events(session_id)
            if not events:
                continue

            meta = self.get_session_meta(session_id)

            has_errors = any(e.get("type") in ("tool_error", "llm_error") for e in events)
            timestamps = [e["timestamp"] for e in events if "timestamp" in e]
            first_ts = min(timestamps) if timestamps else ""
            last_ts = max(timestamps) if timestamps else ""

            duration_seconds = 0
            if first_ts and last_ts:
                try:
                    t0 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    duration_seconds = int((t1 - t0).total_seconds())
                except ValueError:
                    duration_seconds = 0

            results.append({
                "session_id": session_id,
                "title": meta.get("title", ""),
                "created_at": first_ts,
                "last_event_at": last_ts,
                "event_count": len(events),
                "has_errors": has_errors,
                "duration_seconds": duration_seconds,
            })
        return results

    def purge_old(self, days: int = 30) -> int:
        """Delete session JSONL files older than `days` days (by file mtime).

        Returns the number of files deleted.
        """
        import time
        cutoff = time.time() - (days * 86400)
        deleted = 0
        for path in self._conversations_dir.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError:
                continue
        return deleted

    def _session_path(self, session_id: str) -> Path:
        return self._conversations_dir / f"{session_id}.jsonl"
