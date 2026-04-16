"""CRUD layer for ``organizer_suggestions``.

Returns pending suggestions grouped by kind. Does not apply actions on
the referenced note itself — the organizer handler and the
``/api/organizer/suggestions/{id}/accept`` endpoint are responsible for
moving the note, creating topics, or appending wikilinks after calling
``accept``.
"""
from __future__ import annotations

import json
from typing import Any

from mycelos.storage.database import SQLiteStorage


_KINDS = ("move", "new_topic", "link", "refine_type", "merge")


class InboxService:
    def __init__(self, storage: SQLiteStorage) -> None:
        self._storage = storage

    def add(
        self,
        note_path: str,
        kind: str,
        payload: dict[str, Any],
        confidence: float,
    ) -> int:
        if kind not in _KINDS:
            raise ValueError(f"unknown suggestion kind: {kind}")
        cursor = self._storage.execute(
            "INSERT INTO organizer_suggestions (note_path, kind, payload, confidence) "
            "VALUES (?, ?, ?, ?)",
            (note_path, kind, json.dumps(payload), float(confidence)),
        )
        return int(cursor.lastrowid)

    def get(self, suggestion_id: int) -> dict | None:
        row = self._storage.fetchone(
            "SELECT * FROM organizer_suggestions WHERE id=?", (suggestion_id,)
        )
        if not row:
            return None
        row["payload"] = json.loads(row["payload"])
        return row

    def list_pending(self) -> dict[str, list[dict]]:
        rows = self._storage.fetchall(
            "SELECT * FROM organizer_suggestions WHERE status='pending' "
            "ORDER BY confidence DESC, created_at DESC"
        )
        grouped: dict[str, list[dict]] = {k: [] for k in _KINDS}
        for row in rows:
            row["payload"] = json.loads(row["payload"])
            kind = row["kind"]
            if kind in grouped:
                grouped[kind].append(row)
        return grouped

    def list_pending_by_topic(self) -> list[dict]:
        """Return pending move + new_topic suggestions grouped by target topic.

        Each group is a dict:
        ``{"topic": "topics/coffee", "topic_name": "Coffee", "is_new": True,
           "notes": [<suggestion dicts>]}``

        Link and refine_type suggestions are returned as a flat list under
        a special group with ``topic=None``.
        """
        rows = self._storage.fetchall(
            "SELECT s.*, n.title AS note_title "
            "FROM organizer_suggestions s "
            "LEFT JOIN knowledge_notes n ON s.note_path = n.path "
            "WHERE s.status='pending' "
            "ORDER BY s.confidence DESC, s.created_at DESC"
        )
        topic_groups: dict[str, dict] = {}  # keyed by topic path or proposed name
        links: list[dict] = []

        for row in rows:
            row["payload"] = json.loads(row["payload"])
            kind = row["kind"]

            if kind == "move":
                target = row["payload"].get("target") or ""
                key = target or "__uncategorized__"
                if key not in topic_groups:
                    topic_groups[key] = {
                        "topic": target,
                        "topic_name": target.rsplit("/", 1)[-1] if target else "Uncategorized",
                        "is_new": False,
                        "notes": [],
                    }
                topic_groups[key]["notes"].append(row)

            elif kind == "new_topic":
                name = row["payload"].get("name") or "Unnamed"
                key = f"__new__{name}"
                if key not in topic_groups:
                    topic_groups[key] = {
                        "topic": f"topics/{name.lower().replace(' ', '-')}",
                        "topic_name": name,
                        "is_new": True,
                        "notes": [],
                    }
                # The suggestion itself represents the topic creation;
                # any member notes are bundled with it.
                topic_groups[key]["notes"].append(row)
                for member in row["payload"].get("members", []):
                    if member != row["note_path"]:
                        topic_groups[key]["notes"].append({
                            "id": row["id"],
                            "note_path": member,
                            "kind": "move",
                            "payload": {"target": f"topics/{name.lower().replace(' ', '-')}"},
                            "confidence": row["confidence"],
                            "status": "pending",
                            "_synthetic": True,
                        })

            elif kind in ("link", "refine_type", "merge"):
                links.append(row)

        result = sorted(topic_groups.values(), key=lambda g: g["topic_name"].lower())
        if links:
            result.append({
                "topic": None,
                "topic_name": "Links",
                "is_new": False,
                "notes": links,
            })
        return result

    def accept_all_pending(self) -> int:
        """Accept every pending suggestion. Returns count."""
        cursor = self._storage.execute(
            "UPDATE organizer_suggestions SET status='accepted' WHERE status='pending'"
        )
        return cursor.rowcount

    def accept(self, suggestion_id: int) -> None:
        self._storage.execute(
            "UPDATE organizer_suggestions SET status='accepted' WHERE id=?",
            (suggestion_id,),
        )

    def dismiss(self, suggestion_id: int) -> None:
        self._storage.execute(
            "UPDATE organizer_suggestions SET status='dismissed' WHERE id=?",
            (suggestion_id,),
        )
