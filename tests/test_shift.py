import pytest

from shift.runtime import build_review_input, run_shift


def turn(prompt, ref, dispatch, context, messages):
    dispatch("read_metrics", {"service": context["service"],
                             "logical_time": context["logical_time"], "scope": "overall"})
    return {"messages": [], "tool_calls": [{}], "response_text": "offline fixture",
            "stop_reason": "final_answer", "assessment": {
                "assessment": "recovered", "next_state": (
                    "VERIFYING" if context["state"] == "INVESTIGATING" else "PATROLLING")}}


def args(tmp_path, runner=turn):
    return dict(scenario_id="training", run_turn=runner, run_id="test", shift_id="shift",
                session_id="session", events_path=tmp_path / "transitions.jsonl")


def test_budget_enforced_before_each_read_even_if_adapter_undercounts(tmp_path):
    def greedy(prompt, ref, dispatch, context, messages):
        value = turn(prompt, ref, dispatch, context, messages)
        for _ in range(3):
            try:
                dispatch("read_metrics", {"service": context["service"],
                         "logical_time": context["logical_time"], "scope": "instances"})
            except ValueError:
                pass
        # 不信任适配器自行报告的调用数量和成功停止状态。
        value["tool_calls"] = []
        return value
    session, state = run_shift(**args(tmp_path, greedy), ticks=[5], investigation_budget=2)
    assert len(session["turns"][0]["observation_reads"]) == 2
    assert state["investigation_calls"] == 2
    assert session["budget_exhausted"] is True
    assert session["review_input"]["complete"] is False
    assert state["state"] == "INVESTIGATING"


@pytest.mark.parametrize("ticks", [[], [5, 5], [10, 5], [True], [-1]])
def test_invalid_clock_rejected_before_execution(tmp_path, ticks):
    with pytest.raises(ValueError):
        run_shift(**args(tmp_path), ticks=ticks)
    assert not (tmp_path / "transitions.jsonl").exists()


def test_handoff_cannot_replay_old_tick(tmp_path):
    _, state = run_shift(**args(tmp_path), ticks=[5])
    with pytest.raises(ValueError):
        run_shift(**args(tmp_path), ticks=[5], state=state, investigation_record=state["records"])


def test_incomplete_turn_cannot_confirm_recovery(tmp_path):
    def interrupted(*parameters):
        value = turn(*parameters)
        if parameters[3]["logical_time"] == 25:
            value["stop_reason"] = "max_iterations"
        return value
    session, state = run_shift(**args(tmp_path, interrupted), ticks=[5, 15, 20, 25])
    assert state["state"] == "VERIFYING"
    assert not state["service_recovered"]
    assert not session["review_input"]["complete"]


def test_empty_review_is_incomplete():
    state = dict(run_id="test", shift_id="s", records=[], transitions=[],
                 state="PATROLLING", sensor={})
    assert build_review_input([], state)["complete"] is False


def test_end_records_last_executed_tick_on_budget_stop(tmp_path):
    session, state = run_shift(**args(tmp_path), ticks=[0, 5], max_patrol_rounds=1, end_shift=True)
    assert session["budget_exhausted"]
    assert state["transitions"][-1]["logical_time"] == 0
