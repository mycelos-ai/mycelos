"""KnowledgeBase service — CRUD, FTS5 search, linking, vector search."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from mycelos.knowledge import ranking
from mycelos.knowledge.indexer import KnowledgeIndexer
from mycelos.knowledge.note import Note, parse_frontmatter, render_note
from mycelos.knowledge.topic_map import build_topic_mermaid

if TYPE_CHECKING:
    from mycelos.app import App

logger = logging.getLogger("mycelos.knowledge")


def _parse_source(raw: str | None) -> dict | None:
    """Parse the provenance `source` JSON column; None when absent/invalid."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _filter_results(
    results: list[dict], type: str | None = None, tags: list[str] | None = None
) -> list[dict]:
    """Apply the same type/tags semantics as ``KnowledgeIndexer.search_fts``
    to a result list post-hoc — used for the vector arm, which has no SQL
    WHERE clause of its own.

    Mirrors search_fts exactly: type is filtered (equality), tags is
    accepted for interface symmetry but not filtered — search_fts declares
    a ``tags`` parameter yet never applies it in its SQL, so real tag
    filtering does not exist in this codebase today. Replicating that
    (rather than inventing new semantics here) keeps both arms consistent.
    """
    if type:
        results = [r for r in results if r.get("type") == type]
    return results


def bucket_note(note: dict) -> str:
    """Decide the parent_path for a fresh note. No LLM, no I/O.

    Rules (in order):
    1. If the caller supplied a non-empty ``parent_path`` it wins.
    2. Reminders or notes with a due date → ``tasks``.
    3. Everything else → ``notes``.
    """
    explicit = note.get("parent_path")
    if explicit:
        return explicit
    if note.get("reminder"):
        return "tasks"
    if note.get("due"):
        return "tasks"
    return "notes"


class PathTraversalError(Exception):
    """Raised when a path escapes the knowledge directory."""


