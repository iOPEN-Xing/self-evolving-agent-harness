"""技能创建、隔离修订和真实 Hermes Curator 的候选接入。

候选文件操作采用 Hermes skill_manage 的全量 edit、精确 patch 与
write_file 语义，直接操作候选副本，不改变进程全局 HERMES_HOME。
Curator 另起受写入范围限制的真实 Hermes 进程；只有实际整合成功的技能
才转换成 CandidateBundle，后续是否采用由评测与集成层决定。
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import config
from ..contracts import CandidateBundle, Event, tree_hashes, write_json


BASELINE_SKILL = """---
name: payment-status-investigation
description: 根据订单支付流水核对支付状态，汇总成功与处理中金额并给出结论。
---

# 支付状态调查

1. 按订单号查询支付流水，确认订单信息与查询结果。
2. 分别汇总成功金额与处理中金额。
3. 根据查询结果给出订单支付状态结论，说明成功与处理中金额。

只读查询和报告，不发起支付、不改订单、不补发通知。
"""


def _safe_tree(root: Path) -> None:
    """不跟随任何符号链接，也不接受管道等特殊文件。"""
    for entry in (root, *root.parents):
        if entry.is_symlink():
            raise ValueError(f"不接受符号链接路径：{entry}")
    if root.exists():
        for entry in root.rglob("*"):
            if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
                raise ValueError(f"目录包含符号链接或特殊文件：{entry}")


def _under(path: Path, parent: Path, *, direct: bool = False) -> Path:
    path = Path(os.path.abspath(path))
    parent = Path(os.path.abspath(parent))
    _safe_tree(path)
    _safe_tree(parent)
    if path == parent or parent not in path.parents or (direct and path.parent != parent):
        raise ValueError(f"路径须位于指定目录内：{parent}")
    return path


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", value):
        raise ValueError("技能名只能包含小写字母、数字、下划线或连字符")
    return value


def _target(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("文件路径必须是非空的 POSIX 相对路径")
    path = Path(relative)
    if path.is_absolute() or any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError(f"不允许越界路径：{relative}")
    return _under(root / path, root)


def _write_text(path: Path, content: str) -> None:
    if not isinstance(content, str):
        raise TypeError("技能文件内容必须是字符串")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def init_adopted(skill_name: str = config.SKILL_NAME) -> Path:
    """仅在正式技能不存在时写入基线；已存在的正式技能不覆写。"""
    skill_name = _name(skill_name)
    directory = _under(config.OUTPUT_DIR / "adopted" / skill_name,
                       config.OUTPUT_DIR / "adopted", direct=True)
    if directory.exists():
        if not (directory / "SKILL.md").is_file():
            raise ValueError(f"正式目录已存在但缺少 SKILL.md：{directory}")
        return directory
    directory.mkdir(parents=True)
    _write_text(directory / "SKILL.md", BASELINE_SKILL.replace(
        "name: payment-status-investigation", f"name: {skill_name}", 1))
    return directory


def _apply_changes(directory: Path, changes: Mapping[str, str] | Sequence[Mapping[str, Any]]) -> list[str]:
    if isinstance(changes, Mapping):
        operations = [{"action": "write_file", "file_path": path, "file_content": text}
                      for path, text in changes.items()]
    elif isinstance(changes, (list, tuple)):
        operations = list(changes)
    else:
        raise TypeError("changes 应为相对路径到全文的映射，或 Hermes 风格操作列表")
    descriptions = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise TypeError("每个操作必须是字典")
        action = operation.get("action")
        relative = operation.get("file_path") or "SKILL.md"
        target = _target(directory, relative)
        if action in ("edit", "create"):
            if relative != "SKILL.md":
                raise ValueError("edit/create 只用于 SKILL.md；支持文件请用 write_file")
            if action == "create" and target.exists():
                raise ValueError("候选中已存在 SKILL.md，请用 edit 或 patch")
            _write_text(target, operation.get("content"))
        elif action == "write_file":
            _write_text(target, operation.get("file_content", operation.get("content")))
        elif action == "patch":
            old, new = operation.get("old_string"), operation.get("new_string")
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ValueError("patch 需要非空 old_string 和字符串 new_string")
            before = target.read_text(encoding="utf-8")
            occurrences = before.count(old)
            if occurrences == 0 or (occurrences > 1 and not operation.get("replace_all", False)):
                raise ValueError(f"patch 命中 {occurrences} 处；请提供唯一定位或 replace_all")
            _write_text(target, before.replace(old, new, -1 if operation.get("replace_all") else 1))
        elif action in ("delete", "remove_file"):
            # 整项技能删除不会产生可评测版本，故这里必须明确指定支持文件。
            if not operation.get("file_path") or relative == "SKILL.md":
                raise ValueError("可评测候选不能删除 SKILL.md；delete 必须指定支持文件")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        else:
            raise ValueError(f"不支持的候选操作：{action}")
        descriptions.append(f"{action}: {relative}")
    return descriptions


def create_candidate(adopted_dir: Path, origin: str,
                     changes: Mapping[str, str] | Sequence[Mapping[str, Any]]) -> CandidateBundle:
    """复制完整正式技能后只修订候选，记录前后全树摘要及统一候选描述。"""
    adopted_dir = _under(Path(adopted_dir), config.OUTPUT_DIR / "adopted", direct=True)
    baseline = tree_hashes(adopted_dir)
    if "SKILL.md" not in baseline:
        raise ValueError("正式技能缺少 SKILL.md")
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("origin 不能为空")
    candidate_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:12]}"
    directory = _under(config.OUTPUT_DIR / "candidates" / candidate_id,
                       config.OUTPUT_DIR / "candidates", direct=True)
    evidence = _under(config.OUTPUT_DIR / "lifecycle" / "candidates" / f"{candidate_id}.json",
                      config.OUTPUT_DIR / "lifecycle")
    directory.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(adopted_dir, directory)
    try:
        descriptions = _apply_changes(directory, changes)
        _safe_tree(directory)
        content = (directory / "SKILL.md").read_text(encoding="utf-8")
        import yaml
        frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", content, re.S)
        if not frontmatter:
            raise ValueError("候选 SKILL.md 必须有完整的 frontmatter 和正文")
        metadata = yaml.safe_load(frontmatter.group(1))
        if not isinstance(metadata, dict) or metadata.get("name") != adopted_dir.name:
            raise ValueError("候选 frontmatter name 必须与正式技能目录名称一致")
        if not isinstance(metadata.get("description"), str) or not metadata["description"].strip() or not frontmatter.group(2).strip():
            raise ValueError("候选必须包含非空 description 和正文")
        current = tree_hashes(directory)
        after = tree_hashes(adopted_dir)
        if baseline != after:
            raise RuntimeError("创建候选期间正式目录发生变化，候选不得提交评测")
        bundle = CandidateBundle(candidate_id=candidate_id, skill_name=adopted_dir.name,
                                 candidate_dir=str(directory), base_skill_hash=baseline["SKILL.md"],
                                 candidate_hash=current["SKILL.md"], origin=origin, changes=descriptions)
        write_json(evidence, {"candidate": bundle.to_dict(), "base_manifest": baseline,
                              "candidate_manifest": current, "adopted_after_manifest": after,
                              "adopted_unchanged": True,
                              "event": Event("lifecycle.candidate_created", detail={"candidate_id": candidate_id}).to_dict()})
        return bundle
    except Exception as error:
        # 失败候选保留诊断，但没有 CandidateBundle，因此不会误送统一评测。
        write_json(evidence, {"candidate_id": candidate_id, "failed": True,
                              "error": f"{type(error).__name__}: {error}", "base_manifest": baseline,
                              "adopted_unchanged": tree_hashes(adopted_dir) == baseline})
        raise


def _fixtures() -> dict[str, dict[str, str]]:
    general = BASELINE_SKILL + """
