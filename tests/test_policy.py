"""评分契约的合成证据，仅用于单元测试，不能作为真实模型运行结果。"""
import copy
import hashlib
import json

import pytest

from eval.cases import EVAL_CASES
from eval.judge import judge_case, self_check
from shift.runtime import run_shift
from versioning.policy import complete_eval, decide


def reports(tmp_path, version, degraded=False):
    skill_hash = hashlib.sha256(version.encode()).hexdigest()
    rows = []
    for case in EVAL_CASES:
        def turn(prompt, ref, dispatch, context, messages):
            for query in case["assertions"]["actual_queries"]:
                name, _, scope = query.partition(":")
                arguments = dict(service=context["service"], logical_time=context["logical_time"])
                if scope:
                    arguments["scope"] = scope
                dispatch(name, arguments)
            bad = degraded and case["set"] == "new_incident"
            return dict(messages=[], response_text="synthetic contract test", tool_calls=[],
                        skill_sha256=skill_hash, stop_reason="final_answer", tool_errors=[],
                        assessment=dict(assessment="healthy" if bad else case["assertions"]["assessment"][0],
                          next_state="VERIFYING" if context["state"] == "INVESTIGATING" else context["state"]))
        session, state = run_shift(scenario_id=case["id"], ticks=[5, 10], run_turn=turn,
            run_id="unit-test", shift_id=case["id"], session_id=case["id"],
            events_path=tmp_path / version / (case["id"] + ".jsonl"))
        execution = dict(session=session, state=state)
        score = 1 if degraded and case["set"] == "new_incident" else 5
        row = judge_case(case, "synthetic contract test", lambda *a, **k: json.dumps(
            dict(score=score, reason="unit test")), execution=execution)
        row["execution"] = execution
        rows.append(row)
    return dict(version=version, results=rows, skill_sha256=skill_hash,
                case_set_sha256=hashlib.sha256(json.dumps(EVAL_CASES, sort_keys=True,
                    ensure_ascii=False).encode()).hexdigest())


def check():
    responses = iter(['{"score":1,"reason":"bad"}', '{"score":5,"reason":"good"}'])
    return self_check(lambda *a, **k: next(responses))


def test_complete_comparison_can_adopt_or_rollback(tmp_path):
    baseline, candidate = reports(tmp_path, "v0", True), reports(tmp_path, "v1")
    assert decide({}, self_check=check(), v0_eval=baseline, v1_eval=candidate)["decision"] == "ADOPT"
    unchanged_baseline = reports(tmp_path, "v0")
    assert decide({}, self_check=check(), v0_eval=unchanged_baseline, v1_eval=candidate)["decision"] == "ROLLBACK"


@pytest.mark.parametrize("bad", [None, [], {"turns": None}, {"turns": [None, None]}])
def test_malformed_execution_is_rejected_without_crashing(tmp_path, bad):
    value = reports(tmp_path, "v1")
    value["results"][0]["execution"]["session"] = bad
    assert complete_eval(value, "v1") is False


@pytest.mark.parametrize("mutation", ["hash", "fixture", "missing", "duplicate", "regrade"])
def test_untrusted_reports_rejected(tmp_path, mutation):
    baseline, candidate = reports(tmp_path, "v0", True), reports(tmp_path, "v1")
    if mutation == "hash":
        candidate["results"][0]["execution"]["session"]["turns"][0]["skill_sha256"] = "0" * 64
    elif mutation == "fixture":
        candidate["results"][0]["execution"]["fixture"] = True
    elif mutation == "missing":
        candidate["results"].pop()
    elif mutation == "duplicate":
        candidate["results"][1] = copy.deepcopy(candidate["results"][0])
    else:
        candidate["results"][0]["execution"]["session"]["turns"][0]["observation_reads"] = []
    assert decide({}, self_check=check(), v0_eval=baseline, v1_eval=candidate)["decision"] == "REJECT"


def test_reports_must_match_trusted_loaded_skill(tmp_path):
    baseline, candidate = reports(tmp_path, "v0", True), reports(tmp_path, "v1")
    result = decide({}, self_check=check(), v0_eval=baseline, v1_eval=candidate,
                    expected_skill_hashes={"v0": baseline["skill_sha256"], "v1": "0" * 64})
    assert result["decision"] == "REJECT"
