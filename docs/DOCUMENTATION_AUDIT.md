# 逐份文档复核：知识、代码与证据范围

本次以 `becc7a1` 为基线，检查全部 41 份原有 Markdown 和 9 份 Notebook 的阅读内容，新增本记录后共有 42 份 Markdown。每份按用途核对问题、机制、条件、案例推理、源码定位和结论范围。章节补充不同的知识重点；操作 Skill 保持简明协议，历史报告保持原实验含义。

## 怎样判断内容可信

先找到具体主张的来源：教学数据定义条件，代码决定执行和状态，工具回执说明实际观察，评分与采用记录支持相应结论。再核对主张是否越过来源范围。比如唯一匹配有助于定位条目，却不保存审计历史；独立 Thread 隔开对话，却不隔开共用文件；一次成功调用也不证明方法可靠。

深度来自解释为什么这样判断，以及条件改变时判断怎样变化。前九章从动作、请求、规则与历史推到边界；经验维护说明事实与方法如何修订；评测与发布说明数据独立性、版本身份和失败出口。工程建议标为接入要求，未实现或未运行的路径保留明确状态。

## Markdown 逐份对应

下表的源码用于定位已经阅读核对的实现。入口存在由 `check_contracts` 检查，发布链接由 `check_links` 检查；它们不能自动证明表述的业务含义，内容仍按具体调用和数据读写审阅。

