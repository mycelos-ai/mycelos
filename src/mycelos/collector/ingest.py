"""Validated collector units enter the existing knowledge import service.

Registration never inspects a server path. The intake module owns durable
receipts; this module preserves per-unit transactions on receipt retries.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
from pathlib import Path, PurePosixPath
import re

from mycelos.knowledge.local_readers import SourceUnit
from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.knowledge.source_lock import source_lock
from mycelos.knowledge.source_workflows import resolve_workflow


_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}\Z')


def _ensure_tombstones(app):
    # A NULL note_id also denotes an obsolete snapshot after an in-place update.
    # Record actual deletions separately, before ON DELETE SET NULL removes proof.
    app.storage.execute('''CREATE TABLE IF NOT EXISTS collector_version_tombstones (
        user_id TEXT NOT NULL,fingerprint TEXT NOT NULL,recipe TEXT NOT NULL,
        PRIMARY KEY(user_id,fingerprint,recipe))''')
    app.storage.execute('''CREATE TRIGGER IF NOT EXISTS collector_note_deleted
        BEFORE DELETE ON knowledge_notes BEGIN
        INSERT OR IGNORE INTO collector_version_tombstones(user_id,fingerprint,recipe)
        SELECT v.user_id,v.fingerprint,v.recipe FROM local_source_versions v
        WHERE v.note_id=OLD.id AND (EXISTS (
            SELECT 1 FROM local_source_items i JOIN collector_sources c ON c.source_id=i.source_id
            WHERE i.version_id=v.id) OR EXISTS (
            SELECT 1 FROM collector_sources c WHERE c.source_id=json_extract(OLD.source,'$.source_id')));
        END''')


def _device(app, device):
    if not isinstance(device, dict) or not _ID.fullmatch(str(device.get('id', ''))):
        raise ValueError('Invalid collector identity')
    current = app.storage.fetchone('SELECT * FROM collector_devices WHERE id=?', (device['id'],))
    if not current or current['user_id'] != device.get('user_id') or current['status'] != 'active':
        raise ValueError('Collector is not approved')
    return current


def _validate(scan):
    from .intake import validate_scan
    if not isinstance(scan, dict):
        raise ValueError('Invalid collector scan')
    validate_scan(scan)
    if not _ID.fullmatch(scan['source_id']) or not _ID.fullmatch(scan['scan_id']):
        raise ValueError('Invalid collector source identity')
    for unit in scan['units']:
        metadata = unit['metadata']
        try:
            if len(json.dumps(metadata, ensure_ascii=False)) > 1_000_000:
                raise ValueError('Collector source metadata exceeds its limit')
        except (TypeError, RecursionError) as error:
            raise ValueError('Invalid collector source metadata') from error
        if scan['kind'] == 'obsidian' and metadata:
            _validate_obsidian(metadata)
    return resolve_workflow(scan['kind'], Path('.'), scan['workflow'])


def _validate_obsidian(metadata):
    links = metadata.get('links')
    if (not isinstance(links, list) or len(links) > 10000
            or not isinstance(metadata.get('properties', {}), dict)):
        raise ValueError('Invalid Obsidian metadata')
    for link in links:
        if (not isinstance(link, dict) or not isinstance(link.get('state'), str)
                or link['state'] not in {'resolved', 'attachment', 'excluded', 'external', 'outside', 'ambiguous', 'missing'}
                or not isinstance(link.get('reference'), str) or len(link['reference']) > 10000):
            raise ValueError('Invalid Obsidian link')
        target = link.get('target')
        if target is not None and (not isinstance(target, str) or len(target) > 2048):
            raise ValueError('Invalid Obsidian target')
        if link['state'] == 'resolved' and (not target or PurePosixPath(target).is_absolute()
                or '..' in PurePosixPath(target).parts or '\\' in target or ':' in target):
            raise ValueError('Invalid Obsidian target')


def _ownership(app, device, source_id):
    _device(app, device)
    row = app.storage.fetchone(
        'SELECT s.*,c.device_id,c.rule_version FROM local_sources s '
        'JOIN collector_sources c ON c.source_id=s.id WHERE s.id=?', (source_id,))
    if not row or row['device_id'] != device['id'] or row['user_id'] != device['user_id']:
        raise ValueError('Collector source ownership does not match')
    return row


def register_source(app, device: dict, scan: dict) -> dict:
    workflow = _validate(scan)
    with app.storage.transaction():
        current = _device(app, device)
        _ensure_tombstones(app)
        old = app.storage.fetchone('SELECT * FROM local_sources WHERE id=?', (scan['source_id'],))
        if old:
            owned = _ownership(app, current, scan['source_id'])
            if owned['kind'] != scan['kind'] or scan['rule_version'] < owned['rule_version']:
                raise ValueError('Collector source rules do not match')
            if scan['rule_version'] == owned['rule_version'] and json.loads(owned['workflow']) != workflow:
                raise ValueError('Collector source rules do not match')
            if scan['rule_version'] > owned['rule_version']:
                if owned['status'] in {'running','queued'}:
                    raise ValueError('Source is busy')
                app.storage.execute('UPDATE local_sources SET workflow=? WHERE id=?',
                                    (json.dumps(workflow,ensure_ascii=False),scan['source_id']))
                app.storage.execute('UPDATE collector_sources SET rule_version=? WHERE source_id=?',
                                    (scan['rule_version'],scan['source_id']))
                app.audit.log('collector.rules_updated',details={'source_id':scan['source_id'],'rule_version':scan['rule_version']})
        else:
            app.storage.execute(
                'INSERT INTO local_sources(id,user_id,name,kind,root,purpose,mode,model,status,workflow) '
                "VALUES(?,?,?,?,?,'','analyze',NULL,'paused',?)",
                (scan['source_id'], current['user_id'], scan['name'], scan['kind'],
                 'collector://' + current['id'] + '/' + scan['source_id'], json.dumps(workflow, ensure_ascii=False)))
            app.storage.execute('INSERT INTO collector_sources(source_id,device_id,rule_version) VALUES(?,?,?)',
                                (scan['source_id'], current['id'], scan['rule_version']))
        app.storage.execute('''CREATE TABLE IF NOT EXISTS collector_rule_history (
            source_id TEXT NOT NULL,revision INTEGER NOT NULL,workflow_hash TEXT NOT NULL,workflow TEXT NOT NULL,
            PRIMARY KEY(source_id,revision))''')
        encoded=json.dumps(workflow,sort_keys=True,ensure_ascii=False)
        app.storage.execute('INSERT OR IGNORE INTO collector_rule_history VALUES(?,?,?,?)',
                            (scan['source_id'],scan['rule_version'],hashlib.sha256(encoded.encode()).hexdigest(),encoded))
    return LocalSourceService(app, current['user_id']).get(scan['source_id'])


def adopt_source(app, user_id: str, device: dict, scan: dict, *, local_grant: bool = False) -> dict:
    """Management-only migration after an explicit local source grant.

    Never invoke this from a scan transport message. Its caller must obtain
    user approval on the server and confirmation of the local client grant.
    """
    workflow = _validate(scan)
    if local_grant is not True or device.get('user_id') != user_id:
        raise ValueError('Source adoption needs its owner and an explicit local grant')
    with source_lock(app.data_dir, scan['source_id']), app.storage.transaction():
        current = _device(app, device)
        _ensure_tombstones(app)
        service = LocalSourceService(app, user_id)
        source = service.get(scan['source_id'])
        if app.storage.fetchone('SELECT 1 FROM collector_sources WHERE source_id=?', (source['id'],)):
            raise ValueError('The source already belongs to a collector')
        if source['status'] in {'running', 'queued'}:
            raise ValueError('The source is busy')
        if source['kind'] != scan['kind'] or source['workflow'] != workflow:
            raise ValueError('Collector source rules do not match')
        # Preserve deletion evidence already present at the migration boundary.
        app.storage.execute('INSERT OR IGNORE INTO collector_version_tombstones(user_id,fingerprint,recipe) '
                            'SELECT v.user_id,v.fingerprint,v.recipe FROM local_source_versions v '
                            'JOIN local_source_items i ON i.version_id=v.id WHERE i.source_id=? AND v.note_id IS NULL',
                            (source['id'],))
        app.storage.execute('UPDATE local_sources SET root=? WHERE id=? AND user_id=?',
                            ('collector://' + current['id'] + '/' + source['id'], source['id'], user_id))
        app.storage.execute('INSERT INTO collector_sources(source_id,device_id,rule_version) VALUES(?,?,?)',
                            (source['id'], current['id'], scan['rule_version']))
    return service.get(source['id'])


class _GuardedObject:
    """Per-import facade; never replaces the shared application's services."""
    def __init__(self, target, check, methods):
        self._target, self._check, self._methods = target, check, methods

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if name not in self._methods:
            return value
        def checked(*args, **kwargs):
            self._check()
            result = value(*args, **kwargs)
            self._check()
            return result
        return checked


