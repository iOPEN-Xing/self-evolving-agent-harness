"""技能整目录快照与恢复；清单保存在技能目录之外，不污染版本哈希。"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from .. import config
from ..contracts import Snapshot, tree_hashes, write_json


def _plain_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"需要真实目录：{root}")
    for item in root.rglob("*"):
        if item.is_symlink() or not (item.is_dir() or item.is_file()):
            raise ValueError(f"快照不接受符号链接或特殊文件：{item}")


def _adopted_path(path: Path) -> Path:
    """只恢复约定中的单个正式技能，禁止覆盖 output 或 adopted 根目录。"""
    path = Path(path).absolute()
    root = config.OUTPUT_DIR / "adopted"
    if root.is_symlink() or path.is_symlink():
        raise ValueError("正式技能目录不能是符号链接")
    if path.name in ("", ".", "..") or path.resolve().parent != root.resolve():
        raise ValueError("正式技能必须位于 output/adopted/<skill_name>/")
    return path.resolve()


def snapshot(adopted_dir: Path, label: str) -> Snapshot:
    """复制全部文件及子目录；相同标签拒绝覆盖，避免破坏可恢复版本。"""
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", label):
        raise ValueError("快照标签只能包含字母、数字、下划线、点和连字符")
    source = _adopted_path(adopted_dir)
    _plain_tree(source)
    root = config.OUTPUT_DIR / "snapshots"
    if root.is_symlink():
        raise ValueError("快照根目录不能是符号链接")
    root.mkdir(parents=True, exist_ok=True)
    target = root / label
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"快照标签已存在：{label}")
    before = tree_hashes(source)
    try:
        shutil.copytree(source, target)
        copied = tree_hashes(target)
        if copied != before or tree_hashes(source) != before:
            raise RuntimeError("复制期间正式目录发生变化，快照未成立")
    except BaseException:
        if target.is_dir():
            shutil.rmtree(target)
        raise
    result = Snapshot(label=label, snapshot_dir=str(target), manifest=copied)
    write_json(config.OUTPUT_DIR / "lifecycle" / "snapshots" / f"{label}.json", result)
    return result


def restore(saved: Snapshot, adopted_dir: Path) -> dict[str, str]:
    """先验快照，再整目录替换；多余文件会消失，切换失败时还原原目录。

    集成层须在没有并行写入该技能时调用。两次目录重命名之间不是事务，
    进程突然终止时可从同级 .restore-backup-* 目录恢复。
    """
    source = Path(saved.snapshot_dir).absolute()
    root = config.OUTPUT_DIR / "snapshots"
    if not isinstance(saved.label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", saved.label):
        raise ValueError("快照标签不合法")
    if (root.is_symlink() or source.name != saved.label
            or source.resolve().parent != root.resolve()):
        raise ValueError("只能恢复 output/snapshots/<label>/ 中的快照")
    _plain_tree(source)
    if tree_hashes(source) != saved.manifest:
        raise ValueError("快照文件与 manifest 不一致，正式目录未改动")
    target = _adopted_path(adopted_dir)
    if target.exists():
        _plain_tree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    stage = target.parent / f".restore-stage-{token}"
    backup = target.parent / f".restore-backup-{token}"
    moved_old = False
    installed = False
    try:
        shutil.copytree(source, stage)
        if tree_hashes(stage) != saved.manifest or tree_hashes(source) != saved.manifest:
            raise RuntimeError("恢复准备期间快照发生变化，正式目录未改动")
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(stage, target)
        installed = True
        manifest = tree_hashes(target)
        if manifest != saved.manifest:
            raise RuntimeError("恢复后校验失败")
    except BaseException:
        if installed and target.exists():
            shutil.rmtree(target)
        if moved_old:
            os.replace(backup, target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if backup.exists():
        shutil.rmtree(backup)
    write_json(config.OUTPUT_DIR / "lifecycle" / "snapshots" / f"{saved.label}-restore.json",
               {"snapshot": saved.to_dict(), "adopted_dir": str(target),
                "restored_manifest": manifest, "matched": manifest == saved.manifest})
    return manifest
