"""Tests for the docs API endpoint."""
from pathlib import Path


def test_docs_list_returns_all_sections(tmp_path):
    """GET /api/docs returns list of available doc sections."""
    from mycelos.gateway.routes import _list_docs

    docs_dir = tmp_path / "website"
    docs_dir.mkdir()
    (docs_dir / "getting-started.md").write_text(
        "---\ntitle: Getting Started\ndescription: Install Mycelos\norder: 1\nicon: rocket_launch\n---\n\n## Prerequisites\n"
    )
    (docs_dir / "security.md").write_text(
        "---\ntitle: Security\ndescription: Security model\norder: 7\nicon: shield\n---\n\nSecurity content.\n"
    )

    result = _list_docs(docs_dir)
    assert len(result) == 2
    assert result[0]["slug"] == "getting-started"
    assert result[0]["title"] == "Getting Started"
    assert result[0]["order"] == 1
    assert result[1]["slug"] == "security"


def test_docs_get_returns_markdown(tmp_path):
    """GET /api/docs/{slug} returns the Markdown content without frontmatter."""
    from mycelos.gateway.routes import _get_doc

    docs_dir = tmp_path / "website"
    docs_dir.mkdir()
    (docs_dir / "getting-started.md").write_text(
        "---\ntitle: Getting Started\ndescription: Install Mycelos\norder: 1\nicon: rocket_launch\n---\n\n## Prerequisites\n\nPython 3.12+\n"
    )

    result = _get_doc(docs_dir, "getting-started")
    assert result is not None
    assert result["title"] == "Getting Started"
    assert "## Prerequisites" in result["content"]
    assert "---" not in result["content"]


def test_docs_get_returns_none_for_missing(tmp_path):
    """GET /api/docs/{slug} returns None for non-existent slug."""
    from mycelos.gateway.routes import _get_doc

    docs_dir = tmp_path / "website"
    docs_dir.mkdir()

    result = _get_doc(docs_dir, "nonexistent")
    assert result is None


def test_docs_get_rejects_path_traversal(tmp_path):
    """Slug with path traversal characters is rejected."""
    from mycelos.gateway.routes import _get_doc

    docs_dir = tmp_path / "website"
    docs_dir.mkdir()

    result = _get_doc(docs_dir, "../../../etc/passwd")
    assert result is None
