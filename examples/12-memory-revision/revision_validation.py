"""修订的三层证据：正常完成、实际 replace 回执、精确文件变化。"""
import json


def revision_checks(before, after, result):
    requested = set()
    for message in result.get("messages", []):
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                args = json.loads(function.get("arguments", "{}"))
            except (ValueError, TypeError):
                continue
            operations = args.get("operations", [args]) if isinstance(args, dict) else []
            if not isinstance(operations, list):
                operations = []
            if function.get("name") == "memory" and any(
                isinstance(operation, dict) and operation.get("action") == "replace"
                for operation in operations
            ):
                requested.add(call.get("id"))
    success = False
    for message in result.get("messages", []):
        if message.get("role") != "tool" or not message.get("tool_call_id") or message["tool_call_id"] not in requested:
            continue
        try:
            receipt = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        success |= isinstance(receipt, dict) and receipt.get("success") is True
    return {
        "turn_completed": result.get("completed") is True and not any(
            result.get(flag) for flag in ("failed", "partial", "interrupted"))
            and str(result.get("turn_exit_reason", "")).startswith("text_response("),
        "replace_receipt_succeeded": success,
        "exact_revision": "Redis 6.2" in before and after == before.replace("Redis 6.2", "Redis 7.2"),
    }
