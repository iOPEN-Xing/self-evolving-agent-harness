# 课程与代码对照

课程按 23 讲、五部分展开。下表对照每讲的技术主题与本仓库的练习实现。

第 01 至 09 讲使用 Codex SDK 与 Jupyter Notebook；第 11 至 23 讲使用 Python 脚本，分别调用 Hermes、SkillClaw 原生接口或练习自建的模型与工具循环，具体实现以各讲 README 为准。第 17 讲生命周期练习不调用模型。

[自进化主线](docs/SELF_EVOLUTION.md) 将这些机制连接到“经历、候选、验证、决定、实际重用”。表中的课程主题描述学习目标；本次代码实现与真实验证范围按各章及验证记录判断。

| 部分 | 讲次 | 技术主题 | 练习实现 |
| --- | --- | --- | --- |
| 初识 Harness | 01 | Agent Loop | [examples/01-agent-loop/](examples/01-agent-loop/) |
| 初识 Harness | 02 | 运行时控制 | [examples/02-instruction-control/](examples/02-instruction-control/) |
| 初识 Harness | 03 | 上下文引擎 | 已实现：[examples/03-context-engine/](examples/03-context-engine/) |
| 长程运行 | 04 | AGENTS.md | 已实现：[examples/04-agents-md/](examples/04-agents-md/) |
| 长程运行 | 05 | 上下文压缩 | 已实现：[examples/05-context-compression/](examples/05-context-compression/) |
| 长程运行 | 06 | 会话分支 | 已实现：[examples/06-thread-fork/](examples/06-thread-fork/) |
| 长程运行 | 07 | 经验技能化 | 已实现：[examples/07-skill-extraction/](examples/07-skill-extraction/) |
| 长程运行 | 08 | 渐进披露 | 已实现：[examples/08-progressive-disclosure/](examples/08-progressive-disclosure/) |
| 长程运行 | 09 | 隔离与审批 | 已实现：[examples/09-isolation-approval/](examples/09-isolation-approval/) |
| 经验回流 | 10 | 执行与学习的接口 | [机制桥接](docs/10-learning-loop.md) |
| 经验回流 | 11 | Background Review | [examples/11-background-review/](examples/11-background-review/) |
| 经验回流 | 12 | Memory 维护 | [examples/12-memory-revision/](examples/12-memory-revision/) |
| 经验回流 | 13 | 旧 Session 搜索与还原 | [examples/13-session-lookup/](examples/13-session-lookup/) |
| 经验回流 | 14 | Memory Provider 与企业知识 | [examples/14-external-memory-provider/](examples/14-external-memory-provider/) |
| 经验回流 | 15 | 自主技能学习 | [examples/15-skill-autocreation/](examples/15-skill-autocreation/) |
| 经验回流 | 16 | 增量技能演化 | [examples/16-skill-incremental-patch/](examples/16-skill-incremental-patch/) |
| 可信可控 | 17 | Skill Curation | [examples/17-curator/](examples/17-curator/) |
| 可信可控 | 18 | 业务 Eval | [examples/18-business-eval/](examples/18-business-eval/) |
| 可信可控 | 19 | 企业数据集：怎样把真实经历变成可重跑的评测题？ | [examples/19-enterprise-dataset/](examples/19-enterprise-dataset/) |
| 可信可控 | 20 | 离线优化 | [examples/20-gepa-offline-optimization/](examples/20-gepa-offline-optimization/) |
| 群体进化 | 21 | 离线程序记忆：怎样为 Skill 修订准备可靠经历？ | [examples/21-skillclaw-session-collection/](examples/21-skillclaw-session-collection/) |
| 群体进化 | 22 | 技能共享与分发 | [examples/22-skillclaw-shared-revision/](examples/22-skillclaw-shared-revision/) |
| 群体进化 | 23 | 受控巡检：候选、评测、版本与恢复 | [examples/23-final-assembly/](examples/23-final-assembly/) |
| 综合实践 | CP | 原生支付学习循环与独立采用决定 | [examples/capstone/](examples/capstone/) |

## 代码与文档契约

各章及跨章专题的稳定入口、函数和 Notebook 单元见 [chapters.json](docs/chapters.json)，由 scripts/check_docs.py 检查。`Assembly.candidate` 等限定名同时核对方法所属的类。单元 ID 比编辑后易变的行号更适合定位。本地链接的目标须随仓库发布，仅在本机存在的忽略文件无法使检查通过。

## 上游依赖

四个上游的完整提交由 [deps.lock.json](deps.lock.json) 固定，获取脚本是 [setup_deps.sh](scripts/setup_deps.sh)。其中 Hermes aaf9688519cca58dd5f76a589a0911aff269b060、SkillClaw bf4dc2ee9430ecffb60e19630d26f57dfa2bd326 沿用课程引用版本。

模型统一 deepseek-flash，官网直连；依赖与凭证准备见 [MODEL_SETUP](docs/MODEL_SETUP.md)。源码可复现不等于已完成整轮模型与平台验证，实际覆盖见 [本轮验证](docs/DOCUMENTATION_VALIDATION.md)。