| 文档 | 知识重点 | 本轮处理 | 依据入口 |
|---|---|---|---|
| [COURSE_MAP.md](../COURSE_MAP.md) | 章节学习依赖与故障定位路线 | 补充解释或修正范围；历史结果保持原义 | [docs/chapters.json](chapters.json)<br>[deps.lock.json](../deps.lock.json) |
| [README.md](../README.md) | 课程的判断能力、运行路线与机制边界 | 补充解释或修正范围；历史结果保持原义 | [deps.lock.json](../deps.lock.json)<br>[scripts/setup_deps.sh](../scripts/setup_deps.sh) |
| [docs/10-learning-loop.md](10-learning-loop.md) | 事实、方法、经历与验证的接口 | 补充机制、条件与反例；核对现有入口和范围 | [examples/assembly/assembly/contracts.py](../examples/assembly/assembly/contracts.py)<br>[examples/assembly/assembly/eval/policy.py](../examples/assembly/assembly/eval/policy.py) |
| [docs/ARCHITECTURE.md](ARCHITECTURE.md) | 观察、建议、状态与版本职责 | 补充解释或修正范围；历史结果保持原义 | [examples/23-final-assembly/shift/runtime.py](../examples/23-final-assembly/shift/runtime.py)<br>[examples/23-final-assembly/versioning/policy.py](../examples/23-final-assembly/versioning/policy.py) |
| [docs/DOCUMENTATION_AUDIT.md](DOCUMENTATION_AUDIT.md) | 将内容审阅与机械检查分开，完整列出覆盖与边界 | 新增逐份复核记录 | [scripts/check_docs.py](../scripts/check_docs.py)<br>[tests/test_doc_contracts.py](../tests/test_doc_contracts.py) |
| [docs/DOCUMENTATION_PLAN.md](DOCUMENTATION_PLAN.md) | 内容验收与真实模型验收分别判断 | 补充解释或修正范围；历史结果保持原义 | [docs/chapters.json](chapters.json)<br>[docs/validation/notebook-runs.json](validation/notebook-runs.json) |
| [docs/DOCUMENTATION_VALIDATION.md](DOCUMENTATION_VALIDATION.md) | 历史运行按源码、协议与结论范围读取 | 补充解释或修正范围；历史结果保持原义 | [docs/validation/notebook-runs.json](validation/notebook-runs.json)<br>[docs/validation/payment-eval-summary.json](validation/payment-eval-summary.json) |
| [docs/ENGINEERING_PLAN.md](ENGINEERING_PLAN.md) | 先确定不变量，再故障注入验证 | 补充解释或修正范围；历史结果保持原义 | [tests/test_snapshot.py](../tests/test_snapshot.py)<br>[tests/test_source.py](../tests/test_source.py) |
| [docs/ENGINEERING_REVIEW.md](ENGINEERING_REVIEW.md) | 工程故障证据与后续依赖状态分轮解释 | 补充解释或修正范围；历史结果保持原义 | [tests/test_shift.py](../tests/test_shift.py)<br>[tests/test_manifest.py](../tests/test_manifest.py) |
| [docs/EVOLUTION_REVIEW.md](EVOLUTION_REVIEW.md) | 符号存在、控制职责与业务效果分层核对 | 补充解释或修正范围；历史结果保持原义 | [scripts/check_docs.py](../scripts/check_docs.py)<br>[tests/test_doc_contracts.py](../tests/test_doc_contracts.py) |
| [docs/MODEL_SETUP.md](MODEL_SETUP.md) | 源码、安装集合与实验协议的可复现性 | 补充解释或修正范围；历史结果保持原义 | [scripts/setup_deps.sh](../scripts/setup_deps.sh)<br>[scripts/model_probe.py](../scripts/model_probe.py) |
| [docs/RUNBOOK.md](RUNBOOK.md) | 按失败层定位、按恢复对象操作、按证据继续 | 补充解释或修正范围；历史结果保持原义 | [scripts/verify.py](../scripts/verify.py)<br>[examples/23-final-assembly/versioning/snapshot.py](../examples/23-final-assembly/versioning/snapshot.py) |
| [docs/SELF_EVOLUTION.md](SELF_EVOLUTION.md) | 更新对象、函数产物、正式采用与后续使用 | 复核后保留：机制、案例与范围已逐项展开 | [examples/capstone/run_assembly_light.py](../examples/capstone/run_assembly_light.py)<br>[examples/assembly/run_assembly.py](../examples/assembly/run_assembly.py) |
| [examples/01-agent-loop/README.md](../examples/01-agent-loop/README.md) | 动作建议、工具观察与样例因果范围 | 补充机制、条件与反例；核对现有入口和范围 | [examples/01-agent-loop/workshop.ipynb](../examples/01-agent-loop/workshop.ipynb) |
| [examples/02-instruction-control/README.md](../examples/02-instruction-control/README.md) | 活跃任务身份、告警状态与取消责任 | 补充机制、条件与反例；核对现有入口和范围 | [examples/02-instruction-control/workshop.ipynb](../examples/02-instruction-control/workshop.ipynb) |
| [examples/03-context-engine/README.md](../examples/03-context-engine/README.md) | 磁盘、回执、请求装配与判断的区别 | 补充机制、条件与反例；核对现有入口和范围 | [examples/03-context-engine/workshop.ipynb](../examples/03-context-engine/workshop.ipynb) |
| [examples/04-agents-md/README.md](../examples/04-agents-md/README.md) | 持久规则的目录范围与知识类型 | 补充机制、条件与反例；核对现有入口和范围 | [examples/04-agents-md/workshop.ipynb](../examples/04-agents-md/workshop.ipynb) |
| [examples/05-context-compression/README.md](../examples/05-context-compression/README.md) | 状态保留、原文回查与数值验证 | 补充机制、条件与反例；核对现有入口和范围 | [examples/05-context-compression/workshop.ipynb](../examples/05-context-compression/workshop.ipynb) |
| [examples/06-thread-fork/README.md](../examples/06-thread-fork/README.md) | 历史前缀、污染条件与资源隔离 | 补充机制、条件与反例；核对现有入口和范围 | [examples/06-thread-fork/workshop.ipynb](../examples/06-thread-fork/workshop.ipynb) |
| [examples/07-skill-extraction/README.md](../examples/07-skill-extraction/README.md) | 从旧答案抽象方法，用改变条件的反例验证 | 补充机制、条件与反例；核对现有入口和范围 | [examples/07-skill-extraction/workshop.ipynb](../examples/07-skill-extraction/workshop.ipynb) |
| [examples/08-progressive-disclosure/README.md](../examples/08-progressive-disclosure/README.md) | 分层加载的职责、读取代价与语义检查 | 补充机制、条件与反例；核对现有入口和范围 | [examples/08-progressive-disclosure/workshop.ipynb](../examples/08-progressive-disclosure/workshop.ipynb) |
| [examples/09-isolation-approval/README.md](../examples/09-isolation-approval/README.md) | 目录权限、动作授权与结果检查 | 补充机制、条件与反例；核对现有入口和范围 | [examples/09-isolation-approval/workshop.ipynb](../examples/09-isolation-approval/workshop.ipynb) |
| [examples/11-background-review/README.md](../examples/11-background-review/README.md) | 异步触发、完成、去重和写入状态 | 补充机制、条件与反例；核对现有入口和范围 | [examples/11-background-review/run_background_review.py](../examples/11-background-review/run_background_review.py) |
| [examples/12-memory-revision/README.md](../examples/12-memory-revision/README.md) | 整条替换时保护未被纠正事实 | 补充机制、条件与反例；核对现有入口和范围 | [examples/12-memory-revision/revision_validation.py](../examples/12-memory-revision/revision_validation.py)<br>[examples/12-memory-revision/run_memory_revision.py](../examples/12-memory-revision/run_memory_revision.py) |
| [examples/13-session-lookup/README.md](../examples/13-session-lookup/README.md) | 关键词、窗口、编号与持久化范围 | 补充机制、条件与反例；核对现有入口和范围 | [examples/13-session-lookup/run_session_lookup.py](../examples/13-session-lookup/run_session_lookup.py) |
| [examples/14-external-memory-provider/README.md](../examples/14-external-memory-provider/README.md) | 后端生命周期、共享记录与共同学习 | 补充机制、条件与反例；核对现有入口和范围 | [examples/14-external-memory-provider/run_provider.py](../examples/14-external-memory-provider/run_provider.py)<br>[examples/14-external-memory-provider/run_shared_sqlite.py](../examples/14-external-memory-provider/run_shared_sqlite.py) |
| [examples/15-skill-autocreation/README.md](../examples/15-skill-autocreation/README.md) | 新建、修订、保留的适用条件与来源 | 补充机制、条件与反例；核对现有入口和范围 | [examples/15-skill-autocreation/run_lab15.py](../examples/15-skill-autocreation/run_lab15.py) |
| [examples/16-skill-incremental-patch/README.md](../examples/16-skill-incremental-patch/README.md) | 正文与参考一致、未知结果与发布边界 | 补充机制、条件与反例；核对现有入口和范围 | [examples/16-skill-incremental-patch/run_lab16.py](../examples/16-skill-incremental-patch/run_lab16.py) |
| [examples/17-curator/README.md](../examples/17-curator/README.md) | 活动不等于价值，整合要保护关键特例 | 补充机制、条件与反例；核对现有入口和范围 | [examples/17-curator/run_lab17.py](../examples/17-curator/run_lab17.py)<br>[examples/17-curator/run_lab17_consolidate.py](../examples/17-curator/run_lab17_consolidate.py) |
| [examples/18-business-eval/README.md](../examples/18-business-eval/README.md) | 同协议对照、独立预期与评分反例 | 补充机制、条件与反例；核对现有入口和范围 | [examples/18-business-eval/run_lab18.py](../examples/18-business-eval/run_lab18.py)<br>[examples/18-business-eval/selftest.py](../examples/18-business-eval/selftest.py) |
| [examples/19-enterprise-dataset/README.md](../examples/19-enterprise-dataset/README.md) | 截止时点、反馈来源、排除与隔离 | 补充机制、条件与反例；核对现有入口和范围 | [examples/19-enterprise-dataset/scripts/analyze.py](../examples/19-enterprise-dataset/scripts/analyze.py)<br>[examples/19-enterprise-dataset/scripts/convert.py](../examples/19-enterprise-dataset/scripts/convert.py) |
| [examples/19-enterprise-dataset/skill/payment-report/SKILL.md](../examples/19-enterprise-dataset/skill/payment-report/SKILL.md) | 商户订单币种与截止时点过滤、净额和状态协议 | 复核后保留：执行提示须简明，字段与评分一致 | [examples/19-enterprise-dataset/scripts/convert.py](../examples/19-enterprise-dataset/scripts/convert.py)<br>[examples/19-enterprise-dataset/scripts/grader.py](../examples/19-enterprise-dataset/scripts/grader.py) |
| [examples/19-snapshot-restore/README.md](../examples/19-snapshot-restore/README.md) | 方法回退保留经历，导入字段不等于整库恢复 | 补充机制、条件与反例；核对现有入口和范围 | [examples/19-snapshot-restore/snapshot_restore.py](../examples/19-snapshot-restore/snapshot_restore.py) |
| [examples/20-gepa-offline-optimization/README.md](../examples/20-gepa-offline-optimization/README.md) | 搜索反馈、留出暴露和采用资格 | 补充机制、条件与反例；核对现有入口和范围 | [examples/20-gepa-offline-optimization/run_exercise.py](../examples/20-gepa-offline-optimization/run_exercise.py)<br>[examples/20-gepa-offline-optimization/gepa_offline.py](../examples/20-gepa-offline-optimization/gepa_offline.py) |
| [examples/21-skillclaw-session-collection/README.md](../examples/21-skillclaw-session-collection/README.md) | 采集归属、重复快照与候选来源 | 补充机制、条件与反例；核对现有入口和范围 | [examples/21-skillclaw-session-collection/audit21.py](../examples/21-skillclaw-session-collection/audit21.py)<br>[examples/21-skillclaw-session-collection/candidate_evidence.py](../examples/21-skillclaw-session-collection/candidate_evidence.py) |
| [examples/22-skillclaw-shared-revision/README.md](../examples/22-skillclaw-shared-revision/README.md) | 未加载对照、版本递增与内容回退 | 补充机制、条件与反例；核对现有入口和范围 | [examples/22-skillclaw-shared-revision/run22.py](../examples/22-skillclaw-shared-revision/run22.py)<br>[examples/22-skillclaw-shared-revision/shared_revision.py](../examples/22-skillclaw-shared-revision/shared_revision.py) |
| [examples/23-final-assembly/README.md](../examples/23-final-assembly/README.md) | 观测支持状态，评分完整性支持决定 | 补充机制、条件与反例；核对现有入口和范围 | [examples/23-final-assembly/final_assembly.py](../examples/23-final-assembly/final_assembly.py)<br>[examples/23-final-assembly/shift/runtime.py](../examples/23-final-assembly/shift/runtime.py) |
| [examples/README.md](../examples/README.md) | 从条件到代码和反例的阅读方法，解释历史日志 | 补充解释或修正范围；历史结果保持原义 | [examples/notebook_support.py](../examples/notebook_support.py)<br>[examples/build_notebooks.py](../examples/build_notebooks.py) |
| [examples/assembly/README.md](../examples/assembly/README.md) | 跨模块身份绑定与本地、远端责任 | 补充机制、条件与反例；核对现有入口和范围 | [examples/assembly/assembly/config.py](../examples/assembly/assembly/config.py)<br>[examples/assembly/assembly/contracts.py](../examples/assembly/assembly/contracts.py) |
| [examples/capstone/README-oncall.md](../examples/capstone/README-oncall.md) | 单轮回流、确定性后备来源与历史证据 | 补充解释或修正范围；历史结果保持原义 | [examples/capstone/capstone.py](../examples/capstone/capstone.py)<br>[examples/23-final-assembly/versioning/policy.py](../examples/23-final-assembly/versioning/policy.py) |
| [examples/capstone/README.md](../examples/capstone/README.md) | 单轮控制、实际重用与下一轮证据 | 补充机制、条件与反例；核对现有入口和范围 | [examples/capstone/run_assembly_light.py](../examples/capstone/run_assembly_light.py) |
| [examples/capstone/new-order-reserve/README.md](../examples/capstone/new-order-reserve/README.md) | 新订单覆盖、暴露边界与尚未实现的接线 | 补充解释或修正范围；历史结果保持原义 | [examples/capstone/new-order-reserve/inputs/NEW-101.json](../examples/capstone/new-order-reserve/inputs/NEW-101.json)<br>[examples/capstone/new-order-reserve/inputs/NEW-102.json](../examples/capstone/new-order-reserve/inputs/NEW-102.json) |

