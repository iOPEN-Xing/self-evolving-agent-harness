"""课程默认模型的直连接口；导入时不读凭证、不请求网络。

Source: https://api-docs.deepseek.com/guides/thinking_mode/
Source: https://api-docs.deepseek.com/guides/tool_calls/
Chat 工具循环显式关闭 thinking，避免遗漏 reasoning_content 的续传；
Notebook 使用原生 Responses，保留其协议中的 reasoning 项。
"""
from __future__ import annotations

import json
import os
import urllib.request

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
PROVIDER = "deepseek"
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy")


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("请设置 DEEPSEEK_API_KEY 环境变量")
    return key


def direct_environment(environ=None) -> dict[str, str]:
    """构造直连子进程环境，不修改调用者，也不加载家目录中的密钥文件。"""
    env = dict(os.environ if environ is None else environ)
    for name in PROXY_KEYS:
        env.pop(name, None)
    return env


def chat_completion(messages, *, timeout=120, **parameters):
    """返回完整响应，保留模型/usage；调用方负责检查输出与业务结论。"""
    if "model" in parameters and parameters["model"] != MODEL:
        raise ValueError("本课程固定使用 deepseek-flash")
    payload = {"model": MODEL, "messages": messages,
               "thinking": {"type": "disabled"}, **parameters}
    request = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key(), "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)