## 完整核对
按订单找到流水，核对商户、金额单位与币种，取得 channel_transaction_id。
用渠道交易号查询渠道最终状态，受理成功不是付款成功；然后核对本地结果与商户通知。
通用字段见 [支付记录](references/payment-records.md)。
"""
    legacy = """---
name: payment-legacy-channel
description: 调查旧渠道订单支付状态，先把受理流水号映射成渠道交易号。
---
# 旧渠道支付状态调查
这是 payment-status-investigation 的旧渠道特例，同属单笔订单支付状态调查。
按订单查流水，汇总成功与处理中金额，给出结论；只读、不发起支付和补发通知。
先按 merchant_id 与 acceptance_no 查询 legacy_transaction_map，取得唯一
channel_transaction_id，再查渠道最终状态、本地处理和商户通知。
映射缺失或多条匹配时停止判断，报告待确认；绝不能拿受理流水号当渠道交易号。
见 [旧渠道说明](references/legacy-channel.md)。
"""
    def simple(name: str, description: str, body: str) -> dict[str, str]:
        return {"SKILL.md": f"---\nname: {name}\ndescription: {description}\n---\n\n# {description}\n\n{body}\n"}
    return {
        config.SKILL_NAME: {"SKILL.md": general,
            "references/payment-records.md": "# 支付记录\norder_id：订单号；merchant_id：商户；amount、currency：金额和币种。\nchannel_transaction_id：渠道交易号；final_status：最终付款状态。\n请求受理结果与付款最终状态不同；通知超时不能证明未付款。\n"},
        "payment-legacy-channel": {"SKILL.md": legacy,
            "references/legacy-channel.md": "# 旧渠道映射\n先按 merchant_id 和 acceptance_no 查 legacy_transaction_map，确认唯一 channel_transaction_id 后再查询最终支付状态。映射缺失或多条匹配均停止并报告待确认。\n"},
        "payment-status-page-design": simple("payment-status-page-design", "设计支付状态页面的视觉稿，面向设计师，不调查业务流水。",
            "输入为品牌色和视觉组件规范；输出为页面颜色、字号、间距与静态 SVG 草图。"
            "先设计成功/等待/失败图标，再校对色彩对比与响应式排版。"
            "禁止查询订单或支付流水，不处理商户或渠道状态。此工作没有订单号，不能触发支付调查。"
            "这是独立的 UI 设计方法，与订单支付状态调查仅名称相近，输入、权限、步骤和输出均不同，应保持独立技能。"),
        "tax-rate-lookup": simple("tax-rate-lookup", "按地区和日期查找税率的只读方法。", "先确认地区和生效日期，再核验官方税率说明；本方法仍有效，十天内没有相关任务。"),
        "obsolete-weekly-layout": simple("obsolete-weekly-layout", "过期的周报排版模板。", "旧版内部周报模板已停止使用，可以可恢复归档。"),
        "incident-escalation": simple("incident-escalation", "重大故障升级联系顺序。", "先确认影响，再联系值班负责人；保留升级链和时间记录。此技能已被人工固定。"),
    }


def _redact(text: str) -> str:
    for key in (os.environ.get("DEEPSEEK_API_KEY"),):
        if key:
            text = text.replace(key, "[密钥已隐藏]")
    return text


def _curator_worker(home: str) -> None:
    """只在独立、受限子进程内导入 Hermes，保留其原生调度与工具执行。"""
    home_dir = Path(home)
    os.environ["HERMES_HOME"] = str(home_dir)
    sys.path.insert(0, str(config.HERMES_SRC))
    from tools.skill_manager_tool import skill_manage
    from tools import skill_usage
    from agent import curator
    from run_agent import AIAgent

    # 上游提示要求把前缀相似项积极合并，甚至要求至少归档十项；本小规模
    # 总装练习遵循用户规定的业务范围。仅附加任务约束，不替换模型、工具或
    # Curator 的整合实现，同时旁录原生返回值避免把异常退出误认作完成。
    scope = """
