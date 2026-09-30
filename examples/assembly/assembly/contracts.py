"""总装共享数据契约：runtime / skillclaw / lifecycle / eval 四个模块共用。

所有结构都可 to_dict() 后 json.dumps；证据统一写到 config.OUTPUT_DIR。
约定：
- 前台任务、后台复盘、评测、共享修订都带可相互对应的 id 与哈希；
- 候选目录与正式目录分离，正式目录哈希在评测期间必须不变（除非决策 ADOPT）；
- 评分器自检不通过时，不产生自动采用。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------- 通用辅助 ----------

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tree_hashes(root: Path) -> Dict[str, str]:
    """目录内每个文件相对路径 -> sha256（二进制安全）。"""
    root = Path(root)
    out: Dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _default(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"不可序列化: {type(obj)}")


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_default),
                    encoding="utf-8")


@dataclass
class Event:
    kind: str                       # foreground.start / background.done / eval.fail / adopt
    at: float = field(default_factory=time.time)
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------- 前台 / 后台 ----------

@dataclass
class ToolCallRecord:
    id: str
    name: str
    arguments: Dict[str, Any]
    result: str
    has_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRun:
    session_id: str
    instance: str
    prompt: str
    skill_hash: Optional[str] = None
    answer: str = ""
    stop_reason: str = ""           # final_answer / max_iterations / interrupted / error
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    elapsed_sec: float = 0.0
    foreground_elapsed_sec: float = 0.0        # 前台返回耗时（不等后台）
    background_elapsed_sec: Optional[float] = None   # 后台实际结束耗时（若观测）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BackgroundReviewResult:
    session_id: str
    triggered: bool
    blocked_foreground: bool        # 必须为 False：前台不等待
    actions: List[str] = field(default_factory=list)
    started_after_sec: Optional[float] = None
    finished_after_sec: Optional[float] = None
    failed: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------- 候选 / 生命周期 ----------

@dataclass
class CandidateBundle:
    """从已采用版复制出的隔离候选；正式目录在评测期间保持不变。"""
    candidate_id: str
    skill_name: str
    candidate_dir: str
    base_skill_hash: str
    candidate_hash: str
    origin: str                     # foreground_review / gepa / shared
    changes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Snapshot:
    label: str
    snapshot_dir: str
    manifest: Dict[str, str]        # 相对路径 -> sha256
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------- 评测 / 评分器 ----------

@dataclass
class CaseResult:
    case_id: str
    passed: bool
    business_correct: bool
    key_points: Dict[str, bool]
    answer: str
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvalReport:
    label: str                      # v0 / v1 / candidate_id
    skill_hash: str
    cases: List[CaseResult]
    passed: int
    total: int
    evaluator_version: str
    ran_at: float = field(default_factory=time.time)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pass_rate"] = self.pass_rate
        return d


@dataclass
class JudgeSelfCheck:
    """评分器自检：已知错误必须被拒，合法同义不能误拒。"""
    known_bad_rejected: bool
    valid_paraphrase_accepted: bool
    details: List[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.known_bad_rejected and self.valid_paraphrase_accepted

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


# ---------- 采用决策 / 共享 ----------

@dataclass
class AdoptionDecision:
    """决策语义分清：
    ADOPT 候选通过，正式目录切换到候选；
    REJECT 候选不达标，正式目录保持旧版，候选与原因留档；
    RESTORE 已采用版在后续任务中退化，取回已验证快照恢复正式目录。
    """
    decision: str                   # ADOPT / REJECT / RESTORE
    candidate_id: Optional[str]
    checks: List[Dict[str, Any]]
    reason: str
    previous_skill_hash: Optional[str] = None
    new_skill_hash: Optional[str] = None
    ran_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SharedRevision:
    """SkillClaw 共享修订：上传 -> 聚合/演化 -> 验证 -> 发布 -> 第三实例加载。"""
    source_instances: List[str]
    uploaded_sessions: List[str]
    evolved: bool
    verified: bool
    published_version: Optional[str]
    loaded_by_instance: Optional[str]
    loaded_skill_hash: Optional[str]
    consumed_in_next_round: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