class KnowledgeBase:
    """Main knowledge base service.

    Stores notes as Markdown files under data_dir/knowledge/ and maintains
    a SQLite FTS5 index for fast search and filtered listing.
    """

    def __init__(self, app: "App") -> None:
        self._app = app
        self._knowledge_dir = self._resolve_knowledge_dir(app)
        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._indexer = KnowledgeIndexer(app.storage)
        self._indexer.ensure_fts(rebuild_content_provider=self._note_body)
        self._embedding_provider = self._init_embedding_provider()
        self._ensure_vec_table()

    @staticmethod
    def _resolve_knowledge_dir(app: "App") -> "Path":
        """Resolve the knowledge directory from config or default.

        Priority:
        1. Memory config: system.knowledge_path (set during onboarding)
        2. Default: ~/.mycelos/knowledge/
        """
        from pathlib import Path
        try:
            custom_path = app.memory.get("default", "system", "knowledge_path")
            if custom_path:
                p = Path(custom_path).expanduser().resolve()
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:
            pass
        return app.data_dir / "knowledge"

    def _safe_path(self, path: str) -> "Path":
        """Resolve a user-supplied note path safely within the knowledge dir.

        Prevents path traversal attacks (e.g., ../../etc/passwd).
        Returns the resolved Path object with .md extension.
        Raises PathTraversalError if the path escapes the knowledge directory.
        """
        from pathlib import Path
        resolved = (self._knowledge_dir / (path + ".md")).resolve()
        try:
            resolved.relative_to(self._knowledge_dir.resolve())
        except ValueError:
            self._app.audit.log(
                "knowledge.traversal.blocked",
                details={"path": path},
            )
            raise PathTraversalError(f"Path escapes knowledge directory: {path}")
        return resolved

    def _note_body(self, path: str) -> str:
        """Read a note's markdown body by path, for FTS rebuild re-indexing.

        Returns "" when the file is missing rather than raising, so a stale
        knowledge_notes row (deleted on disk but not yet cleaned up) doesn't
        abort the rebuild.
        """
        file_path = self._safe_path(path)
        if not file_path.exists():
            return ""
        return parse_frontmatter(file_path.read_text(encoding="utf-8")).content

    def _init_embedding_provider(self):
        """Gather configuration facts and let embeddings.py decide."""
        from mycelos.knowledge.embeddings import (
            EUModeViolation, FallbackProvider, get_embedding_provider,
        )
        proxy = getattr(self._app, "proxy_client", None)
        has_credential = self._has_openai_credential(proxy)
        eu_mode = False
        try:
            from mycelos.llm.eu_mode import get_eu_mode
            eu_mode = get_eu_mode(self._app, "default")
        except Exception:
            pass
        explicit = None
        try:
            row = self._app.storage.fetchone(
                "SELECT value FROM knowledge_config WHERE key = 'embedding_provider_setting'"
            )
            explicit = row["value"] if row else None
        except Exception:
            pass
        try:
            return get_embedding_provider(
                explicit=explicit, eu_mode=eu_mode,
                has_openai_credential=has_credential, proxy_client=proxy,
            )
        except EUModeViolation as e:
            logger.error("Embedding configuration refused: %s", e)
            return FallbackProvider()

    @staticmethod
    def _has_openai_credential(proxy) -> bool:
        """Ask the credential proxy whether an OpenAI credential is actually
        stored — never inferred from the proxy object merely existing.

        Uses the same metadata-only ``credential_list()`` RPC that
        ``DelegatingCredentialProxy.list_services`` uses elsewhere, so the
        plaintext key never has to leave the SecurityProxy container. Fails
        closed: any exception (proxy unreachable, malformed response, no
        proxy at all) means "no credential".
        """
        if proxy is None:
            return False
        try:
            credentials = proxy.credential_list()
            return any(c.get("service") == "openai" for c in credentials)
        except Exception:
            return False

    def _ensure_vec_table(self):
        """Create sqlite-vec virtual table if provider has embeddings.

        If the provider's dimension, name, or model identity changed since
        the table was last built (e.g. local 384 → OpenAI 1536, or a
        same-dimension model swap like MiniLM → e5-small), drops and
        recreates the table, then re-embeds every note from scratch —
        single-user deployment, so old vectors are disposable rather than
        migrated.
        """
        if self._embedding_provider.dimension == 0:
            return
        try:
            import sqlite_vec
            conn = self._app.storage._get_connection()
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            dim = self._embedding_provider.dimension
            provider_name = self._embedding_provider.name
            model_id = self._embedding_provider.model_id

            # Check if provider identity changed (stamped in knowledge_config).
            stored_dim = None
            stored_provider = None
            stored_model = None
            try:
                row = self._app.storage.fetchone(
                    "SELECT value FROM knowledge_config WHERE key = 'embedding_dimension'"
                )
                if row:
                    stored_dim = int(row["value"])
                row = self._app.storage.fetchone(
                    "SELECT value FROM knowledge_config WHERE key = 'embedding_provider'"
                )
                if row:
                    stored_provider = row["value"]
                row = self._app.storage.fetchone(
                    "SELECT value FROM knowledge_config WHERE key = 'embedding_model'"
                )
                if row:
                    stored_model = row["value"]
            except Exception:
                pass

            # A mismatch in ANY stamp — including a same-dimension model
            # swap that leaves stored_dim untouched — invalidates the
            # existing vectors.
            changed = (
                stored_dim is not None
                and (stored_dim != dim or stored_provider != provider_name or stored_model != model_id)
            )
            if changed:
                logger.info(
                    "Embedding config changed (dim %s → %d, provider %s → %s, model %s → %s) — "
                    "recreating vector table",
                    stored_dim, dim, stored_provider, provider_name, stored_model, model_id,
                )
                try:
                    conn.execute("DROP TABLE IF EXISTS knowledge_vec")
                    conn.commit()
                except Exception:
                    pass

            # Pin the metric to cosine. sqlite-vec defaults to L2; the
            # similarity math in find_relevant/find_duplicates assumes cosine
            # distance (similarity = 1 - distance). Mixing the two silently
            # miscalibrates every threshold.
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec "
                f"USING vec0(embedding float[{dim}] distance_metric=cosine)"
            )
            conn.commit()

            # Store current config in knowledge_config
            self._app.storage.execute(
                "INSERT OR REPLACE INTO knowledge_config (key, value) VALUES (?, ?)",
                ("embedding_dimension", str(dim)),
            )
            self._app.storage.execute(
                "INSERT OR REPLACE INTO knowledge_config (key, value) VALUES (?, ?)",
                ("embedding_provider", provider_name),
            )
            self._app.storage.execute(
                "INSERT OR REPLACE INTO knowledge_config (key, value) VALUES (?, ?)",
                ("embedding_model", model_id),
            )

            if changed:
                self._backfill_embeddings()
        except Exception as e:
            logger.warning("sqlite-vec not available: %s", e)
            # Fallback: no vector search
            from mycelos.knowledge.embeddings import FallbackProvider
            self._embedding_provider = FallbackProvider()

    def _backfill_embeddings(self) -> int:
        """Re-embed every note after a provider/model/dimension change.

        Single-user deployment: old vectors are disposable, so this is a
        full rebuild rather than an incremental migration. Failures log a
        warning and stop the loop rather than crashing startup — the
        vector arm simply stays partial and hybrid search degrades to FTS.
        """
        if self._embedding_provider.dimension == 0:
            return 0
        notes = self._app.storage.fetchall(
            "SELECT id, path, title FROM knowledge_notes WHERE type != 'topic'"
        )
        if not notes:
            return 0
        from mycelos.knowledge.embeddings import serialize_embedding
        done = 0
        batch_size = 32
        for start in range(0, len(notes), batch_size):
            chunk = notes[start:start + batch_size]
            texts = [f"{n['title']} {self._note_body(n['path'])}" for n in chunk]
            try:
                vectors = self._embedding_provider.compute_batch(texts)
            except Exception as e:
                logger.warning("Embedding backfill failed at offset %d: %s", start, e)
                break
            for note, vector in zip(chunk, vectors):
                if not vector:
                    continue
                self._app.storage.execute(
                    "INSERT OR REPLACE INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
                    (note["id"], serialize_embedding(vector)),
                )
                done += 1
        logger.info("Re-embedded %d notes with %s", done, self._embedding_provider.name)
        return done

    # ─── Write ─────────────────────────────────────────────────────────────────

    def write(
        self,
        title: str,
        content: str,
        type: str = "note",
        tags: list[str] | None = None,
        status: str = "active",
        due: str | None = None,
        links: list[str] | None = None,
        priority: int = 0,
        topic: str | None = None,
        reminder: bool = False,
        auto_classify: bool = False,
        created_by: str | None = None,
        source: dict | None = None,
    ) -> str:
        """Create a new note. Returns the note path (without .md extension).

        Args:
            topic: Parent topic path. If set, note is linked to this topic.
            reminder: If True and due is set, triggers notifications.
            auto_classify: If True, calls LLM to classify topic/type/tags.
            created_by: Provenance — agent id, 'user', 'organizer', 'import'.
                Defaults to 'user'.
            source: Provenance JSON — kind plus references (conversation_id,
                connector, external_id, filename, url).
        """
        # Auto-classify via LLM if requested
        if auto_classify:
            classification = self._auto_classify(title, content)
            if classification:
                if classification.get("topic") == "__new__" and classification.get("new_topic_name"):
                    topic = self.create_topic(
                        classification["new_topic_name"],
                        tags=classification.get("suggested_tags", []),
                    )
                elif classification.get("topic") and classification["topic"] != "__new__":
                    # Match topic by name/path
                    matched = self._find_topic(classification["topic"])
                    if matched:
                        topic = matched
                if classification.get("suggested_tags"):
                    tags = list(set((tags or []) + classification["suggested_tags"]))
                if classification.get("suggested_type"):
                    type = classification["suggested_type"]
                if classification.get("due_date"):
                    due = classification["due_date"]
                if classification.get("reminder") is True:
                    reminder = True

        # Deterministic bucketing: if no topic was supplied, place the note
        # under ``tasks/`` or ``notes/`` based on its own fields.
        # Skip bucketing for topic-type notes — they use their own
        # ``topics/`` prefix from generate_path().
        if not topic and type != "topic":
            topic = bucket_note({
                "reminder": reminder,
                "due": due,
                "parent_path": "",
            })

        # Auto-create missing topic: if the caller specified a topic path
        # that starts with "topics/" but doesn't exist yet, create it so
        # notes don't end up as orphans with an invisible parent.
        if topic and topic.startswith("topics/") and type != "topic":
            existing = self._app.storage.fetchone(
                "SELECT path FROM knowledge_notes WHERE path=? AND type='topic'",
                (topic,),
            )
            if not existing:
                topic_name = topic.rsplit("/", 1)[-1].replace("-", " ").title()
                self.create_topic(topic_name)

        created_by = created_by or "user"
        note = Note(
            title=title,
            content=content,
            type=type,
            tags=tags or [],
            status=status,
            due=due,
            links=links or [],
            priority=priority,
            reminder=reminder,
            parent_path=topic or "",
            created_at=_now(),
            updated_at=_now(),
            created_by=created_by,
            source=source,
        )
        path = note.generate_path()

        # Sub-topics: if the note is a topic with a parent, nest it
        # under the parent path instead of the default "topics/" folder.
        if note.type == "topic" and note.parent_path:
            slug = path.rsplit("/", 1)[-1]  # e.g. "rezepte" from "topics/rezepte"
            path = f"{note.parent_path}/{slug}"

        # Handle duplicate paths by appending a counter. Probe through
        # _safe_path so a traversal-y title (e.g. "../../etc/passwd") cannot
        # check for file existence outside the knowledge dir.
        base_path = path
        counter = 2
        while self._safe_path(path).exists():
            path = f"{base_path}-{counter}"
            counter += 1
        note.path = path

        # Write file — _safe_path raises PathTraversalError if the resolved
        # path escapes self._knowledge_dir.
        file_path = self._safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(render_note(note), encoding="utf-8")

        # remind_via is resolved at fire time by ReminderService, not here.
        # Storing NULL means "notify every active channel"; the ReminderService
        # falls back to its _default_channels() which re-reads the channels
        # table. That way a user who adds Telegram AFTER the reminder was
        # created still gets the notification on Telegram. Callers who want
        # to pin a specific channel set should write remind_via on the note
        # explicitly via the knowledge indexer.
        remind_via_json = None

        # Index note
        content_hash = hashlib.md5(content.encode()).hexdigest()
        self._indexer.index_note(
            path,
            title,
            type,
            status,
            json.dumps(tags or []),
            priority,
            due,
            content_hash,
            content,
            parent_path=topic,
            reminder=reminder,
            remind_via=remind_via_json,
            created_by=created_by,
            source=json.dumps(source) if source else None,
        )

        # Store links
        self._indexer.replace_links(path, links or [])

        # Compute and store embedding if provider is available
        if self._embedding_provider.dimension > 0:
            try:
                embedding = self._embedding_provider.compute(f"{title} {content}")
                if embedding:
                    from mycelos.knowledge.embeddings import serialize_embedding
                    note_id = self._indexer.get_note_id(path)
                    if note_id:
                        emb_bytes = serialize_embedding(embedding)
                        self._app.storage.execute(
                            "INSERT OR REPLACE INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
                            (note_id, emb_bytes),
                        )
            except Exception as e:
                logger.debug("Embedding failed for %s: %s", path, e)

        # Audit — record the creator so provenance is reconstructable from
        # the audit trail too, not only from the note row.
        self._app.audit.log(
            "knowledge.note.created",
            agent_id=created_by if created_by != "user" else None,
            details={
                "path": path, "type": type, "tags": tags or [],
                "created_by": created_by,
                "source_kind": (source or {}).get("kind"),
            },
        )

        return path

    # ─── Document storage ──────────────────────────────────────────────────────

    def store_document(
        self,
        file_bytes: bytes,
        filename: str,
        title: str = "",
        summary: str = "",
        tags: list[str] | None = None,
        created_by: str | None = None,
        source: dict | None = None,
    ) -> str:
        """Store a document file and create a Knowledge Note for it. Returns the note path."""
        from pathlib import Path
        from mycelos.files.inbox import sanitize_filename

        # Save file to knowledge/documents/
        doc_dir = self._knowledge_dir / "documents"
        doc_dir.mkdir(parents=True, exist_ok=True)

        date_prefix = _now()[:10]  # YYYY-MM-DD
        safe_name = sanitize_filename(filename)
        doc_path = doc_dir / f"{date_prefix}_{safe_name}"

        # Handle duplicates
        if doc_path.exists():
            stem, suffix = doc_path.stem, doc_path.suffix
            counter = 2
            while doc_path.exists():
                doc_path = doc_dir / f"{stem}-{counter}{suffix}"
                counter += 1

        doc_path.write_bytes(file_bytes)

        # Relative path from knowledge dir
        relative_doc = str(doc_path.relative_to(self._knowledge_dir))

        if not title:
            title = filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()

        content = summary or f"Document: {filename}"
        if summary:
            content += f"\n\n---\n*Source: {filename}*"

        path = self.write(
            title=title,
            content=content,
            type="document",
            tags=tags or [],
            created_by=created_by,
            source=source or {"kind": "document", "filename": filename},
        )

        # Set source_file on the note
        self._app.storage.execute(
            "UPDATE knowledge_notes SET source_file=? WHERE path=?",
            (relative_doc, path),
        )

        return path

    def get_document_path(self, source_file: str) -> "Path | None":
        """Return the absolute path to a document file, or None."""
        from pathlib import Path
        if not source_file:
            return None
        full = self._knowledge_dir / source_file
        if full.exists() and full.is_file():
            return full
        return None

    # ─── Read ──────────────────────────────────────────────────────────────────

    def read(self, path: str) -> dict | None:
        """Read a note by path. Returns None if not found.

        The returned dict merges the markdown frontmatter (title, content,
        tags, status, due, …) with the indexed DB row so reminder metadata
        (``reminder``, ``remind_at``, ``remind_via``) is surfaced to the
        detail view without requiring a separate fetch.
        """
        file_path = self._safe_path(path)
        if not file_path.exists():
            return None
        md = file_path.read_text(encoding="utf-8")
        note = parse_frontmatter(md)
        backlinks = self._indexer.get_backlinks(path)

        # Pull reminder/status fields from the index row — these are the
        # authoritative source for scheduler-related metadata, not the
        # frontmatter (which may be stale after DB-only updates).
        row = self._indexer.get_note_meta(path) or {}
        reminder_flag = bool(row.get("reminder")) if row else note.reminder
        remind_at = row.get("remind_at") if row else None
        remind_via_raw = row.get("remind_via") if row else None
        remind_via: list[str] | None = None
        if remind_via_raw:
            try:
                import json as _json
                parsed = _json.loads(remind_via_raw)
                if isinstance(parsed, list):
                    remind_via = parsed
            except (ValueError, TypeError):
                remind_via = None
        if remind_via is None:
            remind_via = ["chat"]

        return {
            "title": note.title,
            "content": note.content,
            "type": note.type,
            "tags": note.tags,
            "links": note.links,
            "status": note.status,
            "priority": note.priority,
            "due": note.due,
            "path": path,
            "backlinks": backlinks,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "reminder": reminder_flag,
            "remind_at": remind_at,
            "remind_via": remind_via,
            "source_file": row.get("source_file") if row else None,
            "created_by": (row.get("created_by") if row else None) or note.created_by or None,
            "source": _parse_source(row.get("source")) if row else note.source,
        }

    # ─── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Hybrid search: FTS5 and vector KNN fused via RRF.

        Degrades to FTS-only when no embedding provider is configured, and
        to the LIKE fallback when both arms come back empty (typo net).
        """
        fts_results = self._indexer.search_fts(
            query, type=type, tags=tags, limit=limit * 2
        )
        vector_results: list[dict] = []
        if self._embedding_provider.dimension > 0:
            candidates = self._find_relevant_by_vector(
                query, top_k=limit * 2, threshold=0.25
            )
            vector_results = _filter_results(candidates, type=type, tags=tags)
        if not fts_results and not vector_results:
            return self._indexer.search_like(query, type=type, limit=limit)
        if not vector_results:
            return fts_results[:limit]
        return ranking.rrf_fuse([fts_results, vector_results], limit=limit)

    # ─── List ──────────────────────────────────────────────────────────────────

    def list_notes(
        self,
        type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        due: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List notes with optional filters."""
        return self._indexer.list_notes(
            type=type, status=status, tags=tags, due=due, limit=limit
        )

    # ─── Update ────────────────────────────────────────────────────────────────

    def update(
        self,
        path: str,
        status: str | None = None,
        tags: list[str] | None = None,
        due: str | None = None,
        content: str | None = None,
        append: bool = False,
        priority: int | None = None,
    ) -> bool:
        """Update an existing note. Returns False if not found."""
        file_path = self._safe_path(path)
        if not file_path.exists():
            return False

        md = file_path.read_text(encoding="utf-8")
        note = parse_frontmatter(md)

        if status is not None:
            note.status = status
        if tags is not None:
            note.tags = tags
        if due is not None:
            note.due = due
        if priority is not None:
            note.priority = priority
        if content is not None:
            if append:
                note.content = note.content + "\n\n" + content
            else:
                note.content = content
        note.updated_at = _now()

        file_path.write_text(render_note(note), encoding="utf-8")

        # Re-index. Preserve columns that `update()` does not manage —
        # parent_path, reminder, remind_via, source_file, provenance — by
        # forwarding the existing row's values. Without this, index_note's
        # UPDATE branch overwrites them with defaults, orphaning the note from
        # its topic, clearing pending reminders, and destroying provenance.
        existing_meta = self._indexer.get_note_meta(path) or {}
        content_hash = hashlib.md5(note.content.encode()).hexdigest()
        self._indexer.index_note(
            path,
            note.title,
            note.type,
            note.status,
            json.dumps(note.tags),
            note.priority,
            note.due,
            content_hash,
            note.content,
            parent_path=existing_meta.get("parent_path"),
            reminder=bool(existing_meta.get("reminder")),
            remind_via=existing_meta.get("remind_via"),
            source_file=existing_meta.get("source_file"),
            created_by=existing_meta.get("created_by"),
            source=existing_meta.get("source"),
        )
        # Re-compute embedding if content changed
        if content is not None and self._embedding_provider.dimension > 0:
            try:
                embedding = self._embedding_provider.compute(f"{note.title} {note.content}")
                if embedding:
                    from mycelos.knowledge.embeddings import serialize_embedding
                    note_id = self._indexer.get_note_id(path)
                    if note_id:
                        emb_bytes = serialize_embedding(embedding)
                        self._app.storage.execute(
                            "INSERT OR REPLACE INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
                            (note_id, emb_bytes),
                        )
            except Exception as e:
                logger.debug("Embedding update failed for %s: %s", path, e)

        self._app.audit.log("knowledge.note.updated", details={"path": path})

        return True

    # ─── Link ──────────────────────────────────────────────────────────────────

    def append_related_link(self, note_path: str, target_path: str) -> bool:
        """Append a wikilink under an appended '## Verwandt' heading.

        Append-only: never edits existing prose. Creates the heading if
        missing, otherwise appends a new bullet under it.

        Both note_path and target_path are validated against the knowledge
        dir — a traversal path (e.g. "../../etc/passwd") raises
        PathTraversalError instead of writing outside.

        Returns True when the link was written (or already present),
        False when it no-ops because the source note file does not exist.
        """
        file_path = self._safe_path(note_path)
        # Validate target_path too so we never embed a traversal string as a
        # wikilink — the link is append-only but a traversal target in the
        # body would be rendered as an active link.
        self._safe_path(target_path)
        if not file_path.exists():
            return False
        body = file_path.read_text(encoding="utf-8")
        heading = "## Verwandt"
        link = f"- [[{target_path}]]"
        if heading not in body:
            body = body.rstrip() + f"\n\n{heading}\n{link}\n"
        elif link not in body:
            body = body.rstrip() + f"\n{link}\n"
        else:
            return True
        file_path.write_text(body, encoding="utf-8")
        return True

    def link(self, from_path: str, to_path: str) -> bool:
        """Create a directional link between two notes."""
        self._indexer.add_link(from_path, to_path)
        self._app.audit.log(
            "knowledge.note.linked",
            details={"from_path": from_path, "to_path": to_path},
        )

        return True

    # ─── Topics ────────────────────────────────────────────────────────────────

    def create_topic(
        self,
        title: str,
        tags: list[str] | None = None,
        parent: str | None = None,
    ) -> str:
        """Create a topic note. Returns the topic path.

        Args:
            parent: If set, the topic is created as a sub-topic under this
                    parent path (e.g. ``"topics/kaffee"``). The resulting
                    path will be ``<parent>/<slug>``.
        """
        return self.write(
            title=title,
            content=f"# {title}\n",
            type="topic",
            tags=tags,
            status="active",
            topic=parent,
        )

    def list_topics(self, limit: int = 100, top_level_only: bool = False) -> list[dict]:
        """List topic notes. If top_level_only, exclude sub-topics."""
        return self._indexer.list_topics(limit=limit, top_level_only=top_level_only)

    def list_children(self, topic_path: str, limit: int = 100) -> list[dict]:
        """List notes belonging to a topic."""
        return self._indexer.list_children(topic_path, limit=limit)

    def move_to_topic(self, path: str, topic_path: str) -> bool:
        """Move a note to a different topic."""
        if not self._indexer.get_note_meta(path):
            return False
        self._indexer.set_parent(path, topic_path)
        # Also update the file's frontmatter
        file_path = self._safe_path(path)
        if file_path.exists():
            md = file_path.read_text(encoding="utf-8")
            note = parse_frontmatter(md)
            note.parent_path = topic_path
            note.updated_at = _now()
            file_path.write_text(render_note(note), encoding="utf-8")
        self._app.audit.log(
            "knowledge.note.moved",
            details={"path": path, "topic": topic_path},
        )
        return True

    # ─── Topic Management ─────────────────────────────────────────────────────

    def rename_topic(self, path: str, new_name: str) -> str:
        """Rename a topic. Updates DB rows and renames the .md file on disk.

        Returns the new topic path.
        """
        from mycelos.knowledge.note import slugify
        new_slug = slugify(new_name)
        # Keep parent prefix: topics/drinks/kaffee -> topics/drinks/<new_slug>
        parts = path.rsplit("/", 1)
        if len(parts) == 2:
            new_path = f"{parts[0]}/{new_slug}"
        else:
            new_path = new_slug

        # Rename file on disk
        old_file = self._safe_path(path)
        new_file = self._safe_path(new_path)
        new_file.parent.mkdir(parents=True, exist_ok=True)
        if old_file.exists():
            old_file.rename(new_file)

        # Update the topic note's path and title in DB
        self._app.storage.execute(
            "UPDATE knowledge_notes SET path=?, title=?, updated_at=? WHERE path=?",
            (new_path, new_name, _now(), path),
        )

        # Update all children's parent_path
        self._app.storage.execute(
            "UPDATE knowledge_notes SET parent_path=?, updated_at=? WHERE parent_path=?",
            (new_path, _now(), path),
        )

        # Keep the graph consistent: re-point link endpoints to the new path
        # and sync the FTS row so search finds the new title (the raw UPDATE
        # above bypasses index_note's FTS sync).
        self._indexer.repoint_links(path, new_path)
        note_id = self._indexer.get_note_id(new_path)
        if note_id:
            self._app.storage.execute(
                "DELETE FROM knowledge_fts WHERE rowid = ?", (note_id,)
            )
            self._app.storage.execute(
                "INSERT INTO knowledge_fts(rowid, title, content, tags) VALUES (?, ?, '', '')",
                (note_id, new_name),
            )

        self._app.audit.log(
            "knowledge.topic.renamed",
            details={"old_path": path, "new_path": new_path, "new_name": new_name},
        )
        return new_path

    def merge_topics(self, source: str, target: str) -> dict:
        """Merge source topic into target. Moves all children, leaves a redirect.

        Returns dict with ``moved`` count and ``redirect`` path.
        """
        children = self.list_children(source)
        for child in children:
            self.move_to_topic(child["path"], target)

        # Overwrite source .md with a redirect (kept on disk for humans
        # browsing the folder), but remove the DB row — a merged-away topic
        # must disappear from list_topics, search, and the graph.
        source_meta = self._indexer.get_note_meta(source)
        old_title = source_meta["title"] if source_meta else source
        source_file = self._safe_path(source)
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(
            f"# {old_title}\n\n> Moved to [[{target}]]\n",
            encoding="utf-8",
        )

        # Re-point inbound graph edges at the target before dropping the row
        # (remove_note would delete them outright, losing the relationship).
        self._indexer.repoint_links(source, target)
        self._indexer.remove_note(source)

        self._app.audit.log(
            "knowledge.topic.merged",
            details={"source": source, "target": target, "moved": len(children)},
        )
        return {"moved": len(children), "redirect": source}

    def delete_topic(self, path: str) -> bool:
        """Delete an empty topic. Raises ValueError if topic has children."""
        children = self.list_children(path)
        if children:
            raise ValueError(
                f"Cannot delete topic '{path}': has {len(children)} children"
            )

        # Delete file on disk
        file_path = self._safe_path(path)
        if file_path.exists():
            file_path.unlink()

        # Delete DB row (indexer handles FTS cleanup)
        self._indexer.remove_note(path)

        self._app.audit.log(
            "knowledge.topic.deleted",
            details={"path": path},
        )
        return True

    def archive_note(self, path: str) -> bool:
        """Archive a note: sets status and organizer_state to 'archived'."""
        self._app.storage.execute(
            "UPDATE knowledge_notes SET status='archived', organizer_state='archived', "
            "organizer_seen_at=? WHERE path=?",
            (_now(), path),
        )
        self._app.audit.log(
            "knowledge.note.archived",
            details={"path": path},
        )
        return True

    # ─── Task Actions ─────────────────────────────────────────────────────────

    def mark_done(self, path: str) -> bool:
        """Mark a task as done."""
        return self.update(path, status="done")

    def set_reminder(self, path: str, due: str, remind_at: str | None = None) -> bool:
        """Set a reminder on a note.

        Args:
            path: note path.
            due: day-granularity deadline (ISO date or datetime).
            remind_at: optional exact ISO datetime at which the scheduler
                should fire. Pass ``None`` (the default) to clear any
                previously-configured datetime.
        """
        if not self._indexer.get_note_meta(path):
            return False
        self._indexer.set_reminder(path, due, reminder=True, remind_at=remind_at)
        # Also update the file
        file_path = self._safe_path(path)
        if file_path.exists():
            md = file_path.read_text(encoding="utf-8")
            note = parse_frontmatter(md)
            note.due = due
            note.reminder = True
            note.updated_at = _now()
            file_path.write_text(render_note(note), encoding="utf-8")
        self._app.audit.log(
            "knowledge.reminder.set",
            details={"path": path, "due": due, "remind_at": remind_at},
        )
        return True

    # ─── Topic Index Generation ───────────────────────────────────────────────

    def regenerate_topic_indexes(self) -> int:
        """Regenerate content for all topic notes with child listings.

        Returns the number of topics updated.
        """
        topics = self.list_topics()
        count = 0
        for topic in topics:
            topic_path = topic["path"]
            children = self.list_children(topic_path)

            notes = [c for c in children if c.get("type") != "task"]
            tasks = [c for c in children if c.get("type") == "task"]

            lines = [f"# {topic['title']}\n"]

            if notes:
                lines.append(f"## Notes ({len(notes)})\n")
                for n in notes:
                    lines.append(f"- [[{n['path']}|{n['title']}]]")
                lines.append("")

            if tasks:
                open_tasks = [t for t in tasks if t.get("status") in ("open", "in-progress")]
                done_tasks = [t for t in tasks if t.get("status") == "done"]

                if open_tasks:
                    lines.append(f"## Tasks ({len(open_tasks)} open)\n")
                    for t in sorted(open_tasks, key=lambda x: x.get("due") or "9999"):
                        due = f" — due: {t['due']}" if t.get("due") else ""
                        checkbox = "[ ]"
                        lines.append(f"- {checkbox} [[{t['path']}|{t['title']}]]{due}")
                    lines.append("")

                if done_tasks:
                    lines.append(f"## Completed ({len(done_tasks)})\n")
                    for t in done_tasks:
                        lines.append(f"- [x] [[{t['path']}|{t['title']}]]")
                    lines.append("")

            # Collect tags from children
            all_tags: set[str] = set()
            for c in children:
                child_tags = c.get("tags", "[]")
                if isinstance(child_tags, str):
                    try:
                        child_tags = json.loads(child_tags)
                    except (json.JSONDecodeError, TypeError):
                        child_tags = []
                for tag in child_tags:
                    all_tags.add(tag)
            if all_tags:
                lines.append("## Tags\n")
                lines.append(", ".join(sorted(all_tags)))
                lines.append("")

            mermaid_block = build_topic_mermaid(topic_path, self)
            if mermaid_block:
                # Insert the topic map right after the title header so it
                # appears at the top of the topic-index page.
                lines.insert(1, mermaid_block)

            new_content = "\n".join(lines)

            # Write to file
            file_path = self._safe_path(topic_path)
            if file_path.exists():
                md = file_path.read_text(encoding="utf-8")
                note = parse_frontmatter(md)
                note.content = new_content
                note.updated_at = _now()
                file_path.write_text(render_note(note), encoding="utf-8")
                count += 1

        return count

    # ─── Auto-Classify ────────────────────────────────────────────────────────

    def _auto_classify(self, title: str, content: str) -> dict | None:
        """Classify a note using LLM. Returns classification dict or None."""
        llm = getattr(self._app, "_llm", None)
        if not llm:
            return None

        topics = self.list_topics()
        topic_list = ", ".join(t["title"] for t in topics) if topics else "none yet"

        prompt = (
            f"Classify this note:\n"
            f"Title: {title}\n"
            f"Content: {content}\n\n"
            f"Existing topics: {topic_list}\n\n"
            "Respond with JSON only:\n"
            '{"topic": "<existing topic name or __new__>", '
            '"new_topic_name": "<only if __new__>", '
            '"suggested_tags": ["tag1", "tag2"], '
            '"suggested_type": "note|task|decision|reference", '
            '"due_date": "<YYYY-MM-DD or null>", '
            '"reminder": <true/false>}'
        )
        try:
            model = self._app.resolve_cheapest_model()
            response = llm.complete(
                [
                    {"role": "system", "content": "You are a note classifier. Respond only with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=model,
            )
            return json.loads(response.content)
        except Exception as e:
            logger.debug("Auto-classify failed: %s", e)
            return None

    def _find_topic(self, topic_name: str) -> str | None:
        """Find a topic path by name (case-insensitive)."""
        topics = self.list_topics()
        name_lower = topic_name.lower()
        for t in topics:
            if t["title"].lower() == name_lower:
                return t["path"]
            # Also match by path slug
            if t["path"].split("/")[-1] == name_lower:
                return t["path"]
        return None

    # ─── Relations / Graph ────────────────────────────────────────────────────

    def sync_relations(self) -> dict[str, int]:
        """Rebuild note relations from frontmatter links + wiki links in content."""
        notes = self.list_notes(limit=5000)
        all_paths = {n["path"] for n in notes}
        title_to_path = {n["title"].strip().lower(): n["path"] for n in notes if n.get("title")}

        edge_count = 0
        for entry in notes:
            path = entry["path"]
            note = self.read(path)
            if not note:
                continue

            targets: set[str] = set()
            for frontmatter_link in note.get("links", []) or []:
                if frontmatter_link in all_paths and frontmatter_link != path:
                    targets.add(frontmatter_link)

            for raw_link in self._extract_wikilinks(note.get("content", "")):
                if raw_link in all_paths:
                    targets.add(raw_link)
                    continue
                mapped = title_to_path.get(raw_link.strip().lower())
                if mapped and mapped != path:
                    targets.add(mapped)

            self._indexer.replace_links(path, sorted(targets))
            edge_count += len(targets)

        self._app.audit.log(
            "knowledge.relations.synced",
            details={"notes": len(notes), "links": edge_count},
        )
        return {"notes": len(notes), "links": edge_count}

    def get_graph_data(self) -> dict:
        """Return note graph data suitable for web visualization.

        Archived notes are excluded; edges carry a ``kind``
        (wikilink | parent | related | merged_from). Topic membership
        (parent_path) is synthesized into ``parent`` edges so the graph
        shows the hierarchy without a separate query.
        """
        notes = [n for n in self.list_notes(limit=5000)
                 if n.get("status") != "archived"]
        visible = {n["path"] for n in notes}
        links = self._indexer.list_links()
        nodes = [
            {
                "id": n["path"],
                "title": n["title"],
                "type": n["type"],
                "status": n["status"],
                "priority": n["priority"],
                "updated_at": n["updated_at"],
            }
            for n in notes
        ]
        edges = [
            {"source": l["from_path"], "target": l["to_path"],
             "kind": l.get("kind") or "wikilink"}
            for l in links
            if l["from_path"] in visible and l["to_path"] in visible
        ]
        # Topic membership as typed edges.
        for n in notes:
            parent = n.get("parent_path")
            if parent and parent in visible:
                edges.append({"source": n["path"], "target": parent, "kind": "parent"})
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {"notes": len(nodes), "links": len(edges)},
        }

    @staticmethod
    def _extract_wikilinks(content: str) -> list[str]:
        """Extract wikilinks like [[path]] or [[path|Alias]]."""
        results: list[str] = []
        for match in re.findall(r"\[\[([^\]]+)\]\]", content or ""):
            target = match.split("|", 1)[0].strip()
            if target:
                results.append(target)
        return results

    # ─── Relevance ─────────────────────────────────────────────────────────────

    def _find_relevant_by_vector(
        self,
        text: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[dict]:
        """Vector-only relevance search. Returns [] when no embedding provider
        is available or the vector query fails — never falls back to keyword
        search, so callers (e.g. duplicate detection) can rely on the
        threshold actually being honored.

        Always embeds ``text`` as a query (E5 ``query: `` prefix) — every
        caller passes search/duplicate-lookup text, never a document body.
        """
        if self._embedding_provider.dimension == 0:
            return []
        try:
            embedding = self._embedding_provider.compute(text, is_query=True)
            if not embedding:
                return []
            from mycelos.knowledge.embeddings import serialize_embedding
            emb_bytes = serialize_embedding(embedding)
            rows = self._app.storage.fetchall(
                """SELECT kn.path, kn.title, kn.type, kn.status, kn.priority,
                          kn.due, kn.tags, kn.created_at, kn.updated_at,
                          v.distance
                   FROM knowledge_vec v
                   JOIN knowledge_notes kn ON kn.id = v.rowid
                   WHERE embedding MATCH ?
                   AND k = ?
                   ORDER BY v.distance""",
                (emb_bytes, top_k),
            )
            # The vec table uses cosine distance, so similarity = 1 - distance.
            results = []
            for r in rows:
                score = 1.0 - r.get("distance", 1.0)
                # Priority boost: each priority level adds 0.05
                score += r.get("priority", 0) * 0.05
                if score >= threshold:
                    result = dict(r)
                    result["score"] = score
                    results.append(result)
            return sorted(results, key=lambda x: x["score"], reverse=True)
        except Exception as e:
            logger.debug("Vector search failed: %s", e)
            return []

    def find_relevant(
        self,
        text: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[dict]:
        """Find notes relevant to the given text — hybrid FTS+vector via RRF.

        ``threshold`` bounds the vector arm's cosine similarity only; FTS
        matches join through rank fusion regardless. Degrades to FTS-only
        when no embedding provider is available. Relevance is
        non-destructive, so keyword participation is appropriate here
        (unlike duplicate detection, which stays vector-only).
        """
        vector_results: list[dict] = []
        if self._embedding_provider.dimension > 0:
            vector_results = self._find_relevant_by_vector(
                text, top_k=top_k * 2, threshold=threshold
            )
        fts_results = self._indexer.search_fts(text, limit=top_k * 2)
        if not vector_results:
            return fts_results[:top_k]
        if not fts_results:
            return vector_results[:top_k]
        return ranking.rrf_fuse([fts_results, vector_results], limit=top_k)

    def find_duplicates(
        self,
        path: str,
        threshold: float = 0.92,
        top_k: int = 3,
    ) -> list[dict]:
        """Find notes that are potential duplicates of the given note.

        Uses vector similarity search. Returns notes with cosine similarity
        >= threshold, excluding self-matches and archived notes.
        """
        note = self._indexer.get_note_meta(path)
        if not note:
            return []

        # Duplicate detection requires real semantic similarity. Without an
        # embedding provider we must NOT fall back to keyword search — FTS
        # ignores the similarity threshold, so mere keyword overlap ("invoice",
        # "meeting") would be proposed as a merge and could destroy data.
        # Fail closed: no embeddings → no duplicate claims.
        if self._embedding_provider.dimension == 0:
            return []

        # Read note content for embedding query
        file_path = self._safe_path(path)
        if not file_path.exists():
            return []

        md = file_path.read_text(encoding="utf-8")
        parsed = parse_frontmatter(md)
        query_text = f"{parsed.title} {parsed.content[:400]}"

        candidates = self._find_relevant_by_vector(query_text, top_k=top_k + 1, threshold=threshold)

        # Filter out self and archived notes
        results = []
        for c in candidates:
            if c.get("path") == path:
                continue
            if c.get("status") == "archived":
                continue
            results.append(c)

        return results[:top_k]

    # ─── Index Generation ──────────────────────────────────────────────────

    def regenerate_index(self) -> None:
        """Regenerate knowledge/index.md with overview of all notes."""
        notes = self.list_notes(limit=1000)
        tasks_open = [n for n in notes if n.get("type") == "task" and n.get("status") in ("open", "in-progress")]
        recent = sorted(notes, key=lambda n: n.get("updated_at") or n.get("created_at") or "", reverse=True)[:10]

        lines = ["# Knowledge Base\n"]

        if tasks_open:
            lines.append(f"## Open Tasks ({len(tasks_open)})\n")
            for t in sorted(tasks_open, key=lambda x: x.get("due") or "9999"):
                due = f" — due: {t['due']}" if t.get("due") else ""
                prio = f" [P{t['priority']}]" if t.get("priority", 0) > 0 else ""
                lines.append(f"- [[{t['path']}|{t['title']}]]{due}{prio}")
            lines.append("")

        if recent:
            lines.append("## Recent\n")
            for n in recent:
                updated = (n.get("updated_at") or n.get("created_at") or "")[:10]
                lines.append(f"- [[{n['path']}|{n['title']}]] ({n['type']}, {updated})")
            lines.append("")

        # Tag summary
        all_tags = {}
        for n in notes:
            tags = n.get("tags", "[]")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            for tag in tags:
                all_tags[tag] = all_tags.get(tag, 0) + 1
        if all_tags:
            lines.append("## Tags\n")
            tag_str = " | ".join(f"#{t} ({c})" for t, c in sorted(all_tags.items(), key=lambda x: -x[1])[:20])
            lines.append(tag_str)
            lines.append("")

        index_path = self._knowledge_dir / "index.md"
        index_path.write_text("\n".join(lines), encoding="utf-8")