## Notebook 阅读内容

九份 Notebook 各新增一个 `labNN-knowledge` Markdown 单元，在清理前归纳本次观察应支持什么判断。保留原有主流程与单元 ID；第 03 章摘录补齐实际调用中的文件范围和失败停止条件。`check_notebook_quotes` 检查 Python 摘录是实际某个代码格的连续片段，允许缩进和空行不同，不判断解释质量。

第 01 章仅改动一条凭证错误提示，将 GLM API Key 改为 DEEPSEEK_API_KEY；其它 8 份 Notebook 的代码格内容、执行计数、输出与元数据保持。新增知识小结不产生模型调用。整份文件哈希改变，旧迁移运行仍只对应其旧源码，不被改写为当前版本实跑。

| Notebook | 主要知识判断 | 当前核对 |
|---|---|---|
| [examples/01-agent-loop/workshop.ipynb](../examples/01-agent-loop/workshop.ipynb) | 动作建议、工具观察与样例因果范围 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/02-instruction-control/workshop.ipynb](../examples/02-instruction-control/workshop.ipynb) | 活跃任务身份、告警状态与取消责任 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/03-context-engine/workshop.ipynb](../examples/03-context-engine/workshop.ipynb) | 磁盘、回执、请求装配与判断的区别 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/04-agents-md/workshop.ipynb](../examples/04-agents-md/workshop.ipynb) | 持久规则的目录范围与知识类型 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/05-context-compression/workshop.ipynb](../examples/05-context-compression/workshop.ipynb) | 状态保留、原文回查与数值验证 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/06-thread-fork/workshop.ipynb](../examples/06-thread-fork/workshop.ipynb) | 历史前缀、污染条件与资源隔离 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/07-skill-extraction/workshop.ipynb](../examples/07-skill-extraction/workshop.ipynb) | 从旧答案抽象方法，用改变条件的反例验证 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/08-progressive-disclosure/workshop.ipynb](../examples/08-progressive-disclosure/workshop.ipynb) | 分层加载的职责、读取代价与语义检查 | 摘录、稳定 ID、知识小结与实际代码对应 |
| [examples/09-isolation-approval/workshop.ipynb](../examples/09-isolation-approval/workshop.ipynb) | 目录权限、动作授权与结果检查 | 摘录、稳定 ID、知识小结与实际代码对应 |


