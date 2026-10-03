#!/usr/bin/env python3
"""离线检查章节路径、稳定单元 ID、Python 符号及本仓库 Markdown 链接。"""
import ast
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def check_contracts(root=ROOT):
    contract = json.loads((root / "docs/chapters.json").read_text())
    chapters = contract["chapters"]
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
                ids = {cell.get("id") for cell in json.loads(path.read_text())["cells"]}
                declared = set(chapter["notebook_cells"])
                mentioned = set(re.findall(r"`(lab\d\d-[a-zA-Z0-9-]+)`", text))
                if declared != mentioned or not declared <= ids:
                    raise ValueError(f"Notebook 单元定位失配：{entry}")
        for entry, expected in chapter["symbols"].items():
            tree = ast.parse((root / entry).read_text())
            names = {node.name for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            if not set(expected) <= names or any(f"`{name}`" not in text for name in expected):
                raise ValueError(f"文档与代码符号失配：{entry}")
    return len(chapters)


def check_links(root=ROOT):
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                    cwd=root).decode().split("\0")
    count = 0
    for name in sorted(set(filter(None, names))):
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
            count += 1
    return count


def main():
    chapters = check_contracts()
    links = check_links()
    print(f"文档检查：{chapters} 个章节契约，{links} 个本地链接")


if __name__ == "__main__":
    main()
