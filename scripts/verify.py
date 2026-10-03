#!/usr/bin/env python3
"""编译所有已跟踪 Python/Notebook，检查 JSON/YAML/shell，再跑离线回归。"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import yaml
from IPython.core.inputtransformer2 import TransformerManager

ROOT = Path(__file__).resolve().parents[1]


def validate_files():
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                    cwd=ROOT).decode().split("\0")
    counts = dict(python=0, notebooks=0, notebook_cells=0, json=0, yaml=0, shell=0)
    transformer = TransformerManager()
    for name in sorted(set(filter(None, names))):
        path = ROOT / name
        text = None
        if path.suffix in {".py", ".json", ".yaml", ".yml", ".ipynb"}:
            text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            compile(text, name, "exec")
            counts["python"] += 1
        elif path.suffix == ".json":
            json.loads(text)
            counts["json"] += 1
        elif path.suffix in {".yaml", ".yml"}:
            list(yaml.safe_load_all(text))
            counts["yaml"] += 1
        elif path.suffix == ".ipynb":
            notebook = json.loads(text)
            if notebook.get("nbformat") != 4 or not isinstance(notebook.get("cells"), list):
                raise ValueError(f"Notebook 结构不合法: {name}")
            for index, cell in enumerate(notebook["cells"]):
                if cell.get("cell_type") == "code":
                    code = transformer.transform_cell("".join(cell["source"]))
                    compile(code, f"{name}:cell-{index}", "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                    counts["notebook_cells"] += 1
            counts["notebooks"] += 1
        elif path.suffix == ".sh":
            subprocess.run(["bash", "-n", str(path)], check=True)
            counts["shell"] += 1
    print("静态文件检查：" + json.dumps(counts, ensure_ascii=False), flush=True)


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("工程验证统一使用 Python 3.12")
    validate_files()
    subprocess.run([sys.executable, "-m", "ruff", "check", "."], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
