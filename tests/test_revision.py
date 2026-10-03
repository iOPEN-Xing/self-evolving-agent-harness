import copy
import json

from conftest import load_module

validate = load_module("revision_validation", "examples/12-memory-revision/revision_validation.py").revision_checks


def completed():
    return {"completed": True, "turn_exit_reason": "text_response(finish_reason=stop)", "messages": [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "memory",
            "arguments": json.dumps({"action": "replace"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'}]}


def test_revision_requires_all_three_layers():
    assert all(validate("Redis 6.2", "Redis 7.2", completed()).values())


def test_changed_file_and_summary_cannot_mask_budget_exhaustion():
    result = completed()
    result["turn_exit_reason"] = "max_iterations_reached(6/6)"
    assert not validate("Redis 6.2", "Redis 7.2", result)["turn_completed"]


def test_wrong_or_missing_receipt_is_not_success():
    for change in ("id", "failure", "missing"):
        result = copy.deepcopy(completed())
        if change == "id": result["messages"][1]["tool_call_id"] = "another-call"
        elif change == "failure": result["messages"][1]["content"] = '{"success": false}'
        else: result["messages"].pop()
        assert not validate("Redis 6.2", "Redis 7.2", result)["replace_receipt_succeeded"]


def test_other_entries_cannot_be_silently_changed():
    assert not validate("Redis 6.2\n§\nkeep", "Redis 7.2\n§\nchanged", completed())["exact_revision"]


def test_native_batch_replace_is_recognized():
    result = completed()
    result["messages"][0]["tool_calls"][0]["function"]["arguments"] = json.dumps(
        {"target": "memory", "operations": [{"action": "replace", "old_text": "Redis 6.2", "content": "Redis 7.2"}]})
    assert all(validate("Redis 6.2", "Redis 7.2", result).values())


def test_batch_add_does_not_count_as_replace():
    result = completed()
    result["messages"][0]["tool_calls"][0]["function"]["arguments"] = json.dumps(
        {"operations": [{"action": "add", "content": "Redis 7.2"}]})
    assert not validate("Redis 6.2", "Redis 7.2", result)["replace_receipt_succeeded"]
