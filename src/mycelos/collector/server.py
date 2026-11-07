"""Device approval and Noise session ownership for the collector gateway."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import secrets
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .crypto import Channel, Identity, encode, fingerprint, respond

INVITATION_TTL = 600
SESSION_TTL = 900
MAX_SESSIONS = 128
_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS collector_invitations (
        token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS collector_devices (
        id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,public_key TEXT NOT NULL UNIQUE,
        fingerprint TEXT NOT NULL,attempt TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,
        confirmed REAL,last_contact REAL,pair_token_hash TEXT)''',
    '''CREATE TABLE IF NOT EXISTS collector_sources (
        source_id TEXT PRIMARY KEY,device_id TEXT NOT NULL REFERENCES collector_devices(id),
        rule_version INTEGER NOT NULL DEFAULT 1)''',
    '''CREATE TABLE IF NOT EXISTS collector_receipts (
        id TEXT PRIMARY KEY,device_id TEXT NOT NULL,digest TEXT NOT NULL,status TEXT NOT NULL,
        created REAL NOT NULL,expires REAL NOT NULL,error TEXT)''',
    '''CREATE TABLE IF NOT EXISTS collector_jobs (
        id TEXT PRIMARY KEY,device_id TEXT NOT NULL,source_id TEXT NOT NULL,type TEXT NOT NULL,
        payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created REAL NOT NULL,expires REAL NOT NULL)''',
)


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in {'', '/'}):
        raise ValueError('Use a server origin without a path or credentials')
    if parts.scheme == 'http' and parts.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('HTTPS is required for remote servers')
    return url.rstrip('/')


@dataclass
class Session:
    device_id: str
    channel: Channel
    expires: float
    established: bool = False


class CollectorServer:
    def __init__(self, app):
        self.app = app
        self.db = app.storage
        self.identity = Identity.load(app.data_dir / 'collector-private' / 'identity')
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self._hellos: dict[str, float] = {}
        self._untrusted: dict[str, list[float]] = {}
        for statement in _SCHEMA:
            self.db.execute(statement)
        try:
            self.db.fetchone("SELECT pair_token_hash FROM collector_devices LIMIT 0")
        except Exception:
            self.db.execute("ALTER TABLE collector_devices ADD COLUMN pair_token_hash TEXT")

    def _audit(self, action: str, **details):
        self.app.audit.log('collector.' + action, details=details)

    def invitation(self, user_id: str, url: str) -> dict:
        url = validate_url(url)
        secret = secrets.token_urlsafe(32)
        expires = time.time() + INVITATION_TTL
        self.db.execute('DELETE FROM collector_invitations WHERE expires<?', (time.time(),))
        self.db.execute('INSERT INTO collector_invitations(token_hash,user_id,expires) VALUES(?,?,?)',
                        (hashlib.sha256(secret.encode()).hexdigest(), user_id, expires))
        self._audit('invitation_created', user_id=user_id)
        return dict(version=1,url=url,server_key=encode(self.identity.public),secret=secret,expires=expires)

    def devices(self, user_id: str) -> list[dict]:
        rows = self.db.fetchall('SELECT * FROM collector_devices WHERE user_id=? ORDER BY created', (user_id,))
        for row in rows:
            row['online'] = bool(row['status'] == 'active' and row['last_contact'] and row['last_contact'] > time.time()-90)
            row.pop('pair_token_hash',None)
            row['sources'] = self.db.fetchall('SELECT c.source_id,c.rule_version,s.name,s.kind,s.mode,s.model,s.status,s.purpose FROM collector_sources c LEFT JOIN local_sources s ON s.id=c.source_id WHERE c.device_id=?', (row['id'],))
        return rows

    def _device(self, device_id: str, user_id: str | None = None) -> dict:
        row = self.db.fetchone('SELECT * FROM collector_devices WHERE id=?', (device_id,))
        if not row or (user_id is not None and row['user_id'] != user_id):
            raise ValueError('Device not found')
        return row

    def confirm(self, user_id: str, device_id: str, expected: str, attempt: str) -> dict:
        with self.lock, self.db.transaction():
            device = self._device(device_id, user_id)
            if (device['status'] != 'pending' or not secrets.compare_digest(device['fingerprint'], expected)
                    or not secrets.compare_digest(device['attempt'], attempt)
                    or device['created'] < time.time()-INVITATION_TTL):
                raise ValueError('Device confirmation does not match this pending exchange')
            self.db.execute("UPDATE collector_devices SET status='active',confirmed=? WHERE id=?", (time.time(), device_id))
            self._audit('confirmed', device_id=device_id, user_id=user_id)
        return self._device(device_id, user_id)

    def set_status(self, user_id: str, device_id: str, status: str) -> dict:
        if status not in {'active','paused','revoked'}:
            raise ValueError('Invalid device state')
        with self.lock, self.db.transaction():
            device = self._device(device_id, user_id)
            if device['status'] not in {'active','paused'} and not (status == 'revoked' and device['status'] == 'pending'):
                raise ValueError('Device needs a new confirmed invitation')
            self.db.execute('UPDATE collector_devices SET status=? WHERE id=?', (status, device_id))
            if status in {'paused', 'revoked'}:
                self.db.execute("UPDATE collector_jobs SET status='cancelled' WHERE device_id=? AND status='pending'", (device_id,))
                for sid, session in list(self.sessions.items()):
                    if session.device_id == device_id:
                        session.channel.closed = True
                        del self.sessions[sid]
            self._audit(status, device_id=device_id,user_id=user_id)
        return self._device(device_id, user_id)

    def accept_handshake(self, first: bytes, address: str = 'local') -> tuple[str, bytes]:
        with self.lock:
            now = time.time()
            self._hellos = {h:t for h,t in self._hellos.items() if t>now-SESSION_TTL}
            hello_hash = hashlib.sha256(first).hexdigest()
            if hello_hash in self._hellos:
                raise ValueError('Repeated handshake')
            for sid, session in list(self.sessions.items()):
                if session.expires <= now:
                    del self.sessions[sid]
            if len(self.sessions) >= MAX_SESSIONS:
                raise ValueError('Connection limit reached')
            conn, peer, hello, reply = respond(self.identity, first, {'version':1,'status':'connected'})
            if (set(hello) - {'version','name','invitation'} or type(hello.get('version')) is not int
                    or hello['version'] != 1 or not isinstance(hello.get('name'), str)
                    or not 1 <= len(hello['name']) <= 100):
                raise ValueError('Invalid device hello')
            public = encode(peer)
            with self.db.transaction():
                device = self.db.fetchone('SELECT * FROM collector_devices WHERE public_key=?', (public,))
                if not device:
                    attempts = [t for t in self._untrusted.get(address, []) if t>now-60]
                    if len(attempts)>=10:
                        raise ValueError('Invitation attempt limit reached')
                    if len(self._untrusted)>1024:
                        self._untrusted.clear()
                    self._untrusted[address] = attempts+[now]
                invitation = hello.get('invitation')
                if invitation is not None:
                    if not isinstance(invitation,str) or len(invitation)>100:
                        raise ValueError('Invalid invitation')
                    digest = hashlib.sha256(invitation.encode()).hexdigest()
                    invite = self.db.fetchone('SELECT * FROM collector_invitations WHERE token_hash=?', (digest,))
                    recovery = bool(invite and device and device['pair_token_hash'] == digest
                                    and device['status'] in {'pending','active'})
                    if not invite or invite['expires'] <= now or (invite['used'] and not recovery):
                        raise ValueError('Invitation is expired or already used')
                    if not recovery:
                        if device and device['status'] != 'revoked':
                            raise ValueError('Device already has an invitation')
                        claimed = self.db.execute('UPDATE collector_invitations SET used=1 WHERE token_hash=? AND used=0 AND expires>?', (digest, now))
                        if claimed.rowcount != 1:
                            raise ValueError('Invitation already used')
                        did = device['id'] if device else uuid4().hex
                        if device and device['user_id'] != invite['user_id']:
                            raise ValueError('Device belongs to another user')
                        self.db.execute('''INSERT INTO collector_devices(id,user_id,name,public_key,fingerprint,attempt,status,created,pair_token_hash)
                            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,attempt=excluded.attempt,
                            status='pending',created=excluded.created,confirmed=NULL,last_contact=NULL,pair_token_hash=excluded.pair_token_hash''',
                            (did, invite['user_id'], hello['name'], public, fingerprint(peer),encode(conn.get_handshake_hash()),'pending',now,digest))
                        device = self._device(did)
                        self._audit('pairing_pending',device_id=did,user_id=invite['user_id'])
                if not device or device['status'] == 'revoked':
                    raise ValueError('Device has no valid approval')
                if device['status'] == 'pending' and device['created'] < now-INVITATION_TTL:
                    raise ValueError('Pending confirmation expired')
            provisional = sum(s.device_id == device['id'] and not s.established for s in self.sessions.values())
            if provisional >= 4:
                raise ValueError('Too many incomplete device exchanges')
            self._hellos[hello_hash] = now
            sid = secrets.token_urlsafe(32)
            self.sessions[sid] = Session(device['id'], Channel(conn), now+60)
            return sid, reply

    def exchange(self, session_id: str, frames: list[str]) -> list[str]:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session.expires <= time.time():
                raise ValueError('Session expired; reconnect required')
            try:
                device = self._device(session.device_id)
                if device['status'] == 'revoked':
                    raise ValueError('Device revoked')
                message = session.channel.open(frames)
                if not session.established:
                    # Fresh transport authentication, not a replayable IK hello,
                    # authorizes replacement of a previous active channel.
                    for other_id, other in list(self.sessions.items()):
                        if other_id != session_id and other.device_id == device['id'] and other.established:
                            other.channel.closed = True
                            del self.sessions[other_id]
                    session.established = True
                    session.expires = time.time()+SESSION_TTL
                if (set(message) != {'version','type','id','payload'} or type(message['version']) is not int
                        or message['version'] != 1 or message['type'] not in {'poll','status','scan','job_result'}
                        or not isinstance(message['id'],str) or not 1<=len(message['id'])<=128
                        or not isinstance(message['payload'],dict)):
                    raise ValueError('Invalid protocol message')
                if device['status'] != 'active' and message['type'] not in {'poll','status'}:
                    raise ValueError('Device has no active approval')
                result = {'version':1, 'status':device['status'], 'ack':None, 'jobs':[], 'receipts':[]}
                if device['status'] == 'active':
                    if message['type'] == 'scan':
                        from .intake import receive
                        result['ack'] = receive(self, device, message['id'], message['payload'])
                    elif message['type'] == 'job_result':
                        from .intake import complete_job
                        result['ack'] = complete_job(self, device, message['id'], message['payload'])
                    result['jobs'] = self.db.fetchall("SELECT id,type,payload FROM collector_jobs WHERE device_id=? AND status='pending' AND expires>? ORDER BY created LIMIT 5", (device['id'],time.time()))
                    for job in result['jobs']:
                        job['payload'] = json.loads(job['payload'])
                    result['receipts'] = self.db.fetchall('SELECT id,status,error FROM collector_receipts WHERE device_id=? ORDER BY created DESC LIMIT 100', (device['id'],))
                self.db.execute('UPDATE collector_devices SET last_contact=? WHERE id=?', (time.time(),device['id']))
                return session.channel.seal(result)
            except Exception:
                session.channel.closed = True
                self.sessions.pop(session_id, None)
                raise ValueError('Collector message rejected') from None
