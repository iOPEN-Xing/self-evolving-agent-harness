"""第 19 讲：Skill 变更溯源与版本恢复，会话历史备份仅作补充。

从仓库根目录运行：
    .deps/hermes-agent/.venv/bin/python examples/19-snapshot-restore/snapshot_restore.py
需要环境变量 GLM_API_KEY 或 BIGMODEL_API_KEY；运行时会调用 3 次 glm-5.2。
"""

import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from openai import OpenAI

# 按本讲要求直连，在任何网络调用之前清理代理。
for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(k, None)

REPO_ROOT = Path(__file__).resolve().parents[2]
HERMES_SRC = Path(
    os.environ.get("HERMES_SRC") or REPO_ROOT / ".deps" / "hermes-agent"
).expanduser().resolve()
HERMES_ROOT = HERMES_SRC
sys.path.insert(0, str(HERMES_ROOT))
from hermes_state import SessionDB  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
SKILL_NAME = "oncall-triage"
SNAPSHOT_LABEL = "受理成功不等于恢复"
SKILL_V1 = """---
name: oncall-triage
description: 核查服务告警，区分处置请求已受理与服务恢复已确认。
---

# On-call 告警核查

适用于 search-api 的只读告警调查与恢复复查。

1. 从告警找到处置记录和重新加载请求号，核对服务、实例与观测窗口。
2. 查询处置后的监控观测。控制端返回受理成功或 202 Accepted 只说明请求被接收，不等于服务恢复；在拿到满足恢复条件的观测前，不得认定已恢复。
3. 若处置请求仅受理而恢复未确认，结论首行必须是 VERDICT=INDETERMINATE，并写明需要继续复查监控观测。
4. 监控观测确认满足恢复条件后，结论首行才是 VERDICT=SUCCESS，报告已恢复。

字段含义见 references/oncall-records.md。本技能只用于调查，不执行重启或修改配置。
"""
REFERENCE = """# 值守记录字段

- 告警号：监控系统记录本次告警的唯一标识。
- 处置请求号：控制端分配的请求标识，用于查询重新加载进度。
- 服务、实例与观测窗口：应与告警、处置记录及监控观测逐项核对。
- 受理态：控制端已接收本次重新加载请求，返回 202 Accepted。
- 恢复确认：监控观测已满足本次告警的恢复条件。
"""
OLD_STRING = (
    "2. 查询处置后的监控观测。控制端返回受理成功或 202 Accepted 只说明请求被接收，不等于服务恢复；在拿到满足恢复条件的观测前，不得认定已恢复。\n"
    "3. 若处置请求仅受理而恢复未确认，结论首行必须是 VERDICT=INDETERMINATE，并写明需要继续复查监控观测。"
)
NEW_STRING = (
    "2. 控制端返回受理成功即说明服务恢复，可直接判定已恢复，无需再查监控观测。\n"
    "3. 只要处置请求受理成功，结论首行就是 VERDICT=SUCCESS，直接报告已恢复。"
)
PROBE = (
    "告警 INC-2026-09：search-api 的重新加载请求刚返回 202 Accepted（已受理），但监控观测尚未确认满足恢复条件。"
    "处置记录存在，服务、实例与观测窗口一致。请按你的 Skill 给出核查结论。"
)


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_skill(skill_dir: Path, snapshot_dir: Path) -> dict:
    """先复制整个目录，再为其中所有文件建立清单；清单不计算自身哈希。"""
    shutil.copytree(skill_dir, snapshot_dir)
    manifest = {
        "version": "v1",
        "label": SNAPSHOT_LABEL,
        "files": {
            path.relative_to(snapshot_dir).as_posix(): sha256(path)
            for path in sorted(snapshot_dir.rglob("*")) if path.is_file()
        },
    }
    write_json(snapshot_dir / "manifest.json", manifest)
    write_json(OUTPUT_DIR / "snapshot_manifest.json", manifest)
    return manifest


def restore_skill(skill_dir: Path, snapshot_dir: Path) -> None:
    """按选定版本清单恢复 Skill 文件，不打开也不修改 SessionDB。"""
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    # 先核对快照，避免把损坏的快照写回当前目录。
    for relative, expected in files.items():
        if sha256(snapshot_dir / relative) != expected:
            raise RuntimeError(f"快照文件哈希不一致：{relative}")
    for path in sorted(skill_dir.rglob("*"), reverse=True):
        relative = path.relative_to(skill_dir).as_posix()
        if path.is_file() and relative not in files:
            path.unlink()
            print(f"删除当前 Skill 中的多余文件：{relative}")
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for relative, expected in files.items():
        target = skill_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_dir / relative, target)
        if sha256(target) != expected:
            raise RuntimeError(f"恢复后文件哈希不一致：{relative}")
        print(f"恢复并核对 SHA-256：{relative}")


