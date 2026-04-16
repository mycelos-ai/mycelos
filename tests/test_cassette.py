from mycelos.llm.cassette import fingerprint_request


def test_fingerprint_is_deterministic():
    fp1 = fingerprint_request(
        model="anthropic/claude-haiku-4-5",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
    )
    fp2 = fingerprint_request(
        model="anthropic/claude-haiku-4-5",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
    )
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_fingerprint_changes_with_message_change():
    fp1 = fingerprint_request("m", [{"role": "user", "content": "hi"}], None)
    fp2 = fingerprint_request("m", [{"role": "user", "content": "bye"}], None)
    assert fp1 != fp2


def test_fingerprint_changes_with_model_change():
    fp1 = fingerprint_request("haiku", [{"role": "user", "content": "x"}], None)
    fp2 = fingerprint_request("opus", [{"role": "user", "content": "x"}], None)
    assert fp1 != fp2


def test_fingerprint_stable_across_dict_key_order():
    fp1 = fingerprint_request(
        "m", [{"role": "user", "content": "hi"}], None
    )
    # Same message dict, but constructed with keys in opposite order — Python
    # dicts preserve insertion order, so we simulate the canonicalization need.
    msg = {}
    msg["content"] = "hi"
    msg["role"] = "user"
    fp2 = fingerprint_request("m", [msg], None)
    assert fp1 == fp2


from mycelos.llm.cassette import Cassette
from mycelos.llm.broker import LLMResponse


def test_cassette_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "test_foo.json"
    c = Cassette(path)
    fp = "abc123"
    response = LLMResponse(
        content="hello world",
        total_tokens=42,
        model="anthropic/claude-haiku-4-5",
        tool_calls=None,
        cost=0.001,
    )
    c.put(fp, response)
    c.save()

    c2 = Cassette(path)
    c2.load()
    got = c2.get(fp)
    assert got is not None
    assert got.content == "hello world"
    assert got.total_tokens == 42
    assert got.model == "anthropic/claude-haiku-4-5"


def test_cassette_get_returns_none_for_unknown_fingerprint(tmp_path):
    c = Cassette(tmp_path / "empty.json")
    assert c.get("nonexistent") is None


def test_cassette_load_missing_file_is_empty(tmp_path):
    c = Cassette(tmp_path / "does_not_exist.json")
    c.load()  # must not raise
    assert c.get("anything") is None


def test_cassette_preserves_tool_calls(tmp_path):
    path = tmp_path / "tools.json"
    c = Cassette(path)
    response = LLMResponse(
        content="",
        total_tokens=10,
        model="m",
        tool_calls=[{"id": "t1", "name": "search", "arguments": {"q": "x"}}],
    )
    c.put("fp1", response)
    c.save()

    c2 = Cassette(path)
    c2.load()
    got = c2.get("fp1")
    assert got.tool_calls == [{"id": "t1", "name": "search", "arguments": {"q": "x"}}]


import pytest
from mycelos.llm.cassette import CassetteRecorder, CassetteMissError


def _fake_response():
    return LLMResponse(content="real api answer", total_tokens=5, model="m")


def test_replay_mode_returns_recorded_response(tmp_path):
    cassette_path = tmp_path / "case.json"
    pre = Cassette(cassette_path)
    pre.put(
        fingerprint_request("m", [{"role": "user", "content": "hi"}], None),
        LLMResponse(content="recorded", total_tokens=3, model="m"),
    )
    pre.save()

    rec = CassetteRecorder(cassette_path, mode="replay")

    def real_call():
        raise AssertionError("real_call must not be invoked in replay mode")

    response = rec.intercept(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        real_call=real_call,
    )
    assert response.content == "recorded"


def test_replay_mode_raises_on_miss(tmp_path):
    rec = CassetteRecorder(tmp_path / "missing.json", mode="replay")
    with pytest.raises(CassetteMissError):
        rec.intercept(
            model="m",
            messages=[{"role": "user", "content": "unknown"}],
            tools=None,
            real_call=lambda: _fake_response(),
        )


def test_record_mode_calls_real_and_writes(tmp_path):
    cassette_path = tmp_path / "case.json"
    rec = CassetteRecorder(cassette_path, mode="record")

    response = rec.intercept(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        real_call=_fake_response,
    )
    assert response.content == "real api answer"
    rec.flush()
    assert cassette_path.exists()

    rec2 = CassetteRecorder(cassette_path, mode="replay")
    replayed = rec2.intercept(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        real_call=lambda: (_ for _ in ()).throw(AssertionError("must not call")),
    )
    assert replayed.content == "real api answer"


def test_auto_mode_replays_when_present_records_when_missing(tmp_path):
    cassette_path = tmp_path / "case.json"
    rec = CassetteRecorder(cassette_path, mode="auto")

    rec.intercept(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        real_call=lambda: LLMResponse(content="first", total_tokens=1, model="m"),
    )
    rec.flush()

    rec2 = CassetteRecorder(cassette_path, mode="auto")
    response = rec2.intercept(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        real_call=lambda: (_ for _ in ()).throw(AssertionError("must not call")),
    )
    assert response.content == "first"


def test_broker_uses_recorder_in_replay_mode(tmp_path):
    """LLMBroker.complete consults the recorder before calling litellm."""
    from unittest.mock import patch
    from mycelos.llm.broker import LLMBroker

    cassette_path = tmp_path / "broker_test.json"
    pre = Cassette(cassette_path)
    pre.put(
        fingerprint_request(
            "anthropic/claude-haiku-4-5",
            [{"role": "user", "content": "hello"}],
            None,
        ),
        LLMResponse(content="cassette answer", total_tokens=2, model="m"),
    )
    pre.save()

    recorder = CassetteRecorder(cassette_path, mode="replay")
    broker = LLMBroker(
        default_model="anthropic/claude-haiku-4-5",
        recorder=recorder,
    )

    with patch("litellm.completion", side_effect=AssertionError("must not call")):
        response = broker.complete(
            messages=[{"role": "user", "content": "hello"}],
        )

    assert response.content == "cassette answer"
