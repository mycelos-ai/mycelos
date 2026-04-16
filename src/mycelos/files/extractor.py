"""Text extraction — PDF (sandboxed subprocess), DOCX, CSV, TXT."""

from __future__ import annotations

import csv
import io
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("mycelos.files")


def extract_text(file_path: Path) -> tuple[str, str]:
    """Extract text from a file.

    Returns (text, method) where method is one of:
    'pdf', 'docx', 'csv', 'text', 'vision_needed', 'unsupported'
    """
    suffix = file_path.suffix.lower()

    if suffix == '.pdf':
        text = _extract_pdf(file_path)
        if text.strip():
            return text, "pdf"
        return "", "vision_needed"  # Image-only PDF

    elif suffix in ('.docx', '.doc'):
        text = _extract_docx(file_path)
        return text, "docx" if text else "unsupported"

    elif suffix == '.csv':
        text = _extract_csv(file_path)
        return text, "csv"

    elif suffix in ('.txt', '.md', '.json', '.yaml', '.yml', '.xml', '.html'):
        try:
            text = file_path.read_text(errors='replace')[:10000]
            return text, "text"
        except Exception:
            return "", "unsupported"

    elif suffix in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff'):
        return "", "vision_needed"

    else:
        return "", "unsupported"


def _extract_pdf(file_path: Path, timeout: int = 30) -> str:
    """Extract PDF text in a sandboxed subprocess.

    Runs pymupdf in a separate process so a malicious PDF cannot
    crash or exploit the Gateway process.
    """
    try:
        # Use repr() for safe path quoting
        script = (
            "import sys; import fitz; "
            f"doc = fitz.open(sys.argv[1]); "
            "text = '\\n'.join(page.get_text() for page in doc); "
            "print(text[:10000])"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(file_path)],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.debug("PDF extraction failed (rc=%d): %s", result.returncode, result.stderr[:200])
        return ""
    except subprocess.TimeoutExpired:
        logger.warning("PDF extraction timed out for %s", file_path.name)
        return ""
    except Exception as e:
        logger.debug("PDF extraction error: %s", e)
        return ""


def _extract_docx(file_path: Path) -> str:
    """Extract text from a DOCX file."""
    try:
        from docx import Document
        doc = Document(str(file_path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)[:10000]
    except ImportError:
        logger.debug("python-docx not installed — DOCX extraction unavailable")
        return ""
    except Exception as e:
        logger.debug("DOCX extraction error: %s", e)
        return ""


def _extract_csv(file_path: Path) -> str:
    """Extract text from a CSV file (first 100 rows)."""
    try:
        with open(file_path, newline='', errors='replace') as f:
            reader = csv.reader(f)
            rows = []
            for i, row in enumerate(reader):
                if i >= 100:
                    break
                rows.append(", ".join(row))
        return "\n".join(rows)
    except Exception as e:
        logger.debug("CSV extraction error: %s", e)
        return ""


def render_pdf_pages(
    file_path: Path,
    dpi: int = 300,
    max_pages: int = 20,
) -> list[bytes]:
    """Render PDF pages as PNG byte buffers for Vision analysis.

    Returns list of PNG bytes, one per page, up to max_pages.
    If rendering fails, returns an empty list.

    Args:
        file_path: Path to the PDF file
        dpi: Resolution in dots per inch (default 300)
        max_pages: Maximum number of pages to render (default 20)

    Returns:
        List of PNG byte strings, one per rendered page
    """
    try:
        import fitz
        doc = fitz.open(str(file_path))
        pages: list[bytes] = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            pages.append(pix.tobytes("png"))
        doc.close()
        return pages
    except Exception as e:
        logger.warning("PDF rendering failed for %s: %s", file_path.name, e)
        return []


def pdf_page_count(file_path: Path) -> int:
    """Return the number of pages in a PDF, or 0 on error.

    Args:
        file_path: Path to the PDF file

    Returns:
        Number of pages, or 0 if the file cannot be read
    """
    try:
        import fitz
        doc = fitz.open(str(file_path))
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0
