"""Locale support for browser tests of the web components.

The components read their strings through ``window.t`` (``shared/i18n.js``),
which loads ``/api/i18n``. Browser tests serve the same web translations so
assertions can use the real German or English strings.
"""
from __future__ import annotations

import json

from mycelos.i18n import get_web_translations


def add_i18n_route(api, lang: str = "de") -> None:
    """Serve ``/api/i18n`` on a test FastAPI app (register before any "/" mount)."""

    @api.get("/api/i18n")
    def i18n():
        return {"lang": lang, "translations": get_web_translations(lang)}


def install_t(page, lang: str = "de") -> None:
    """Define ``window.t`` on an already loaded page, like ``i18n.js`` does.

    For tests that fulfil the host page through ``page.route`` and inject the
    component with ``add_script_tag``: call this before injecting it.
    """
    data = json.dumps(get_web_translations(lang), ensure_ascii=False)
    page.add_script_tag(content=f"""
      window._mycelos_lang = {json.dumps(lang)};
      window.t = function (key, vars) {{
        let node = {data};
        for (const part of key.split('.')) {{
          if (node == null || typeof node !== 'object') return key;
          node = node[part];
        }}
        if (typeof node !== 'string') return key;
        return vars ? node.replace(/\\{{(\\w+)\\}}/g, (m, name) => (name in vars ? String(vars[name]) : m)) : node;
      }};""")
