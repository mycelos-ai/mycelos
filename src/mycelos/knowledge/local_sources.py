"""Persistent local source intake shared by CLI, tools and HTTP.

Source parsing never modifies originals. Explicit imports write into the
existing knowledge service and record runs in the existing workflow_runs table.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from collections.abc import Callable, Iterable

from mycelos.knowledge.local_readers import SourceUnit, read_units
from mycelos.knowledge.source_lock import source_lock
from mycelos.knowledge import source_articles
from mycelos.knowledge.source_workflows import resolve_workflow, inspect_source, source_root
from mycelos.scheduler.run_recorder import CAUSES, RunRecorder
from mycelos.i18n import t as _t


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_recipe(source: dict) -> str:
    return _hash(json.dumps([source["mode"], source["model"], source["purpose"],
                            2 if source["mode"] == "analyze" else 1]))


class LocalSourceService:
    def __init__(self, app, user_id: str = "default", summarize=None):
        self.app = app
        self.db = app.storage
        self.user_id = user_id
        self.summarize = summarize or self._summarize

    @staticmethod
    def _validate_options(name, mode, model):
        if not name.strip():
            raise ValueError(_t("sources.name_required"))
        if mode not in {"analyze", "direct"}:
            raise ValueError(_t("sources.unknown_mode"))
        if mode == "analyze" and not (model or "").strip():
            raise ValueError(_t("sources.model_required"))

    @staticmethod
    def _validate_root(kind, root):
        if not root.strip():
            raise ValueError(_t("sources.root_required"))
        raw_path = Path(root).expanduser()
        if raw_path.is_symlink():
            raise ValueError(_t("sources.no_symlink"))
        path = raw_path.resolve()
        if kind not in {"file", "folder", "git", "mercurial", "codex", "claude", "obsidian"}:
            raise ValueError(_t("sources.unknown_kind"))
        if kind == "file":
            if not path.is_file():
                raise ValueError(_t("sources.file_unavailable"))
            return source_root(kind, path)
        if not path.is_dir() or path == Path(path.anchor) or path == Path.home():
            raise ValueError(_t("sources.folder_too_broad"))
        return source_root(kind, path)

    def add(self, name: str, kind: str, root: str, purpose: str = "",
            mode: str = "analyze", model: str | None = None, workflow: dict | None = None) -> dict:
        path = self._validate_root(kind, root)
        self._validate_options(name, mode, model)
        workflow = resolve_workflow(kind, path, workflow)
        source_id = uuid4().hex
        self.db.execute(
            "INSERT INTO local_sources(id,user_id,name,kind,root,purpose,mode,model,workflow) VALUES(?,?,?,?,?,?,?,?,?)",
            (source_id, self.user_id, name.strip(), kind, str(path), purpose, mode, model, json.dumps(workflow, ensure_ascii=False)))
        # The root path stays out of the audit payload: it can name clients.
        self._changed("local_source.added", source_id, {"kind": kind, "mode": mode},
                      f"Local source {source_id} added", "local_source_add")
        return self.get(source_id)

    def update(self, source_id, name, purpose, mode, model=None, workflow=None):
        source = self.get(source_id)
        workflow = resolve_workflow(source["kind"], Path(source["root"]),
                                    source["workflow"] if workflow is None else workflow)
        self._validate_options(name, mode, model)
        with source_lock(self.app.data_dir, source_id):
            result = self.db.execute(
                "UPDATE local_sources SET name=?,purpose=?,mode=?,model=?,workflow=? "
                "WHERE id=? AND user_id=? AND status NOT IN ('running','queued')",
                (name.strip(), purpose, mode, model, json.dumps(workflow, ensure_ascii=False), source_id, self.user_id))
            if not result.rowcount:
                raise ValueError(_t("sources.wait_for_import"))
        self._changed("local_source.updated", source_id, {"mode": mode},
                      f"Local source {source_id} updated", "local_source_update")
        return self.get(source_id)

    def inspect(self, kind, root, workflow=None):
        path = self._validate_root(kind, root)
        return {**inspect_source(kind, path, workflow), "source_id": None}

    def get(self, source_id: str) -> dict:
        result = self.db.fetchone("SELECT * FROM local_sources WHERE id=? AND user_id=?",
                                  (source_id, self.user_id))
        if not result:
            raise ValueError(_t("sources.not_found"))
        return self._decode_source(result)

    def _decode_source(self, source):
        source = dict(source)
        stored = source.get("workflow")
        source["workflow"] = (json.loads(stored) if stored else
                              resolve_workflow(source["kind"], Path(source["root"])))
        if not stored:
            # Freeze the legacy defaults once. A later template must not change
            # an existing source without a reviewed edit.
            self.db.execute("UPDATE local_sources SET workflow=? WHERE id=? AND user_id=? AND workflow IS NULL",
                            (json.dumps(source["workflow"], ensure_ascii=False), source["id"], self.user_id))
        return source

    def list(self) -> list[dict]:
        return [self._decode_source(row) for row in self.db.fetchall(
            "SELECT * FROM local_sources WHERE user_id=? ORDER BY created_at,id", (self.user_id,))]

    def pause(self, source_id: str, paused: bool = True) -> dict:
        self.get(source_id)
        cursor = self.db.execute("UPDATE local_sources SET status=? WHERE id=? AND status!='running'",
                                 ("paused" if paused else "ready", source_id))
        if not cursor.rowcount:
            raise ValueError(_t("sources.already_running"))
        self._changed("local_source.paused" if paused else "local_source.resumed", source_id, {},
                      f"Local source {source_id} {'paused' if paused else 'resumed'}",
                      "local_source_pause")
        return self.get(source_id)

    def filing(self, source_id: str) -> dict:
        """Where this source files and under which rule (shared source model).

        Local sources use the same attachment and rule tables as connectors,
        keyed ``local:<id>``; the knowledge organizer and the article import
        both read them.
        """
        from mycelos.knowledge.source_attachment import SourceAttachmentService, local_source_key
        self.get(source_id)
        service = SourceAttachmentService(self.db)
        key = local_source_key(source_id)
        return {"source_key": key,
                "attachments": service.list_attachments(key, self.user_id),
                "rule_text": service.get_rule(key, self.user_id)}

    def _changed(self, event, source_id, details, description, trigger):
        """Audit every mutation and record a config generation (Rules 1 and 2)."""
        audit = getattr(self.app, "audit", None)
        if audit is not None:
            audit.log(event, user_id=self.user_id, details={"source": source_id, **details})
        notifier = getattr(self.app, "config_notifier", None)
        if notifier is not None:
            notifier.notify_change(description, trigger)

    def preview(self, source_id: str, limit: int = 3) -> dict:
        source = self.get(source_id)
        return self._preview(source, limit)

    @staticmethod
    def require_local(source):
        if source['root'].startswith('collector://'):
            raise ValueError(_t("sources.needs_collector"))

    def _preview(self, source, limit):
        self.require_local(source)
        result = inspect_source(source["kind"], Path(source["root"]), source.get("workflow"))
        result["samples"] = result["samples"][:max(0, min(limit, 10))]
        return {**result, "source_id": source["id"]}

    def enqueue(self, source_id: str) -> dict:
        source = self.get(source_id)
        self.require_local(source)
        task = getattr(self.app, "local_source_task", None)
        if task is None:
            raise RuntimeError(_t("sources.background_unavailable"))
        try:
            with source_lock(self.app.data_dir, source_id):
                self._recover_locked(source_id)
                source = self.get(source_id)
                if source["status"] == "queued":
                    return {"source_id": source_id, "status": "queued"}
                request_id = uuid4().hex
                claimed = self.db.execute(
                    "UPDATE local_sources SET status='queued',error=NULL,import_request_id=? WHERE id=? AND status IN ('ready','error')",
                    (request_id, source_id))
                if not claimed.rowcount:
                    raise ValueError(_t("sources.paused"))
        except ValueError:
            if self.get(source_id)["status"] == "running":
                return {"source_id": source_id, "status": "running"}
            raise
        try:
            task(source_id, self.user_id, request_id)
        except Exception:
            self.db.execute("UPDATE local_sources SET status='error',import_request_id=NULL,error=? WHERE id=?",
                            (_t("sources.enqueue_failed"), source_id))
            raise
        return {"source_id": source_id, "status": "queued"}

    def probe(self, source_id: str) -> dict:
        """Analyze one bounded sample with the explicitly selected source model."""
        source = self.get(source_id)
        self.require_local(source)
        samples = []
        for unit in read_units(source["kind"], Path(source["root"]), source.get("workflow")):
            sample = SourceUnit(unit.external_id, unit.title, unit.content[:24000], unit.files, unit.project)
            started = perf_counter()
            summary = sample.content if source["mode"] == "direct" else self.summarize(sample, source)
            samples.append({"title": unit.title, "summary": source_articles.overview(summary) if source["mode"] == "analyze" else summary,
                            "articles": (source_articles.payload(summary) or {}).get("articles", []) if source["mode"] == "analyze" else [],
                            "seconds": round(perf_counter() - started, 3),
                            "sample_characters": len(sample.content),
                            "total_characters": len(unit.content),
                            "partial": len(sample.content) < len(unit.content)})
            break
        return {"source_id": source_id, "samples": samples, "estimated_cost": None,
                "model": source["model"], "saved_to_knowledge": False}

    def _summarize(self, unit: SourceUnit, source: dict) -> str:
        from mycelos.knowledge.source_analysis import extract_articles
        return json.dumps(extract_articles(self.app, unit, source), ensure_ascii=False)

    def ingest(self, source_id: str, request_id: str | None = None, *,
               units: Iterable[SourceUnit] | None = None, complete_inventory: bool = True,
               removed: Iterable[str] = (), check: Callable[[], None] | None = None) -> dict:
        source = self.get(source_id)
        if units is None:
            self.require_local(source)
        elif source['root'].startswith('collector://') and check is None:
            raise ValueError('Collector imports need an approval check')
        if check:
            check()
        with source_lock(self.app.data_dir, source_id):
            if request_id is not None:
                source = self.get(source_id)
                if source['status'] != 'queued' or source.get('import_request_id') != request_id:
                    return {'source_id': source_id, 'status': 'deferred'}
            self._recover_locked(source_id)
            return self._ingest_locked(source_id, units, complete_inventory, removed, check)

    def _recover_locked(self, source_id):
        if self.get(source_id)["status"] != "running":
            return
        # Owning the OS lock proves no importer still owns this source.
        recorder = RunRecorder(self.db)
        for run in self.db.fetchall(
                "SELECT id FROM workflow_runs WHERE kind='source_sync' AND routine_key=? AND status='running'",
                (source_id,)):
            recorder.fail(run["id"], CAUSES["source_failed"])
        self.db.execute("UPDATE local_sources SET status='error',import_request_id=NULL,error=? WHERE id=?",
                        (_t("sources.interrupted"), source_id))

    def _ingest_locked(self, source_id: str, units=None, complete_inventory=True, removed=(), check=None) -> dict:
        source = self.get(source_id)
        claimed = self.db.execute(
            "UPDATE local_sources SET status='running',error=NULL WHERE id=? AND status IN ('ready','error','queued')",
            (source_id,))
        if not claimed.rowcount:
            raise ValueError(_t("sources.paused_or_running"))
        recorder = RunRecorder(self.db)
        run_id = None
        seen_run = uuid4().hex
        counts = {"created": 0, "updated": 0, "linked": 0, "unchanged": 0, "articles": 0, "relations": 0}
        # Selection rules already affect the input fingerprint. Changing only
        # those rules must not reanalyze otherwise unchanged content.
        recipe = source_recipe(source)
        try:
            run_id = recorder.start("source_sync", source_id, self.user_id)
            # Initialize the index before entering the per-item write transaction.
            self.app.knowledge_base
            seen_ids = set()
            incoming = units if units is not None else read_units(source["kind"], Path(source["root"]), source.get("workflow"))
            for unit in incoming:
                if check:
                    check()
                if unit.external_id in seen_ids:
                    raise ValueError(_t("sources.duplicate_session"))
                seen_ids.add(unit.external_id)
                fingerprint = _hash(unit.content)
                old = self.db.fetchone(
                    "SELECT i.*,v.fingerprint,v.recipe,v.note_id FROM local_source_items i "
                    "JOIN local_source_versions v ON v.id=i.version_id WHERE source_id=? AND external_id=?",
                    (source_id, unit.external_id))
                if old and old["fingerprint"] == fingerprint and old["recipe"] == recipe:
                    self.db.execute("UPDATE local_source_items SET seen_run=?,available=1,files=?,project=? WHERE id=?",
                                    (seen_run, json.dumps(unit.files), unit.project, old["id"]))
                    counts["unchanged"] += 1
                    with source_articles.file_changes(), self.db.transaction():
                        counts["articles"] += source_articles.materialize(self.app, source, old["version_id"])
                        if source['kind'] == 'obsidian':
                            from .obsidian import persist_metadata
                            persist_metadata(self.db, source_id, unit)
                    continue
                self.db.execute("UPDATE workflow_runs SET current_step=? WHERE id=?", (_t("sources.step_extract"), run_id))
                existing = self._version(fingerprint, recipe)
                if check:
                    check()
                summary = existing["summary"] if existing else (
                    unit.content if source["mode"] == "direct" else self.summarize(unit, source))
                if not isinstance(summary, str) or not summary.strip():
                    raise ValueError(_t("sources.analysis_empty"))
                with source_articles.file_changes(), self.db.transaction():
                    # Recheck after analysis: another source may have imported it meanwhile.
                    existing = self._version(fingerprint, recipe)
                    # Collector tombstones distinguish deletion from an obsolete snapshot.
                    suppressed = existing and existing['note_id'] is None and self._suppressed(existing)
                    if suppressed:
                        version_id = existing['id']
                        counts['unchanged'] += 1
                    elif existing and existing["note_id"] is not None:
                        version_id = existing["id"]
                        if existing["auto_archived"]:
                            restored = self.db.fetchone("SELECT path FROM knowledge_notes WHERE id=?",
                                                       (existing["note_id"],))
                            self.app.knowledge_base.update(restored["path"], status="active")
                            self.db.execute("UPDATE knowledge_notes SET organizer_state=? WHERE id=?",
                                            ("done" if source["mode"] == "analyze" and source_articles.payload(summary) else "pending", existing["note_id"]))
                            self.db.execute("UPDATE local_source_versions SET auto_archived=0 WHERE note_id=?",
                                            (existing["note_id"],))
                        counts["linked"] += 1
                    else:
                        version_id = existing["id"] if existing else uuid4().hex
                        note_id, updated = self._write_note(unit, summary, source, old)
                        if existing:
                            self.db.execute("UPDATE local_source_versions SET note_id=? WHERE id=?",
                                            (note_id, version_id))
                        else:
                            self.db.execute(
                                "INSERT INTO local_source_versions(id,user_id,fingerprint,recipe,content,summary,note_id) "
                                "VALUES(?,?,?,?,?,?,?)",
                                (version_id, self.user_id, fingerprint, recipe, unit.content, summary, note_id))
                        counts["updated" if updated else "created"] += 1
                    self.db.execute(
                        "INSERT INTO local_source_items(id,source_id,external_id,version_id,files,project,seen_run) "
                        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id,external_id) DO UPDATE SET "
                        "version_id=excluded.version_id,files=excluded.files,project=excluded.project,"
                        "seen_run=excluded.seen_run,available=1",
                        (uuid4().hex, source_id, unit.external_id, version_id,
                         json.dumps(unit.files), unit.project, seen_run))
                    if old and old["note_id"] is not None:
                        current = self.db.fetchone("SELECT note_id FROM local_source_versions WHERE id=?",
                                                   (version_id,))
                        remaining = self.db.fetchone(
                            "SELECT count(*) AS n FROM local_source_items i "
                            "JOIN local_source_versions v ON v.id=i.version_id WHERE v.note_id=?",
                            (old["note_id"],))["n"]
                        if current["note_id"] != old["note_id"] and not remaining:
                            previous = self.db.fetchone("SELECT path,status FROM knowledge_notes WHERE id=?",
                                                        (old["note_id"],))
                            if previous and previous["status"] != "archived":
                                self.app.knowledge_base.update(previous["path"], status="archived")
                                self.app.knowledge_base.archive_note(previous["path"])
                                self.db.execute("UPDATE local_source_versions SET auto_archived=1 WHERE note_id=?",
                                                (old["note_id"],))
                    counts["articles"] += source_articles.materialize(self.app, source, version_id, old)
                    if source['kind'] == 'obsidian':
                        from .obsidian import persist_metadata
                        persist_metadata(self.db, source_id, unit)
            # Only a fully read source may mark an old original as unavailable.
            if complete_inventory:
                self.db.execute("UPDATE local_source_items SET available=0 WHERE source_id=? AND seen_run!=?",
                                (source_id, seen_run))
            else:
                for external_id in removed:
                    self.db.execute('UPDATE local_source_items SET available=0 WHERE source_id=? AND external_id=?',
                                    (source_id, external_id))
            if source['kind'] == 'obsidian':
                from .obsidian import materialize_links
                counts['relations'] += materialize_links(self.app, source_id)
            # Finish extraction for all units before comparing their articles.
            self.db.execute("UPDATE workflow_runs SET current_step=? WHERE id=?", (_t("sources.step_relations"), run_id))
            for version in self.db.fetchall("SELECT DISTINCT version_id FROM local_source_items WHERE source_id=? AND available=1", (source_id,)):
                counts["relations"] += source_articles.link_pending(self.app, version["version_id"], source["model"])
            self.db.execute("UPDATE local_sources SET status='ready',import_request_id=NULL,last_run=CURRENT_TIMESTAMP WHERE id=?",
                            (source_id,))
            recorder.finish(run_id, counts=counts)
            return {"source_id": source_id, "run_id": run_id, **counts}
        except Exception as error:
            from .source_analysis import SourceAnalysisError
            message = str(error) if isinstance(error, SourceAnalysisError) else _t("sources.import_failed")
            self.db.execute("UPDATE local_sources SET status='error',import_request_id=NULL,error=? WHERE id=?",
                            (message, source_id))
            if run_id:
                recorder.fail(run_id, CAUSES["source_failed"])
            raise

    def _version(self, fingerprint, recipe):
        # Collector deletions bind to content, even after model or mode changes.
        if self._has_collector_tombstones():
            deleted = self.db.fetchone(
                'SELECT v.* FROM local_source_versions v JOIN collector_version_tombstones t '
                'ON t.user_id=v.user_id AND t.fingerprint=v.fingerprint '
                'WHERE v.user_id=? AND v.fingerprint=? AND v.note_id IS NULL LIMIT 1',
                (self.user_id, fingerprint))
            if deleted:
                return deleted
        return self.db.fetchone("SELECT * FROM local_source_versions WHERE user_id=? AND fingerprint=? AND recipe=?",
                                (self.user_id, fingerprint, recipe))

    def _has_collector_tombstones(self):
        return bool(self.db.fetchone("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collector_version_tombstones'"))

    def _suppressed(self, version):
        if self._has_collector_tombstones():
            return bool(self.db.fetchone(
                'SELECT 1 FROM collector_version_tombstones WHERE user_id=? AND fingerprint=?',
                (self.user_id, version['fingerprint'])))
        return False

    def _write_note(self, unit, summary, source, old):
        kb = self.app.knowledge_base
        if source["mode"] == "analyze" and source_articles.payload(summary):
            return source_articles.write_overview(self.app, unit, summary, source, old)
        if old and old["note_id"] is not None:
            shared = self.db.fetchone(
                "SELECT count(*) AS n FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id "
                "WHERE v.note_id=? AND i.id!=?", (old["note_id"], old["id"]))["n"]
            note = self.db.fetchone("SELECT path,type FROM knowledge_notes WHERE id=?", (old["note_id"],))
            if not shared and note and note["type"] == "note" and kb.update(note["path"], content=summary):
                # Old snapshots must never resolve to the now updated summary.
                self.db.execute("UPDATE local_source_versions SET note_id=NULL WHERE note_id=?",
                                (old["note_id"],))
                return old["note_id"], True
        path = kb.write(unit.title, summary, tags=[unit.project] if unit.project else [],
                        created_by="source-import", source={"kind": source["kind"],
                        "source_id": source["id"], "external_id": unit.external_id})
        note_id = self.db.fetchone("SELECT id FROM knowledge_notes WHERE path=?", (path,))["id"]
        return note_id, False

    def articles(self, source_id: str) -> list[dict]:
        self.get(source_id)
        rows = self.db.fetchall(
            "SELECT DISTINCT n.id,n.path,n.title,n.parent_path,n.status,a.evidence,a.relation_state "
            "FROM local_source_items i JOIN source_articles a ON a.version_id=i.version_id "
            "JOIN knowledge_notes n ON n.id=a.note_id "
            "WHERE i.source_id=? AND i.available=1 AND n.status='active' "
            "UNION SELECT DISTINCT n.id,n.path,n.title,n.parent_path,n.status,'[]','original' "
            "FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id "
            "JOIN knowledge_notes n ON n.id=v.note_id "
            "WHERE i.source_id=? AND i.available=1 AND n.status='active' "
            "AND NOT EXISTS (SELECT 1 FROM source_articles a WHERE a.version_id=i.version_id) "
            "ORDER BY title LIMIT 200", (source_id, source_id))
        for row in rows:
            row["evidence"] = json.loads(row["evidence"])
            row["relations"] = self.app.knowledge_base._indexer.article_relations(row["path"])
            row["origins"] = self.db.fetchall(
                "SELECT i.external_id,m.metadata FROM local_source_items i "
                "JOIN local_source_versions v ON v.id=i.version_id "
                "LEFT JOIN local_source_metadata m ON m.item_id=i.id "
                "WHERE i.source_id=? AND i.available=1 AND (v.note_id=? OR EXISTS "
                "(SELECT 1 FROM source_articles a WHERE a.version_id=v.id AND a.note_id=?))",
                (source_id, row["id"], row["id"]))
            for origin in row["origins"]:
                origin["source_metadata"] = json.loads(origin.pop("metadata") or "{}")
        return rows

    def origins(self, note_id: int) -> list[dict]:
        return self.db.fetchall(
            "SELECT i.id AS item_id,i.external_id,i.available,i.files,i.project,s.id AS source_id,s.name,s.kind "
            "FROM local_source_items i JOIN local_sources s ON s.id=i.source_id "
            "JOIN local_source_versions v ON v.id=i.version_id WHERE s.user_id=? AND (v.note_id=? OR EXISTS "
            "(SELECT 1 FROM source_articles a WHERE a.version_id=v.id AND a.note_id=?))",
            (self.user_id, note_id, note_id))

    def evidence(self, item_id: str) -> dict:
        row = self.db.fetchone(
            "SELECT i.*,v.content,v.summary,v.fingerprint,s.mode FROM local_source_items i "
            "JOIN local_source_versions v ON v.id=i.version_id JOIN local_sources s ON s.id=i.source_id "
            "WHERE i.id=? AND s.user_id=?", (item_id, self.user_id))
        if not row:
            raise ValueError("Beleg nicht gefunden")
        row["files"] = json.loads(row["files"])
        metadata = self.db.fetchone('SELECT metadata FROM local_source_metadata WHERE item_id=?', (item_id,))
        if metadata:
            row['source_metadata'] = json.loads(metadata['metadata'])
        row["articles"] = self.db.fetchall(
            "SELECT a.note_id,a.evidence,n.path,n.title FROM source_articles a "
            "LEFT JOIN knowledge_notes n ON n.id=a.note_id WHERE a.version_id=?", (row["version_id"],))
        for article in row["articles"]:
            article["evidence"] = json.loads(article["evidence"])
        row["summary"] = source_articles.overview(row["summary"]) if row["mode"] == "analyze" else row["summary"]
        return row

    def search(self, query: str, limit: int = 10) -> list[dict]:
        results = []
        for hit in self.app.knowledge_base.search(query, limit=min(max(limit, 1), 50)):
            note = self.db.fetchone("SELECT id FROM knowledge_notes WHERE path=?", (hit["path"],))
            origins = self.origins(note["id"]) if note else []
            if origins:
                results.append({**hit, "origins": origins})
        return results
