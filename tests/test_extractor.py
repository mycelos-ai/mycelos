"""Tests for file extraction and PDF rendering."""

import pytest
from pathlib import Path
from mycelos.files.extractor import render_pdf_pages, pdf_page_count

try:
    import fitz
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False


@pytest.fixture
def single_page_pdf(tmp_path):
    """Create a single-page PDF for testing."""
    fitz_module = pytest.importorskip("fitz")
    pdf_path = tmp_path / "single_page.pdf"
    doc = fitz_module.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Test PDF Page 1", fontsize=12)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def multi_page_pdf(tmp_path):
    """Create a 5-page PDF for testing."""
    fitz_module = pytest.importorskip("fitz")
    pdf_path = tmp_path / "multi_page.pdf"
    doc = fitz_module.open()
    for i in range(5):
        page = doc.new_page()
        page.insert_text((50, 50), f"Test PDF Page {i + 1}", fontsize=12)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def three_page_pdf(tmp_path):
    """Create a 3-page PDF for testing."""
    fitz_module = pytest.importorskip("fitz")
    pdf_path = tmp_path / "three_page.pdf"
    doc = fitz_module.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((50, 50), f"Test PDF Page {i + 1}", fontsize=12)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.mark.skipif(not HAS_FITZ, reason="pymupdf not installed")
def test_render_pdf_pages_returns_pngs(single_page_pdf):
    """Test that render_pdf_pages returns PNG byte buffers."""
    pngs = render_pdf_pages(single_page_pdf)

    # Should return a list with one PNG
    assert isinstance(pngs, list)
    assert len(pngs) == 1

    # Each item should be PNG bytes (start with PNG magic number)
    png_data = pngs[0]
    assert isinstance(png_data, bytes)
    assert png_data.startswith(b'\x89PNG'), "PNG magic number should be present"


@pytest.mark.skipif(not HAS_FITZ, reason="pymupdf not installed")
def test_render_pdf_pages_respects_max(multi_page_pdf):
    """Test that render_pdf_pages respects the max_pages parameter."""
    # Render only 3 pages from a 5-page PDF
    pngs = render_pdf_pages(multi_page_pdf, max_pages=3)

    # Should return exactly 3 PNGs
    assert len(pngs) == 3

    # Each should be valid PNG
    for png_data in pngs:
        assert isinstance(png_data, bytes)
        assert png_data.startswith(b'\x89PNG')


@pytest.mark.skipif(not HAS_FITZ, reason="pymupdf not installed")
def test_pdf_page_count(three_page_pdf):
    """Test that pdf_page_count returns the correct page count."""
    count = pdf_page_count(three_page_pdf)

    # Should return exactly 3
    assert count == 3


def test_pdf_page_count_returns_zero_on_error():
    """Test that pdf_page_count returns 0 for non-existent files."""
    count = pdf_page_count(Path("/nonexistent/path/to/file.pdf"))
    assert count == 0


def test_render_pdf_pages_returns_empty_on_error():
    """Test that render_pdf_pages returns empty list for non-existent files."""
    pngs = render_pdf_pages(Path("/nonexistent/path/to/file.pdf"))
    assert pngs == []
