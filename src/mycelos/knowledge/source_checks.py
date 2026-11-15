"""Persist complete local scans without knowledge, model or embedding access."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from tempfile import TemporaryDirectory
from uuid import uuid4
from pathlib import Path

from mycelos.knowledge.local_readers import read_units
from mycelos.knowledge.local_sources import LocalSourceService, source_recipe
from mycelos.knowledge.source_lock import source_lock
from mycelos.i18n import t as _t


INTERVALS = {0, 15, 60, 360, 1440}


def _utc(minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class SourceCheckService:
    def __init__(self, app, user_id='default'):
        self.app = app
        self.db = app.storage
        self.user_id = user_id
        self.sources = LocalSourceService(app, user_id)

    def _ensure(self, source_id):
        source = self.sources.get(source_id)
        self.db.execute('INSERT OR IGNORE INTO local_source_checks(source_id) VALUES(?)', (source_id,))
        return source

    def _state(self, source_id):
        return self.db.fetchone('SELECT * FROM local_source_checks WHERE source_id=?', (source_id,))

    def _pending(self, source):
        # Resolve saved scans and imports against the current recipe. A recipe
        # change needs no new scan, filesystem read, or model request.
        recipe = source_recipe(source)
        return self.db.fetchall(
            "SELECT c.external_id,c.title,CASE WHEN c.change='removed' THEN 'removed' "
            "WHEN i.available=1 AND v.fingerprint=c.fingerprint AND v.recipe!=? THEN 'reprocess' "
            "WHEN i.id IS NOT NULL THEN 'changed' ELSE c.change END AS change,"
            "c.fingerprint,c.files,c.project FROM local_source_check_items c "
            "LEFT JOIN local_source_items i ON i.source_id=c.source_id AND i.external_id=c.external_id "
            "LEFT JOIN local_source_versions v ON v.id=i.version_id "
            "WHERE c.source_id=? AND ((c.change='removed' AND (i.id IS NULL OR i.available=1)) "
            "OR (c.change!='removed' AND (i.id IS NULL OR i.available=0 OR v.fingerprint!=c.fingerprint OR v.recipe!=?))) "
            "UNION ALL "
            "SELECT i.external_id,COALESCE(n.title,i.external_id),'reprocess',v.fingerprint,i.files,i.project "
            "FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id "
            "LEFT JOIN knowledge_notes n ON n.id=v.note_id "
            "WHERE i.source_id=? AND i.available=1 AND v.recipe!=? AND NOT EXISTS "
            "(SELECT 1 FROM local_source_check_items c WHERE c.source_id=i.source_id AND c.external_id=i.external_id) "
            "ORDER BY external_id", (recipe, source['id'], recipe, source['id'], recipe))

    def status(self, source_id):
        source = self._ensure(source_id)
        state = self._state(source_id)
        state.pop('lease_until')
        pending = self._pending(source)
        state['pending_count'] = len(pending)
        state['removed_count'] = sum(row['change'] == 'removed' for row in pending)
        state['reprocess_count'] = sum(row['change'] == 'reprocess' for row in pending)
        state['relations_pending_count'] = self.db.fetchone(
            "SELECT count(DISTINCT a.note_id) AS count FROM local_source_items i "
            "JOIN source_articles a ON a.version_id=i.version_id "
            "JOIN knowledge_notes n ON n.id=a.note_id "
            "WHERE i.source_id=? AND i.available=1 AND n.status='active' AND a.relation_state='pending'",
            (source_id,))['count']
        return state

    def pending(self, source_id):
        source = self._ensure(source_id)
        rows = self._pending(source)[:200]
        for row in rows:
            row['files'] = json.loads(row['files'])
        return rows

    def configure(self, source_id, interval_minutes):
        self.sources.require_local(self._ensure(source_id))
        if type(interval_minutes) is not int or interval_minutes not in INTERVALS:
            raise ValueError(_t("sources.invalid_interval"))
        self.db.execute('UPDATE local_source_checks SET interval_minutes=?,next_check=? WHERE source_id=?',
                        (interval_minutes, _utc(interval_minutes) if interval_minutes else None, source_id))
        return self.status(source_id)

    def claim(self, source_id, due_only=False):
        self.sources.require_local(self._ensure(source_id))
        try:
            with source_lock(self.app.data_dir, source_id):
                if self.sources.get(source_id)['status'] in {'paused', 'running', 'queued'}:
                    return False
                with self.db.transaction():
                    state = self._state(source_id)
                    if state['status'] in {'queued', 'running'}:
                        return False
                    if due_only and (not state['interval_minutes'] or not state['next_check']
                                     or datetime.fromisoformat(state['next_check'].replace('Z', '+00:00')) > datetime.now(timezone.utc)):
                        return False
                    self.db.execute("UPDATE local_source_checks SET status='queued',error=NULL,request_id=?,lease_until=?,next_check=? WHERE source_id=?",
                                    (uuid4().hex, _utc(15), _utc(state['interval_minutes']) if state['interval_minutes'] else None, source_id))
                    return True
        except ValueError:
            return False

    def _failure(self, source_id, message):
        state = self._state(source_id)
        self.db.execute("UPDATE local_source_checks SET status='error',error=?,failure_count=failure_count+1,request_id=NULL,lease_until=NULL,next_check=? WHERE source_id=?",
                        (message, _utc(state['interval_minutes']) if state['interval_minutes'] else None, source_id))

    def fail_enqueue(self, source_id):
        self._ensure(source_id)
        self._failure(source_id, _t("sources.check_enqueue_failed"))
        return self.status(source_id)

    def due(self):
        return self.db.fetchall(
            "SELECT s.id AS source_id,s.user_id FROM local_sources s JOIN local_source_checks c ON c.source_id=s.id "
            "WHERE s.user_id=? AND s.root NOT LIKE 'collector://%' AND s.status NOT IN ('paused','running','queued') "
            "AND c.status IN ('idle','error') AND c.interval_minutes>0 "
            "AND julianday(c.next_check)<=julianday(?) ORDER BY s.id", (self.user_id, _utc()))

    def recover(self):
        """Release abandoned scans; keep queued jobs until their 15-minute lease expires.

        Call at queue startup and before scheduled due scans. Only ownership of
        the OS lock proves a running worker has stopped. A queued job may have
        survived in Huey; its old request ID cannot start another scan.
        """
        rows = self.db.fetchall(
            "SELECT c.source_id FROM local_source_checks c JOIN local_sources s ON s.id=c.source_id "
            "WHERE s.user_id=? AND (c.status='running' OR (c.status='queued' "
            "AND julianday(c.lease_until)<=julianday(?)))", (self.user_id, _utc()))
        for row in rows:
            sid = row['source_id']
            try:
                with source_lock(self.app.data_dir, sid):
                    state = self._state(sid)
                    if state['status'] == 'running' or (state['status'] == 'queued' and state['lease_until'] and
                            datetime.fromisoformat(state['lease_until'].replace('Z', '+00:00')) <= datetime.now(timezone.utc)):
                        self._failure(sid, _t("sources.check_interrupted"))
            except ValueError:
                continue

    def check(self, source_id, request_id=None):
        self.sources.require_local(self._ensure(source_id))
        with source_lock(self.app.data_dir, source_id):
            state = self._state(source_id)
            if request_id is not None and (state['status'] != 'queued' or state['request_id'] != request_id):
                return self.status(source_id)
            source = self.sources.get(source_id)
            if source['status'] in {'paused', 'running', 'queued'}:
                raise ValueError(_t("sources.paused_or_running"))
            self.db.execute("UPDATE local_source_checks SET status='running',error=NULL WHERE source_id=?", (source_id,))
            try:
                self._scan(source)
            except Exception:
                self._failure(source_id, _t("sources.check_failed"))
        return self.status(source_id)

    def _scan(self, source):
        self.sources.require_local(source)
        sid = source['id']
        previous = {r['external_id']: r for r in self.db.fetchall(
            'SELECT external_id,fingerprint,change FROM local_source_check_items WHERE source_id=?', (sid,))}
        imported = {r['external_id']: r for r in self.db.fetchall(
            'SELECT i.external_id,i.available,v.fingerprint FROM local_source_items i '
            'JOIN local_source_versions v ON v.id=i.version_id WHERE i.source_id=?', (sid,))}
        root = Path(source['root'])
        missing_file = source['kind'] == 'file' and not root.exists() and root.parent.is_dir()
        if root.is_symlink() or (not root.exists() and not missing_file):
            raise ValueError(_t("sources.source_unavailable"))
        units = () if missing_file else read_units(source['kind'], root, source['workflow'])
        # A disk-backed stage bounds content memory to one reader unit. The live
        # inventory stays untouched until every source file has parsed.
        with TemporaryDirectory(prefix='mycelos-source-check-') as directory:
            stage = sqlite3.connect(str(Path(directory) / 'stage.db'))
            try:
                stage.execute('CREATE TABLE items(external_id TEXT PRIMARY KEY,title TEXT,fingerprint TEXT,content TEXT,files TEXT,project TEXT,change TEXT)')
                checked = 0
                for unit in units:
                    fingerprint = hashlib.sha256(unit.content.encode('utf-8')).hexdigest()
                    old = previous.get(unit.external_id)
                    baseline = imported.get(unit.external_id)
                    change = 'new'
                    if baseline:
                        change = 'unchanged' if baseline['fingerprint'] == fingerprint and baseline['available'] else 'changed'
                    elif old:
                        change = old['change'] if old['fingerprint'] == fingerprint and old['change'] != 'removed' else 'changed'
                    try:
                        stage.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?)',
                            (unit.external_id, unit.title, fingerprint, unit.content,
                             json.dumps(unit.files), unit.project, change))
                    except sqlite3.IntegrityError as error:
                        raise ValueError(_t("sources.duplicate_session")) from error
                    checked += 1
                stage.commit()
                self._publish(sid, stage, checked)
            finally:
                stage.close()

    def _publish(self, sid, stage, checked):
        # Only a complete parse may replace the inventory. Retain old contents
        # directly in SQLite instead of copying removed versions through memory.
        with self.db.transaction():
            self.db.execute("UPDATE local_source_check_items SET change='removed' WHERE source_id=?", (sid,))
            self.db.execute(
                "INSERT OR IGNORE INTO local_source_check_items(source_id,external_id,title,fingerprint,content,files,project,change) "
                "SELECT i.source_id,i.external_id,i.external_id,v.fingerprint,v.content,i.files,i.project,'removed' "
                "FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id WHERE i.source_id=?", (sid,))
            for row in stage.execute('SELECT external_id,title,fingerprint,content,files,project,change FROM items'):
                self.db.execute(
                    'INSERT INTO local_source_check_items(source_id,external_id,title,fingerprint,content,files,project,change) '
                    'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id,external_id) DO UPDATE SET '
                    'title=excluded.title,fingerprint=excluded.fingerprint,content=excluded.content,'
                    'files=excluded.files,project=excluded.project,change=excluded.change', (sid, *row))
            interval = self._state(sid)['interval_minutes']
            self.db.execute("UPDATE local_source_checks SET status='idle',last_check=?,next_check=?,request_id=NULL,lease_until=NULL,error=NULL,failure_count=0,checked_units=? WHERE source_id=?",
                            (_utc(), _utc(interval) if interval else None, checked, sid))
