import subprocess

import pytest
import yaml

from conftest import ROOT, load_module


@pytest.fixture
def suite(tmp_path, monkeypatch):
    module = load_module("enterprise_suite", "examples/19-enterprise-dataset/scripts/run_suite.py")
    root, out = tmp_path / "course", tmp_path / "run"
    files = ["manifest.json", "skill/payment-report/SKILL.md", "scripts/legacy_service.py",
             "scripts/hermes_worker.py", "scripts/grader.py", "scripts/isolation.py", "bin/skill-up"]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    (root / "evals").mkdir()
    for variant in ("legacy", "hermes"):
        source = ROOT / "examples/19-enterprise-dataset/evals" / f"eval.demo.{variant}.yaml"
        (root / "evals" / source.name).write_bytes(source.read_bytes())
    (root / "skill-up.config.yaml").write_text("personal: keep-me\n")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "OUT", out)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
    monkeypatch.setattr(module.subprocess, "run", run)
    return module, root, out, calls


def test_native_configuration_is_private_to_each_run(suite):
    module, root, out, calls = suite
    module.main()
    assert (root / "skill-up.config.yaml").read_text() == "personal: keep-me\n"
    assert not list((root / "evals").glob("eval.runtime.*"))
    for command in calls[1:]:
        config = command[command.index("--config") + 1]
        assert str(out) in config
    for variant in ("legacy", "hermes"):
        materialized = yaml.safe_load((out / f"eval.runtime.{variant}.yaml").read_text())
        assert materialized["skills"][0]["path"] == str(root / "skill/payment-report")
        assert all(path.startswith(str(root)) for path in materialized["cases"]["files"])


def test_run_history_cannot_be_overwritten(suite):
    module, _, _, calls = suite
    module.main()
    prior = len(calls)
    with pytest.raises(SystemExit):
        module.main()
    assert len(calls) == prior
