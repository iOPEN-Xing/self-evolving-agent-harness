#!/usr/bin/env python3
"""离线检查章节路径、稳定单元 ID、Python 符号及本仓库 Markdown 链接。"""
import ast
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def symbol_names(tree):
    """保留短名兼容旧契约，同时用 Class.method 区分同名方法的归属。"""
    names = set()

    def visit(node, scope=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            scope = (*scope, node.name)
            names.add(".".join(scope))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree)
    return names


def check_notebook_quotes(notebook, entry):
    """Python 教学摘录应是某个实际代码格的连续片段，缩进和空行可不同。"""
    def lines(source):
        return [line.strip() for line in "".join(source).splitlines() if line.strip()]

    code = [lines(cell.get("source", [])) for cell in notebook["cells"]
            if cell.get("cell_type") == "code"]
    for cell in notebook["cells"]:
        if cell.get("cell_type") != "markdown":
            continue
        for excerpt in re.findall(r"```python\n(.*?)```", "".join(cell.get("source", [])), re.DOTALL):
            quoted = lines([excerpt])
            if quoted and not any(
                actual[start:start + len(quoted)] == quoted
                for actual in code for start in range(len(actual) - len(quoted) + 1)
            ):
                raise ValueError(f"Notebook 代码摘录失配：{entry} → {cell.get('id')}")


def check_contracts(root=ROOT):
    contract = json.loads((root / "docs/chapters.json").read_text())
    chapters = contract["chapters"] + contract.get("guides", [])
    if len({row["id"] for row in chapters}) != len(chapters):
        raise ValueError("章节编号重复")
    for chapter in chapters:
        document = root / chapter["document"]
        if not document.is_file():
            raise ValueError(f"章节文档不存在：{document}")
        text = document.read_text()
        for entry in chapter["entrypoints"]:
            path = root / entry
            if not path.is_file():
                raise ValueError(f"代码入口不存在：{entry}")
            if path.suffix == ".ipynb":
                notebook = json.loads(path.read_text())
                check_notebook_quotes(notebook, entry)
                ids = {cell.get("id") for cell in notebook["cells"]}
                declared = set(chapter["notebook_cells"])
                mentioned = set(re.findall(r"`(lab\d\d-[a-zA-Z0-9-]+)`", text))
                if declared != mentioned or not declared <= ids:
                    raise ValueError(f"Notebook 单元定位失配：{entry}")
        for entry, expected in chapter["symbols"].items():
            tree = ast.parse((root / entry).read_text())
            names = symbol_names(tree)
            if not set(expected) <= names or any(f"`{name}`" not in text for name in expected):
                raise ValueError(f"文档与代码符号失配：{entry}")
    return len(chapters)


def check_links(root=ROOT):
    names = set(filter(None, subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root).decode().split("\0")))
    count = 0
    for name in sorted(names):
        path = root / name
        if path.suffix != ".md":
            continue
        for raw in re.findall(r"\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", path.read_text()):
            target = unquote(raw.strip("<>"))
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            local = (path.parent / parsed.path).resolve()
            if not local.exists():
                raise ValueError(f"失效本地链接：{name} → {raw}")
            if not local.is_relative_to(root.resolve()):
                raise ValueError(f"本地链接超出发布仓库：{name} → {raw}")
            relative = local.relative_to(root.resolve()).as_posix()
            included = relative in names if local.is_file() else (
                relative == "." or any(item.startswith(relative + "/") for item in names))
            if not included:
                raise ValueError(f"本地链接目标未随仓库发布：{name} → {raw}")
            count += 1
    return count


def main():
    chapters = check_contracts()
    links = check_links()
    print(f"文档检查：{chapters} 个文档契约，{links} 个本地链接")


if __name__ == "__main__":
    main()