def classify_answer(answer: str) -> str:
    """仅按本练习约定的标记判断；缺失或同时出现 2 个标记均无法判定。"""
    old = "VERDICT=INDETERMINATE" in answer
    broken = "VERDICT=SUCCESS" in answer
    if old and not broken:
        return "INDETERMINATE"
    if broken and not old:
        return "SUCCESS"
    return "UNKNOWN"


def run_probe(client: OpenAI, skill_dir: Path, filename: str) -> str:
    system_prompt = (
        "你是 On-call 值守助手，必须严格遵守下面这份 Skill，结论首行按 Skill 要求输出 VERDICT 标记。\n\n"
        + (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        + "\n\n"
        + (skill_dir / "references/oncall-records.md").read_text(encoding="utf-8")
    )
    # 每次新建 messages，只使用本次读取的 Skill，不带任何上一轮对话。
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": PROBE},
    ]
    response = client.chat.completions.create(
        model="glm-5.2", messages=messages, temperature=0, max_tokens=1024,
        extra_body={"reasoning_effort": "low"},
    )
    answer = (response.choices[0].message.content or "") if response.choices else ""
    (OUTPUT_DIR / filename).write_text(answer, encoding="utf-8")
    verdict = classify_answer(answer)
    print("模型原始回答：")
    print(answer)
    labels = {
        "INDETERMINATE": "旧行为（正确）：INDETERMINATE",
        "SUCCESS": "错误行为：SUCCESS",
        "UNKNOWN": "无法判定（缺少标记或标记冲突）",
    }
    print(f"判定：{labels[verdict]}")
    return verdict


def seed_sessions(db: SessionDB) -> dict[str, int]:
    """写入 2 个演示会话，各 2 条消息，并记录各自的消息数。"""
    counts = {}
    for sid, title in (("lab19-A", "告警状态核查"), ("lab19-B", "恢复观测跟进")):
        db.create_session(sid, source="lab", model="glm-5.2")
        db.set_session_title(sid, title)
        db.append_message(sid, "user", content=f"请协助{title}。")
        db.append_message(sid, "assistant", content="已记录调查请求，等待恢复观测。")
        counts[sid] = len(db.get_messages(sid))
    return counts


def history_fields(sessions: list[dict]) -> dict:
    """只比对本补充实验选定的会话字段和消息字段。"""
    return {
        row["id"]: {
            **{key: row[key] for key in ("id", "source", "title", "model", "started_at")},
            "messages": [
                {key: message[key] for key in ("role", "content", "timestamp")}
                for message in row["messages"]
            ],
        }
        for row in sessions
    }


def sessiondb_backup_demo() -> None:
    section("补充实验：会话历史备份（不是本讲主例）")
    backup_home = Path(tempfile.mkdtemp(prefix="lesson19-sessiondb-"))
    db_path = backup_home / "history.db"
    print(f"补充实验临时数据库：{db_path}")
    db = SessionDB(db_path=db_path)
    try:
        seed_sessions(db)
        exported = db.export_all()
        write_json(OUTPUT_DIR / "sessiondb_backup.json", exported)
    finally:
        db.close()
    print("export_all() 导出会话行字段（含 id/source/model/title/started_at 等）和消息字段（含 role/content/timestamp 等），不是整库备份。")
    print("本地 Hermes 版本还导出 last_activity_* 活动字段；import_sessions() 会将这些运行时字段重置为 NULL。")
    print("import_sessions() 将会话历史导入库中，已有会话 ID 会跳过（skipped），不会让整库回到某一时刻。")
    print("要用导出历史重新建立当时的会话集合，需要先清空目标库，再导入空库；未导出或被重置的状态不会恢复。")
    # 这里只清空补充实验的临时库，绝不触碰主例的 state.db。
    db_path.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    db = SessionDB(db_path=db_path)
    try:
        assert not db.search_sessions(), "目标应为空库"
        result = db.import_sessions(exported)
        print("空库导入结果：", {key: result[key] for key in ("ok", "imported", "skipped", "detached")})
        assert result["ok"] and result["imported"] == 2 and result["skipped"] == 0
        assert history_fields(db.export_all()) == history_fields(exported), "选定导出字段不一致"
        repeated = db.import_sessions(exported)
        print("再次导入结果：", {key: repeated[key] for key in ("ok", "imported", "skipped", "detached")})
        assert repeated["ok"] and repeated["imported"] == 0 and repeated["skipped"] == 2
        for row in db.search_sessions():
            for key in ("last_activity_at", "last_activity_description", "last_activity_provenance"):
                assert row[key] is None, f"运行时字段未重置：{key}"
    finally:
        db.close()
    print("导出字段在空库导入后可复原；已有 ID 会被跳过，运行时字段被重置")


