"""Test Runner -- executes generated agent tests in an isolated environment.

Runs pytest on generated code in a temporary directory with timeout.
Used by the Creator Pipeline to verify agent code before audit.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


_SDK_MOCK_CONFTEST = '''\
"""Auto-generated conftest.py — mocks mycelos.sdk + common third-party libs.

mycelos.sdk.run() uses stdio JSON-RPC which doesn't work in pytest.
Third-party libraries that are NOT installed are mocked so agent code
can import and call them without errors. Libraries that ARE installed
(e.g., after user approved pip install) are used as-is.
"""
import sys
import importlib.util
from unittest.mock import MagicMock
import mycelos.sdk

# --- Mock mycelos.sdk ---
mycelos.sdk.run = MagicMock(side_effect=lambda tool, args=None: {
    "status": "ok",
    "content": "mocked content",
})
mycelos.sdk.progress = lambda text: None

# --- Mock third-party libraries ONLY if not installed ---
# If the user approved installing a package (e.g., pdfplumber),
# we use the real library so tests run against actual behavior.
_MOCK_MODULES = [
    "playwright", "playwright.sync_api", "playwright.async_api",
    "pdfplumber",
    "requests",
    "beautifulsoup4", "bs4",
    "selenium", "selenium.webdriver",
    "pdf2image",
    "pytesseract",
    "PIL", "PIL.Image",
    "httpx",
]
for _mod_name in _MOCK_MODULES:
    _base = _mod_name.split(".")[0]
    _installed = False
    try:
        _installed = importlib.util.find_spec(_base) is not None
    except (ValueError, ModuleNotFoundError):
        pass
    if not _installed and _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# --- Test fixture helpers ---
# Provides create_sample_pdf() so tests can create real PDF files
# without needing fpdf2, reportlab, or other PDF-generation libraries.
import pytest
import os

@pytest.fixture
def create_sample_pdf(tmp_path):
    """Create a minimal valid PDF file with text content.

    Usage in tests:
        def test_extract(create_sample_pdf):
            pdf_path = create_sample_pdf("Hello World", pages=1)
            # ... test extraction from pdf_path
    """
    def _create(text="Sample text content", pages=1, filename="test.pdf"):
        path = tmp_path / filename
        # Build a minimal valid PDF with embedded text
        objects = []
        # Object 1: Catalog
        objects.append("1 0 obj\\n<< /Type /Catalog /Pages 2 0 R >>\\nendobj")
        # Object 2: Pages
        page_refs = " ".join(f"{3 + i * 2} 0 R" for i in range(pages))
        objects.append(f"2 0 obj\\n<< /Type /Pages /Kids [{page_refs}] /Count {pages} >>\\nendobj")
        # For each page: Page object + Content stream
        obj_num = 3
        for i in range(pages):
            page_text = f"{text}\\nPage {i + 1}" if pages > 1 else text
            # Escape special PDF characters
            safe_text = page_text.replace("\\\\", "\\\\\\\\").replace("(", "\\\\(").replace(")", "\\\\)")
            stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET"
            stream_bytes = stream.encode("latin-1", errors="replace")
            # Content stream object
            content_obj = obj_num + 1
            objects.append(
                f"{obj_num} 0 obj\\n"
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_obj} 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>\\n"
                f"endobj"
            )
            objects.append(
                f"{content_obj} 0 obj\\n"
                f"<< /Length {len(stream_bytes)} >>\\n"
                f"stream\\n{stream}\\nendstream\\n"
                f"endobj"
            )
            obj_num += 2
        # Build the PDF
        pdf_parts = ["%PDF-1.4"]
        offsets = []
        for obj in objects:
            offsets.append(len("\\n".join(pdf_parts)) + 1)
            pdf_parts.append(obj)
        # Cross-reference table
        xref_offset = len("\\n".join(pdf_parts)) + 1
        pdf_parts.append("xref")
        pdf_parts.append(f"0 {len(objects) + 1}")
        pdf_parts.append("0000000000 65535 f ")
        for off in offsets:
            pdf_parts.append(f"{off:010d} 00000 n ")
        pdf_parts.append("trailer")
        pdf_parts.append(f"<< /Size {len(objects) + 1} /Root 1 0 R >>")
        pdf_parts.append("startxref")
        pdf_parts.append(str(xref_offset))
        pdf_parts.append("%%EOF")
        path.write_text("\\n".join(pdf_parts))
        return str(path)
    return _create
'''


@dataclass(frozen=True)
class TestResult:
    """Result of running agent tests."""

    passed: bool
    output: str  # stdout
    error: str  # stderr or failure details
    duration_ms: int  # execution time
    tests_run: int  # number of tests executed
    tests_failed: int  # number of tests that failed


def run_agent_tests(
    code: str,
    tests: str,
    timeout: int = 30,
    extra_files: dict[str, str] | None = None,
) -> TestResult:
    """Run pytest on generated agent code in an isolated temp directory.

    Creates a temp directory with:
    - agent_code.py (the generated code)
    - test_agent.py (the generated tests)
    - Any extra_files provided

    Args:
        code: The agent Python code.
        tests: The pytest test code.
        timeout: Maximum execution time in seconds.
        extra_files: Additional files to write (filename -> content).

    Returns:
        TestResult with pass/fail status and output.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Write agent code
        (tmp_path / "agent_code.py").write_text(code)

        # Write test file
        (tmp_path / "test_agent.py").write_text(tests)

        # Write conftest that auto-mocks mycelos.sdk (stdio-based IPC doesn't work in pytest)
        (tmp_path / "conftest.py").write_text(_SDK_MOCK_CONFTEST)

        # Write any extra files
        if extra_files:
            for filename, content in extra_files.items():
                (tmp_path / filename).write_text(content)

        # Run pytest
        start = time.time()
        try:
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "pytest",
                    "test_agent.py",
                    "-v",
                    "--tb=short",
                    "--no-header",
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                timeout=timeout,
                env=_safe_env(str(tmp_path)),
            )
            duration_ms = int((time.time() - start) * 1000)

            # Parse results
            tests_run, tests_failed = _parse_pytest_summary(result.stdout)

            return TestResult(
                passed=result.returncode == 0,
                output=result.stdout,
                error=result.stderr if result.returncode != 0 else "",
                duration_ms=duration_ms,
                tests_run=tests_run,
                tests_failed=tests_failed,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return TestResult(
                passed=False,
                output="",
                error=f"Tests timed out after {timeout}s",
                duration_ms=duration_ms,
                tests_run=0,
                tests_failed=0,
            )
        except FileNotFoundError:
            return TestResult(
                passed=False,
                output="",
                error="python3 not found",
                duration_ms=0,
                tests_run=0,
                tests_failed=0,
            )


def _safe_env(tmp_dir: str | None = None) -> dict[str, str]:
    """Create a minimal environment for test execution.

    Strips sensitive variables. Sets HOME to the temp directory so
    generated agent code cannot access ~/.mycelos or other user data.
    Excludes VIRTUAL_ENV to limit filesystem access.

    Note: Full network sandboxing requires container-level isolation
    (out of scope for process-level sandboxing).
    """
    import os

    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    # Set HOME to temp dir so agent code can't access ~/.mycelos
    env["HOME"] = tmp_dir or "/tmp"
    # Keep PYTHONPATH for mycelos imports but not VIRTUAL_ENV
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return env


def _parse_pytest_summary(output: str) -> tuple[int, int]:
    """Parse pytest output to extract test counts.

    Returns:
        Tuple of (tests_run, tests_failed).
    """
    import re

    passed = 0
    failed = 0

    passed_match = re.search(r"(\d+) passed", output)
    if passed_match:
        passed = int(passed_match.group(1))

    failed_match = re.search(r"(\d+) failed", output)
    if failed_match:
        failed = int(failed_match.group(1))

    return passed + failed, failed
