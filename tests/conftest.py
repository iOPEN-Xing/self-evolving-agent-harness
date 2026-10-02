"""显式加载课程模块，不依赖 Hermes 或模型服务。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ONCALL = ROOT / "examples/23-final-assembly"
sys.path.insert(0, str(ONCALL))


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