def main() -> None:
    api_key = os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY")
    if not api_key:
        sys.exit("缺少 API key：请先设置环境变量 GLM_API_KEY 或 BIGMODEL_API_KEY。")
    OUTPUT_DIR.mkdir(exist_ok=True)
    LAB_HOME = Path(tempfile.mkdtemp(prefix="lesson19-skill-"))
    SKILLS_DIR = LAB_HOME / "skills"
    SNAPSHOTS_DIR = LAB_HOME / "snapshots"
    skill_dir = SKILLS_DIR / SKILL_NAME
    snapshot_dir = SNAPSHOTS_DIR / f"v1-{SNAPSHOT_LABEL}"
    print(f"主例临时目录：{LAB_HOME}")
    print(f"产物目录：{OUTPUT_DIR}")
    client = OpenAI(api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4")
    try:
        section("步骤 1：构造完整 Skill 目录")
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SKILL_V1, encoding="utf-8")
        (skill_dir / "references/oncall-records.md").write_text(REFERENCE, encoding="utf-8")

        section("步骤 2：保存变更前完整目录快照 v1")
        manifest = snapshot_skill(skill_dir, snapshot_dir)
        print(f"快照：{snapshot_dir}")
        print("快照覆盖整个 Skill 目录（SKILL.md 与 references/），不是 state.db。")
        print(json.dumps(manifest, ensure_ascii=False, indent=2))

        section("步骤 3：旧版行为探针（基线，新会话）")
        baseline = run_probe(client, skill_dir, "probe_v1_old.txt")

        section("步骤 4：确定性改坏 Skill，生成 v2")
        skill_path = skill_dir / "SKILL.md"
        before = skill_path.read_text(encoding="utf-8")
        if before.count(OLD_STRING) != 1:
            raise RuntimeError("old_string 未唯一匹配 SKILL.md，停止改动。")
        skill_path.write_text(before.replace(OLD_STRING, NEW_STRING, 1), encoding="utf-8")

        section("步骤 5：查询 v1 与 v2 的差异")
        diff = "".join(difflib.unified_diff(
            (snapshot_dir / "SKILL.md").read_text(encoding="utf-8").splitlines(keepends=True),
            skill_path.read_text(encoding="utf-8").splitlines(keepends=True),
            fromfile="v1_before", tofile="v2_after",
        ))
        print(diff, end="")
        (OUTPUT_DIR / "skill_diff.txt").write_text(diff, encoding="utf-8")
        shutil.copy2(snapshot_dir / "SKILL.md", OUTPUT_DIR / "v1_SKILL.md")
        shutil.copy2(skill_path, OUTPUT_DIR / "v2_SKILL.md")

        section("步骤 6：退化版探针（新会话）")
        broken = run_probe(client, skill_dir, "probe_v2_broken.txt")
        regression_observed = baseline == "INDETERMINATE" and broken == "SUCCESS"
        print("已观测到退化" if regression_observed else "尚未确认退化，请查看 2 次原始回答。")

        # 步骤 9 的前半部分必须在恢复之前完成，这些记录晚于 v1 快照。
        db_path = LAB_HOME / "state.db"
        db = SessionDB(db_path=db_path)
        try:
            counts_before = seed_sessions(db)
            total_before = sum(counts_before.values())
        finally:
            db.close()
        print(f"恢复前写入 SessionDB：{counts_before}，消息总数：{total_before}")

        section("步骤 7：取回选定的 v1，逐文件核对 SHA-256")
        restore_skill(skill_dir, snapshot_dir)
        shutil.copy2(skill_path, OUTPUT_DIR / "restored_SKILL.md")

        section("步骤 8：新会话验证旧能力恢复")
        restored = run_probe(client, skill_dir, "probe_restored.txt")
        if regression_observed and restored == "INDETERMINATE":
            print("旧行为已恢复")
        else:
            print("Skill 文件已恢复，但本次探针尚未完整验证退化后旧行为恢复，请查看原始回答。")

        section("步骤 9：核对 Skill 回退后会话记录仍在")
        db = SessionDB(db_path=db_path)
        try:
            sessions = db.search_sessions(limit=100)
            ids_after = {row["id"] for row in sessions}
            assert len(sessions) == 2 and ids_after == set(counts_before), "会话 ID 或数量改变"
            counts_after = {sid: len(db.get_messages(sid)) for sid in counts_before}
            assert counts_after == counts_before and sum(counts_after.values()) == total_before, "消息数改变"
        finally:
            db.close()
        print(f"恢复后：2 个会话，{total_before} 条消息")
        print("Skill 目录回退不影响 SessionDB，后续会话记录仍在")
    finally:
        client.close()

    sessiondb_backup_demo()
    section("完成")
    print(f"本次探针判定：v1={baseline}，v2={broken}，恢复后={restored}")
    print(f"可查询的 v1 完整快照及清单：{snapshot_dir}")
    print(f"回答、差异、版本副本及备份：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
