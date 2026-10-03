"""连接契约离线验证；不把测试响应称为真实模型结果。"""
import io
import json

import pytest

from harness_engineering import model


def test_credentials_are_explicit(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("GLM_API_KEY", "unrelated-provider")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        model.api_key()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "  synthetic-key  ")
    assert model.api_key() == "synthetic-key"


def test_direct_environment_does_not_modify_caller():
    original = {"https_proxy": "http://proxy.invalid", "ALL_PROXY": "socks5://invalid",
                "DEEPSEEK_API_KEY": "synthetic-key", "PATH": "/usr/bin"}
    child = model.direct_environment(original)
    assert "https_proxy" not in child and "ALL_PROXY" not in child
    assert child["PATH"] == original["PATH"]
    assert "https_proxy" in original


def test_chat_uses_fixed_official_endpoint_and_nonthinking(monkeypatch):
    requests = []
    class FakeOpener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            return io.BytesIO(json.dumps({"model": "deepseek-flash", "choices": [
                {"message": {"role": "assistant", "content": "synthetic"}}]}).encode())
    monkeypatch.setattr(model.urllib.request, "build_opener", lambda *args: FakeOpener())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    response = model.chat_completion([{"role": "user", "content": "fixture"}], max_tokens=16)
    assert response["choices"][0]["message"]["content"] == "synthetic"
    request, timeout = requests[0]
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    payload = json.loads(request.data)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["model"] == "deepseek-flash"
    assert timeout == 120


def test_model_endpoint_cannot_be_overridden(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    with pytest.raises(ValueError):
        model.chat_completion([], model="other-model")
