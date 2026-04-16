"""Translation loader — reads YAML files and provides t() function.

Translations live in src/mycelos/i18n/locales/{lang}.yaml.
Keys are dot-separated paths: "init.welcome", "config.show.title".
Supports {variable} interpolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_LOCALES_DIR = Path(__file__).parent / "locales"
_current_language: str = "en"
_translations: dict[str, dict[str, str]] = {}


def set_language(lang: str) -> None:
    """Set the active language. Loads translations if not cached."""
    global _current_language
    _current_language = lang
    if lang not in _translations:
        _load_language(lang)


def get_language() -> str:
    """Get the current active language."""
    return _current_language


def t(key: str, **kwargs: Any) -> str:
    """Translate a key to the current language.

    Args:
        key: Dot-separated translation key (e.g., "init.welcome").
        **kwargs: Variables for interpolation (e.g., count=3).

    Returns:
        Translated string, or the key itself if not found.
    """
    # Ensure current language is loaded
    if _current_language not in _translations:
        _load_language(_current_language)

    translations = _translations.get(_current_language, {})
    text = translations.get(key)

    if text is None:
        # Fallback to English
        if "en" not in _translations:
            _load_language("en")
        text = _translations.get("en", {}).get(key)

    if text is None:
        return key  # Key as fallback — makes missing translations visible

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass  # Return unformatted if interpolation fails

    return text


def get_web_translations(lang: str) -> dict[str, Any]:
    """Return the 'web' subtree as a nested dict for the given language.

    Used by the /api/i18n endpoint. Falls back to English if the
    requested language has no 'web' section.
    """
    yaml_path = _LOCALES_DIR / f"{lang}.yaml"
    if not yaml_path.exists():
        yaml_path = _LOCALES_DIR / "en.yaml"

    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "web" in data:
            return data["web"]
    except Exception:
        pass

    # Fallback: read English
    en_path = _LOCALES_DIR / "en.yaml"
    try:
        data = yaml.safe_load(en_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "web" in data:
            return data["web"]
    except Exception:
        pass

    return {}


def _load_language(lang: str) -> None:
    """Load a language YAML file into the cache."""
    yaml_path = _LOCALES_DIR / f"{lang}.yaml"
    if not yaml_path.exists():
        _translations[lang] = {}
        return

    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            _translations[lang] = {}
            return
        # Flatten nested dict: {"init": {"welcome": "Hello"}} → {"init.welcome": "Hello"}
        _translations[lang] = _flatten(data)
    except Exception:
        _translations[lang] = {}


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into dot-separated keys."""
    result: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, full_key))
        elif isinstance(value, str):
            result[full_key] = value
        else:
            result[full_key] = str(value)
    return result


def reload_translations() -> None:
    """Clear cache and reload. Useful for testing."""
    global _translations
    _translations = {}