本次执行的范围由技能库所有者明确限定：这里只维护下列六个练习技能。
整合条件是输入、需要作出的业务判断和输出属于同一项工作。名字或前缀
相似不构成整合理由；不得把相互独立的工作塞入同一份总技能。
payment-status-page-design 是设计师制作静态视觉稿的独立工作，必须保留
独立目录与正文，不得整合、改写或归档。incident-escalation 已被固定，
保持不动。tax-rate-lookup 仍有效，仅低频，保留独立方法。
请实际审阅 payment-status-investigation 与 payment-legacy-channel 的
全部文件，再判断是否整合并保留旧渠道映射特例。可以沿用现有主技能或
新建合理的主技能。不要求凑够十次归档；处理完实际适合整合的项目即结束。
所有文件操作只能发生在环境变量 HERMES_HOME 指向的隔离目录，切勿使用
真实用户的 ~/.hermes。上面原生流程的通用数量要求以本次明确范围为准。
"""
    native_run = AIAgent.run_conversation
    def observed_run(agent: Any, *args: Any, **kwargs: Any) -> Any:
        original_prompt = str(kwargs.get("user_message", ""))
        _write_text(home_dir / "native-curator-prompt.txt", original_prompt)
        kwargs["user_message"] = original_prompt + "\n\n" + scope
        _write_text(home_dir / "effective-curator-prompt.txt", kwargs["user_message"])
        native_result = native_run(agent, *args, **kwargs)
        if isinstance(native_result, dict):
            observed = {key: native_result.get(key) for key in (
                "completed", "failed", "partial", "interrupted", "turn_exit_reason", "api_calls",
                "model", "provider", "input_tokens", "output_tokens")}
            write_json(home_dir / "native_run_status.json", observed)
            write_json(home_dir / "native_tool_calls.json", [
                call for message in native_result.get("messages", []) if isinstance(message, dict)
                for call in message.get("tool_calls", []) or []])
        return native_result
    AIAgent.run_conversation = observed_run
    _write_text(home_dir / "assembly-curator-scope.txt", scope)

    now = datetime.now(timezone.utc)
    seeds = _fixtures()
    for name, files in seeds.items():
        for relative, content in files.items():
            operation = ({"action": "create", "content": content} if relative == "SKILL.md" else
                         {"action": "write_file", "file_path": relative, "file_content": content})
            reply = json.loads(skill_manage(name=name, **operation))
            if not reply.get("success"):
                raise RuntimeError(f"创建 Curator 练习技能失败：{name}：{reply}")
        skill_usage.mark_agent_created(name)
    with skill_usage._usage_file_lock():
        usage = skill_usage.load_usage()
        for name in seeds:
            days = {"tax-rate-lookup": 10, "obsolete-weekly-layout": 20, "incident-escalation": 100}.get(name, 1)
            timestamp = (now - timedelta(days=days)).isoformat()
            usage[name].update(created_at=timestamp, use_count=3, last_used_at=timestamp,
                               last_viewed_at=None, last_patched_at=None, state="active")
        skill_usage.save_usage(usage)
    skill_usage.set_pinned("incident-escalation", True)
    before = {name: tree_hashes(home_dir / "skills" / name) for name in seeds}
    write_json(home_dir / "usage_before.json", skill_usage.load_usage())
    write_json(home_dir / "skills_before.json", before)
    # 这些配置从原生读取函数得到，说明实际门控而非猜测参数含义。
    write_json(home_dir / "entry.json", {
        "entry": "agent.curator.run_curator_review(synchronous=True, dry_run=False, consolidate=True)",
        "enabled": curator.is_enabled(), "consolidate": curator.get_consolidate(),
        "interval_hours": curator.get_interval_hours(), "min_idle_hours": curator.get_min_idle_hours(),
        "stale_after_days": curator.get_stale_after_days(), "archive_after_days": curator.get_archive_after_days(),
        "manual_entry_bypasses_idle_and_interval": True, "native_agent_execution_unmodified": True,
        "adapter": "仅追加任务范围约束，并旁录原生 run_conversation 返回状态；不预先安排整合操作",
        "constructed_usage_records": True,
    })
    result = curator.run_curator_review(synchronous=True, dry_run=False, consolidate=True)
    write_json(home_dir / "return.json", result)
    write_json(home_dir / "usage_after.json", skill_usage.load_usage())
    write_json(home_dir / "state_after.json", curator.load_state())
    write_json(home_dir / "skills_after.json", {
        name: tree_hashes(home_dir / "skills" / name) for name in seeds})


def run_curator(home_dir: Path) -> dict[str, Any]:
    """在新建的隔离 HERMES_HOME 执行原生 Curator，并导出整合候选。

