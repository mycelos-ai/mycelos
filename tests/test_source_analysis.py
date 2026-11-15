from types import SimpleNamespace
import pytest
from mycelos.knowledge.local_readers import SourceUnit
from mycelos.knowledge.source_analysis import summarize_unit


def test_long_source_is_reduced_to_one_summary_without_dropping_tail():
    calls = []
    class Broker:
        def complete(self, messages, model, purpose=None):
            calls.append(messages[-1]["content"])
            text = messages[-1]["content"]
            if "Combine section findings" in text:
                return SimpleNamespace(content="Final decision [Zeile 9]")
            return SimpleNamespace(content="Finding [Zeile 9]" if "TAIL" in text else "Earlier finding")
    unit = SourceUnit("s1", "Project", "A" * 24000 + "TAIL", ("session.jsonl",))
    result = summarize_unit(SimpleNamespace(llm=Broker()), unit, {"model": "test", "purpose": "decisions"})
    assert result == "Final decision [Zeile 9]"
    assert any("TAIL" in call for call in calls)
    assert "Finding [Zeile 9]" in calls[-1]


def test_empty_model_result_is_not_accepted():
    class Broker:
        def complete(self, *args, **kwargs):
            return SimpleNamespace(content="")
    with pytest.raises(ValueError, match="Empty"):
        summarize_unit(SimpleNamespace(llm=Broker()), SourceUnit("s", "X", "text", ()),
                       {"model": "test", "purpose": ""})
