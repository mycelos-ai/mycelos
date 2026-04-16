"""Tests for Workflow YAML Parser."""
from pathlib import Path
from textwrap import dedent

import pytest

from mycelos.workflows.models import Workflow
from mycelos.workflows.parser import WorkflowParser


@pytest.fixture
def parser() -> WorkflowParser:
    return WorkflowParser()


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    f = tmp_path / "email.yaml"
    f.write_text(
        dedent("""\
        name: email-summary
        version: 1
        description: "Summarize emails"
        scope: [email.read]
        steps:
          - id: fetch
            action: "Fetch emails"
            agent: email-agent
            policy: always
          - id: summarize
            action: "Summarize"
            agent: summary-agent
            policy: always
            evaluation:
              format: markdown
    """)
    )
    return f


def test_parse_valid_yaml(parser: WorkflowParser, sample_yaml: Path) -> None:
    wf = parser.parse_file(sample_yaml)
    assert wf.name == "email-summary"
    assert len(wf.steps) == 2
    assert wf.scope == ["email.read"]


def test_parse_from_string(parser: WorkflowParser) -> None:
    wf = parser.parse_string(
        "name: simple\nsteps:\n  - id: s1\n    action: do\n    agent: a\n    policy: always"
    )
    assert wf.name == "simple"


def test_parse_missing_name_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="name"):
        parser.parse_string(
            "steps:\n  - id: s1\n    action: x\n    agent: a\n    policy: always"
        )


def test_parse_missing_steps_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="steps"):
        parser.parse_string("name: no-steps")


def test_parse_empty_steps_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="steps"):
        parser.parse_string("name: empty\nsteps: []")


def test_parse_step_missing_agent_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="agent"):
        parser.parse_string(
            "name: bad\nsteps:\n  - id: s1\n    action: x\n    policy: always"
        )


def test_parse_step_missing_policy_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="policy"):
        parser.parse_string(
            "name: bad\nsteps:\n  - id: s1\n    action: x\n    agent: a"
        )


def test_parse_step_missing_id_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="id"):
        parser.parse_string(
            "name: bad\nsteps:\n  - action: x\n    agent: a\n    policy: always"
        )


def test_parse_step_with_condition(parser: WorkflowParser) -> None:
    wf = parser.parse_string(
        dedent("""\
        name: cond
        steps:
          - id: s1
            action: check
            agent: a
            policy: always
          - id: s2
            action: process
            agent: b
            policy: always
            condition: "steps.s1.result.count > 0"
    """)
    )
    assert wf.steps[1].condition is not None


def test_parse_step_with_on_empty(parser: WorkflowParser) -> None:
    wf = parser.parse_string(
        "name: exit\nsteps:\n  - id: s1\n    action: check\n    agent: a\n    policy: always\n    on_empty: skip_remaining"
    )
    assert wf.steps[0].on_empty == "skip_remaining"


def test_load_directory(parser: WorkflowParser, tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        "name: a\nsteps:\n  - id: s1\n    action: x\n    agent: a1\n    policy: always"
    )
    (tmp_path / "b.yaml").write_text(
        "name: b\nsteps:\n  - id: s1\n    action: y\n    agent: b1\n    policy: always"
    )
    (tmp_path / "ignore.txt").write_text("not yaml")
    wfs = parser.load_directory(tmp_path)
    assert len(wfs) == 2
    assert {w.name for w in wfs} == {"a", "b"}


def test_parse_non_mapping_raises(parser: WorkflowParser) -> None:
    with pytest.raises(ValueError, match="mapping"):
        parser.parse_string("- item1\n- item2")


def test_parse_workflow_defaults(parser: WorkflowParser) -> None:
    wf = parser.parse_string(
        "name: minimal\nsteps:\n  - id: s1\n    agent: a\n    policy: always"
    )
    assert wf.description == ""
    assert wf.goal == ""
    assert wf.version == 1
    assert wf.scope == []
    assert wf.mcps == []
    assert wf.tags == []
    assert wf.metadata == {}


def test_parse_step_defaults(parser: WorkflowParser) -> None:
    wf = parser.parse_string(
        "name: defaults\nsteps:\n  - id: s1\n    agent: a\n    policy: always"
    )
    step = wf.steps[0]
    assert step.action == ""
    assert step.model_tier == "haiku"
    assert step.condition is None
    assert step.on_empty is None
    assert step.inputs == []
    assert step.outputs == []
    assert step.evaluation == {}
    assert step.max_cost is None
    assert step.notification == {}
