"""Selected local knowledge sources, distinct from connector filing scopes."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictInt

from mycelos.gateway.routers._helpers import resolve_user_id
from mycelos.knowledge.local_sources import LocalSourceService

router = APIRouter(prefix="/api/local-sources")


class SourceCreate(BaseModel):
    name: str
    kind: str
    root: str
    purpose: str = ""
    mode: str = "analyze"
    model: str | None = None
    workflow: dict | None = None


class SourceInspect(BaseModel):
    kind: str
    root: str
    workflow: dict | None = None


class SourceUpdate(BaseModel):
    name: str
    purpose: str = ""
    mode: str
    model: str | None = None
    workflow: dict | None = None


def service(request):
    return LocalSourceService(request.app.state.mycelos, resolve_user_id(request))


def invoke(fn):
    try:
        return fn()
    except (ValueError, OSError) as error:
        raise HTTPException(422, str(error)) from error


@router.get("")
def sources(request: Request):
    return service(request).list()


@router.post("")
def add(body: SourceCreate, request: Request):
    return invoke(lambda: service(request).add(**body.model_dump()))


@router.get("/templates")
def reference_templates():
    from mycelos.knowledge.source_workflows import templates
    return templates()


@router.get("/search")
def search(q: str, request: Request):
    return invoke(lambda: service(request).search(q))


@router.post("/inspect")
def inspect(body: SourceInspect, request: Request):
    return invoke(lambda: service(request).inspect(**body.model_dump()))


@router.get("/evidence/{item_id}")
def evidence(item_id: str, request: Request):
    return invoke(lambda: service(request).evidence(item_id))


@router.get("/{source_id}/preview")
def preview(source_id: str, request: Request):
    return invoke(lambda: service(request).preview(source_id))


@router.get("/{source_id}")
def get(source_id: str, request: Request):
    return invoke(lambda: service(request).get(source_id))


@router.get("/{source_id}/filing")
def filing(source_id: str, request: Request):
    """Attachments and rule of this source in the shared source model."""
    return invoke(lambda: service(request).filing(source_id))


@router.put("/{source_id}")
def update(source_id: str, body: SourceUpdate, request: Request):
    return invoke(lambda: service(request).update(source_id, **body.model_dump()))


@router.post("/{source_id}/import")
def ingest(source_id: str, request: Request):
    try:
        return invoke(lambda: service(request).enqueue(source_id))
    except RuntimeError as error:
        raise HTTPException(503, "Die Hintergrundverarbeitung ist nicht verfügbar") from error


@router.post("/{source_id}/probe")
def probe(source_id: str, request: Request):
    return invoke(lambda: service(request).probe(source_id))


@router.post("/{source_id}/pause")
def pause(source_id: str, request: Request, paused: bool = True):
    return invoke(lambda: service(request).pause(source_id, paused))


class CheckSchedule(BaseModel):
    interval_minutes: StrictInt


def check_service(request):
    from mycelos.knowledge.source_checks import SourceCheckService
    return SourceCheckService(request.app.state.mycelos, resolve_user_id(request))


@router.get("/{source_id}/check-status")
def check_status(source_id: str, request: Request):
    return invoke(lambda: check_service(request).status(source_id))


@router.get("/{source_id}/pending")
def pending(source_id: str, request: Request):
    return invoke(lambda: check_service(request).pending(source_id))


@router.put("/{source_id}/check-schedule")
def check_schedule(source_id: str, body: CheckSchedule, request: Request):
    return invoke(lambda: check_service(request).configure(source_id, body.interval_minutes))


@router.post("/{source_id}/check")
def check_source(source_id: str, request: Request):
    from mycelos.scheduler.source_check_task import enqueue_source_check
    try:
        return invoke(lambda: enqueue_source_check(request.app.state.mycelos, source_id, resolve_user_id(request)))
    except RuntimeError as error:
        raise HTTPException(503, "Die Quellenprüfung ist nicht verfügbar") from error


@router.get('/{source_id}/articles')
def source_articles(source_id: str, request: Request):
    return invoke(lambda: service(request).articles(source_id))
