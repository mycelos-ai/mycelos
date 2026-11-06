"""Outgoing collector connection. Both transports carry identical Noise frames."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import time
from uuid import uuid4

import httpx

from .crypto import MAX_FRAMES, Channel, Identity, decode, encode, fingerprint, initiate
from .local import LocalCollector
from .server import validate_url


def save_json(path: Path, value: dict):
    temporary = path.with_name(path.name+'.'+secrets.token_hex(8))
    fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,ensure_ascii=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def client_identity(directory: Path, use_keyring: bool) -> Identity:
    marker = directory/'key-store.json'
    private = directory/'identity'
    if private.exists():
        return Identity.load(private)
    import keyring
    backend = keyring.get_keyring()
    secure = type(backend).__module__ in {
        'keyring.backends.macOS','keyring.backends.Windows','keyring.backends.SecretService',
        'keyring.backends.kwallet','keyring.backends.libsecret'}
    if marker.exists():
        account = json.loads(marker.read_text())['account']
        if not secure:
            raise ValueError('The system key store is unavailable')
        secret = keyring.get_password('mycelos-collector',account)
        if not secret:
            raise ValueError('The device key is missing from the system key store')
        return Identity(decode(secret,32))
    if use_keyring and secure:
        identity = Identity.generate()
        account = uuid4().hex
        keyring.set_password('mycelos-collector',account,encode(identity._secret))
        save_json(marker,{'account':account})
        return identity
    return Identity.load(private)


class CollectorClient:
    def __init__(self, state_dir: Path, use_keyring: bool = True):
        self.directory = Path(state_dir).expanduser()
        self.local = LocalCollector(self.directory)
        self.identity = client_identity(self.directory,use_keyring)
        self.config_path = self.directory/'connection.json'
        self.config = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
        self.channel = None
        self.session_id = None
        self.websocket = None
        self.connection_status = 'offline'
        self.source_status = {}
        self.receipt_status = {}

    @contextmanager
    def _config_lock(self):
        import fcntl
        with (self.directory/'connection.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _reload_config(self):
        config = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
        if config:
            if (not isinstance(config, dict) or set(config)-{'url','server_key','invitation','name'}
                    or not isinstance(config.get('name'), str) or not 1 <= len(config['name']) <= 100):
                raise ValueError('Invalid connection settings')
            config['url'] = validate_url(config['url'])
            decode(config['server_key'], 32)
            if 'invitation' in config and (not isinstance(config['invitation'], str)
                    or not 32 <= len(config['invitation']) <= 100):
                raise ValueError('Invalid invitation')
        if self.config and self.config.get('server_key') != config.get('server_key'):
            raise ValueError('Server identity changed; use a new local collector profile and confirm its sources')
        if config != self.config:
            self.close()
            self.config = config

    def pair(self, invitation: dict):
        if (not isinstance(invitation,dict) or set(invitation) != {'version','url','server_key','secret','expires'}
                or type(invitation['version']) is not int or invitation['version'] != 1
                or not isinstance(invitation['secret'],str) or not 32 <= len(invitation['secret']) <= 100
                or not isinstance(invitation['expires'],(int,float)) or invitation['expires']<=time.time()):
            raise ValueError('Invalid or expired invitation')
        url = validate_url(invitation['url'])
        key = decode(invitation['server_key'],32)
        fingerprint(key)
        with self._config_lock():
            self._reload_config()
            if self.config and self.config.get('server_key') != invitation['server_key']:
                raise ValueError('Server identity changed; use a new local collector profile and confirm its sources')
            self.config = dict(url=url,server_key=invitation['server_key'],invitation=invitation['secret'],name='myCelos collector')
            save_json(self.config_path,self.config)
            (self.directory/'approval.json').unlink(missing_ok=True)
        return self.status()

    def _hello(self):
        self._reload_config()
        if not self.config:
            raise ValueError('Connect a server invitation first')
        payload = {'version':1,'name':self.config['name']}
        if self.config.get('invitation'):
            payload['invitation'] = self.config['invitation']
        return initiate(self.identity,decode(self.config['server_key'],32),payload)

    def _connected(self, conn, response: bytes):
        try:
            value = json.loads(conn.read_message(response))
            if value != {'version':1,'status':'connected'}:
                raise ValueError()
        except Exception:
            raise ValueError('Server identity verification failed') from None
        self.channel = Channel(conn)
        # A control process can replace the invitation during the handshake.
        # Do not overwrite its new, explicitly supplied configuration.
        with self._config_lock():
            saved = json.loads(self.config_path.read_text())
            if saved == self.config:
                self.config.pop('invitation',None)
                save_json(self.config_path,self.config)
        self.connection_status = 'connected'

    def connect_http(self, http: httpx.Client):
        conn, first = self._hello()
        response = http.post(self.config['url']+'/api/collector-transport/handshake',json={'message':encode(first)})
        response.raise_for_status()
        body = response.json()
        self._connected(conn,decode(body['message'],8192))
        self.session_id = body['session_id']

    def connect_websocket(self):
        from websockets.sync.client import connect
        conn, first = self._hello()
        url = self.config['url'].replace('https://','wss://',1).replace('http://','ws://',1)
        socket = connect(url+'/api/collector-transport/ws',open_timeout=10,max_size=90000)
        try:
            socket.send(first)
            response = socket.recv(timeout=15)
            if not isinstance(response,bytes):
                raise ValueError('Invalid handshake response')
            self._connected(conn,response)
            self.websocket = socket
        except Exception:
            socket.close()
            raise

    def close(self):
        if self.websocket:
            self.websocket.close()
        self.websocket=None;self.channel=None;self.session_id=None
        self.connection_status='offline'

    def exchange(self, message_type: str, payload: dict, id: str | None = None, http: httpx.Client | None = None):
        if not self.channel:
            raise ValueError('No verified connection')
        try:
            frames = self.channel.seal(dict(version=1,type=message_type,id=id or uuid4().hex,payload=payload))
            if self.websocket:
                self.websocket.send(json.dumps({'count':len(frames)}))
                for frame in frames:
                    self.websocket.send(frame)
                header=json.loads(self.websocket.recv(timeout=60))
                if not isinstance(header,dict) or set(header)!={'count'} or type(header['count']) is not int or not 1<=header['count']<=MAX_FRAMES:
                    raise ValueError('Invalid frame count')
                result=[]
                for _ in range(header['count']):
                    frame=self.websocket.recv(timeout=15)
                    if not isinstance(frame,str) or len(frame)>90000:
                        raise ValueError('Invalid transport frame')
                    result.append(frame)
            elif http and self.session_id:
                response = http.post(self.config['url']+'/api/collector-transport/exchange',
                    json={'session_id':self.session_id,'frames':frames})
                response.raise_for_status()
                result = response.json()['frames']
            else:
                raise ValueError('No transport')
            message = self.channel.open(result)
            self.connection_status = message['status']
            if message['status'] == 'active':
                with self._config_lock():
                    saved = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
                    if saved == self.config and 'invitation' not in saved:
                        save_json(self.directory/'approval.json', dict(
                            server_key=self.config['server_key'], public_key=encode(self.identity.public)))
            return message
        except Exception:
            self.close()
            raise ValueError('Connection interrupted; a new verified session is required') from None

    def status(self):
        local = self.local.status()
        runtime_path = self.directory/'runtime.json'
        runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
        approval_path = self.directory/'approval.json'
        approval = json.loads(approval_path.read_text()) if approval_path.exists() else {}
        approved = bool(self.config and approval == dict(
            server_key=self.config.get('server_key'), public_key=encode(self.identity.public)))
        states = {item['source_id']: item for item in runtime.get('sources', [])}
        sources = [dict(source, **{key: value for key, value in states.get(source['source_id'], {}).items()
                                  if key in {'status','error','checked'}}) for source in self.local.list_sources()]
        return dict(local,public_key=encode(self.identity.public),fingerprint=fingerprint(self.identity.public),
                    server_key=self.config.get('server_key'),
                    server_fingerprint=fingerprint(decode(self.config['server_key'],32)) if self.config else None,
                    server_url=self.config.get('url'), sources=sources,
                    runtime=runtime,connected=self.channel is not None,
                    onboarding_complete=approved, daemon_running=self.daemon_running())

    def daemon_running(self):
        import fcntl
        with (self.directory/'daemon.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock, fcntl.LOCK_UN)
        return False

    def disconnect(self, timeout=75):
        import fcntl
        (self.directory/'pause-request').touch(mode=0o600)
        (self.directory/'stop').touch(mode=0o600)
        deadline = time.monotonic() + timeout
        with (self.directory/'daemon.lock').open('a') as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('The collector has not stopped') from None
                    time.sleep(0.05)
            # Hold the daemon lock until all state changes finish. A concurrent
            # start cannot reconnect between the stop check and grant removal.
            with self._config_lock():
                self.close()
                self.local.pause(True)
                self.local.clear_connection_state()
                self.config_path.unlink(missing_ok=True)
                (self.directory/'approval.json').unlink(missing_ok=True)
                (self.directory/'runtime.json').unlink(missing_ok=True)
                self.config = {}
                self.source_status = {}
        return {'status': 'disconnected'}

    def _stopped(self):
        return (self.directory/'stop').exists() or self.local.status()['paused']

    def _runtime(self, **details):
        save_json(self.directory/'runtime.json', dict(
            status=self.connection_status, sources=list(self.source_status.values()), **details))

    def _source_state(self, source_id, status, error=None):
        self.source_status[source_id] = dict(source_id=source_id, status=status, error=error, checked=time.time())
        self._runtime()

    def _send_scan(self, scan, http):
        source_id = scan['source_id']
        self._source_state(source_id, 'transferring')
        try:
            response = self._request('scan', scan, http, id=scan['scan_id'])
        except Exception:
            self._source_state(source_id, 'transfer_failed', 'Transfer failed')
            raise
        status = (response.get('ack') or {}).get('status')
        if status in {'received', 'awaiting_configuration', 'processing', 'processed'}:
            if not scan['complete']:
                self._source_state(source_id, 'incomplete', 'Scan incomplete')
            elif not scan['units'] and not scan['removed'] and status != 'awaiting_configuration':
                self._source_state(source_id, 'unchanged')
            else:
                self._source_state(source_id, status)
        else:
            self._source_state(source_id, 'transfer_failed', 'Transfer failed')
        return response

    def _request(self, type, payload, http, id=None):
        original = type == 'job_result' and isinstance(payload.get('content'), str) and bool(payload.get('files'))
        if original:
            self.local.record_original(id, payload)
        try:
            response = self.exchange(type, payload, id=id, http=http)
        except Exception:
            if type == 'scan' or original:
                self.local.fail_scan('original:'+id if original else id)
            raise
        # Every reply can carry a durable acknowledgement, including the direct
        # scan reply when older receipts fall outside the poll response limit.
        received = {'received', 'awaiting_configuration', 'processing', 'processed'}
        self.receipt_status = {item['id']: item['status'] for item in response.get('receipts', [])}
        for receipt in response.get('receipts',[]):
            if receipt['status'] in received:
                self.local.mark_received(receipt['id'])
            if receipt['status']=='processed':
                self.local.acknowledge(receipt['id'])
            elif receipt['status'] in {'failed', 'error', 'expired', 'cancelled'}:
                self.local.fail_scan(receipt['id'])
        ack = response.get('ack') or {}
        if type == 'scan' and ack.get('status') in received:
            self.local.mark_received(id)
        if original and ack.get('status') == 'complete':
            self.local.mark_received('original:'+id)
        if type == 'scan' and ack.get('status') == 'processed':
            self.local.acknowledge(id)
        elif type == 'scan' and ack.get('status') in {'failed', 'error', 'expired', 'cancelled', 'rejected', 'denied'}:
            self.local.fail_scan(id)
        return response

    def cycle(self,http: httpx.Client):
        try:
            self._cycle(http)
        finally:
            self._runtime(last_contact=time.time(), error=None)

    def _cycle(self,http: httpx.Client):
        if self._stopped():
            return
        response = self._request('poll', {}, http)
        if response['status']!='active':
            return
        # Complete the bounded job batch before routine source checks.
        for job in response.get('jobs',[]):
            if self._stopped():
                return
            current = self._request('poll', {}, http)
            if current['status'] != 'active' or self._stopped():
                return
            if job['id'] not in {item['id'] for item in current.get('jobs', [])}:
                continue
            payload = job['payload']
            scan = None
            try:
                if job['type']=='original':
                    result=self.local.original(payload['source_id'],payload['external_id'],payload['fingerprint'])
                elif job['type']=='scan':
                    self._source_state(payload['source_id'], 'scanning')
                    scan=self.local.scan(payload['source_id'])
                    result={'status':'complete'}
                elif job['type']=='cancel':
                    result={'status':'cancelled'}
                else:
                    result={'status':'denied'}
            except (ValueError,OSError):
                result={'status':'unavailable'}
                self._source_state(payload['source_id'], 'unavailable', 'Source is unavailable')
            if self._stopped():
                return
            # Cancellation can arrive while the bounded local read runs.
            current = self._request('poll', {}, http)
            if current['status'] != 'active' or self._stopped():
                return
            if job['id'] not in {item['id'] for item in current.get('jobs', [])}:
                continue
            if scan is not None:
                current = self._send_scan(scan, http)
                if current['status'] != 'active' or self._stopped():
                    return
            current = self._request('job_result', result, http, id=job['id'])
            if current['status'] != 'active':
                return
        remaining = {source['source_id']: source for source in self.local.list_sources()}
        while remaining:
            # Re-read requests after every scan, including requests made during
            # a previous scan. Requests for completed sources remain for the next cycle.
            requested = [source['source_id'] for source in self.local.list_sources()
                         if source['sync_requested'] and source['source_id'] in remaining]
            source = remaining.pop(requested[0] if requested else next(iter(remaining)))
            if self._stopped():
                return
            manual = self.local.claim_sync(source['source_id'])
            pending = self.local.pending_delivery(source['source_id'])
            receipt_status = self.receipt_status.get(pending['scan_id']) if pending else None
            if not manual and pending and pending['received_at'] is not None:
                if receipt_status in {'failed', 'error'}:
                    self._source_state(source['source_id'], 'processing_failed', 'Source processing failed')
                    continue
                if receipt_status in {'received', 'processing', 'awaiting_configuration'}:
                    if not pending['complete']:
                        self._source_state(source['source_id'], 'incomplete', 'Scan incomplete')
                    else:
                        self._source_state(source['source_id'], receipt_status)
                    continue
            self._source_state(source['source_id'], 'scanning')
            try:
                scan = self.local.scan(source['source_id'])
            except (ValueError,OSError):
                self._source_state(source['source_id'], 'unavailable', 'Source is unavailable')
                continue
            if self._stopped():
                return
            current = self._send_scan(scan, http)
            if current['status'] != 'active':
                return

    def run(self):
        import fcntl
        lock_path=self.directory/'daemon.lock'
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Collector already runs') from None
            (self.directory/'stop').unlink(missing_ok=True)
            delay=1
            with httpx.Client(timeout=60,follow_redirects=False) as http:
                while not (self.directory/'stop').exists():
                    wait=15
                    requests = self.local.sync_requests()
                    was_paused = self.local.status()['paused']
                    try:
                        self._reload_config()
                        if not self.channel and not self.local.status()['paused']:
                            # A blocked WebSocket starts a fresh Noise handshake over HTTPS.
                            try:
                                self.connect_websocket()
                            except Exception:
                                self.close();self.connect_http(http)
                        self.cycle(http)
                        delay=1
                        self._runtime(last_contact=time.time(), error=None)
                    except Exception:
                        self.close()
                        self._runtime(error='Connection failed', checked=time.time())
                        wait=delay;delay=min(delay*2,60)
                    for _ in range(wait * 2):
                        if (self.directory/'stop').exists():
                            break
                        if not self.local.status()['paused'] and (was_paused or any(
                                requests.get(key) != token for key, token in self.local.sync_requests().items())):
                            break
                        time.sleep(0.5)
            self.close()
            self.connection_status = 'stopped'
            self._runtime()
