import json

import pytest

from scripts.check_docs import check_contracts, check_links


def fixture(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "lesson.md").write_text("Use `run` and `lab01-reader-05`. [code](lesson.py)")
    (tmp_path / "lesson.py").write_text("def run(): pass\n")
    (tmp_path / "lesson.ipynb").write_text(json.dumps({"cells": [{"id": "lab01-reader-05"}]}))
    (tmp_path / "docs/chapters.json").write_text(json.dumps({"chapters": [
        {"id": "lesson", "document": "lesson.md", "entrypoints": ["lesson.py", "lesson.ipynb"],
         "symbols": {"lesson.py": ["run"]}, "notebook_cells": ["lab01-reader-05"]}]}))
    return tmp_path


def test_notebook_cell_rename_requires_document_update(tmp_path):
    root = fixture(tmp_path)
    assert check_contracts(root) == 1
    (root / "lesson.ipynb").write_text('{"cells": [{"id": "new-id"}]}')
    with pytest.raises(ValueError, match="Notebook"):
        check_contracts(root)


def test_deleted_symbol_cannot_leave_stale_reading_route(tmp_path):
    root = fixture(tmp_path)
    (root / "lesson.py").write_text("def renamed(): pass\n")
    with pytest.raises(ValueError, match="符号"):
        check_contracts(root)


def test_missing_local_reference_is_reported(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    monkeypatch.setattr("scripts.check_docs.subprocess.check_output", lambda *a, **k: b"lesson.md\0")
    assert check_links(root) == 1
    (root / "lesson.py").unlink()
    with pytest.raises(ValueError, match="链接"):
        check_links(root)
