"""Web components and pages take their strings from the locale files.

Guards the collector-era leftovers: no hardcoded German in the converted
components, every key a component asks for exists in English and German,
and the server-side source messages exist in both languages too.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from mycelos.i18n.loader import get_web_translations

FRONTEND = Path(__file__).resolve().parents[1] / "src/mycelos/frontend"
SRC = Path(__file__).resolve().parents[1] / "src/mycelos"

# component file -> namespace below "web"
COMPONENTS = {
    "shared/local-sources.js": "local_sources",
    "shared/telegram-settings.js": "telegram_settings",
    "shared/knowledge-connectors.js": "knowledge_connectors",
    "shared/collectors.js": "collector_ui",
    "shared/knowledge-card.js": "knowledge_card",
    "shared/notifications.js": "notifications",
    "pages/settings.html": "settings_page",
}

# Keys built at runtime from an id ("T('kind_' + id)").
DYNAMIC = {
    "local_sources": [
        *(f"kind_{k}" for k in ("obsidian", "file", "git", "mercurial", "folder", "codex", "claude")),
        *(f"state_{k}" for k in ("ready", "queued", "running", "error", "paused")),
        *(f"link_{k}" for k in ("resolved", "attachment", "external", "excluded", "outside", "ambiguous", "missing")),
        *(f"relation_{k}" for k in ("related", "supports", "contradicts", "extends", "duplicate")),
        *(f"check_{k}" for k in ("idle", "queued", "running", "error")),
        *(f"change_{k}" for k in ("new", "changed", "removed", "reprocess")),
        *(f"interval_{k}" for k in (0, 15, 60, 360, 1440)),
    ],
    "collector_ui": [f"state_{k}" for k in ("active", "pending", "paused", "revoked")],
    "knowledge_connectors": [
        f"state_{k}" for k in ("healthy", "failing", "unknown", "ready", "setup_incomplete",
                               "inactive", "disabled", "pending")],
}

STATIC_PAGES = ["pages/collectors.html", "pages/settings.html", "pages/docs.html", "pages/about.html"]

GERMAN = re.compile(r"[äöüÄÖÜß]")


def _lookup(tree: dict, dotted: str):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


@pytest.mark.parametrize("rel", [*COMPONENTS, *STATIC_PAGES])
def test_no_hardcoded_german(rel):
    text = (FRONTEND / rel).read_text(encoding="utf-8")
    hits = [line.strip()[:80] for line in text.splitlines() if GERMAN.search(line)]
    assert hits == []


@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("rel,ns", list(COMPONENTS.items()))
def test_component_keys_exist(rel, ns, lang):
    web = get_web_translations(lang)
    text = (FRONTEND / rel).read_text(encoding="utf-8")
    used = set(re.findall(r"\bT\(\s*'([a-z0-9_]+)'(?!\s*\+)", text)) | set(DYNAMIC.get(ns, []))
    assert used, "component uses no translation keys"
    missing = sorted(key for key in used if _lookup(web, f"{ns}.{key}") is None)
    assert missing == []


@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("rel", STATIC_PAGES)
def test_static_page_keys_exist(rel, lang):
    web = get_web_translations(lang)
    text = (FRONTEND / rel).read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-aria|-title)?="([a-z0-9_.]+)"', text))
    assert used
    assert sorted(key for key in used if _lookup(web, key) is None) == []


def test_server_source_messages_exist_in_both_languages():
    import yaml
    locales = SRC / "i18n/locales"
    en = yaml.safe_load((locales / "en.yaml").read_text(encoding="utf-8"))["sources"]
    de = yaml.safe_load((locales / "de.yaml").read_text(encoding="utf-8"))["sources"]
    assert set(en) == set(de)
    used = set()
    for path in SRC.rglob("*.py"):
        used |= set(re.findall(r'_t\("sources\.([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    assert used and sorted(used - set(en)) == []


def test_server_messages_follow_the_language():
    from mycelos.i18n import set_language, t
    try:
        set_language("de")
        assert t("sources.not_found") == "Quelle nicht gefunden"
        set_language("en")
        assert t("sources.not_found") == "Source not found"
    finally:
        set_language("en")


def test_js_t_interpolates_placeholders():
    script = r"""
const fs = require('node:fs');
global.XMLHttpRequest = class { open(){} send(){ this.status = 200; this.responseText = JSON.stringify(
  {lang:'de', translations:{a:{b:'{count} Dateien · {missing}'}}}); } };
global.window = global;
global.document = {readyState:'complete', documentElement:{}, querySelectorAll(){ return []; }};
eval(fs.readFileSync(process.argv[1], 'utf8'));
if (t('a.b', {count: 3}) !== '3 Dateien · {missing}') throw new Error(t('a.b', {count: 3}));
if (t('a.b') !== '{count} Dateien · {missing}') throw new Error('plain');
if (t('a.x') !== 'a.x') throw new Error('missing key');
if (document.documentElement.lang !== 'de') throw new Error('lang');
console.log('ok');
"""
    result = subprocess.run(["node", "-e", script, str(FRONTEND / "shared/i18n.js")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
