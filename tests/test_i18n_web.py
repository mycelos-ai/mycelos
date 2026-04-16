"""Tests for web UI translation extraction."""

from __future__ import annotations

from mycelos.i18n.loader import get_web_translations, reload_translations, set_language


def test_get_web_translations_returns_nested_dict():
    """get_web_translations() returns the web subtree as nested dict."""
    reload_translations()
    set_language("en")
    result = get_web_translations("en")

    # Must be a nested dict, not flattened
    assert isinstance(result, dict)
    assert "sidebar" in result
    assert isinstance(result["sidebar"], dict)
    assert "dashboard" in result["sidebar"]


def test_get_web_translations_german():
    """German translations return German strings."""
    reload_translations()
    set_language("de")
    result = get_web_translations("de")

    assert isinstance(result, dict)
    assert "sidebar" in result
    # German sidebar labels should differ from English for at least some keys
    en_result = get_web_translations("en")
    assert result["sidebar"]["knowledge"] != en_result["sidebar"]["knowledge"]


def test_get_web_translations_fallback_to_english():
    """Unknown language falls back to English."""
    reload_translations()
    result = get_web_translations("xx")

    assert isinstance(result, dict)
    assert "sidebar" in result
    assert result["sidebar"]["dashboard"] == "Dashboard"


def test_web_keys_parity_en_de():
    """en.yaml and de.yaml must have identical web.* key structures."""
    reload_translations()
    en = get_web_translations("en")
    de = get_web_translations("de")

    def collect_keys(d: dict, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys.update(collect_keys(v, full))
            else:
                keys.add(full)
        return keys

    en_keys = collect_keys(en)
    de_keys = collect_keys(de)

    missing_in_de = en_keys - de_keys
    missing_in_en = de_keys - en_keys

    assert not missing_in_de, f"Keys in EN but missing in DE: {missing_in_de}"
    assert not missing_in_en, f"Keys in DE but missing in EN: {missing_in_en}"
