# 另一接入示例：巡检总装（历史说明）

主练习为原生Hermes支付总装，见 [README.md](README.md)。下述巡检实现与历史验证分开保留。

# 综合实践：Skill 层递归闭环演示

本例以 `search-api` 的 On-call 值守为场景，把 A/B 的真实模型工具循环、同步事故复盘、Skill 修订、隔离评测、版本决策、快照恢复和 C 的跨实例复用串起来。它演示 Skill 层的经历回流，不证明评测方法或版本管理方法能够自我改进。

## 运行

在仓库根设置 `GLM_API_KEY`（兼容 `BIGMODEL_API_KEY`）后执行：

```bash
bash scripts/setup_deps.sh
.deps/hermes-agent/.venv/bin/python examples/capstone/capstone.py
```

需要已安装仓库依赖以及 `.deps/hermes-agent`。脚本使用 `glm-5.2`，在任何网络调用前清除代理变量。每次运行会清空并重建 `examples/capstone/output/`；3 个实例的独立 `HERMES_HOME` 和数据库均在该目录内。

隔离评测已传入本例自己的 `run_case(skill_text, version, tc)`。每个版本、每个用例独立构造 2 个观测窗口，复用 `OncallToolLoopAgent` 和 `query_oncall_observations`，按模型请求实际执行部署、配置、指标与日志查询；执行模型只看到技能、当前窗口和本用例此前的调查记录。评测循环不调用第 23 讲的 `run_shift` 或 `turn_runner`，也不向共享会话池上传评测经历。

需要先检查接口时，可单独执行真实 GLM 单用例双版本验证：

```bash
.deps/hermes-agent/.venv/bin/python docs/capstone-run-case-fix-20260930/run_eval_smoke.py
```

该命令仅运行 NI-01 的 v0/v1 各 2 轮、`compare_versions` 及 `self_check`，文件写入 `docs/capstone-run-case-fix-20260930/smoke/`，不代表完成全部 6 个用例或其他阶段。密钥仍只从环境变量读取。`run_case` 最终报告解析容忍 Markdown JSON 围栏和前后说明文字，JSON 内容及字段仍须合法；内容错误如实记为 `invalid_report`，不补写判断或工具记录。此处理不适用于裁判输出。

## 6 个环节

1. A 用 v0 查询 INC-A1/A2，分别观察实例状态不一致、重新加载已受理但恢复未确认；B 用 v0 查询 INC-B1/B2，分别观察正常巡检、实例健康但缺少恢复确认。模型通过原生 function calling 调用只读工具 `query_oncall_observations`，获取确定性 mock 回执；完整轨迹落入各实例 SessionDB，同时上传 `shared_hub/sessions.jsonl`。请求样本计数只描述观测范围，不作为 SLO 达标或长期健康的证明。
2. 同步调用模型复盘 A/B 经历，为 `oncall-service-investigation` 生成指定 2 处修订的 patch：核对实例生效配置，逐个检查实例。无效 patch 回退到规格给定的确定性修订；实际调用 `skill_manage(patch)`，保存修订来源。生成候选后先恢复 A 的 v0 加载文件，再进行隔离评测，异常时也不会遗留已加载的候选。
3. 复用第 23 讲的 `run_eval`，传入本例的 `run_case`，每版运行全部 6 个用例及 `self_check`。每例 2 轮的回答、真实工具调用与回执、报告和状态转换另存于 `eval_cases/v0/` 或 `eval_cases/v1/`，由公共裁判核查。裁判必须返回完整合法的 JSON；不截取 JSON 片段、不补分，首次格式失败就把该项记为 `parse_error`，不能重试到通过。自检包含已知错误与正确答案 2 项。
4. `policy.decide` 先检查自检通过、2 版各 6 项完整、全部评分合法，任一不满足即 `REJECT / WAIT_FOR_MANUAL_REVIEW`；通过后才检查保留集不退化、新事故集提升、回归集全过。记忆先 add 待确认发现，再 replace 写入决策。ROLLBACK 保留 v0，写决策日志并进入 `WAIT_FOR_NEXT_BATCH`，不发布候选 v1。
5. 采用版 Skill 整目录 copytree，生成逐文件 SHA-256 清单；另用 `vsnap` 导出数据库。故意改坏 Skill、增加文件和会话后恢复，验证文件清单及哈希一致、新增会话消失。
6. C 在空技能目录下运行 INC-B1，再从 hub 读取采用版并核对 hash、落盘，重跑同一任务。2 次工具配置相同，回答和调用差异如实展示，不保证无技能时答错。C 的 loaded 经历回流共享池，按实例统计下一轮 review 能读取的会话。