本例显式使用 macOS sandbox-exec 约束原生 fork 及终端后代的写入。
其他平台须先提供等价的受限执行环境，不能裸跑后声称目录已隔离。
失败也写出 actions 与 checks；不会把纯模型建议冒充已执行整合。
"""
    home_dir = _under(Path(home_dir), config.OUTPUT_DIR / "homes")
    if home_dir.exists() and any(home_dir.iterdir()):
        raise ValueError("Curator 演示需要空的隔离 HERMES_HOME，请使用新的目录名")
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("此真实 Curator 入口需要 macOS sandbox-exec 限制写入范围")
    if not (os.environ.get("DEEPSEEK_API_KEY")):
        raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY；请先 export DEEPSEEK_API_KEY")
    adopted = init_adopted(config.SKILL_NAME)
    adopted_before = tree_hashes(adopted)
    home_dir.mkdir(parents=True, exist_ok=True)
    source_bytes = Path(__file__).read_bytes()
    _write_text(home_dir / "source-skills.py", source_bytes.decode("utf-8"))
    for name in ("skills", "memories", "tmp", "cache"):
        (home_dir / name).mkdir()
    _write_text(home_dir / "config.yaml", f"""model:
  default: {config.MODEL}
  provider: deepseek
terminal:
  backend: local
  cwd: {json.dumps(str(home_dir))}
