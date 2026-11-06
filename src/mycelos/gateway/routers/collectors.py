"""Management uses gateway auth; device routes require a Noise session."""
import asyncio
import json
import threading

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from mycelos.collector.crypto import MAX_FRAMES, MAX_PAYLOAD, decode, encode
from mycelos.collector.server import CollectorServer
from ._helpers import resolve_user_id

router = APIRouter()
_lock = threading.Lock()


def service(app):
    with _lock:
        if not hasattr(app.state, 'collector_server'):
            app.state.collector_server = CollectorServer(app.state.mycelos)
        return app.state.collector_server


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Invitation(StrictBody):
    url: str = Field(max_length=2048)


class Confirmation(StrictBody):
    fingerprint: str = Field(max_length=100)
    attempt: str = Field(max_length=100)


class State(StrictBody):
    status: str


def invoke(fn):
    try:
        return fn()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get('/api/collectors')
def devices(request: Request):
    return service(request.app).devices(resolve_user_id(request))


@router.get('/api/collectors/identity')
def identity(request: Request):
    from mycelos.collector.crypto import fingerprint
    key = service(request.app).identity.public
    return dict(public_key=encode(key),fingerprint=fingerprint(key))


@router.post('/api/collectors/invitations')
def invitation(body: Invitation, request: Request):
    return invoke(lambda: service(request.app).invitation(resolve_user_id(request),body.url))


@router.post('/api/collectors/{device_id}/confirm')
def confirm(device_id: str, body: Confirmation, request: Request):
    return invoke(lambda: service(request.app).confirm(resolve_user_id(request),device_id,body.fingerprint,body.attempt))


@router.post('/api/collectors/{device_id}/state')
def state(device_id: str, body: State, request: Request):
    return invoke(lambda: service(request.app).set_status(resolve_user_id(request),device_id,body.status))


async def bounded_body(request: Request, limit: int):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>limit:
            raise HTTPException(413,'Message limit exceeded')
    try:
        value = json.loads(data)
        if not isinstance(value,dict):
            raise ValueError()
        return value
    except ValueError:
        raise HTTPException(422,'Invalid message') from None


@router.post('/api/collector-transport/handshake')
async def handshake(request: Request):
    body = await bounded_body(request, 16000)
    try:
        if set(body) != {'message'}:
            raise ValueError()
        sid, reply = await asyncio.to_thread(service(request.app).accept_handshake, decode(body['message'],8192), request.client.host if request.client else 'unknown')
        return dict(session_id=sid,message=encode(reply))
    except ValueError:
        raise HTTPException(403,'Device handshake rejected') from None


@router.post('/api/collector-transport/exchange')
async def exchange(request: Request):
    body = await bounded_body(request, MAX_PAYLOAD*2+MAX_FRAMES*1024)
    try:
        if set(body) != {'session_id','frames'} or not isinstance(body['session_id'],str):
            raise ValueError()
        frames = await asyncio.to_thread(service(request.app).exchange, body['session_id'],body['frames'])
        return dict(frames=frames)
    except ValueError:
        raise HTTPException(403,'Collector message rejected') from None


@router.websocket('/api/collector-transport/ws')
async def websocket(socket: WebSocket):
    # Browsers never need this device-only endpoint. No cookie authorizes it.
    if socket.headers.get('origin'):
        await socket.close(code=1008)
        return
    await socket.accept()
    server = service(socket.app)
    sid = None
    try:
        first = await asyncio.wait_for(socket.receive_bytes(),15)
        sid, reply = await asyncio.to_thread(server.accept_handshake,first,socket.client.host if socket.client else "unknown")
        await socket.send_bytes(reply)
        while True:
            raw = await asyncio.wait_for(socket.receive_text(),90)
            if len(raw)>100:
                raise ValueError()
            header = json.loads(raw)
            if not isinstance(header,dict) or set(header)!={'count'} or type(header['count']) is not int or not 1<=header['count']<=MAX_FRAMES:
                raise ValueError()
            frames=[]
            for _ in range(header['count']):
                frame=await asyncio.wait_for(socket.receive_text(),15)
                if len(frame)>90000:
                    raise ValueError()
                frames.append(frame)
            result = await asyncio.to_thread(server.exchange,sid,frames)
            await socket.send_json({'count':len(result)})
            for frame in result:
                await socket.send_text(frame)
    except (ValueError, TypeError, KeyError, asyncio.TimeoutError, WebSocketDisconnect):
        try:
            await socket.close(code=1008)
        except RuntimeError:
            pass
    finally:
        if sid:
            with server.lock:
                server.sessions.pop(sid,None)


