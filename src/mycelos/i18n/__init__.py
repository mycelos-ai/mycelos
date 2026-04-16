"""Internationalization (i18n) — YAML-based translations for CLI output.

All user-facing CLI strings go through this module.
LLM prompts stay in English (the LLM handles multilingual responses).

Usage:
    from mycelos.i18n import t
    console.print(t("init.welcome"))
    console.print(t("init.provider_prompt", count=3))
"""

from mycelos.i18n.loader import t, set_language, get_language, get_web_translations

__all__ = ["t", "set_language", "get_language", "get_web_translations"]