curator:
  enabled: true
  consolidate: true
  prune_builtins: false
  interval_hours: 168
  min_idle_hours: 2
  stale_after_days: 7
  archive_after_days: 14
auxiliary:
  curator:
    provider: deepseek
    model: {config.MODEL}
    base_url: {config.BASE_URL}
    extra_body:
      temperature: 0.2
""")
    profile = home_dir / "write-boundary.sb"
    _write_text(profile, '(version 1)\n(allow default)\n(deny file-write*)\n'
                + '(allow file-write* (subpath ' + json.dumps(str(home_dir), ensure_ascii=False) + ') (literal "/dev/null"))\n')
    env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
    env.update(HERMES_HOME=str(home_dir), PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               PYTHONPATH=str(config.ASSEMBLY_DIR) + os.pathsep + str(config.HERMES_SRC),
               TMPDIR=str(home_dir / "tmp"), TMP=str(home_dir / "tmp"), TEMP=str(home_dir / "tmp"),
               XDG_CACHE_HOME=str(home_dir / "cache"))
    python = str(config.HERMES_SRC / ".venv" / "bin" / "python")
    command = ["/usr/bin/sandbox-exec", "-f", str(profile), python]
    # 启动模型之前，用真实文件写入确认正式目录受到 OS 保护。
    probe = """from pathlib import Path
import sys
inside, outside = map(Path, sys.argv[1:])
(inside / 'sandbox-probe.txt').write_text('范围内可写')
try:
    with outside.open('a'):
        pass
except PermissionError:
    print('write-boundary-verified')
else:
    raise SystemExit('正式目录写入未被阻止')
