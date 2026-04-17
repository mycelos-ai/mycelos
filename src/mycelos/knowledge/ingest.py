"""PDF ingest pipeline — extract, summarize, store in Knowledge Base."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mycelos.files.extractor import extract_text, pdf_page_count, render_pdf_pages

logger = logging.getLogger("mycelos.knowledge")


def ingest_pdf(app: Any, file_path: Path) -> dict[str, Any]:

    kb = app.knowledge_base
    filename = file_path.name
    page_count = pdf_page_count(file_path)

    text, method = extract_text(file_path)
    has_text = bool(text.strip())

    if has_text:
        summary = _summarize(app, text, filename)
        tags = _extract_tags(app, text)

        note_path = kb.store_document(
            file_bytes=file_path.read_bytes(),
            filename=filename,
            title=filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title(),
            summary=summary,
            tags=tags,
        )

        app.audit.log("knowledge.document.ingested", details={
            "path": note_path, "filename": filename,
            "pages": page_count, "method": "text",
        })

        return {
            "status": "summarized",
            "note_path": note_path,
            "text_extracted": True,
            "vision_needed": False,
            "page_count": page_count,
        }
    else:
        note_path = kb.store_document(
            file_bytes=file_path.read_bytes(),
            filename=filename,
            title=filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title(),
            summary=f"Scanned document ({page_count} pages) — vision analysis available.",
            tags=["scanned"],
        )

        app.audit.log("knowledge.document.ingested", details={
            "path": note_path, "filename": filename,
            "pages": page_count, "method": "placeholder",
        })

        return {
            "status": "stored",
            "note_path": note_path,
            "text_extracted": False,
            "vision_needed": True,
            "page_count": page_count,
        }


def _summarize(app: Any, text: str, filename: str) -> str:
    try:
        response = app.llm.complete(
            [
                {"role": "system", "content": (
                    "You summarize documents concisely. Extract key points, decisions, "
                    "and action items. Use bullet points. Respond in the same language "
                    "as the document."
                )},
                {"role": "user", "content": (
                    f"Document: {filename}\n\n{text[:8000]}\n\n"
                    "Summarize this document. Include key points and any action items."
                )},
            ],
            model=app.resolve_cheapest_model(),
        )
        return response.content
    except Exception as e:
        logger.warning("Document summarization failed: %s", e)
        return text[:2000]


def _extract_tags(app: Any, text: str) -> list[str]:
    try:
        response = app.llm.complete(
            [
                {"role": "system", "content": (
                    "Extract 2-5 short tags (single words or short phrases) from this text. "
                    "Return ONLY a JSON array of strings, nothing else. Example: [\"finance\", \"q1-report\"]"
                )},
                {"role": "user", "content": text[:3000]},
            ],
            model=app.resolve_cheapest_model(),
        )
        import json
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        tags = json.loads(raw)
        if isinstance(tags, list):
            return [str(t).lower().strip() for t in tags[:5]]
    except Exception:
        pass
    return []


def vision_analyze(app: Any, note_path: str) -> dict[str, Any]:
    import base64

    kb = app.knowledge_base
    meta = app.storage.fetchone(
        "SELECT source_file FROM knowledge_notes WHERE path=?", (note_path,)
    )
    if not meta or not meta.get("source_file"):
        return {"status": "error", "message": "No source file linked to this note"}

    doc_path = kb.get_document_path(meta["source_file"])
    if not doc_path:
        return {"status": "error", "message": "Source file not found"}

    pages = render_pdf_pages(doc_path, dpi=300, max_pages=20)
    if not pages:
        return {"status": "error", "message": "Could not render PDF pages"}

    # Build vision message
    image_content: list[dict] = []
    for i, png_bytes in enumerate(pages):
        b64 = base64.b64encode(png_bytes).decode("ascii")
        image_content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
        image_content.append({"type": "text", "text": f"(Page {i + 1})"})

    image_content.append({
        "type": "text",
        "text": "Extract all text and structure from these document pages. Preserve tables, headings, and lists as Markdown. Respond in the document's language.",
    })

    try:
        response = app.llm.complete(
            [{"role": "user", "content": image_content}],
            model=app.resolve_strongest_model(),
        )
        extracted_text = response.content
    except Exception as e:
        logger.warning("Vision analysis failed: %s", e)
        return {"status": "error", "message": str(e)}

    summary = _summarize(app, extracted_text, doc_path.name)
    tags = _extract_tags(app, extracted_text)

    kb.update(note_path, content=summary)
    if tags:
        kb.update(note_path, tags=tags)

    # Remove "scanned" tag and merge new tags
    import json as _json
    current_meta = app.storage.fetchone(
        "SELECT tags FROM knowledge_notes WHERE path=?", (note_path,)
    )
    if current_meta:
        current_tags = _json.loads(current_meta["tags"] or "[]")
        if "scanned" in current_tags:
            current_tags.remove("scanned")
            kb.update(note_path, tags=list(set(current_tags + tags)))

    app.audit.log("knowledge.document.vision_analyzed", details={
        "path": note_path, "pages": len(pages),
    })

    return {"status": "analyzed", "pages_analyzed": len(pages)}
