"""Noise IK and bounded transport frames. No home-grown key exchange.

The invitation pins the responder's X25519 key. IK authenticates the initiator
before the responder records a pending device. Only post-handshake messages
carry source data, giving those messages forward secrecy.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from noise.connection import Keypair, NoiseConnection

PROTOCOL = b'Noise_IK_25519_ChaChaPoly_SHA256'
PROLOGUE = b'mycelos-collector/1'
CHUNK_BYTES = 32_000
MAX_PAYLOAD = 32 * 1024 * 1024
MAX_FRAMES = (MAX_PAYLOAD + CHUNK_BYTES - 1) // CHUNK_BYTES


def encode(value: bytes) -> str:
    return base64.b64encode(value).decode('ascii')


def decode(value: str, limit: int = 65535) -> bytes:
    if not isinstance(value, str) or len(value) > ((limit + 2) // 3) * 4:
        raise ValueError('Invalid encoded data')
    try:
        result = base64.b64decode(value, validate=True)
    except Exception:
        raise ValueError('Invalid encoded data') from None
    if len(result) > limit:
        raise ValueError('Data limit exceeded')
    return result


def fingerprint(public: bytes) -> str:
    if len(public) != 32:
        raise ValueError('Invalid public key')
    value = hashlib.sha256(public).hexdigest().upper()
    return ':'.join(value[i:i+2] for i in range(0, len(value), 2))


@dataclass(frozen=True)
class Identity:
    _secret: bytes = field(repr=False)

    @classmethod
    def generate(cls) -> Identity:
        key = X25519PrivateKey.generate()
        return cls(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))

    @property
    def public(self) -> bytes:
        return X25519PrivateKey.from_private_bytes(self._secret).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @classmethod
    def load(cls, path: Path) -> Identity:
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(cls.generate()._secret)
                stream.flush()
                os.fsync(stream.fileno())
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                    raise ValueError('Identity file permissions are not private')
                secret = stream.read(33)
            if len(secret) != 32:
                raise ValueError('Invalid identity file')
            return cls(secret)
        except OSError:
            raise ValueError('Cannot read identity file') from None


def _connection(identity: Identity, initiator: bool) -> NoiseConnection:
    conn = NoiseConnection.from_name(PROTOCOL)
    conn.set_prologue(PROLOGUE)
    (conn.set_as_initiator if initiator else conn.set_as_responder)()
    conn.set_keypair_from_private_bytes(Keypair.STATIC, identity._secret)
    return conn


def initiate(identity: Identity, server_key: bytes, payload: dict) -> tuple[NoiseConnection, bytes]:
    fingerprint(server_key)
    conn = _connection(identity, True)
    conn.set_keypair_from_public_bytes(Keypair.REMOTE_STATIC, server_key)
    conn.start_handshake()
    data = json.dumps(payload, separators=(',', ':')).encode()
    if len(data) > 4096:
        raise ValueError('Handshake limit exceeded')
    return conn, bytes(conn.write_message(data))


def respond(identity: Identity, first: bytes, payload: dict) -> tuple[NoiseConnection, bytes, dict, bytes]:
    try:
        if len(first) > 8192:
            raise ValueError('Handshake limit exceeded')
        conn = _connection(identity, False)
        conn.start_handshake()
        data = json.loads(conn.read_message(first))
        # noiseprotocol 0.3.1 exposes the authenticated remote static key here.
        # Read it before write_message discards the completed handshake state.
        peer = conn.noise_protocol.handshake_state.rs.public_bytes
        if not isinstance(data, dict) or len(peer) != 32:
            raise ValueError('Invalid handshake')
        reply = bytes(conn.write_message(json.dumps(payload).encode()))
        return conn, peer, data, reply
    except Exception:
        raise ValueError('Device handshake failed') from None


class Channel:
    def __init__(self, connection: NoiseConnection):
        self.connection = connection
        self.closed = False

    def seal(self, value: dict) -> list[str]:
        if self.closed:
            raise ValueError('Channel closed')
        data = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        if len(data) > MAX_PAYLOAD:
            raise ValueError('Payload limit exceeded')
        count = max(1, (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES)
        header = dict(id=uuid4().hex, count=count, length=len(data), digest=hashlib.sha256(data).hexdigest())
        result = []
        for index in range(count):
            frame = dict(header, index=index, data=encode(data[index*CHUNK_BYTES:(index+1)*CHUNK_BYTES]))
            result.append(encode(self.connection.encrypt(json.dumps(frame, separators=(',', ':')).encode())))
        return result

    def open(self, frames: list[str]) -> dict:
        try:
            if self.closed or not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
                raise ValueError('Invalid frames')
            chunks, header = [], None
            for index, frame in enumerate(frames):
                value = json.loads(self.connection.decrypt(decode(frame)))
                if not isinstance(value, dict) or set(value) != {'id', 'count', 'length', 'digest', 'index', 'data'}:
                    raise ValueError('Invalid frame')
                if any(type(value[k]) is not int for k in ('count', 'length', 'index')):
                    raise ValueError('Invalid frame order')
                current = {key: value[key] for key in ('id', 'count', 'length', 'digest')}
                if index == 0:
                    header = current
                if (current != header or value['index'] != index or value['count'] != len(frames)
                        or not 0 < value['length'] <= MAX_PAYLOAD):
                    raise ValueError('Invalid frame context')
                chunk = decode(value['data'], CHUNK_BYTES)
                if index < len(frames)-1 and len(chunk) != CHUNK_BYTES:
                    raise ValueError('Invalid chunk length')
                chunks.append(chunk)
            data = b''.join(chunks)
            if len(data) != header['length'] or hashlib.sha256(data).hexdigest() != header['digest']:
                raise ValueError('Incomplete payload')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError('Invalid message')
            return result
        except Exception:
            self.closed = True
            raise ValueError('Encrypted message rejected; reconnect required') from None