"""
    checked = subprocess.run(command + ["-c", probe, str(home_dir), str(adopted / "SKILL.md")],
                             env=env, cwd=home_dir, capture_output=True, text=True, timeout=20)
    if checked.returncode or "write-boundary-verified" not in checked.stdout:
        raise RuntimeError("OS 写入隔离验证失败：" + _redact(checked.stderr))
    started = time.monotonic()
    error = None
    with (home_dir / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command + ["-c", "from assembly.lifecycle.skills import _curator_worker; import sys; _curator_worker(sys.argv[1])", str(home_dir)],
                                   env=env, cwd=home_dir, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            error = "Curator 运行超过 300 秒，已停止子进程组"
        if process.returncode and error is None:
            error = f"Curator 子进程退出码 {process.returncode}，见 worker.log"
    _write_text(home_dir / "worker.log", _redact((home_dir / "worker.log").read_text(encoding="utf-8")))
    def read(name: str) -> Any:
        path = home_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    state, before_usage, usage = read("state_after.json"), read("usage_before.json"), read("usage_after.json")
    report_dir = Path(state.get("last_report_path") or home_dir / "missing-report")
    report = json.loads((report_dir / "run.json").read_text(encoding="utf-8")) if (report_dir / "run.json").is_file() else {}
    before = read("skills_before.json")
    final = report.get("llm_final", "")
    status = read("native_run_status.json")
    completed = bool(report.get("model") and final and report.get("tool_calls") and not report.get("llm_error")
                     and status.get("completed") is True and not any(status.get(k) for k in ("failed", "partial", "interrupted"))
                     and not re.match(r"\s*(?:HTTP\s+[45]\d\d|Error\b|APIError\b|错误[：:])", final, re.I))
    legacy_name = "payment-legacy-channel"
    consolidated = report.get("consolidated", [])
    actual_target = next((item.get("into") for item in consolidated if item.get("name") == legacy_name), None)
    general_dir = home_dir / "skills" / _name(actual_target or config.SKILL_NAME)
    _safe_tree(home_dir / "skills")
    merged_text = "\n".join(path.read_text(encoding="utf-8") for path in general_dir.rglob("*.md")) if general_dir.exists() else ""
    special_preserved = all(token in merged_text for token in ("merchant_id", "acceptance_no", "legacy_transaction_map", "channel_transaction_id", "缺失")) and bool(re.search("多条|不唯一|多个", merged_text))
    auto = report.get("auto_transitions", read("return.json").get("auto_transitions", {}))
    checks = {
        "real_llm_review_completed": completed,
        "stale_transition_observed": auto.get("marked_stale") == 1,
        "old_skill_archived": usage.get("obsolete-weekly-layout", {}).get("state") == "archived"
            and tree_hashes(home_dir / "skills" / ".archive" / "obsolete-weekly-layout") == before.get("obsolete-weekly-layout"),
        "pinned_preserved": usage.get("incident-escalation", {}).get("pinned") is True
            and tree_hashes(home_dir / "skills" / "incident-escalation") == before.get("incident-escalation"),
        "similar_name_different_job_preserved": tree_hashes(home_dir / "skills" / "payment-status-page-design") == before.get("payment-status-page-design"),
        "legacy_consolidated": bool(actual_target and general_dir.is_dir()),
        "legacy_archived_intact": tree_hashes(home_dir / "skills" / ".archive" / legacy_name) == before.get(legacy_name)
            and not (home_dir / "skills" / legacy_name).exists(),
        "special_mapping_preserved": special_preserved,
        "adopted_unchanged": tree_hashes(adopted) == adopted_before,
        "os_write_boundary_verified": True,
    }
    bundles = []
    identity_adapter = None
    if all(checks.values()):
        curated = tree_hashes(general_dir)
        changes = [{"action": "write_file", "file_path": relative,
                    "file_content": (general_dir / relative).read_text(encoding="utf-8")}
                   for relative in curated]
        if actual_target != config.SKILL_NAME:
            original_text = (general_dir / "SKILL.md").read_text(encoding="utf-8")
            normalized_text = re.sub(r"(?m)^name:\s*[^\n]+$", f"name: {config.SKILL_NAME}", original_text, count=1)
            for operation in changes:
                if operation["file_path"] == "SKILL.md":
                    operation["file_content"] = normalized_text
            identity_adapter = {"from": actual_target, "to": config.SKILL_NAME,
                                "changed_field": "frontmatter.name", "source_manifest": curated,
                                "source_skill_sha256": curated["SKILL.md"],
                                "candidate_skill_sha256": hashlib.sha256(normalized_text.encode()).hexdigest()}
        changes += [{"action": "delete", "file_path": relative}
                    for relative in adopted_before if relative not in curated]
        bundle = create_candidate(adopted, "curator", changes)
        bundles.append(bundle.to_dict())
        expected = dict(curated)
        if identity_adapter:
            expected["SKILL.md"] = identity_adapter["candidate_skill_sha256"]
        checks["curated_candidate_matches_source"] = tree_hashes(Path(bundle.candidate_dir)) == expected
    actions = {"auto_marked_stale": ["tax-rate-lookup"] if auto.get("marked_stale") == 1 else [],
               "auto_archived": ["obsolete-weekly-layout"] if auto.get("archived") == 1 else [],
               "consolidated": consolidated, "llm_pruned": report.get("pruned", []),
               "pinned_retained": checks["pinned_preserved"],
               "similar_name_not_absorbed": checks["similar_name_different_job_preserved"]}
    result = {"passed": all(checks.values()) and bool(bundles) and error is None,
              "home_dir": str(home_dir), "entry": read("entry.json"), "model": report.get("model"),
              "report_path": str(report_dir / "run.json"), "actions": actions, "checks": checks,
              "candidate_bundles": bundles, "auto_transitions": auto, "usage_before": before_usage,
              "native_run_status": status, "identity_adapter": identity_adapter,
              "source_sha256": {"skills.py": hashlib.sha256(source_bytes).hexdigest(),
                                "curator.py": hashlib.sha256((config.HERMES_SRC / "agent" / "curator.py").read_bytes()).hexdigest()},
              "usage_after": usage, "adopted_before_manifest": adopted_before,
              "adopted_after_manifest": tree_hashes(adopted), "error": error or report.get("llm_error"),
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "limitation": "时间与技能为构造练习条件；模型与原生 Curator 工具调用真实。语义核验为保守字段检查，正式采用仍须业务评测。"}
    run_evidence = _under(config.OUTPUT_DIR / "lifecycle" / "curator" / f"{home_dir.name}.json", config.OUTPUT_DIR / "lifecycle")
    write_json(run_evidence, result)
    write_json(config.OUTPUT_DIR / "lifecycle" / "curator.json", result)
    write_json(config.OUTPUT_DIR / "lifecycle" / "curator_result.json", result)
    return result