## 结果与停止条件

统一入口是运行后生成的 `output/run_summary.json`：包含源码指纹、采用版、决策、通过率、实例技能哈希、共享池计数、快照验证、停止原因及耗时。细节见 `eval_report.json`、`patch_result.json`、`turns/`、`c_probe_comparison.json`、`versions.json` 和 `skill_tree_snapshots/`。manifest 的内部编号由现有模块自增，与演示中的 v0/v1 标签分开解释。

本次配置只跑 1 轮，总预算 900 秒，连续无提升上限为 2。ADOPT 时停止原因为 `ADOPT`；评分器无效或检查不完整时为 `REJECT`，等待人工检查；单轮 ROLLBACK 通常为 `max_rounds`；超时为 `wall_clock`。真实模型可能使 v0 已经答对，或使候选没有提升，不预设采用结果。网络请求按剩余预算设置超时，SDK 自动重试关闭；异常和预算停止会保存已完成部分，不能视为整轮成功。

2026-09-29 修复验证：本地 12 项回归测试通过。旧运行虽记录 `5/6 → 6/6、ADOPT`，但已知错误答案在自检中得了 5 分并被放行，因此撤回据此支持采用的结论。旧记录已另存。

场景替换前的重新运行在首次模型请求时返回 HTTP 401，未完成 A/B 值守、候选修订及评测。该次记录为 `INCOMPLETE`、`REJECT / WAIT_FOR_MANUAL_REVIEW`，保留 v0，没有新的通过率或成功采用结论。这些历史记录不能证明当前 On-call 场景已跑通，也不能证明远程评分器已正确识别错误答案。

2026-09-30 首次使用有效环境密钥全量复验：v0/v1 各 6 例、每例 2 轮，共 68 次真实请求，耗时 710.785 秒。24 轮报告均因 JSON 围栏被记为 `invalid_report`，4 项裁判为 `parse_error`，两版均 0/6；正反自检通过。该次记录见 [历史验收报告](../../docs/capstone-run-case-fix-20260930/glm-acceptance-20260930/README.md)，原始输出现保存在 [output-before](../../docs/capstone-json-report-fix-20260930/output-before/)。

同日修复 `run_case` 围栏解析后，再次完成阶段 3 全量验收：12 项、24 轮、66 次真实请求，647.939 秒，预算内。21 轮报告合法，8 个事故版本用例均实际进入 `VERIFYING`；v0/v1 各通过 3/6，9 项裁判评分有效，正反自检通过。仍有 3 轮正文引号未转义、3 项裁判格式或输出额度失败，以及 v0/NI-01 漏查逐实例指标；未放宽公共评分规则，也未补造查询。当前 `output/` 为本次结果，稳定副本、逐例评分、状态、耗时与完整运行差距见 [最新统一报告](../../docs/capstone-json-report-fix-20260930/README.md)。本次未重跑其他阶段或执行版本采用。

`output/source_manifest.json` 保存本入口、复用的 23 讲评测与版本模块、所用 Hermes Python 源文件的 SHA-256 清单与总指纹；`run_summary.json`、`decision.json` 和完成评测后生成的 `eval_report.json` 引用同一指纹。采用前核对源码未变。[完整修改与核查记录](../../docs/p0-judge-fix-20260929/README.md)。

边界：监控观测工具回执是 mock，模型仍真实调用 GLM API；跨实例共享目录只演示生产共享存储的读写语义，不包含生产一致性或权限能力；评测集是参考骨架；后台复盘此处同步调用，第 11 讲的 daemon 异步机制没有在本例重新运行。
