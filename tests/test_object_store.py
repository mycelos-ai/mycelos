"""Tests for content-addressed Object Store."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from mycelos.storage.object_store import ObjectStore


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path)


def test_store_returns_sha256_hash(store: ObjectStore):
    content = "print('hello world')"
    h = store.store(content)
    expected = hashlib.sha256(content.encode()).hexdigest()
    assert h == expected


def test_load_returns_stored_content(store: ObjectStore):
    content = "def foo(): return 42"
    h = store.store(content)
    assert store.load(h) == content


def test_store_is_idempotent(store: ObjectStore):
    content = "same content"
    h1 = store.store(content)
    h2 = store.store(content)
    assert h1 == h2


def test_load_missing_returns_none(store: ObjectStore):
    assert store.load("nonexistent_hash") is None


def test_exists_true_for_stored(store: ObjectStore):
    h = store.store("some code")
    assert store.exists(h) is True


def test_exists_false_for_missing(store: ObjectStore):
    assert store.exists("no_such_hash") is False


def test_list_objects_empty(store: ObjectStore):
    assert store.list_objects() == []


def test_list_objects_returns_hashes(store: ObjectStore):
    h1 = store.store("content 1")
    h2 = store.store("content 2")
    objects = store.list_objects()
    assert set(objects) == {h1, h2}


def test_store_creates_directory(tmp_path: Path):
    store = ObjectStore(tmp_path / "deep" / "nested")
    h = store.store("test")
    assert store.load(h) == "test"


def test_unicode_content(store: ObjectStore):
    content = "def gruesse(): return 'Halloe Woerld'"
    h = store.store(content)
    assert store.load(h) == content