class SourceSettings(StrictBody):
    mode: str
    model: str | None = None
    purpose: str = Field(default='',max_length=2000)


@router.post('/api/collectors/{device_id}/sources/{source_id}/configure')
def configure_source(device_id: str,source_id: str,body: SourceSettings,request: Request):
    from mycelos.knowledge.local_sources import LocalSourceService
    server=service(request.app)
    user=resolve_user_id(request)
    def configure():
        with server.lock:
            server._device(device_id,user)
            if not server.db.fetchone('SELECT 1 FROM collector_sources WHERE source_id=? AND device_id=?',(source_id,device_id)):
                raise ValueError('Source not found')
            sources=LocalSourceService(server.app,user)
            source=sources.get(source_id)
            sources.update(source_id,source['name'],body.purpose,body.mode,body.model)
            result=sources.pause(source_id,False)
            server._audit('source_configured',source_id=source_id,user_id=user)
            return result
    return invoke(configure)


class Job(StrictBody):
    type: str
    payload: dict


@router.post('/api/collectors/{device_id}/jobs')
def create_job(device_id: str,body: Job,request: Request):
    from mycelos.collector.intake import request_job
    return invoke(lambda: request_job(service(request.app),resolve_user_id(request),device_id,body.type,body.payload))


@router.get('/api/collectors/jobs/{job_id}')
def read_job(job_id: str,request: Request):
    from fastapi.responses import JSONResponse
    from mycelos.collector.intake import job_result
    result=invoke(lambda: job_result(service(request.app),resolve_user_id(request),job_id))
    return JSONResponse(result,headers={'Cache-Control':'no-store','Pragma':'no-cache','X-Content-Type-Options':'nosniff'})


@router.post('/api/collectors/jobs/{job_id}/cancel')
def cancel_job(job_id: str,request: Request):
    server=service(request.app);user=resolve_user_id(request)
    def cancel():
        with server.lock:
            job=server.db.fetchone('SELECT j.id FROM collector_jobs j JOIN collector_devices d ON d.id=j.device_id WHERE j.id=? AND d.user_id=?',(job_id,user))
            if not job:
                raise ValueError('Job not found')
            server.db.execute("UPDATE collector_jobs SET status='cancelled' WHERE id=?",(job_id,))
            from mycelos.collector.intake import spool_path
            spool_path(server,'original:'+job_id).unlink(missing_ok=True)
            server._audit('job_cancelled',job_id=job_id,user_id=user)
            return {'id':job_id,'status':'cancelled'}
    return invoke(cancel)


@router.get('/api/collectors/{device_id}/sources/{source_id}/items')
def source_items(device_id: str,source_id: str,request: Request):
    server=service(request.app);user=resolve_user_id(request)
    def items():
        server._device(device_id,user)
        if not server.db.fetchone('SELECT 1 FROM collector_sources WHERE source_id=? AND device_id=?',(source_id,device_id)):
            raise ValueError('Source not found')
        return server.db.fetchall('SELECT i.external_id,i.available,v.fingerprint FROM local_source_items i JOIN local_source_versions v ON v.id=i.version_id WHERE i.source_id=? ORDER BY i.external_id LIMIT 200',(source_id,))
    return invoke(items)


@router.get('/api/collectors/{device_id}/sources/{source_id}/interview')
def source_interview(device_id: str,source_id: str,request: Request):
    from mycelos.collector.interview import preview
    server=service(request.app);user=resolve_user_id(request)
    def sample():
        server._device(device_id,user)
        if not server.db.fetchone('SELECT 1 FROM collector_sources WHERE source_id=? AND device_id=?',(source_id,device_id)):
            raise ValueError('Source not found')
        return preview(server.app,user,source_id)
    from fastapi.responses import JSONResponse
    return JSONResponse(invoke(sample),headers={'Cache-Control':'no-store'})
