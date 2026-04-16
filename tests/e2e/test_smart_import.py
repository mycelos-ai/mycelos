"""E2E: Smart Import modal + drag-drop + submit flow."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


def _make_zip(tmp_path: Path, name: str, entries: dict[str, str]) -> Path:
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        for relpath, body in entries.items():
            zf.writestr(relpath, body)
    return zip_path


@pytest.mark.e2e
def test_import_button_opens_modal(page: Page, base_url: str) -> None:
    """Clicking the Import button opens the Smart Import modal."""
    page.goto(f"{base_url}/pages/knowledge.html", wait_until="networkidle")
    page.wait_for_timeout(500)

    page.locator('[data-testid="smart-import-open"]').click()

    modal = page.locator('[data-testid="smart-import-modal"]')
    expect(modal).to_be_visible(timeout=2000)


@pytest.mark.e2e
def test_import_preserve_zip(
    page: Page, base_url: str, tmp_path: Path
) -> None:
    """Upload a 3-folder zip and verify the success message mentions count."""
    zip_path = _make_zip(
        tmp_path,
        "vault.zip",
        {
            "journal/a.md": "# A\nbody",
            "projects/b.md": "# B\nbody",
            "recipes/c.md": "# C\nbody",
        },
    )

    page.goto(f"{base_url}/pages/knowledge.html", wait_until="networkidle")
    page.wait_for_timeout(500)

    page.locator('[data-testid="smart-import-open"]').click()
    modal = page.locator('[data-testid="smart-import-modal"]')
    expect(modal).to_be_visible(timeout=2000)

    # Reveal options panel, then switch to preserve mode (no LLM calls needed)
    modal.locator("a").first.click()
    page.locator('input[type="radio"][value="preserve"]').check()

    file_input = modal.locator('input[type="file"]')
    file_input.set_input_files(str(zip_path))

    page.locator('[data-testid="smart-import-submit"]').click()

    # Result line includes the imported note count ("3")
    expect(modal).to_contain_text("3", timeout=10000)


@pytest.mark.e2e
def test_import_missing_file_submit_is_disabled(
    page: Page, base_url: str
) -> None:
    """The submit button should be disabled until a file is chosen."""
    page.goto(f"{base_url}/pages/knowledge.html", wait_until="networkidle")
    page.wait_for_timeout(500)

    page.locator('[data-testid="smart-import-open"]').click()
    expect(page.locator('[data-testid="smart-import-modal"]')).to_be_visible(
        timeout=2000
    )

    submit = page.locator('[data-testid="smart-import-submit"]')
    expect(submit).to_be_disabled()
