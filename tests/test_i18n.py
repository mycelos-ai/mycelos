"""Tests for i18n — YAML-based translations."""

from __future__ import annotations

import pytest

from mycelos.i18n.loader import (
    _flatten,
    get_language,
    reload_translations,
    set_language,
    t,
)


@pytest.fixture(autouse=True)
def reset_i18n():
    """Reset i18n state between tests."""
    reload_translations()
    set_language("en")
    yield
    reload_translations()
    set_language("en")


# --- Basic translation ---

def test_english_translation():
    set_language("en")
    assert t("init.welcome") == "Welcome to Mycelos!"


def test_german_translation():
    set_language("de")
    assert t("init.welcome") == "Willkommen bei Mycelos!"


def test_fallback_to_english():
    set_language("fr")  # No French file
    # Should fall back to English
    result = t("init.welcome")
    assert result == "Welcome to Mycelos!"


def test_missing_key_returns_key():
    assert t("nonexistent.key.xyz") == "nonexistent.key.xyz"


# --- Variable interpolation ---

def test_interpolation():
    set_language("en")
    result = t("common.not_initialized", path="/home/user/.mycelos")
    assert "/home/user/.mycelos" in result


def test_interpolation_german():
    set_language("de")
    result = t("common.not_initialized", path="/home/user/.mycelos")
    assert "/home/user/.mycelos" in result


def test_interpolation_missing_var():
    """Missing variables should not crash."""
    set_language("en")
    result = t("common.not_initialized")  # No path provided
    assert "not_initialized" not in result or "{path}" in result


# --- Language switching ---

def test_set_and_get_language():
    set_language("de")
    assert get_language() == "de"
    set_language("en")
    assert get_language() == "en"


def test_switch_language_mid_session():
    set_language("en")
    assert "Welcome" in t("init.welcome")
    set_language("de")
    assert "Willkommen" in t("init.welcome")


# --- Flatten ---

def test_flatten_simple():
    result = _flatten({"a": "1", "b": "2"})
    assert result == {"a": "1", "b": "2"}


def test_flatten_nested():
    result = _flatten({"init": {"welcome": "Hello", "bye": "Goodbye"}})
    assert result == {"init.welcome": "Hello", "init.bye": "Goodbye"}


def test_flatten_deep():
    result = _flatten({"a": {"b": {"c": "deep"}}})
    assert result == {"a.b.c": "deep"}


# --- All keys present in both languages ---

def test_all_english_keys_have_german():
    """Every English key should also exist in German."""
    reload_translations()
    set_language("en")
    en_keys = set()
    # Load English
    from mycelos.i18n.loader import _translations, _load_language
    _load_language("en")
    _load_language("de")
    en_keys = set(_translations.get("en", {}).keys())
    de_keys = set(_translations.get("de", {}).keys())
    missing = en_keys - de_keys
    assert missing == set(), f"Missing German translations: {missing}"


# --- Specific sections ---

def test_common_section():
    assert t("common.error") == "Error"
    set_language("de")
    assert t("common.error") == "Fehler"


def test_chat_section():
    assert "thinking" in t("chat.thinking").lower()
    set_language("de")
    assert "denkt" in t("chat.thinking").lower()


def test_config_section():
    result = t("config.rollback_success", id=5)
    assert "5" in result


def test_slash_section():
    # "key" is both the t() param name and a translation variable,
    # so we pass it via dict unpacking to avoid collision
    result = t("slash.agent_granted", cap="search.web", id="news-agent")
    assert "search.web" in result
    assert "news-agent" in result
