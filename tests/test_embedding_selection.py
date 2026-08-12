from __future__ import annotations

import pytest

from mycelos.knowledge.embeddings import EUModeViolation, select_provider_name


def test_explicit_setting_wins() -> None:
    assert select_provider_name("local", False, True, True) == "local"
    assert select_provider_name("none", False, True, True) == "none"
    assert select_provider_name("openai", False, True, True) == "openai"


def test_explicit_openai_under_eu_mode_is_refused() -> None:
    with pytest.raises(EUModeViolation):
        select_provider_name("openai", True, True, True)


def test_explicit_local_without_model_falls_closed_to_none() -> None:
    # Asking for local when no model is installed must not download at
    # request time — it degrades.
    assert select_provider_name("local", False, False, False) == "none"


def test_eu_mode_prefers_local_never_openai() -> None:
    assert select_provider_name(None, True, True, True) == "local"
    assert select_provider_name(None, True, True, False) == "none"


def test_openai_only_with_real_credential() -> None:
    # The defect this closes: a proxy client existing is NOT a credential.
    assert select_provider_name(None, False, True, False) == "openai"
    assert select_provider_name(None, False, False, True) == "local"
    assert select_provider_name(None, False, False, False) == "none"


def test_credential_present_outranks_local() -> None:
    assert select_provider_name(None, False, True, True) == "openai"


def test_unknown_explicit_value_is_ignored_not_trusted() -> None:
    # Garbage in config must not select a provider by accident.
    assert select_provider_name("gpt9", False, False, True) == "local"
