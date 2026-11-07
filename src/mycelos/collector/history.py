"""Content-free transfer journal, indexed by source and relative file."""
from pathlib import PurePosixPath
import time

MAX_HISTORY_ROWS = 10_000

SCHEMA = '''
CREATE TABLE IF NOT EXISTS transfer_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
    scan_id TEXT NOT NULL, source_id TEXT NOT NULL, source_name TEXT NOT NULL,
    file TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
    UNIQUE(scan_id,file));
CREATE INDEX IF NOT EXISTS history_source_id ON transfer_history(source_id,id);
'''


def record(db, scan, source_name):
    files = {file for unit in scan['units'] for file in unit['files']}
    for file in sorted(files):
        path = PurePosixPath(file)
        if not file or path.is_absolute() or '..' in path.parts or '\\' in file:
            continue
        db.execute('INSERT OR IGNORE INTO transfer_history '
                   '(at,scan_id,source_id,source_name,file,status) VALUES(?,?,?,?,?,?)',
                   (time.time(), scan['scan_id'], scan['source_id'], source_name, file, 'pending'))
    db.execute('DELETE FROM transfer_history WHERE id NOT IN '
               '(SELECT id FROM transfer_history ORDER BY id DESC LIMIT ?)', (MAX_HISTORY_ROWS,))


def update(db, scan_id, status, error=None):
    # Each first transition gets a new monotonic position and its actual local
    # acknowledgement time. The unique transfer/file identity remains intact.
    rows = db.execute("SELECT * FROM transfer_history WHERE scan_id=? "
                      "AND status != 'transferred' AND status != ? ORDER BY id",
                      (scan_id, status)).fetchall()
    at = time.time()
    for row in rows:
        db.execute('DELETE FROM transfer_history WHERE id=?', (row['id'],))
        db.execute('INSERT INTO transfer_history '
                   '(at,scan_id,source_id,source_name,file,status,error) VALUES(?,?,?,?,?,?,?)',
                   (at, scan_id, row['source_id'], row['source_name'], row['file'], status, error))


def fail_source(db, error, source_id=None):
    query = "SELECT scan_id FROM transfer_history WHERE status='pending'"
    values = ()
    if source_id is not None:
        query += ' AND source_id=?'
        values = (source_id,)
    rows = db.execute(query+' GROUP BY scan_id ORDER BY MIN(id)', values).fetchall()
    for row in rows:
        update(db, row['scan_id'], 'failed', error)


def page(db, source_id=None, before=None, limit=100):
    if (source_id is not None and (not isinstance(source_id, str) or len(source_id) > 128)
            or before is not None and (type(before) is not int or before < 1)
            or type(limit) is not int or limit < 1):
        raise ValueError('Invalid history page')
    limit = min(limit, 500)
    where, values = [], []
    if source_id is not None:
        where.append('source_id=?')
        values.append(source_id)
    if before is not None:
        where.append('id<?')
        values.append(before)
    query = 'SELECT id,at,source_id,source_name,file,status,error FROM transfer_history'
    if where:
        query += ' WHERE ' + ' AND '.join(where)
    rows = db.execute(query+' ORDER BY id DESC LIMIT ?', (*values, limit+1)).fetchall()
    entries = [{key: value for key, value in dict(row).items() if key != 'error' or value is not None}
               for row in rows[:limit]]
    sources = {row['source_id']: row['name'] for row in db.execute(
        'SELECT source_id,source_name AS name FROM transfer_history GROUP BY source_id')}
    sources.update({row['id']: row['name'] for row in db.execute('SELECT id,name FROM sources')})
    return dict(entries=entries, sources=[dict(source_id=key, name=name) for key, name in sources.items()],
                next_before=entries[-1]['id'] if len(rows) > limit else None)
