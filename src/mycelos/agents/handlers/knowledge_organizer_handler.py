"""Knowledge Organizer — background system handler.

Runs on a periodic schedule (hourly) or on pressure (>=10 pending notes).
Per run processes at most 30 notes through lifecycle -> classification
-> action. Not a chat handler — invoked directly by the scheduler.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from mycelos.knowledge.inbox import InboxService
from mycelos.prompts import PromptLoader
from mycelos.knowledge.organizer import (
    Classification,
    DUPLICATE_THRESHOLD,
    DUPLICATE_TOP_K,
    decide_action,
    is_archived_older_than,
    is_done_task_older_than,
    is_fired_reminder_past,
)

logger = logging.getLogger("mycelos.knowledge_organizer")

BATCH_LIMIT = 30
PRESSURE_THRESHOLD = 10
PERIODIC_INTERVAL_MINUTES = 60


class KnowledgeOrganizerHandler:
    """System handler. Not user-facing, not registered in the sidebar."""

    def __init__(self, app: Any) -> None:
        self._app = app

    @property
    def agent_id(self) -> str:
        return "knowledge-organizer"

    # ---- entry point ---------------------------------------------------

    def run(self, user_id: str = "default") -> dict:
        storage = self._app.storage
        inbox = InboxService(storage)
        kb = self._app.knowledge_base

        # Housekeeping: remove duplicate pending suggestions (same note+kind).
        storage.execute(
            "DELETE FROM organizer_suggestions "
            "WHERE status='pending' AND id NOT IN ("
            "  SELECT MAX(id) FROM organizer_suggestions "
            "  WHERE status='pending' GROUP BY note_path, kind"
            ")"
        )

        # Re-classify: notes with empty-target suggestions get a fresh chance.
        # Delete the useless suggestion and flip the note back to 'pending'.
        empty_suggestions = storage.fetchall(
            "SELECT id, note_path FROM organizer_suggestions "
            "WHERE status='pending' AND kind='move' "
            "AND (payload LIKE '%\"target\": \"\"%' OR payload LIKE '%\"target\": null%' "
            "     OR payload LIKE '%\"target\":\"\"%' OR payload LIKE '%\"target\":null%')"
        )
        for es in empty_suggestions:
            storage.execute(
                "DELETE FROM organizer_suggestions WHERE id=?", (es["id"],)
            )
            storage.execute(
                "UPDATE knowledge_notes SET organizer_state='pending' WHERE path=?",
                (es["note_path"],),
            )

        # Auto-accept: suggestions pending > 24h get accepted automatically.
        # This creates topics and moves notes without user intervention.
        auto_accepted = self._auto_accept_stale(storage, kb, user_id)

        # Hard-delete archived notes older than 30 days
        archived_notes = storage.fetchall(
            "SELECT * FROM knowledge_notes WHERE status='archived' LIMIT 50"
        )
        hard_deleted = 0
        for note in archived_notes:
            if is_archived_older_than(note, days=30):
                path = note["path"]
                file_path = kb._knowledge_dir / (path + ".md")
                if file_path.exists():
                    try:
                        file_path.unlink()
                    except OSError:
                        pass
                storage.execute(
                    "DELETE FROM knowledge_notes WHERE path=?", (path,)
                )
                storage.execute(
                    "DELETE FROM organizer_suggestions WHERE note_path=?", (path,)
                )
                self._audit(user_id, "organizer.hard_delete", {"path": path})
                hard_deleted += 1

        pending = storage.fetchall(
            "SELECT * FROM knowledge_notes WHERE organizer_state='pending' LIMIT ?",
            (BATCH_LIMIT,),
        )

        archived = 0
        moved = 0
        suggested = 0
        linked = 0

        topics = [t.get("path", "") for t in kb.list_topics(limit=500)]

        for note in pending:
            # Lifecycle first — pure SQL, no LLM
            if is_done_task_older_than(note, days=7):
                self._archive_note(storage, note["path"])
                self._audit(user_id, "organizer.archive",
                            {"path": note["path"], "reason": "done>7d"})
                archived += 1
                continue
            if is_fired_reminder_past(note, days=1):
                self._archive_note(storage, note["path"])
                self._audit(user_id, "organizer.archive",
                            {"path": note["path"], "reason": "reminder_past"})
                archived += 1
                continue

            # Classification via the LLM broker
            result = self._classify(note, topics)
            topic_exists = bool(result.topic_path) and result.topic_path in topics
            action = decide_action(result, topic_exists=topic_exists)

            if action == "silent_move":
                try:
                    kb.move_to_topic(note["path"], result.topic_path)
                except Exception as e:
                    logger.warning("organizer.move failed for %s: %s", note["path"], e)
                self._mark_state(storage, note["path"], "ok")
                self._audit(user_id, "organizer.move",
                            {"from": note["path"], "to": result.topic_path,
                             "confidence": result.confidence})
                moved += 1
            elif action == "suggest_new_topic":
                inbox.add(
                    note_path=note["path"],
                    kind="new_topic",
                    payload={"name": result.new_topic_name, "members": [note["path"]]},
                    confidence=result.confidence,
                )
                self._mark_seen(storage, note["path"])
                suggested += 1
            else:  # suggest_move
                inbox.add(
                    note_path=note["path"],
                    kind="move",
                    payload={
                        "target": result.topic_path or "",
                        "alternatives": [],
                        "reason": "low_confidence",
                    },
                    confidence=result.confidence,
                )
                self._mark_seen(storage, note["path"])
                suggested += 1

            # Lazy Linker
            for related in result.related_note_paths or []:
                inbox.add(
                    note_path=note["path"],
                    kind="link",
                    payload={"from": note["path"], "to": related},
                    confidence=result.confidence,
                )
                linked += 1

            # Duplicate detection via vector similarity
            try:
                dupes = kb.find_duplicates(
                    note["path"],
                    threshold=DUPLICATE_THRESHOLD,
                    top_k=DUPLICATE_TOP_K,
                )
                for dupe in dupes:
                    # Skip if a merge suggestion already exists for this pair
                    existing = storage.fetchone(
                        "SELECT id FROM organizer_suggestions "
                        "WHERE status='pending' AND kind='merge' "
                        "AND ((note_path=? AND payload LIKE ?) "
                        "  OR (note_path=? AND payload LIKE ?))",
                        (
                            note["path"], f'%"{dupe["path"]}"%',
                            dupe["path"], f'%"{note["path"]}"%',
                        ),
                    )
                    if existing:
                        continue

                    # Ensure older note is note_path, newer is duplicate_path
                    note_created = note.get("created_at", "")
                    dupe_created = dupe.get("created_at", "")
                    if note_created <= dupe_created:
                        primary, secondary = note["path"], dupe["path"]
                    else:
                        primary, secondary = dupe["path"], note["path"]

                    similarity = round(dupe.get("score", 0.0), 3)
                    inbox.add(
                        note_path=primary,
                        kind="merge",
                        payload={"duplicate_path": secondary, "similarity": similarity},
                        confidence=similarity,
                    )
            except Exception as exc:
                logger.debug("Duplicate check failed for %s: %s", note["path"], exc)

        return {
            "processed": len(pending),
            "archived": archived,
            "moved": moved,
            "suggested": suggested,
            "linked": linked,
            "hard_deleted": hard_deleted,
        }

    def sweep_duplicates(self, user_id: str = "default") -> int:
        """One-time sweep: find duplicates across all notes. Returns count of suggestions created."""
        storage = self._app.storage
        inbox = InboxService(storage)
        kb = self._app.knowledge_base

        notes = storage.fetchall(
            "SELECT path, title, created_at FROM knowledge_notes "
            "WHERE status != 'archived' ORDER BY created_at"
        )

        seen_pairs: set[tuple[str, str]] = set()
        count = 0

        for note in notes:
            try:
                dupes = kb.find_duplicates(
                    note["path"],
                    threshold=DUPLICATE_THRESHOLD,
                    top_k=DUPLICATE_TOP_K,
                )
            except Exception:
                continue

            for dupe in dupes:
                pair = tuple(sorted([note["path"], dupe["path"]]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                existing = storage.fetchone(
                    "SELECT id FROM organizer_suggestions "
                    "WHERE status='pending' AND kind='merge' "
                    "AND ((note_path=? AND payload LIKE ?) "
                    "  OR (note_path=? AND payload LIKE ?))",
                    (pair[0], f'%"{pair[1]}"%', pair[1], f'%"{pair[0]}"%'),
                )
                if existing:
                    continue

                note_created = note.get("created_at", "")
                dupe_created = dupe.get("created_at", "")
                if note_created <= dupe_created:
                    primary, secondary = note["path"], dupe["path"]
                else:
                    primary, secondary = dupe["path"], note["path"]

                similarity = round(dupe.get("score", 0.0), 3)
                inbox.add(
                    note_path=primary,
                    kind="merge",
                    payload={"duplicate_path": secondary, "similarity": similarity},
                    confidence=similarity,
                )
                count += 1

        if count:
            self._audit(user_id, "organizer.sweep_duplicates", {"found": count})
            logger.info("Duplicate sweep found %d potential pairs", count)
        return count

    # ---- classification -----------------------------------------------

    def _classify(self, note: dict, topics: list[str]) -> Classification:
        prompt = self._build_prompt(note, topics)
        try:
            response = self._app.llm.complete(
                [
                    {"role": "system", "content": PromptLoader().load("knowledge-organizer")},
                    {"role": "user", "content": prompt},
                ],
                model=self._app.resolve_cheapest_model(),
            )
        except Exception as e:
            logger.warning("organizer LLM classification failed: %s", e)
            return Classification(
                topic_path=None, confidence=0.0, related_note_paths=[], new_topic_name=None
            )
        raw = getattr(response, "content", None) or ""
        return self._parse_classification(raw)

    def _build_prompt(self, note: dict, topics: list[str]) -> str:
        topic_list = "\n".join(f"- {t}" for t in topics) or "(none yet)"

        # Read body from disk — the DB doesn't store content.
        body = ""
        kb = self._app.knowledge_base
        try:
            file_path = kb._knowledge_dir / (note["path"] + ".md")
            if file_path.exists():
                body = file_path.read_text(encoding="utf-8")[:400]
        except Exception:
            pass

        return (
            f"Title: {note.get('title', '')}\n"
            f"Body: {body}\n\n"
            f"Existing topics:\n{topic_list}\n\n"
            f"Classify this note. If an existing topic fits, use it. "
            f"If no topic fits, ALWAYS propose a new_topic_name — never leave "
            f"both topic_path and new_topic_name empty.\n\n"
            f"Respond as a single JSON object with keys: "
            f"topic_path (string or null), confidence (0..1), "
            f"related_note_paths (array of strings), "
            f"new_topic_name (string or null)."
        )

    @staticmethod
    def _parse_classification(raw: str) -> Classification:
        text = raw.strip()
        # Strip ```json fences if present
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text)
        except Exception:
            return Classification(
                topic_path=None, confidence=0.0, related_note_paths=[], new_topic_name=None
            )
        return Classification(
            topic_path=data.get("topic_path"),
            confidence=float(data.get("confidence", 0.0)),
            related_note_paths=list(data.get("related_note_paths") or []),
            new_topic_name=data.get("new_topic_name"),
        )

    # ---- state helpers ------------------------------------------------

    def _archive_note(self, storage, path: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        storage.execute(
            "UPDATE knowledge_notes SET status='archived', organizer_state='archived', "
            "organizer_seen_at=? WHERE path=?",
            (now, path),
        )

    def _mark_state(self, storage, path: str, state: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        storage.execute(
            "UPDATE knowledge_notes SET organizer_state=?, organizer_seen_at=? WHERE path=?",
            (state, now, path),
        )

    def _mark_seen(self, storage, path: str) -> None:
        """Mark note as 'suggested' so it is not re-processed on the next run."""
        now = datetime.now(tz=timezone.utc).isoformat()
        storage.execute(
            "UPDATE knowledge_notes SET organizer_state='suggested', "
            "organizer_seen_at=? WHERE path=?",
            (now, path),
        )

    def _auto_accept_stale(self, storage, kb, user_id: str) -> int:
        """Auto-accept suggestions that have been pending > 24 hours.

        Creates new topics as needed and moves the notes. Returns the
        number of suggestions auto-accepted.
        """
        stale = storage.fetchall(
            "SELECT * FROM organizer_suggestions WHERE status='pending' "
            "AND created_at < datetime('now', '-24 hours')"
        )
        if not stale:
            return 0

        count = 0
        for row in stale:
            try:
                payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                kind = row["kind"]

                if kind == "new_topic":
                    name = payload.get("name")
                    if name:
                        try:
                            kb.create_topic(name)
                        except Exception:
                            pass
                        target = f"topics/{name.lower().replace(' ', '-')}"
                        for member in payload.get("members", []):
                            try:
                                kb.move_to_topic(member, target)
                            except Exception:
                                pass
                elif kind == "move":
                    target = payload.get("target")
                    if target:
                        try:
                            kb.move_to_topic(row["note_path"], target)
                        except Exception:
                            pass
                elif kind == "link":
                    dst = payload.get("to")
                    src = payload.get("from") or row["note_path"]
                    if dst:
                        try:
                            kb.append_related_link(src, dst)
                        except Exception:
                            pass
                elif kind == "merge":
                    duplicate_path = payload.get("duplicate_path")
                    if duplicate_path:
                        self._execute_merge(
                            kb, storage, row["note_path"], duplicate_path,
                            payload.get("similarity", 0.0), user_id,
                        )

                storage.execute(
                    "UPDATE organizer_suggestions SET status='accepted' WHERE id=?",
                    (row["id"],),
                )
                # Also flip note state to 'ok' since it's now organized
                storage.execute(
                    "UPDATE knowledge_notes SET organizer_state='ok' WHERE path=?",
                    (row["note_path"],),
                )
                count += 1
            except Exception as e:
                logger.warning("Auto-accept failed for suggestion %s: %s", row["id"], e)

        if count > 0:
            self._audit(user_id, "organizer.auto_accept",
                        {"count": count, "reason": "stale>24h"})
            logger.info("Organizer auto-accepted %d stale suggestions", count)
        return count

    def _execute_merge(
        self, kb, storage, primary_path: str, secondary_path: str,
        similarity: float, user_id: str,
    ) -> None:
        """Merge secondary note into primary: append content, archive secondary."""
        try:
            from mycelos.knowledge.note import parse_frontmatter

            # Read secondary note content
            secondary_file = kb._knowledge_dir / (secondary_path + ".md")
            if not secondary_file.exists():
                return

            secondary_md = secondary_file.read_text(encoding="utf-8")
            secondary = parse_frontmatter(secondary_md)

            # Append to primary
            separator = f"\n\n---\n*Merged from: {secondary.title}*\n\n"
            kb.update(primary_path, content=separator + secondary.content, append=True)

            # Merge tags
            primary_meta = storage.fetchone(
                "SELECT tags FROM knowledge_notes WHERE path=?", (primary_path,)
            )
            if primary_meta:
                primary_tags = json.loads(primary_meta["tags"] or "[]")
                merged_tags = list(set(primary_tags) | set(secondary.tags or []))
                if merged_tags != primary_tags:
                    kb.update(primary_path, tags=merged_tags)

            # Archive secondary
            kb.archive_note(secondary_path)

            self._audit(user_id, "organizer.merge", {
                "primary": primary_path,
                "archived": secondary_path,
                "similarity": similarity,
            })
        except Exception as exc:
            logger.warning("Merge failed %s + %s: %s", primary_path, secondary_path, exc)

    def _audit(self, user_id: str, event: str, details: dict) -> None:
        try:
            self._app.audit.log(event, user_id=user_id, details=details)
        except Exception as e:
            logger.warning("organizer audit failed: %s", e)

