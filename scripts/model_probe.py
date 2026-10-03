#!/usr/bin/env python3
"""短连接探针；只保存状态、usage 与模型标识，不打印密钥或认证头。"""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness_engineering.model import MODEL, BASE_URL, api_key, chat_completion  # noqa: E402


def probe():
    key = api_key()
    rows = []
    for protocol in ("chat/completions", "responses"):
        started = time.monotonic()
        row = {"protocol": protocol, "requested_model": MODEL, "base_url": BASE_URL}
        try:
            if protocol == "chat/completions":
                result = chat_completion([{"role": "user", "content": "只回复 API_OK"}],
                                         timeout=30, max_tokens=64)
                text = result["choices"][0]["message"].get("content", "")
            else:
                body = {"model": MODEL, "input": "Reply API_OK only", "max_output_tokens": 128}
                req = urllib.request.Request(BASE_URL + "/responses", data=json.dumps(body).encode(),
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as response:
                    result = json.load(response)
                text = "".join(part.get("text", "") for item in result.get("output", [])
                               for part in item.get("content", []) if part.get("type") == "output_text")
                row["response_status"] = result.get("status")
            row.update(http_status=200, returned_model=result.get("model"), usage=result.get("usage"),
                       answer_matches=text.strip() == "API_OK")
            row["passed"] = row["answer_matches"] and (protocol != "responses" or result.get("status") == "completed")
        except urllib.error.HTTPError as exc:
            # 不写请求头、原始错误体或密钥；状态足够连接诊断。
            row.update(passed=False, http_status=exc.code, error_type=type(exc).__name__)
        except Exception as exc:
            row.update(passed=False, error_type=type(exc).__name__)
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        rows.append(row)
    return {"source": "real_official_api", "rows": rows, "passed": all(row["passed"] for row in rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = probe()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