class _GuardedStorage(_GuardedObject):
    def __init__(self, target, check):
        super().__init__(target, check, ())

    def execute(self, sql, params=()):
        # Run bookkeeping must still record an import stopped by revocation.
        operational = re.match(r'\s*(?:UPDATE|INSERT INTO)\s+(?:local_sources|workflow_runs)\b', sql, re.I)
        if operational:
            return self._target.execute(sql, params)
        with self._target.transaction():
            self._check()
            result = self._target.execute(sql, params)
            self._check()
            return result

    @contextmanager
    def transaction(self):
        with self._target.transaction():
            self._check()
            yield
            self._check()


class _GuardedApp:
    def __init__(self, app, check):
        self._app, self._check = app, check
        self.storage = _GuardedStorage(app.storage, check)

    @property
    def knowledge_base(self):
        self._check()
        return _GuardedObject(self._app.knowledge_base, self._check, {'write', 'update', 'archive_note'})

    @property
    def llm(self):
        return _GuardedObject(self._app.llm, self._check, {'complete'})

    def __getattr__(self, name):
        return getattr(self._app, name)


def ingest_scan(app, device: dict, scan: dict) -> dict:
    source = register_source(app, device, scan)
    def approved():
        row = _ownership(app, device, source['id'])
        if row['status'] == 'paused' or (row['mode'] == 'analyze' and not row['model']):
            raise ValueError('Collector source needs server configuration')
    approved()
    service = LocalSourceService(_GuardedApp(app, approved), device['user_id'])
    units = (SourceUnit(item['external_id'], item['title'], item['content'], tuple(item['files']),
                        item['project'], item['metadata']) for item in scan['units'])
    return service.ingest(source['id'], units=units, complete_inventory=False,
                          removed=scan['removed'], check=approved)
