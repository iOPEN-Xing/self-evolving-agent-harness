"""测试本仓库工具循环；外部依赖只在导入边界替身，模型响应显式为夹具。"""
import json
import sys
from types import SimpleNamespace

import pytest

from conftest import load_module
from shift.runtime import run_shift


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_state", SimpleNamespace(SessionDB=object))
    monkeypatch.setitem(sys.modules, "skillclaw.skill_hub", SimpleNamespace(SkillHub=object))
    module = load_module("final_adapter", "examples/23-final-assembly/final_assembly.py")
    monkeypatch.setattr(module, "OUTPUT_DIR", tmp_path)
    return module


def call(name, arguments, identifier):
    return dict(id=identifier, type="function", function=dict(name=name, arguments=json.dumps(arguments)))


def report():
    return dict(assessment="suspected", report="synthetic fixture", next_state="VERIFYING",
                service_recovered=False, open_questions=[])


def test_import_does_not_load_credentials_or_start_jobs(adapter, tmp_path):
    assert adapter.API_KEY == ""
    assert not list(tmp_path.iterdir())


def test_budget_error_after_report_cannot_advance_state(adapter, tmp_path, monkeypatch):
    response = dict(content="", tool_calls=[
        call("read_metrics", dict(service="search-api", logical_time=5, scope="overall"), "metrics"),
        call("submit_report", report(), "report"), call("read_config", {}, "over-limit")])
    monkeypatch.setattr(adapter, "chat_completion", lambda payload: response)
    def run(prompt, ref, dispatch, context, messages):
        return adapter.ToolLoopAgent("skill", prompt, "test", max_tool_calls=2,
                tool_dispatch=dispatch, stage_context=context).run(messages)
    session, state = run_shift(scenario_id="training", ticks=[5], run_turn=run,
        run_id="test", shift_id="s", session_id="s1", events_path=tmp_path / "events.jsonl")
    assert session["turns"][0]["tool_errors"]
    assert not session["review_input"]["complete"]
    assert state["state"] == "INVESTIGATING"


def test_loaded_skill_mutation_is_a_tool_error(adapter, tmp_path, monkeypatch):
    path = tmp_path / "SKILL.md"
    path.write_text("changed")
    monkeypatch.setattr(adapter, "chat_completion", lambda payload: dict(content="", tool_calls=[
        call("read_skill", {}, "read"), call("submit_report", report(), "report")]))
    turn = adapter.ToolLoopAgent("original", "task", "test", skill_path=path).run()
    assert turn["tool_errors"]
    assert turn["stop_reason"] != "final_answer"


@pytest.mark.parametrize("missing", ["service_recovered", "open_questions", "report"])
def test_report_contract_rejects_missing_fields(adapter, missing):
    payload = report()
    del payload[missing]
    with pytest.raises(ValueError):
        adapter.parse_assessment(json.dumps(payload))
