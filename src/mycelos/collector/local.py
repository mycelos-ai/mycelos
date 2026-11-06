"""Local grants and bounded adapters. No network operations occur here.

The existing readers see only private snapshots. Source reads use directory
file descriptors, refuse links, and check object identity before and after a
read. A grant never follows a replacement root. SQLite holds local grants,
content-free checkpoints/audit, and an expiring, size-limited delivery outbox.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from . import history as journal

from mycelos.knowledge.source_workflows import EXCLUDED, KINDS, _match, resolve_workflow

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_OUTBOX_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_UNITS = 2_000
MAX_DEPTH = 64
MAX_SCAN_SECONDS = 30
OUTBOX_TTL_SECONDS = 24 * 60 * 60
MAX_AUDIT_ROWS = 10_000
_MEDIA = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.mp3', '.wav',
          '.m4a', '.mp4', '.webm', '.ogg', '.mov', '.canvas', '.base'}
READER_SCRIPT = Path(__file__).with_name('local_reader.py')
_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _identity(info):
    return info.st_dev, info.st_ino


def _version(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def _absolute_fd(path, directory=False):
    """Open every absolute path component without resolving symbolic links."""
    path = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    fd = os.open('/', _FLAGS | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            new_fd = os.open(part, _FLAGS | (os.O_DIRECTORY if directory or not last else 0), dir_fd=fd)
            os.close(fd)
            fd = new_fd
        yield fd
    finally:
        os.close(fd)


def _clean(value):
    """Remove absolute local provenance from untrusted metadata, not content."""
    if isinstance(value, str):
        # Metadata can contain paths supplied by front matter or session IDs.
        return re.sub(r'(?<!\S)(?:[A-Za-z]:[\\/]|/)[^\s<>\"\']+',
                      lambda m: re.split(r'[\\/]', m.group(0).rstrip('/\\'))[-1], value)
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {_clean(str(key)): _clean(item) for key, item in value.items()}
    return value


class LocalCollector:
    def __init__(self, state_dir):
        self.state_dir = Path(os.path.abspath(os.path.expanduser(os.fspath(state_dir))))
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _absolute_fd(self.state_dir, directory=True) as fd:
            info = os.fstat(fd)
            if info.st_uid != os.getuid():
                raise PermissionError('The collector state has another owner')
            os.fchmod(fd, 0o700)
            self._state_identity = _identity(info)
        self.db_path = self.state_dir / 'collector.sqlite3'
        with _absolute_fd(self.state_dir, directory=True) as fd:
            try:
                dbfd = os.open(self.db_path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                               0o600, dir_fd=fd)
            except FileExistsError:
                dbfd = os.open(self.db_path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            try:
                if not stat.S_ISREG(os.fstat(dbfd).st_mode) or os.fstat(dbfd).st_nlink != 1:
                    raise PermissionError('Invalid collector state file')
                os.fchmod(dbfd, 0o600)
            finally:
                os.close(dbfd)
        # Existing profiles need no write lock just to answer a control command.
        with sqlite3.connect(self.db_path) as probe:
            columns = {row[1] for row in probe.execute('PRAGMA table_info(sources)')}
            ready = ('last_synced' in columns and 'received_at' in {
                row[1] for row in probe.execute('PRAGMA table_info(outbox)')} and probe.execute(
                "SELECT 1 FROM sqlite_master WHERE name='transfer_history'").fetchone())
        if ready:
            try:
                with self._db(timeout=0) as db:
                    self._cleanup(db)
                    self._remove_snapshots()
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error):
                    raise
            return
        with self._db() as db:
            schema = '''
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    root TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
                    workflow TEXT NOT NULL, rule_version INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    source_id TEXT NOT NULL, external_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    files TEXT NOT NULL, details_fingerprint TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(source_id,external_id));
                CREATE TABLE IF NOT EXISTS unconfirmed_items (
                    source_id TEXT NOT NULL, external_id TEXT NOT NULL,
                    PRIMARY KEY(source_id,external_id));
                CREATE TABLE IF NOT EXISTS outbox (
                    scan_id TEXT PRIMARY KEY, source_id TEXT UNIQUE NOT NULL,
                    expires REAL NOT NULL, payload TEXT NOT NULL, inventory TEXT NOT NULL,
                    size INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, operation TEXT NOT NULL,
                    source_id TEXT, status TEXT NOT NULL);
            '''
            for statement in schema.split(';'):
                if statement.strip():
                    db.execute(statement)
            if 'rule_version' not in {row['name'] for row in db.execute('PRAGMA table_info(sources)')}:
                db.execute('ALTER TABLE sources ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 1')
            if 'details_fingerprint' not in {row['name'] for row in db.execute('PRAGMA table_info(checkpoints)')}:
                db.execute("ALTER TABLE checkpoints ADD COLUMN details_fingerprint TEXT NOT NULL DEFAULT ''")
            if 'last_synced' not in {row['name'] for row in db.execute('PRAGMA table_info(sources)')}:
                db.execute('ALTER TABLE sources ADD COLUMN last_synced REAL')
            if 'received_at' not in {row['name'] for row in db.execute('PRAGMA table_info(outbox)')}:
                db.execute('ALTER TABLE outbox ADD COLUMN received_at REAL')
            for statement in journal.SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            for pending in db.execute('SELECT payload FROM outbox').fetchall():
                scan = json.loads(pending['payload'])
                journal.record(db, scan, scan['name'])
            self._cleanup(db)
        self._cleanup_snapshots()

    @contextmanager
    def _db(self, operation=None, *, readonly=False, timeout=35):
        with _absolute_fd(self.state_dir, directory=True) as fd:
            if _identity(os.fstat(fd)) != self._state_identity:
                raise PermissionError('Collector state changed')
            info = os.stat(self.db_path.name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError('Invalid collector state file')
        db = sqlite3.connect(self.db_path, timeout=timeout)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA secure_delete=ON')
        try:
            # Serialize scans and acknowledgements across processes as well as threads.
            db.execute('BEGIN' if readonly else 'BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException as error:
            db.rollback()
            if operation:
                self._audit(db, operation, status='denied' if isinstance(error, PermissionError) else 'failed')
                db.commit()
            raise
        finally:
            db.close()

    def _audit(self, db, operation, source_id=None, status='ok'):
        db.execute('INSERT INTO audit(at,operation,source_id,status) VALUES(?,?,?,?)',
                   (time.time(), operation, source_id, status))
        db.execute('DELETE FROM audit WHERE id <= (SELECT MAX(id)-? FROM audit)', (MAX_AUDIT_ROWS,))

    @staticmethod
    def _remember_pending_ids(db, rows):
        # Delivery may finish before its acknowledgement reaches this device.
        # Keep only IDs, never treat an uncertain delivery as a content checkpoint.
        for row in rows:
            db.executemany('INSERT OR IGNORE INTO unconfirmed_items VALUES(?,?)',
                           ((row['source_id'], external_id) for external_id in json.loads(row['inventory'])))

    def _cleanup(self, db):
        now = time.time()
        expired = db.execute('SELECT scan_id,source_id,inventory FROM outbox WHERE expires<=?', (now,)).fetchall()
        self._remember_pending_ids(db, expired)
        for pending in expired:
            journal.update(db, pending['scan_id'], 'failed', 'Delivery expired')
        count = db.execute('DELETE FROM outbox WHERE expires<=?', (now,)).rowcount
        if count:
            self._audit(db, 'outbox_expired')

    def _cleanup_snapshots(self):
        # Active scans hold the database write lock. Startup cleanup follows it.
        with self._db():
            self._remove_snapshots()

    def _remove_snapshots(self):
        for path in self.state_dir.glob('snapshot-*'):
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)

    def _active(self, db):
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        if (self.state_dir/'pause-request').exists() or row and row['value'] == '1':
            raise PermissionError('The collector is paused')

    @staticmethod
    def _source(db, source_id):
        row = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if row is None:
            raise KeyError('Unknown source')
        return dict(row)

    def _public(self, source):
        return dict(id=source['id'], source_id=source['id'], name=source['name'], kind=source['kind'],
                    root=source['root'], workflow=json.loads(source['workflow']), rule_version=source['rule_version'],
                    last_synced=source.get('last_synced'),
                    sync_requested=(self.state_dir / ('sync-'+source['id'])).exists())

    @contextmanager
    def _root(self, source):
        with _absolute_fd(source['root'], directory=source['kind'] != 'file') as fd:
            if _identity(os.fstat(fd)) != (source['device'], source['inode']):
                raise PermissionError('The granted root changed; select the source again')
            yield fd

    def add_source(self, name, kind, root, workflow=None, source_id=None):
        if kind not in KINDS or not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('Invalid source name or kind')
        if source_id is None:
            source_id = str(uuid.uuid4())
        if not isinstance(source_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', source_id):
            raise ValueError('Invalid source ID')
        root = Path(os.path.abspath(os.path.expanduser(os.fspath(root))))
        recipe = resolve_workflow(kind, root, workflow)
        with _absolute_fd(root, directory=kind != 'file') as fd:
            info = os.fstat(fd)
            if kind == 'file' and not stat.S_ISREG(info.st_mode):
                raise ValueError('The source must be a regular file')
            if kind in {'git', 'mercurial'}:
                marker = '.git' if kind == 'git' else '.hg'
                marker_info = os.stat(marker, dir_fd=fd, follow_symlinks=False)
                if not (stat.S_ISDIR(marker_info.st_mode) or stat.S_ISREG(marker_info.st_mode)):
                    raise ValueError('Invalid repository marker')
        with self._db() as db:
            db.execute('INSERT INTO sources(id,name,kind,root,device,inode,workflow) VALUES(?,?,?,?,?,?,?)',
                       (source_id, name.strip(), kind, str(root), info.st_dev, info.st_ino, json.dumps(recipe)))
            self._audit(db, 'grant', source_id)
            return self._public(self._source(db, source_id))

    def update_rules(self, source_id, workflow):
        """Change local selection rules without changing the granted object."""
        with self._db('update_rules') as db:
            source = self._source(db, source_id)
            if not isinstance(workflow, dict):
                raise ValueError('Source rules require an object')
            recipe = resolve_workflow(source['kind'], Path(source['root']), workflow)
            with self._root(source):
                pass
            if recipe != json.loads(source['workflow']):
                db.execute('UPDATE sources SET workflow=?,rule_version=rule_version+1 WHERE id=?',
                           (json.dumps(recipe), source_id))
                # Preserve possible remote IDs before invalidating the payload.
                # Keep confirmed checkpoints so unchanged units stay suppressed.
                self._remember_pending_ids(db, db.execute(
                    'SELECT source_id,inventory FROM outbox WHERE source_id=?', (source_id,)).fetchall())
                journal.fail_source(db, 'Source rules changed', source_id)
                db.execute('DELETE FROM outbox WHERE source_id=?', (source_id,))
            self._audit(db, 'update_rules', source_id)
            return self._public(self._source(db, source_id))

    def remove_source(self, source_id):
        """Remove only collector state; never modify the original source."""
        with self._db('remove_source') as db:
            self._source(db, source_id)
            (self.state_dir/('sync-'+source_id)).unlink(missing_ok=True)
            journal.fail_source(db, 'Source removed', source_id)
            db.execute('DELETE FROM outbox WHERE source_id=?', (source_id,))
            db.execute('DELETE FROM checkpoints WHERE source_id=?', (source_id,))
            db.execute('DELETE FROM unconfirmed_items WHERE source_id=?', (source_id,))
            db.execute('DELETE FROM sources WHERE id=?', (source_id,))
            self._audit(db, 'remove_source', source_id)

    def list_sources(self):
        with self._db(readonly=True) as db:
            return [self._public(dict(row)) for row in db.execute('SELECT * FROM sources ORDER BY rowid')]

    def pause(self, paused):
        if type(paused) is not bool:
            raise ValueError('Pause requires a boolean')
        if paused:
            (self.state_dir/'pause-request').touch(mode=0o600)
        with self._db() as db:
            db.execute("INSERT INTO settings VALUES('paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       ('1' if paused else '0',))
            self._audit(db, 'pause' if paused else 'resume')
        if not paused:
            (self.state_dir/'pause-request').unlink(missing_ok=True)

    def status(self):
        with self._db(readonly=True) as db:
            row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
            return dict(paused=bool((self.state_dir/'pause-request').exists() or row and row['value'] == '1'),
                        sources=db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
                        pending_scans=db.execute('SELECT COUNT(*) FROM outbox WHERE expires>?', (time.time(),)).fetchone()[0])

    def history(self, source_id=None, before=None, limit=100):
        with self._db(readonly=True) as db:
            return journal.page(db, source_id, before, limit)

    def pending_delivery(self, source_id):
        with self._db(readonly=True) as db:
            row = db.execute("SELECT scan_id,received_at,json_extract(payload,'$.complete') AS complete FROM outbox WHERE source_id=?", (source_id,)).fetchone()
            return dict(row) if row else None

    def request_sync(self, source_id):
        # Atomic replacement is independent of the scan's write transaction.
        from .client import save_json
        with self._db(readonly=True) as db:
            self._source(db, source_id)
            save_json(self.state_dir/('sync-'+source_id), {'request': uuid.uuid4().hex})

    def claim_sync(self, source_id):
        path = self.state_dir/('sync-'+source_id)
        claimed = self.state_dir/('claimed-'+uuid.uuid4().hex)
        try:
            os.replace(path, claimed)
        except FileNotFoundError:
            return None
        try:
            return json.loads(claimed.read_text())['request']
        finally:
            claimed.unlink(missing_ok=True)

    def sync_requests(self):
        requests = {}
        for path in self.state_dir.glob('sync-*'):
            try:
                info = path.stat()
                requests[path.name] = (info.st_ino, info.st_mtime_ns)
            except FileNotFoundError:
                continue
        return requests

    def record_original(self, job_id, result):
        with self._db() as db:
            source = self._source(db, result['source_id'])
            journal.record(db, dict(scan_id='original:'+job_id, source_id=source['id'],
                                    units=[{'files': result['files']}]), _clean(source['name']))

    def mark_received(self, scan_id):
        with self._db() as db:
            self._received(db, scan_id)

    def _received(self, db, scan_id):
        journal.update(db, scan_id, 'transferred')
        pending = db.execute('SELECT * FROM outbox WHERE scan_id=?', (scan_id,)).fetchone()
        if pending is not None and pending['received_at'] is None:
            at = time.time()
            db.execute('UPDATE outbox SET received_at=? WHERE scan_id=?', (at, scan_id))
            if json.loads(pending['payload'])['complete']:
                db.execute('UPDATE sources SET last_synced=MAX(COALESCE(last_synced,0),?) WHERE id=?',
                           (at, pending['source_id']))

    def fail_scan(self, scan_id):
        with self._db() as db:
            journal.update(db, scan_id, 'failed', 'Transfer failed')

    def clear_connection_state(self):
        with self._db() as db:
            journal.fail_source(db, 'Server disconnected')
            for table in ('outbox', 'checkpoints', 'unconfirmed_items', 'sources'):
                db.execute('DELETE FROM '+table)
            self._audit(db, 'disconnect')
        for path in self.state_dir.glob('sync-*'):
            path.unlink(missing_ok=True)

    def discover(self, home=None):
        """Check three known directory names. This neither reads content nor grants access."""
        home = Path.home() if home is None else Path(home)
        result = []
        for kind, relative, name in (
            ('codex', '.codex/sessions', 'Codex'),
            ('codex', '.codex/archived_sessions', 'Codex archive'),
            ('claude', '.claude/projects', 'Claude Code'),
        ):
            root = home / relative
            try:
                with _absolute_fd(root, directory=True):
                    result.append(dict(name=name, kind=kind, root=str(root)))
            except OSError:
                continue
        return result

    def _snapshot_units(self, source):
        """Reuse readers through a private snapshot; never give them the granted path."""
        kind, root = source['kind'], Path(source['root'])
        workflow = json.loads(source['workflow'])
        start = time.monotonic()
        counts = {'bytes': 0, 'entries': 0}
        complete = True
        def check():
            if time.monotonic() - start > MAX_SCAN_SECONDS:
                raise ValueError('Scan time limit exceeded')
        excluded = ({'.git', '.hg', '.obsidian', '.trash'} if kind == 'obsidian' else EXCLUDED)
        if kind in {'claude', 'codex'}:
            excluded = excluded | {'subagents'}
        extensions = {'.jsonl'} if kind in {'claude', 'codex'} else {'.md', '.txt', '.pdf'}

        def selected(relative):
            return (any(_match(relative, rule) for rule in workflow['include'])
                    and not any(_match(relative, rule) for rule in workflow['exclude']))

        def copy_file(fd, info, target, relative, parent=None, name=None):
            check()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ValueError('File size or type limit exceeded')
            if info.st_nlink != 1:
                raise PermissionError('Hard links are not permitted')
            if counts['bytes'] + info.st_size > MAX_SCAN_BYTES:
                raise ValueError('Scan byte limit exceeded')
            data = bytearray()
            while True:
                check()
                chunk = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_FILE_BYTES or counts['bytes'] + len(data) > MAX_SCAN_BYTES:
                    raise ValueError('Scan byte limit exceeded')
            if _version(os.fstat(fd)) != _version(info):
                raise PermissionError('The file changed during the read')
            if parent is not None and _version(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _version(info):
                raise PermissionError('The file changed during the read')
            counts['bytes'] += len(data)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        def walk(fd, target, relative='', depth=0, project=None):
            nonlocal complete
            check()
            if depth > MAX_DEPTH:
                raise ValueError('Directory depth limit exceeded')
            before = os.fstat(fd)
            with os.scandir(fd) as stream:
                names = []
                for entry in stream:
                    check()
                    counts['entries'] += 1
                    if counts['entries'] > MAX_ENTRIES:
                        raise ValueError('Directory entry limit exceeded')
                    names.append(entry.name)
            if kind in {'folder', 'git', 'mercurial'}:
                for marker in {'.git', '.hg'} & set(names):
                    marker_info = os.stat(marker, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISREG(marker_info.st_mode) or stat.S_ISDIR(marker_info.st_mode):
                        project = relative
                        break
            for name in sorted(names):
                check()
                rel = f'{relative}/{name}' if relative else name
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        complete = False
                        continue
                    if name in {'.git', '.hg'} and kind in {'folder', 'git', 'mercurial'}:
                        if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
                            (target / name).mkdir(exist_ok=True)
                        continue
                    if name in excluded or any(_match(rel, rule) for rule in workflow['exclude']):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(name, _FLAGS | os.O_DIRECTORY, dir_fd=fd)
                        try:
                            if _identity(os.fstat(child)) != _identity(info):
                                raise PermissionError('The directory changed')
                            (target / name).mkdir(exist_ok=True)
                            walk(child, target / name, rel, depth + 1, project)
                            if _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != _identity(info):
                                raise PermissionError('The directory changed')
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        suffix = Path(name).suffix.lower()
                        if kind == 'obsidian' and suffix in _MEDIA:
                            # The reader needs attachment names, never attachment bytes.
                            (target / name).touch()
                        elif suffix in extensions and selected(rel):
                            if project is not None and workflow['project_documents_only']:
                                project_rel = rel[len(project) + 1:] if project else rel
                                if not (name.lower().startswith(('readme', 'architecture', 'specification'))
                                        or project_rel.split('/')[0].lower() in {'docs', 'specs', 'specifications'}):
                                    continue
                            child = os.open(name, _FLAGS, dir_fd=fd)
                            try:
                                if _version(os.fstat(child)) != _version(info):
                                    raise PermissionError('The file changed')
                                copy_file(child, info, target / name, rel, fd, name)
                            finally:
                                os.close(child)
                except (OSError, ValueError):
                    complete = False
                    rejected = target / name
                    if rejected.is_dir():
                        shutil.rmtree(rejected)
                    elif rejected.exists():
                        rejected.unlink()
            if _version(os.fstat(fd)) != _version(before):
                raise PermissionError('The directory changed during the scan')

        units = []
        with tempfile.TemporaryDirectory(prefix='snapshot-', dir=self.state_dir) as temp:
            snapshot = Path(temp) / root.name
            with self._root(source) as fd:
                if kind == 'file':
                    if root.suffix.lower() in extensions and selected(root.name):
                        try:
                            copy_file(fd, os.fstat(fd), snapshot, root.name)
                        except (OSError, ValueError):
                            complete = False
                else:
                    snapshot.mkdir()
                    try:
                        walk(fd, snapshot)
                    except (OSError, ValueError):
                        complete = False
                        shutil.rmtree(snapshot)
                with self._root(source):
                    pass
            try:
                if snapshot.exists():
                    check()
                    output = Path(temp) / 'units.jsonl'
                    request = dict(kind=kind, root=str(snapshot), workflow=workflow, output=str(output),
                                   max_bytes=MAX_SCAN_BYTES, max_units=MAX_UNITS)
                    parsed = subprocess.run([sys.executable, str(READER_SCRIPT)],
                                            input=json.dumps(request).encode(), stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL,
                                            timeout=max(0.001, MAX_SCAN_SECONDS - (time.monotonic() - start)),
                                            cwd=temp)
                    if parsed.returncode != 0:
                        complete = False
                    if output.exists() and output.stat().st_size <= MAX_SCAN_BYTES:
                        seen = set()
                        for line in output.read_text().splitlines():
                            check()
                            unit = json.loads(line)
                            external_id = _clean(unit['external_id'])
                            if external_id in seen:
                                raise ValueError('Duplicate external ID')
                            seen.add(external_id)
                            files = [Path(path).relative_to(snapshot.parent if kind == 'file' else snapshot).as_posix()
                                     for path in unit['files']]
                            units.append(dict(external_id=external_id, title=_clean(unit['title']),
                                              content=unit['content'], files=files, project=_clean(unit['project']),
                                              metadata=_clean(unit['metadata']),
                                              fingerprint=hashlib.sha256(unit['content'].encode('utf-8')).hexdigest()))
            except (OSError, ValueError, RecursionError, TypeError, subprocess.TimeoutExpired):
                complete = False
        if not complete and kind in {'folder', 'git', 'mercurial'}:
            # A project combines files. Any scan gap can omit part of that unit.
            units = [unit for unit in units if not unit['external_id'].startswith('project:')]
        return units, complete

    def scan(self, source_id):
        with self._db('scan') as db:
            self._active(db)
            self._cleanup(db)
            source = self._source(db, source_id)
            with self._root(source):
                pass
            pending = db.execute('SELECT payload FROM outbox WHERE source_id=?', (source_id,)).fetchone()
            if pending:
                self._audit(db, 'scan_retry', source_id)
                return json.loads(pending['payload'])
            units, complete = self._snapshot_units(source)
            previous = {row['external_id']: (row['fingerprint'], row['details_fingerprint']) for row in
                        db.execute('SELECT external_id,fingerprint,details_fingerprint FROM checkpoints WHERE source_id=?',
                                   (source_id,))}
            known_ids = set(previous) | {row['external_id'] for row in db.execute(
                'SELECT external_id FROM unconfirmed_items WHERE source_id=?', (source_id,))}
            inventory = {}
            for unit in units:
                details = {key: unit[key] for key in ('title', 'files', 'project', 'metadata')}
                details_fingerprint = hashlib.sha256(json.dumps(
                    details, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
                inventory[unit['external_id']] = dict(fingerprint=unit['fingerprint'], files=unit['files'],
                                                     details_fingerprint=details_fingerprint)
            result = dict(scan_id=str(uuid.uuid4()), source_id=source_id, kind=source['kind'], name=_clean(source['name']),
                          workflow=_clean(json.loads(source['workflow'])),
                          units=[u for u in units if previous.get(u['external_id']) != (
                              u['fingerprint'], inventory[u['external_id']]['details_fingerprint'])],
                          removed=sorted(known_ids - set(inventory)) if complete else [],
                          complete=complete, rule_version=source['rule_version'])
            payload, encoded_inventory = json.dumps(result), json.dumps(inventory)
            size = len(payload.encode()) + len(encoded_inventory.encode())
            stored = db.execute('SELECT COALESCE(SUM(size),0) FROM outbox').fetchone()[0]
            if size > MAX_SCAN_BYTES or size + stored > MAX_OUTBOX_BYTES:
                raise ValueError('The delivery outbox is full')
            db.execute('INSERT INTO outbox(scan_id,source_id,expires,payload,inventory,size) VALUES(?,?,?,?,?,?)',
                       (result['scan_id'], source_id, time.time() + OUTBOX_TTL_SECONDS, payload, encoded_inventory, size))
            journal.record(db, result, result['name'])
            self._audit(db, 'scan', source_id, 'complete' if complete else 'incomplete')
            return result

    def acknowledge(self, scan_id):
        with self._db() as db:
            self._cleanup(db)
            pending = db.execute('SELECT * FROM outbox WHERE scan_id=?', (scan_id,)).fetchone()
            if pending is None:
                return  # Repeated acknowledgements are harmless.
            result = json.loads(pending['payload'])
            source_id = pending['source_id']
            if result['complete']:
                db.execute('DELETE FROM checkpoints WHERE source_id=?', (source_id,))
                db.execute('DELETE FROM unconfirmed_items WHERE source_id=?', (source_id,))
            for external_id, item in json.loads(pending['inventory']).items():
                db.execute('INSERT INTO checkpoints(source_id,external_id,fingerprint,files,details_fingerprint) '
                           'VALUES(?,?,?,?,?) ON CONFLICT(source_id,external_id) DO UPDATE '
                           'SET fingerprint=excluded.fingerprint, files=excluded.files, '
                           'details_fingerprint=excluded.details_fingerprint',
                           (source_id, external_id, item['fingerprint'], json.dumps(item['files']),
                            item.get('details_fingerprint', '')))
            self._received(db, scan_id)
            db.execute('DELETE FROM outbox WHERE scan_id=?', (scan_id,))
            self._audit(db, 'acknowledge', source_id)

    def original(self, source_id, external_id, fingerprint):
        with self._db('original') as db:
            self._active(db)
            self._cleanup(db)
            source = self._source(db, source_id)
            known = db.execute('SELECT fingerprint FROM checkpoints WHERE source_id=? AND external_id=?',
                               (source_id, external_id)).fetchone()
            pending = db.execute('SELECT inventory FROM outbox WHERE source_id=?', (source_id,)).fetchone()
            current = json.loads(pending['inventory']).get(external_id) if pending else None
            if not known and not current:
                raise PermissionError('Unknown original ID')
            if fingerprint not in {known['fingerprint'] if known else None, current['fingerprint'] if current else None}:
                raise ValueError('Original version mismatch')
            units, complete = self._snapshot_units(source)
            if not complete:
                raise ValueError('The original is unavailable or the source check is incomplete')
            unit = next((item for item in units if item['external_id'] == external_id), None)
            if unit is None:
                raise FileNotFoundError('The original is unavailable')
            if unit['fingerprint'] != fingerprint:
                self._audit(db, 'original', source_id, 'version_mismatch')
                return dict(status='version_mismatch', fingerprint=unit['fingerprint'], expected=fingerprint)
            self._audit(db, 'original', source_id)
            return dict(source_id=source_id, **unit)