## 历史文本、安装列表与来源

四份 output_timing.txt 保持原文，解释集中在 [练习导航](../examples/README.md)。它们包含历史模型、绝对路径或旧条件，不用于当前工具次数、耗时或 DeepSeek 通过判断。依赖清单保持安装用途；修正 Notebook 依赖注释，并在 [环境准备](MODEL_SETUP.md) 区分直接版本固定、历史安装观察和带哈希的完整开发锁。

外部协议核对使用 [DeepSeek Codex 接入](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/) 与 [工具调用](https://api-docs.deepseek.com/guides/tool_calls/)。官网说明支持原生 Responses；思考模式下带工具的 Chat 历史需要正确保留 reasoning_content，课程自建循环因此明确关闭 thinking。恢复与进程说明继续参考 [SQLite Backup API](https://www.sqlite.org/backup.html) 和 [Python 3.12 subprocess](https://docs.python.org/3.12/library/subprocess.html)。协议与模型可变化，本文不将官方页面当作本地课程已运行的证据。

当前安装技能目录、插件清单与公开检索没有定位到准确的 romain-skill 来源。本次没有安装猜测项或声称使用该技能，按源码对照和现有质量流程完成逐份整理。

## 验证与实际结论

文档契约扩展到全部 42 份 Markdown。Notebook 稳定 ID 与代码摘录均检查；摘录回归先在旧检查器上失败，再修复通过。完整 `make verify` 包括源码/Notebook 编译、数据格式、shell、Ruff 与离线回归；验收数字以最终检查输出为准。

本次没有新增付费模型运行。第 18 章此前的真实结果 4/4 → 4/4 仍无通过率增益；新订单与第二轮学习没有新增结果。内容审阅、离线通过、历史实跑和生产验收是不同证据层次，不能由本记录互相补足。
