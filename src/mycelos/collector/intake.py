"""Durable receipts with bounded temporary content outside the knowledge DB."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import threading
import time
from uuid import uuid4

from .crypto import MAX_PAYLOAD

TTL = 900
MAX_SPOOL_BYTES = 128*1024*1024
MAX_PENDING_JOBS = 50


def spool_path(server, identifier: str) -> Path:
    directory=server.app.data_dir/'collector-private'/'temporary'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    return directory/(hashlib.sha256(identifier.encode()).hexdigest()+'.json')


def _write(server, identifier: str, payload: dict):
    data=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    if len(data)>MAX_PAYLOAD:
        raise ValueError('Content limit exceeded')
    path=spool_path(server,identifier)
    if sum(p.stat().st_size for p in path.parent.glob('*.json'))+len(data)>MAX_SPOOL_BYTES:
        raise ValueError('Temporary content limit reached')
    temp=path.with_suffix('.'+uuid4().hex)
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def cleanup(server):
    now=time.time()
    rows=server.db.fetchall("SELECT id FROM collector_receipts WHERE expires<=? AND status IN ('received','processing','awaiting_configuration','error')",(now,))
    for row in rows:
        spool_path(server,row['id']).unlink(missing_ok=True)
        server.db.execute("UPDATE collector_receipts SET status='expired',error=NULL WHERE id=?",(row['id'],))
    jobs=server.db.fetchall("SELECT id FROM collector_jobs WHERE expires<=? AND status NOT IN ('expired','cancelled')",(now,))
    for row in jobs:
        spool_path(server,'original:'+row['id']).unlink(missing_ok=True)
        server.db.execute("UPDATE collector_jobs SET status='expired' WHERE id=? AND status!='cancelled'",(row['id'],))
    # Remove crash leftovers, including files written before their receipt commit.
    directory=spool_path(server,'cleanup').parent
    for path in directory.iterdir():
        if path.is_file() and path.stat().st_mtime<now-TTL:
            path.unlink(missing_ok=True)
    if rows or jobs:
        server._audit('temporary_cleanup',receipts=len(rows),jobs=len(jobs))


def validate_scan(body: dict):
    from mycelos.knowledge.source_workflows import resolve_workflow
    if set(body)!={'scan_id','source_id','name','kind','workflow','units','removed','complete','rule_version'}:
        raise ValueError('Invalid scan fields')
    for name in ('scan_id','source_id','name','kind'):
        if not isinstance(body[name],str) or not 1<=len(body[name])<=200 or any(ord(c)<32 for c in body[name]):
            raise ValueError('Invalid scan identity')
    if type(body['complete']) is not bool or type(body['rule_version']) is not int or not 1<=body['rule_version']<=1_000_000:
        raise ValueError('Unknown scan rules')
    resolve_workflow(body['kind'],Path('.'),body['workflow'])
    if not isinstance(body['units'],list) or len(body['units'])>10000 or not isinstance(body['removed'],list) or len(body['removed'])>10000:
        raise ValueError('Scan count limit exceeded')
    ids=set()
    for unit in body['units']:
        if not isinstance(unit,dict) or set(unit)!={'external_id','title','content','files','project','metadata','fingerprint'}:
            raise ValueError('Invalid source unit')
        for name,limit in (('external_id',1024),('title',1000),('content',4_000_000),('project',200),('fingerprint',64)):
            if not isinstance(unit[name],str) or len(unit[name])>limit:
                raise ValueError('Invalid source text')
        if not unit['external_id'] or not unit['content'].strip() or unit['external_id'] in ids:
            raise ValueError('Duplicate or empty source unit')
        ids.add(unit['external_id'])
        if hashlib.sha256(unit['content'].encode()).hexdigest()!=unit['fingerprint']:
            raise ValueError('Content checksum does not match')
        if not isinstance(unit['metadata'],dict) or not isinstance(unit['files'],list) or len(unit['files'])>10000:
            raise ValueError('Invalid provenance')
        for file in unit['files']:
            if (not isinstance(file,str) or not file or len(file)>2048 or PurePosixPath(file).is_absolute()
                    or '..' in PurePosixPath(file).parts or '\\' in file or ':' in file):
                raise ValueError('Source paths must be relative')
    if any(not isinstance(item,str) or not item or len(item)>1024 or item in ids for item in body['removed']):
        raise ValueError('Invalid removed unit')
    if len(set(body['removed']))!=len(body['removed']) or (body['removed'] and not body['complete']):
        raise ValueError('Incomplete scans cannot remove entries')


def receive(server, device: dict, identifier: str, body: dict) -> dict:
    from .ingest import register_source
    validate_scan(body)
    if body['scan_id']!=identifier:
        raise ValueError('Scan identity mismatch')
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with server.lock,server.db.transaction():
        if server._device(device['id'])['status']!='active':
            raise ValueError('Device is not active')
        cleanup(server)
        old=server.db.fetchone('SELECT * FROM collector_receipts WHERE id=?',(identifier,))
        if old:
            if old['device_id']!=device['id'] or old['digest']!=digest:
                raise ValueError('Receipt identity conflict')
            if old['status'] in {'expired','error','cancelled'}:
                _write(server,identifier,body)
                server.db.execute("UPDATE collector_receipts SET status='received',error=NULL,expires=? WHERE id=?",(time.time()+TTL,identifier))
                return dict(id=identifier,status='received')
            return dict(id=identifier,status=old['status'])
        register_source(server.app,device,body)
        _write(server,identifier,body)
        server.db.execute('INSERT INTO collector_receipts VALUES(?,?,?,?,?,?,NULL)',
            (identifier,device['id'],digest,'received',time.time(),time.time()+TTL))
        server._audit('received',device_id=device['id'],receipt_id=identifier)
        return dict(id=identifier,status='received')


def process_pending(server):
    from .ingest import ingest_scan
    with server.lock:
        cleanup(server)
        # Advance past deferred receipts without changing their durable identity
        # or age. A new worker starts at the beginning of the queue.
        cursor = getattr(server, '_receipt_cursor', (-1, ''))
        query = "SELECT * FROM collector_receipts WHERE status IN ('received','awaiting_configuration')"
        rows = server.db.fetchall(query + ' AND (created,id)>(?,?) ORDER BY created,id LIMIT 10', cursor)
        if not rows:
            rows = server.db.fetchall(query + ' ORDER BY created,id LIMIT 10')
        if rows:
            server._receipt_cursor = (rows[-1]['created'], rows[-1]['id'])
    for row in rows:
        with server.lock:
            device=server._device(row['device_id'])
            if device['status']=='paused':
                # Pause does not acknowledge or discard unprocessed content.
                continue
            if device['status']!='active':
                server.db.execute("UPDATE collector_receipts SET status='cancelled' WHERE id=?",(row['id'],))
                spool_path(server,row['id']).unlink(missing_ok=True)
                continue
            try:
                body=json.loads(spool_path(server,row['id']).read_text())
                source=server.db.fetchone('SELECT mode,model,status FROM local_sources WHERE id=?',(body['source_id'],))
                if source['status']=='paused' or (source['mode']=='analyze' and not source['model']):
                    server.db.execute("UPDATE collector_receipts SET status='awaiting_configuration' WHERE id=?",(row['id'],))
                    continue
                claimed=server.db.execute("UPDATE collector_receipts SET status='processing' WHERE id=? AND status IN ('received','awaiting_configuration')",(row['id'],))
                if not claimed.rowcount:
                    continue
            except (OSError,ValueError,TypeError):
                server.db.execute("UPDATE collector_receipts SET status='error',error='Temporary data unavailable' WHERE id=?",(row['id'],))
                continue
        try:
            # The service checks approval again before each knowledge write.
            ingest_scan(server.app,device,body)
            with server.lock:
                if server._device(device['id'])['status']!='active':
                    raise ValueError('Device stopped')
                server.db.execute("UPDATE collector_receipts SET status='processed',error=NULL WHERE id=?",(row['id'],))
                spool_path(server,row['id']).unlink(missing_ok=True)
                server._audit('processed',device_id=device['id'],receipt_id=row['id'])
        except Exception:
            with server.lock:
                server.db.execute("UPDATE collector_receipts SET status='error',error='Source processing failed' WHERE id=?",(row['id'],))
                server._audit('processing_failed',receipt_id=row['id'])


def request_job(server,user_id: str,device_id: str,kind: str,payload: dict):
    if kind not in {'scan','original'} or set(payload)-{'source_id','external_id','fingerprint'}:
        raise ValueError('Invalid job')
    with server.lock:
        device=server._device(device_id,user_id)
        source=server.db.fetchone('SELECT * FROM collector_sources WHERE source_id=? AND device_id=?',(payload.get('source_id'),device_id))
        if not source or device['status']!='active':
            raise ValueError('Source is not available')
        if not device['last_contact'] or device['last_contact']<time.time()-90:
            raise ValueError('Collector is offline; stored knowledge remains available')
        if kind=='original':
            known=server.db.fetchone('SELECT v.fingerprint FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id WHERE i.source_id=? AND i.external_id=?',
                                    (payload['source_id'],payload.get('external_id')))
            if not known or known['fingerprint']!=payload.get('fingerprint'):
                raise ValueError('Unknown source version')
        pending = server.db.fetchone(
            "SELECT count(*) AS n FROM collector_jobs WHERE device_id=? AND status='pending' AND expires>?",
            (device_id, time.time()))
        if pending['n'] >= MAX_PENDING_JOBS:
            raise ValueError('Pending job limit reached')
        identifier=uuid4().hex
        server.db.execute('INSERT INTO collector_jobs(id,device_id,source_id,type,payload,created,expires) VALUES(?,?,?,?,?,?,?)',
            (identifier,device_id,payload['source_id'],kind,json.dumps(payload),time.time(),time.time()+TTL))
        server._audit('job_requested',job_id=identifier,device_id=device_id,user_id=user_id)
        return dict(id=identifier,status='pending')


def complete_job(server,device: dict,identifier: str,body: dict):
    job=server.db.fetchone('SELECT * FROM collector_jobs WHERE id=? AND device_id=?',(identifier,device['id']))
    if not job or job['expires']<=time.time() or job['status'] in {'cancelled','expired'}:
        raise ValueError('Job is unavailable')
    if job['status']=='complete':
        return dict(id=identifier,status='complete')
    payload=json.loads(job['payload'])
    if job['type']=='original' and 'content' in body:
        if (body.get('external_id')!=payload['external_id'] or body.get('fingerprint')!=payload['fingerprint']
                or not isinstance(body['content'],str) or hashlib.sha256(body['content'].encode()).hexdigest()!=payload['fingerprint']):
            raise ValueError('Original version mismatch')
    _write(server,'original:'+identifier,body)
    server.db.execute("UPDATE collector_jobs SET status='complete' WHERE id=?",(identifier,))
    server._audit('job_completed',job_id=identifier,device_id=device['id'])
    return dict(id=identifier,status='complete')


def job_result(server,user_id: str,identifier: str):
    cleanup(server)
    job=server.db.fetchone('SELECT j.* FROM collector_jobs j JOIN collector_devices d ON d.id=j.device_id WHERE j.id=? AND d.user_id=?',(identifier,user_id))
    if not job:
        raise ValueError('Job not found')
    result=dict(id=identifier,status=job['status'])
    if job['status']=='complete':
        result['result']=json.loads(spool_path(server,'original:'+identifier).read_text())
    return result


def start_worker(server):
    if getattr(server,'worker',None):
        return
    server.worker_stop=threading.Event()
    # Restart receipts from the last durable receive boundary.
    server.db.execute("UPDATE collector_receipts SET status='received' WHERE status='processing'")
    def work():
        while not server.worker_stop.is_set():
            try:
                process_pending(server)
            except Exception:
                server._audit('worker_failed')
            server.worker_stop.wait(2)
    server.worker=threading.Thread(target=work,name='collector-intake',daemon=True)
    server.worker.start()
