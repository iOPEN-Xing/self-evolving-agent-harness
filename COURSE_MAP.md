# 课程与代码对照

课程按 23 讲、五部分展开。下表对照每讲的技术主题与本仓库的练习实现。

第 01 至 09 讲使用 Codex SDK 与 Jupyter Notebook；第 11 至 23 讲使用 Python 脚本，分别调用 Hermes、SkillClaw 原生接口或练习自建的模型与工具循环，具体实现以各讲 README 为准。第 17 讲生命周期练习不调用模型。

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
| 经验回流 | 10 | Hermes 与递归自我改进 | 背景与机制定位 |
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
| 群体进化 | 23 | 完整的自进化巡检 Agent | [examples/23-final-assembly/](examples/23-final-assembly/) |
| 综合实践 | CP | 端到端自进化实战 | [examples/capstone/](examples/capstone/) |

## 上游依赖

Hermes 与 SkillClaw 源码放在 `.deps/` 目录（已 gitignore），作为上游依赖引用：

- Hermes Agent：`.deps/hermes-agent/`（commit aaf9688）
- SkillClaw：`.deps/SkillClaw/`（commit bf4dc2e）

依赖准备入口为 [scripts/setup_deps.sh](scripts/setup_deps.sh)。上述提交号为本轮核对的本地版本；准备脚本安装分支当前版本，不保证自动恢复这两个提交。
