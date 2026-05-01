"""Docs endpoints — list and read documentation pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import get_doc, list_docs

router = APIRouter()


@router.get("/api/docs")
async def docs_index(request: Request):
    docs_dir = Path(__file__).parent.parent.parent.parent.parent / "docs" / "website"
    docs_dir = docs_dir.resolve()
    return list_docs(docs_dir)


@router.get("/api/docs/{slug}")
async def docs_get(request: Request, slug: str):
    docs_dir = Path(__file__).parent.parent.parent.parent.parent / "docs" / "website"
    docs_dir = docs_dir.resolve()
    result = get_doc(docs_dir, slug)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return result
